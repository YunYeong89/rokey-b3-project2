"""인식기 고르기 — config.DETECTOR (또는 --detector) 에 따라 만든다.

  from vision.factory import make_detector, add_detector_arg
  detector = make_detector()            # config.DETECTOR
  detector = make_detector("gdino")     # 직접 고르기

모든 인식기는 find_all(bgr, targets) → ({이름: [Box]}, 전체 Box 목록) 으로 같은 모양 (vision/detector.py)
"""
import config

CHOICES = ("sam3_dinov3", "gdino", "external")


def add_detector_arg(ap):
    """argparse 에 --detector 옵션 추가"""
    ap.add_argument("--detector", choices=CHOICES, default=None,
                    help=f"물체 인식기 (기본: config.DETECTOR = {config.DETECTOR})")


def make_detector(name=None):
    name = (name or config.DETECTOR).lower()
    config.DETECTOR = name
    if name == "gdino":
        from vision.detector import Detector
        return Detector()
    if name == "external":
        from vision.external import ExternalDetector
        return ExternalDetector()
    if name == "sam3_dinov3":
        try:
            from vision.sam3_dinov3 import Sam3Dinov3Detector
            return Sam3Dinov3Detector()
        except ImportError as e:
            raise SystemExit(f"SAM3/DINOv3 를 불러올 수 없습니다 ({e})\n"
                             "  → pip install -U 'transformers>=5' torchvision (Sam3Model 이 있는 버전 + 이미지 처리기)"
                             "  또는  --detector gdino")
        except OSError as e:
            raise SystemExit(f"SAM3/DINOv3 모델을 내려받지 못했습니다 ({e})\n"
                             f"  → Hugging Face 에서 {config.SAM3_MODEL}, {config.DINOV3_MODEL} 사용 승인 후 "
                             "`huggingface-cli login`  또는  --detector gdino")
    raise SystemExit(f"모르는 인식기: {name} (고를 수 있는 것: {', '.join(CHOICES)})")
