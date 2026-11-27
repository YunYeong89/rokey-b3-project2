"""model/DINOv3/grasp_clustering.py를 이름 검색(sys.path) 대신 파일 경로로 직접 불러옵니다.
model 위치는 VISION_MODEL_DIR 환경변수(docker 이미지 기본값 /ros2_ws/src/vision_project/model)로 정하고,
없으면 소스 기준 vision_project/model을 씁니다. 그래서 colcon --symlink-install 여부와 같은 이름의 다른 모듈에 영향받지 않습니다."""

import importlib.util
import os
from pathlib import Path
import sys

MODEL_DIR = Path(os.environ.get("VISION_MODEL_DIR") or Path(__file__).resolve().parents[1] / "model")


def load_grasp_clustering():
    """MODEL_DIR/DINOv3/grasp_clustering.py 모듈을 반환합니다 (GraspStream, create_parser 등)."""
    spec = importlib.util.spec_from_file_location("grasp_clustering", MODEL_DIR / "DINOv3" / "grasp_clustering.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
