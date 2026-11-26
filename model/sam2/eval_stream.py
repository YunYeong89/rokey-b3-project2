"""웹캠 공유메모리의 최신 프레임을 SAM2로 분할해 OpenCV 창에 표시합니다."""

from collections import deque
import logging
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time

import cv2
import numpy as np

from eval import SAM2Evaluator, create_parser, parse_args as parse_eval_args

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "webcam"))
from frame_shm import FrameShm


class SAM2StreamEvaluator(SAM2Evaluator):
    def __init__(self, args):
        self.source = FrameShm(args.shm_name)
        try:
            super().__init__(args)
        except Exception:
            self.source.close()
            raise
        self.display_hz = args.display_hz
        self.stop = Event()
        self.lock = Lock()
        self.latest = None
        self.error = None

    def _infer(self):
        previous_id = 0
        completed = deque(maxlen=10)
        try:
            while not self.stop.is_set():
                try:
                    frame_id, stamp_ns, bgr = self.source.read(timeout=0.1)
                except TimeoutError:
                    continue
                if frame_id == previous_id or stamp_ns == 0:
                    self.stop.wait(0.005)
                    continue

                start = time.perf_counter()
                result = self.predict(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                overlay = cv2.cvtColor(self.render_result(result), cv2.COLOR_RGB2BGR)
                now = time.perf_counter()
                completed.append(now)
                hz = (
                    (len(completed) - 1) / (now - completed[0])
                    if len(completed) > 1 else 1.0 / (now - start)
                )
                with self.lock:
                    self.latest = (overlay, hz, now, frame_id, len(result.masks))
                previous_id = frame_id
        except Exception as error:
            self.error = error
            self.stop.set()

    def run(self):
        window = "SAM2 webcam"
        worker = Thread(target=self._infer, name="sam2-inference")
        blank = np.zeros(self.source.frame.shape, dtype=np.uint8)
        worker_started = False
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            worker.start()
            worker_started = True
            print("Reading shared memory. Press Q or Esc to exit.", flush=True)
            while not self.stop.is_set():
                with self.lock:
                    latest = self.latest
                if latest is None:
                    overlay = blank
                    text = "SAM2: 0.00 Hz | Waiting for first inference | Q: quit"
                else:
                    overlay, hz, updated, frame_id, count = latest
                    if time.perf_counter() - updated > max(1.0, 2.0 / hz):
                        hz = 0.0
                    text = f"SAM2: {hz:.2f} Hz | Frame: {frame_id} | Masks: {count} | Q: quit"

                display = cv2.copyMakeBorder(overlay, 40, 0, 0, 0, cv2.BORDER_CONSTANT)
                cv2.putText(display, text, (10, 27), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.imshow(window, display)
                key = cv2.waitKey(max(1, round(1000 / self.display_hz))) & 0xFF
                if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            if worker_started:
                worker.join()
            self.source.close()
            cv2.destroyAllWindows()
        if self.error is not None:
            raise RuntimeError("SAM2 stream inference failed.") from self.error


def parse_args(argv=None):
    parser = create_parser("Segment webcam shared-memory frames and visualize masks with inference Hz.")
    parser.add_argument("--shm-name", default="webcam0",
                        help="Shared-memory name created by webcam/publisher.py. Input frames are BGR.")
    parser.add_argument("--display-hz", type=float, default=30,
                        help="OpenCV refresh rate; the displayed SAM2 Hz measures completed inference updates.")
    args = parse_eval_args(argv, parser=parser)
    if not np.isfinite(args.display_hz) or args.display_hz <= 0:
        parser.error("--display-hz must be a positive finite number.")
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    print(f"Starting {args.model} on shared memory '{args.shm_name}'.", flush=True)
    try:
        evaluator = SAM2StreamEvaluator(args)
    except FileNotFoundError:
        logging.error("Shared memory '%s' not found. Start webcam/publisher.py first.", args.shm_name)
        raise SystemExit(1)
    evaluator.run()


if __name__ == "__main__":
    main()
