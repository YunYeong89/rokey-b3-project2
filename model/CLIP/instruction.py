"""웹캠 공유메모리 프레임을 SAM2/FastSAM으로 분할하고, instruction과의 CLIP 점수가 기준을 넘는 마스크를 표시합니다."""

import argparse
from collections import deque
import os
from pathlib import Path
import select
import sys
from threading import Event, Lock, Thread
import time

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
import numpy as np
from PIL import Image, ImageOps
import torch

CLIP_DIR = Path(__file__).resolve().parent
MODEL_DIR = CLIP_DIR.parent
sys.path[:0] = [str(MODEL_DIR / "sam2"), str(MODEL_DIR.parents[1] / "webcam")]
import clip


class Sam2Segmenter:
    """SAM2 자동 분할 (sam2/eval.py와 같은 기본값)."""

    def __init__(self, variant, device):
        from eval import SAM2Evaluator
        self.evaluator = SAM2Evaluator(argparse.Namespace(
            model=variant or "sam2.1_tiny", device=device, conf=0.8,
            stability_thresh=0.95, points_per_side=16, points_per_batch=16,
        ))

    def __call__(self, rgb):
        result = self.evaluator.predict(rgb)
        return result.masks, result.boxes


class FastSamSegmenter:
    """FastSAM everything 모드 (fastsam/eval_stream.py와 같은 기본값), 원본 해상도 마스크를 반환합니다."""

    def __init__(self, variant, device):
        from ultralytics import FastSAM
        self.model = FastSAM(str(MODEL_DIR / "fastsam" / "checkpoints" / f"{variant or 'FastSAM-s'}.pt"))
        self.device = device

    def __call__(self, rgb):
        result = self.model(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), device=self.device, imgsz=640,
                            conf=0.4, iou=0.9, retina_masks=True, verbose=False)[0]
        if result.masks is None:
            return np.empty((0, *rgb.shape[:2]), dtype=bool), np.empty((0, 4), dtype=np.float32)
        return result.masks.data.cpu().numpy().astype(bool), result.boxes.xyxy.cpu().numpy()


SEGMENTERS = {"sam2": Sam2Segmenter, "fastsam": FastSamSegmenter}


def masked_crop(rgb, mask, box, size):
    """마스크 밖을 회색으로 칠한 박스 영역을 비율 유지·정사각 패딩한 size×size PIL 이미지로 반환합니다."""
    x1, y1, x2, y2 = box.astype(int) + (0, 0, 1, 1)
    crop = np.where(mask[y1:y2, x1:x2, None], rgb[y1:y2, x1:x2], 128).astype(np.uint8)
    return ImageOps.pad(Image.fromarray(crop), (size, size), color=(128, 128, 128))


class ClipSelector:
    """마스크 조각의 CLIP 이미지 임베딩과 instruction 텍스트 임베딩의 코사인 유사도를 계산합니다."""

    MIN_AREA, MAX_AREA = 0.001, 0.8  # 프레임 대비 면적 비율: 노이즈와 책상·배경 제외

    def __init__(self, name, device):
        self.device = device
        self.model, self.preprocess = clip.load(name, device=device,
                                                download_root=str(CLIP_DIR / "checkpoints"))
        self.size = self.model.visual.input_resolution
        self.text = None  # 정규화된 텍스트 임베딩 (D,)

    @torch.inference_mode()
    def set_text(self, instruction):
        text = self.model.encode_text(clip.tokenize(f"a photo of a {instruction}").to(self.device))
        self.text = (text / text.norm(dim=-1, keepdim=True)).float().cpu().numpy()[0]

    @classmethod
    def keep(cls, masks):
        """면적 필터를 통과한 마스크 인덱스를 반환합니다."""
        area = masks.mean(axis=(1, 2))
        return np.flatnonzero((area > cls.MIN_AREA) & (area < cls.MAX_AREA))

    @torch.inference_mode()
    def embed(self, rgb, masks, boxes):
        """마스크별 L2 정규화 CLIP 이미지 임베딩 (N, D)를 반환합니다."""
        crops = [self.preprocess(masked_crop(rgb, mask, box, self.size)) for mask, box in zip(masks, boxes)]
        image = self.model.encode_image(torch.stack(crops).to(self.device))
        return (image / image.norm(dim=-1, keepdim=True)).float().cpu().numpy()

    def score(self, rgb, masks, boxes):
        """면적 필터를 통과한 마스크 인덱스와 각 유사도를 반환합니다."""
        keep = self.keep(masks)
        if self.text is None or len(keep) == 0:
            return keep[:0], np.empty(0, dtype=np.float32)
        return keep, self.embed(rgb, masks[keep], boxes[keep]) @ self.text


