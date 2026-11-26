"""웹캠 공유메모리의 최신 프레임을 FastSAM으로 분할해 OpenCV 창에 표시합니다."""

import argparse
from collections import deque
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time

import cv2
import numpy as np
import torch
from ultralytics import FastSAM

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "webcam"))
from frame_shm import FrameShm

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"


class FastSAMStreamEvaluator:
    def __init__(self, args):
        self.args = args
        self.device = args.device if args.device != "auto" else (
            "cuda" if torch.cuda.is_available()
            else "mps" if torch.backends.mps.is_available()
            else "cpu"
        )
        self.model = FastSAM(str(CHECKPOINT_DIR / f"{args.model}.pt"))  # downloaded on first use
        self.source = FrameShm(args.shm_name)
        self.stop = Event()
        self.lock = Lock()
        self.latest = None
        self.error = None

    def predict(self, bgr):
        """BGR 프레임을 분할해 마스크를 겹친 BGR 이미지와 마스크 개수를 반환합니다."""
        result = self.model(bgr, device=self.device, imgsz=self.args.imgsz, conf=self.args.conf,
                            iou=self.args.iou, retina_masks=False, verbose=False)[0]
        count = 0 if result.masks is None else len(result.masks)
        return result.plot(boxes=False, labels=False), count

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
                    self.stop.wait(0.001)
                    continue

                overlay, count = self.predict(bgr)
                latency_ms = (time.time_ns() - stamp_ns) / 1e6  # capture -> overlay ready
                completed.append(time.perf_counter())
                hz = (len(completed) - 1) / (completed[-1] - completed[0]) if len(completed) > 1 else 0.0
                with self.lock:
                    self.latest = (overlay, hz, latency_ms, completed[-1], frame_id, count)
                previous_id = frame_id
        except Exception as error:
            self.error = error
            self.stop.set()

    def run(self):
        window = "FastSAM webcam"
        worker = Thread(target=self._infer, name="fastsam-inference")
        blank = np.zeros(self.source.frame.shape, dtype=np.uint8)
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        worker.start()
        print(f"FastSAM {self.args.model} on {self.device}, shared memory '{self.args.shm_name}'. "
              "Press Q or Esc to exit.", flush=True)
        try:
            while not self.stop.is_set():
                with self.lock:
                    latest = self.latest
                if latest is None:
                    overlay, text = blank, "FastSAM: waiting for first inference | Q: quit"
                else:
                    overlay, hz, latency_ms, updated, frame_id, count = latest
                    if time.perf_counter() - updated > 1.0:  # publisher stopped
                        hz = 0.0
                    text = (f"FastSAM: {hz:.1f} Hz | Latency: {latency_ms:.0f} ms | "
                            f"Frame: {frame_id} | Masks: {count} | Q: quit")

                display = cv2.copyMakeBorder(overlay, 40, 0, 0, 0, cv2.BORDER_CONSTANT)
                cv2.putText(display, text, (10, 27), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.imshow(window, display)
                key = cv2.waitKey(max(1, round(1000 / self.args.display_hz))) & 0xFF
                if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            worker.join()
            self.source.close()
            cv2.destroyAllWindows()
        if self.error is not None:
            raise RuntimeError("FastSAM stream inference failed.") from self.error


def parse_args():
    parser = argparse.ArgumentParser(
        description="Segment webcam shared-memory frames with FastSAM and visualize masks with inference Hz.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", choices=("FastSAM-s", "FastSAM-x"), default="FastSAM-s",
                        help="Weights are downloaded to checkpoints/ on first use.")
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto",
                        help="Inference device. Auto selects CUDA, then MPS, then CPU.")
    parser.add_argument("--imgsz", type=int, default=640,
                        help="Inference size. Lower is faster but yields coarser, fewer masks.")
    parser.add_argument("--conf", type=float, default=0.4, help="Minimum mask confidence.")
    parser.add_argument("--iou", type=float, default=0.9, help="NMS IoU threshold.")
    parser.add_argument("--shm-name", default="webcam0",
                        help="Shared-memory name created by webcam/publisher.py. Input frames are BGR.")
    parser.add_argument("--display-hz", type=float, default=30, help="OpenCV refresh rate.")
    return parser.parse_args()


if __name__ == "__main__":
    FastSAMStreamEvaluator(parse_args()).run()
