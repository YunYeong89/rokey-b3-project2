"""팀원이 만든 인식 함수를 그대로 연결 (config.DETECTOR = "external").

config.EXTERNAL_DETECTOR (또는 실행할 때 PICKPLACE_EXTERNAL=...) = "모듈:함수"
  "team_vision.infer:detect"        → pickplace 폴더 기준 team_vision/infer.py 의 detect
  "/home/me/vision/run.py:detect"   → 파일 경로도 됨
  "team_vision.infer:Recognizer"    → 클래스면 한 번 만들고(모델 로드) 그 객체의 detect(...) 또는 __call__(...) 을 씀

함수는 BGR 사진 (numpy, cv2.imread 와 같은 모양) 하나를 받아 물건 목록을 돌려주면 된다:
  [
    {"pid": "cola",                 # 상품 id (config.DINO_PROMPTS 의 키: cola, cider, cup_ramen, tuna_can …)
                                    #   또는 "label": "콜라" / "pepsi" / "red coca cola can" 같은 이름 (별명표로 상품 id 로 바꿈)
                                    #   모르는 물건은 None (그래도 넣어 주면 '앞 물건에 가려짐' 판단에 씀)
     "score": 0.91,                 # 물건 점수 0~1
     "box": [x1, y1, x2, y2],       # 픽셀 (왼쪽 위, 오른쪽 아래)
     "mask": HxW bool 배열,          # (선택) 있으면 물건 가운데·폭을 마스크로 잼 — 상자보다 정확
     "scores": {"cola": 0.91, "cider": 0.05}},   # (선택) 상품 id 별 점수 — 없으면 {pid: score}
    ...
  ]
이 함수 하나만 맞추면 로봇 코드는 고칠 게 없다. 확인: python3 realsense_check.py --detector external
"""
import importlib
import importlib.util
import os

import numpy as np

import config
from vision.detector import Box


def load_callable(spec):
    """'모듈:이름' 또는 '파일.py:이름' → 부를 수 있는 것"""
    if ":" not in spec:
        raise ValueError(f"EXTERNAL_DETECTOR 는 '모듈:함수' 모양이어야 합니다 (지금: {spec!r})")
    mod_name, attr = spec.rsplit(":", 1)
    if mod_name.endswith(".py"):
        path = mod_name if os.path.isabs(mod_name) else os.path.join(config.BASE_DIR, mod_name)
        loader = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(path))[0], path)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
    else:
        module = importlib.import_module(mod_name)
    obj = getattr(module, attr)
    if isinstance(obj, type):                    # 클래스 → 한 번 만들어 둠 (모델은 여기서 한 번만 로드)
        obj = obj()
    return getattr(obj, "detect", obj)


def to_pid(item):
    """팀원 결과 한 개 → 상품 id 또는 None"""
    pid = item.get("pid")
    if pid in config.DINO_PROMPTS:
        return pid
    name = item.get("label") or pid
    if not name:
        return None
    key = str(name).strip()
    names = {p.lower(): q for q in config.DINO_PROMPTS for p in config.prompts_of(q)}
    for table in (config.DINO_PROMPTS, config.PRODUCT_ALIASES, names):
        for k in (key, key.lower(), key.replace(" ", "_").lower()):
            if k in table:
                return k if table is config.DINO_PROMPTS else table[k]
    return None


def to_box(item, shape):
    """팀원 결과 한 개 → Box (label = 그 상품의 이름, scores = 상품 이름별 점수)"""
    pid = to_pid(item)
    score = float(item.get("score", 1.0))
    x1, y1, x2, y2 = (float(v) for v in item["box"])
    by_pid = item.get("scores") or ({pid: score} if pid else {})
    scores = {}
    for k, s in by_pid.items():
        q = to_pid({"pid": k})
        for name in (config.prompts_of(q) if q else []):
            scores[name] = max(scores.get(name, 0.0), float(s))
    mask = item.get("mask")
    if mask is not None:
        mask = np.asarray(mask).astype(bool)
        if mask.shape[:2] != tuple(shape[:2]):
            raise ValueError(f"mask 크기 {mask.shape} 가 사진 {shape[:2]} 과 다릅니다")
    return Box(config.prompts_of(pid)[0] if pid else "?", score, x1, y1, x2, y2, scores, mask)


class ExternalDetector:
    def __init__(self, spec=None):
        spec = spec or config.EXTERNAL_DETECTOR
        if not spec:
            raise SystemExit("config.EXTERNAL_DETECTOR 가 비어 있습니다 — 예: PICKPLACE_EXTERNAL=team_vision.infer:detect")
        print(f"[detector] 팀원 인식 함수 연결: {spec}")
        self.fn = load_callable(spec)

    def find_all(self, bgr, targets):
        boxes = [to_box(item, bgr.shape) for item in self.fn(bgr)]
        boxes.sort(key=lambda b: b.score, reverse=True)
        return {t: [b for b in boxes if b.label == t] for t in targets}, boxes

    def find(self, bgr, target):
        by, boxes = self.find_all(bgr, [target])
        return by[target], boxes