class InstructionStream:
    """추론 스레드는 분할·선택, 입력 스레드는 instruction 갱신, 메인 스레드는 OpenCV 표시를 담당합니다."""

    def __init__(self, args):
        self.args = args
        self.device = args.device if args.device != "auto" else (
            "cuda" if torch.cuda.is_available()
            else "mps" if torch.backends.mps.is_available()
            else "cpu"
        )
        self.name = "SAM2" if args.model == "sam2" else "FastSAM"
        self.segmenter = SEGMENTERS[args.model](args.variant, self.device)
        self.selector = ClipSelector(args.clip_model, self.device)
        self.source = None  # run()에서 연결해 공유메모리 없이도 process()를 쓸 수 있습니다.
        self.instruction = args.instruction
        self.stop = Event()
        self.lock = Lock()
        self.latest = None
        self.error = None

    def _read_input(self):
        """터미널에 입력한 줄을 다음 프레임부터 새 instruction으로 사용합니다."""
        while not self.stop.is_set():
            if not select.select([sys.stdin], [], [], 0.2)[0]:
                continue
            line = sys.stdin.readline()
            if not line:  # stdin closed
                break
            if line.strip():
                self.instruction = line.strip()
                print(f"Instruction: {self.instruction}", flush=True)

    def _infer(self):
        text, previous_id = None, 0
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

                start = time.perf_counter()
                if self.instruction != text:
                    text = self.instruction
                    self.set_instruction(text)
                overlay, status = self.process(bgr)

                now = time.perf_counter()
                completed.append(now)
                hz = (len(completed) - 1) / (now - completed[0]) if len(completed) > 1 else 1.0 / (now - start)
                with self.lock:
                    self.latest = (overlay, hz, now, text, status)
                previous_id = frame_id
        except Exception as error:
            self.error = error
            self.stop.set()

    def set_instruction(self, text):
        self.selector.set_text(text)

    def process(self, bgr):
        """프레임을 분할하고 CLIP 점수가 기준 이상인 마스크를 그린 (overlay, 상태 문자열)을 반환합니다."""
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        masks, boxes = self.segmenter(rgb)
        keep, scores = self.selector.score(rgb, masks, boxes)
        hit = scores >= self.args.min_score
        overlay = self.render(bgr, masks[keep[hit]], boxes[keep[hit]], scores[hit])
        return overlay, f"Matches: {hit.sum()}/{len(masks)}"

    @staticmethod
    def render(bgr, masks, boxes, scores):
        """기준을 넘은 마스크를 초록색으로 칠하고 윤곽과 점수를 그린 BGR 이미지를 반환합니다."""
        overlay = bgr.copy()
        for mask in masks:
            overlay[mask] = overlay[mask] * 0.5 + np.array((0, 255, 0)) * 0.5
            contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, (0, 255, 0), 2)
        for (x1, y1, _, _), score in zip(boxes.astype(int), scores):
            cv2.putText(overlay, f"{score:.2f}", (x1 + 4, y1 + 20), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 255, 255), 2, cv2.LINE_AA)
        return overlay

    def open_source(self):
        """read(timeout) → (frame_id, stamp_ns, frame), close(), frame(빈 화면 크기)를 갖는 스트림 소스를 엽니다.
        웹캠 공유메모리는 스트림에서만 써서, webcam/이 없는 ROS 패키지에서도 import됩니다."""
        from frame_shm import FrameShm
        print(f"Source: shared memory '{self.args.shm_name}'.", flush=True)
        return FrameShm(self.args.shm_name)

    def run(self):
        self.source = self.open_source()
        window = f"{self.name} instruction"
        worker = Thread(target=self._infer, name="instruction-inference")
        reader = Thread(target=self._read_input, name="instruction-input")
        blank = np.zeros(self.source.frame.shape, dtype=np.uint8)
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        worker.start()
        reader.start()
        print(f"{self.name} + CLIP {self.args.clip_model} on {self.device}.\n"
              "Type an instruction and press Enter to change the target. Press Q or Esc in the window to exit.",
              flush=True)
        try:
            while not self.stop.is_set():
                with self.lock:
                    latest = self.latest
                if latest is None:
                    overlay, text = blank, f"{self.name}: waiting for first inference | Q: quit"
                else:
                    overlay, hz, updated, instruction, status = latest
                    if time.perf_counter() - updated > max(1.0, 2.0 / hz):  # publisher stopped
                        hz = 0.0
                    target = instruction or "type an instruction in the terminal"
                    text = f"{self.name}: {hz:.1f} Hz | {target} | {status} | Q: quit"

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
            reader.join()
            self.source.close()
            cv2.destroyAllWindows()
        if self.error is not None:
            raise RuntimeError(f"{self.name} instruction inference failed.") from self.error


def create_parser(description="Segment webcam shared-memory frames and highlight every mask whose CLIP score "
                              "for the text instruction exceeds --min-score."):
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--model", choices=tuple(SEGMENTERS), default="fastsam", help="Segmentation model.")
    parser.add_argument("--variant",
                        help="Model variant, e.g. sam2.1_tiny|sam2.1_small or FastSAM-s|FastSAM-x. "
                             "Defaults to sam2.1_tiny / FastSAM-s.")
    parser.add_argument("--instruction",
                        help="Initial target in English, e.g. 'red cup'. Type a new one in the terminal at runtime.")
    parser.add_argument("--clip-model", choices=clip.available_models(), default="ViT-B/32",
                        help="CLIP weights are downloaded to checkpoints/ on first use.")
    parser.add_argument("--min-score", type=float, default=0.25,
                        help="Minimum CLIP cosine similarity for a mask to be shown.")
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto",
                        help="Inference device. Auto selects CUDA, then MPS, then CPU.")
    parser.add_argument("--shm-name", default="webcam0",
                        help="Shared-memory name created by webcam/publisher.py. Input frames are BGR.")
    parser.add_argument("--display-hz", type=float, default=30, help="OpenCV refresh rate.")
    return parser


if __name__ == "__main__":
    InstructionStream(create_parser().parse_args()).run()
