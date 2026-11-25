"""SAM2 모델을 선택해 이미지 전체를 자동 분할하고 결과를 저장합니다."""

import argparse
from dataclasses import dataclass
from io import BytesIO
import logging
import os
from pathlib import Path
import shutil
from urllib.request import urlopen

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
from PIL import Image
import torch

from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
from sam2.build_sam import build_sam2
from sam2.model_info import DEFAULT_MODEL_NAME, MODEL_INFOS, get_model_info


IMAGE_DIR = Path(__file__).resolve().parent / "images"
DEFAULT_INPUT = IMAGE_DIR / "demo_images.png"
DEFAULT_OUTPUT = IMAGE_DIR / "result.png"
DEMO_IMAGE_URL = "https://raw.githubusercontent.com/facebookresearch/sam2/main/notebooks/images/groceries.jpg"


@dataclass
class SAM2Result:
    image: np.ndarray
    masks: np.ndarray
    scores: np.ndarray
    boxes: np.ndarray


class SAM2Evaluator:
    def __init__(self, args: argparse.Namespace):
        self.model_info = get_model_info(args.model)
        self.device = args.device
        if self.device == "auto":
            self.device = (
                "cuda" if torch.cuda.is_available()
                else "mps" if torch.backends.mps.is_available()
                else "cpu"
            )

        checkpoint = self.model_info.checkpoint_path
        if not checkpoint.is_file():
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temporary = checkpoint.with_suffix(".pt.part")
            logging.info("Downloading checkpoint: %s", checkpoint.name)
            try:
                with urlopen(self.model_info.checkpoint_url, timeout=60) as source:
                    with temporary.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
                temporary.replace(checkpoint)
            finally:
                temporary.unlink(missing_ok=True)

        logging.info("Loading model: %s | device: %s", args.model, self.device)
        self.model = build_sam2(
            config_file=self.model_info.config_name,
            ckpt_path=checkpoint,
            device=self.device,
        )
        self.generator = SAM2AutomaticMaskGenerator(
            self.model,
            points_per_side=args.points_per_side,
            points_per_batch=args.points_per_batch,
            pred_iou_thresh=args.conf,
            stability_score_thresh=args.stability_thresh,
        )

    @torch.inference_mode()
    def predict(self, image: str | Path | np.ndarray) -> SAM2Result:
        """이미지 경로 또는 RGB uint8 배열을 받아 마스크·품질 점수·XYXY 박스를 반환합니다."""
        if isinstance(image, (str, Path)):
            with Image.open(image) as source:
                image = np.array(source.convert("RGB"))

        annotations = self.generator.generate(image)
        masks = (
            np.stack([item["segmentation"] for item in annotations]).astype(bool)
            if annotations else np.empty((0, *image.shape[:2]), dtype=bool)
        )
        scores = np.array([item["predicted_iou"] for item in annotations], dtype=np.float32)
        boxes = np.array([item["bbox"] for item in annotations], dtype=np.float32).reshape(-1, 4)
        boxes[:, 2:] += boxes[:, :2]
        return SAM2Result(image=image, masks=masks, scores=scores, boxes=boxes)

    @staticmethod
    def render_result(result: SAM2Result) -> np.ndarray:
        """원본 이미지에 마스크 색상을 겹친 RGB 배열을 반환합니다."""
        overlay = result.image.astype(np.float32).copy()
        colors = np.random.default_rng(0)
        order = np.argsort(result.masks.sum(axis=(1, 2)))[::-1]
        for index in order:
            mask = result.masks[index]
            color = colors.integers(40, 256, size=3)
            overlay[mask] = overlay[mask] * 0.55 + color * 0.45

        return overlay.astype(np.uint8)

    @staticmethod
    def save_result(result: SAM2Result, output: str | Path) -> Path:
        """원본 이미지에 마스크 색상을 겹쳐 PNG로 저장합니다."""
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(SAM2Evaluator.render_result(result)).save(output, format="PNG")
        return output


def create_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", choices=tuple(MODEL_INFOS), default=DEFAULT_MODEL_NAME,
                        help="Model variant; its config and checkpoint are selected automatically.")
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto",
                        help="Inference device. Auto selects CUDA, then MPS, then CPU.")
    parser.add_argument("--conf", type=float, default=0.8,
                        help="Minimum predicted mask IoU in [0, 1]; not an object class probability.")
    parser.add_argument("--stability-thresh", type=float, default=0.95,
                        help="Minimum mask stability score in [0, 1].")
    parser.add_argument("--points-per-side", type=int, default=16,
                        help="Sampling points along each side; total prompts equal this value squared.")
    parser.add_argument("--points-per-batch", type=int, default=16,
                        help="Prompts processed together. Lower values reduce peak memory usage.")
    return parser


def parse_args(argv=None, *, parser=None) -> argparse.Namespace:
    if parser is None:
        parser = create_parser("Run SAM2 automatic image segmentation and save a colored mask overlay.")
        parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                            help="Input image path. The default demo image is downloaded if missing.")
        parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                            help="Output PNG path for the colored mask overlay.")
    args = parser.parse_args(argv)
    if not 0 <= args.conf <= 1 or not 0 <= args.stability_thresh <= 1:
        parser.error("--conf and --stability-thresh must be between 0 and 1.")
    if args.points_per_side < 1 or args.points_per_batch < 1:
        parser.error("--points-per-side and --points-per-batch must be positive.")
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.input == DEFAULT_INPUT and not args.input.is_file():
        args.input.parent.mkdir(parents=True, exist_ok=True)
        logging.info("Downloading demo image: %s", args.input)
        with urlopen(DEMO_IMAGE_URL, timeout=30) as response:
            with Image.open(BytesIO(response.read())) as source:
                source.convert("RGB").save(args.input)

    evaluator = SAM2Evaluator(args)
    result = evaluator.predict(args.input)
    output = evaluator.save_result(result, args.output)
    logging.info("Saved %s masks to %s", len(result.masks), output)
    return result


if __name__ == "__main__":
    main()
