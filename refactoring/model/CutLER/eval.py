"""VideoCutLER로 프레임 묶음(클립)의 객체를 클래스 구분 없이 분할·추적하고 결과 영상을 저장합니다."""

import argparse
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
from urllib.request import urlopen

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
import numpy as np
import torch

import detectron2.data.transforms as T
from detectron2.checkpoint import DetectionCheckpointer
from detectron2.config import get_cfg
from detectron2.modeling import build_model
from detectron2.projects.deeplab import add_deeplab_config

from mask2former import add_maskformer2_config
from mask2former_video import add_maskformer2_video_config


ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "configs" / "imagenet_video" / "video_mask2former_R50_cls_agnostic.yaml"
CHECKPOINT = ROOT / "checkpoints" / "videocutler_m2f_rn50.pth"
CHECKPOINT_URL = ("https://drive.usercontent.google.com/download"
                  "?id=1ozeh3U3uxZImjabKVae8kNZSoNuvFx49&export=download&confirm=t")
IMAGE_DIR = ROOT / "images"
DEFAULT_INPUT = IMAGE_DIR / "demo"
DEFAULT_OUTPUT = IMAGE_DIR / "result.mp4"
FRAME_SUFFIXES = (".jpg", ".jpeg", ".png")


@dataclass
class VideoCutLERResult:
    frames: np.ndarray  # (T, H, W, 3) RGB uint8
    masks: np.ndarray   # (N, T, H, W) bool, 같은 인덱스는 클립 전체에서 같은 객체
    scores: np.ndarray  # (N,)


class VideoCutLEREvaluator:
    def __init__(self, args: argparse.Namespace):
        self.device = args.device
        if self.device == "auto":
            self.device = (
                "cuda" if torch.cuda.is_available()
                else "mps" if torch.backends.mps.is_available()
                else "cpu"
            )
        self.conf = args.conf

        if not CHECKPOINT.is_file():
            CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
            temporary = CHECKPOINT.with_suffix(".pth.part")
            logging.info("Downloading checkpoint: %s", CHECKPOINT.name)
            try:
                with urlopen(CHECKPOINT_URL, timeout=60) as source:
                    with temporary.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
                temporary.replace(CHECKPOINT)
            finally:
                temporary.unlink(missing_ok=True)

        cfg = get_cfg()
        add_deeplab_config(cfg)
        add_maskformer2_config(cfg)
        add_maskformer2_video_config(cfg)
        cfg.merge_from_file(str(CONFIG_FILE))
        cfg.MODEL.WEIGHTS = str(CHECKPOINT)
        cfg.MODEL.DEVICE = self.device

        logging.info("Loading model: VideoCutLER R50 | device: %s", self.device)
        self.model = build_model(cfg).eval()
        DetectionCheckpointer(self.model).load(cfg.MODEL.WEIGHTS)
        self.aug = T.ResizeShortestEdge(cfg.INPUT.MIN_SIZE_TEST, cfg.INPUT.MAX_SIZE_TEST)

    @torch.inference_mode()
    def predict(self, frames) -> VideoCutLERResult:
        """RGB uint8 프레임 묶음 (T, H, W, 3)을 받아 --conf 이상인 객체의 프레임별 마스크와 점수를 반환합니다."""
        frames = np.asarray(frames)
        images = [
            torch.as_tensor(self.aug.get_transform(frame).apply_image(frame).astype("float32").transpose(2, 0, 1))
            for frame in frames
        ]
        output = self.model([{"image": images, "height": frames.shape[1], "width": frames.shape[2]}])
        scores = np.array(output["pred_scores"], dtype=np.float32)
        keep = scores >= self.conf
        masks = torch.stack(output["pred_masks"]).numpy()[keep]
        return VideoCutLERResult(frames=frames, masks=masks, scores=scores[keep])

    @staticmethod
    def render_result(result: VideoCutLERResult) -> np.ndarray:
        """각 프레임에 객체별로 고정된 색 마스크를 겹친 RGB 배열 (T, H, W, 3)을 반환합니다."""
        overlay = result.frames.astype(np.float32)
        colors = np.random.default_rng(0).integers(40, 256, size=(len(result.masks), 3))
        for index in np.argsort(result.masks.sum(axis=(1, 2, 3)))[::-1]:
            mask = result.masks[index]
            overlay[mask] = overlay[mask] * 0.55 + colors[index] * 0.45
        return overlay.astype(np.uint8)

    @staticmethod
    def save_result(result: VideoCutLERResult, output: str | Path, fps: float) -> Path:
        """마스크를 겹친 프레임들을 MP4로 저장합니다."""
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        frames = VideoCutLEREvaluator.render_result(result)
        writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps, frames.shape[2:0:-1])
        for frame in frames:
            writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        writer.release()
        return output


def read_frames(path: Path) -> np.ndarray:
    """프레임 이미지 디렉터리(파일명 순) 또는 영상 파일을 RGB 프레임 배열 (T, H, W, 3)로 읽습니다."""
    if path.is_dir():
        frames = [cv2.imread(str(item)) for item in sorted(path.iterdir()) if item.suffix.lower() in FRAME_SUFFIXES]
    else:
        video = cv2.VideoCapture(str(path))
        frames = []
        while (frame := video.read()[1]) is not None:
            frames.append(frame)
        video.release()
    return np.stack([cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) for frame in frames])


def create_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto",
                        help="Inference device. Auto selects CUDA, then MPS, then CPU.")
    parser.add_argument("--conf", type=float, default=0.8,
                        help="Minimum instance score in [0, 1]. Lower values raise recall.")
    return parser


def parse_args(argv=None, *, parser=None) -> argparse.Namespace:
    if parser is None:
        parser = create_parser("Run VideoCutLER class-agnostic video instance segmentation and save a colored MP4.")
        parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                            help="Directory of frame images (sorted by name) or a video file.")
        parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                            help="Output MP4 path for the colored mask overlay.")
        parser.add_argument("--fps", type=float, default=10, help="Output video frame rate.")
    args = parser.parse_args(argv)
    if not 0 <= args.conf <= 1:
        parser.error("--conf must be between 0 and 1.")
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    evaluator = VideoCutLEREvaluator(args)
    frames = read_frames(args.input)
    result = evaluator.predict(frames)
    output = evaluator.save_result(result, args.output, args.fps)
    logging.info("Saved %s instances over %s frames to %s", len(result.masks), len(frames), output)
    return result


if __name__ == "__main__":
    main()
