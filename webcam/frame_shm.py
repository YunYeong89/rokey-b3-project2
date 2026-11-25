import time
from multiprocessing import resource_tracker, shared_memory

import numpy as np

HEADER = 5  # int64: seq, h, w, c, stamp_ns


class FrameShm:
    """Layout: int64[5] header (seq, h, w, c, stamp_ns) + uint8 HxWxC frame.
    Pass shape to create (publisher), omit it to attach (reader)."""

    def __init__(self, name, shape=None):
        self.owner = shape is not None
        size = HEADER * 8 + int(np.prod(shape)) if self.owner else 0
        self.shm = shared_memory.SharedMemory(name, create=self.owner, size=size)
        self.header = np.ndarray((HEADER,), np.int64, self.shm.buf)
        if self.owner:
            self.header[:] = (0, *shape, 0)
        else:
            # Python < 3.13: otherwise the reader unlinks the publisher's segment on exit
            resource_tracker.unregister(self.shm._name, "shared_memory")
        self.frame = np.ndarray(tuple(self.header[1:4]), np.uint8, self.shm.buf, offset=HEADER * 8)

    def write(self, frame):
        self.header[0] += 1  # odd: writing
        self.frame[:] = frame
        self.header[4] = time.time_ns()
        self.header[0] += 1  # even: done

    def read(self, timeout=None):
        """Return (frame_id, stamp_ns, frame copy); timeout bounds incomplete-frame retries."""
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            seq = int(self.header[0])
            if seq % 2 == 0:
                frame = self.frame.copy()
                stamp = int(self.header[4])
                if seq == self.header[0]:
                    return seq // 2, stamp, frame
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("No complete shared-memory frame available.")
            time.sleep(0.001)

    def close(self):
        del self.header, self.frame  # release buffer views before closing
        self.shm.close()
        if self.owner:
            self.shm.unlink()
