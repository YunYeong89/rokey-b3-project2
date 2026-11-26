import time

from frame_shm import FrameShm
from webcam import Webcam


class WebcamPublisher:
    def __init__(self, name="webcam0", camera="C270", hz=30):
        self.period = 1.0 / hz
        self.cam = Webcam(camera, hz)
        self.shm = FrameShm(name, self.cam.read().shape)

    def run(self):
        try:
            while True:
                start = time.perf_counter()
                self.shm.write(self.cam.read())
                time.sleep(max(0.0, self.period - (time.perf_counter() - start)))
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self):
        self.cam.close()
        self.shm.close()


if __name__ == "__main__":
    WebcamPublisher().run()
