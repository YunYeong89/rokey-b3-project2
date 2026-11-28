"""물체 인식 공통 형식 (Box) + Grounding DINO 인식기 (config.DETECTOR = "gdino").

모든 인식기는 같은 모양으로 답한다 — 로봇 코드(logic/pick_place.py)는 어떤 인식기인지 모른다:
  find_all(bgr, targets) → ({DINO 이름: [Box, ...]}, 전체 Box 목록)
    Box.label  = 그 상품의 이름 (config.prompts_of(상품 id)[0]) — 모르는 물건이면 "?"
    Box.scores = {이름: 0~1 점수} — 모든 상품 이름에 대한 점수 (크기로 상품을 바로잡을 때 씀)
    Box.mask   = 물건 마스크 (사진 크기 bool 배열) — 있으면 가운데·폭을 마스크로 잼 (SAM3), 없으면 None
다른 인식기: vision/sam3_dinov3.py (SAM3 + DINOv3), vision/external.py (팀원 코드 연결), vision/factory.py (고르기)

Grounding DINO: 영어 설명("spam can")으로 사진에서 물건을 찾는다. 학습 불필요.
CPU 에서 한 번 찾는 데 약 7초, 메모리 약 2.4GB (실측). 모델은 처음 한 번 내려받고 이후 캐시를 쓴다.
"""
from dataclasses import dataclass, field
from typing import Any

import cv2

import config


@dataclass
class Box:
    label: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float
    scores: dict = field(default_factory=dict)   # find_all: 이름별 점수 {이름: 점수} — 1등 이름이 틀렸을 때 다시 고르는 데 씀
    mask: Any = field(default=None, repr=False)   # 물건 마스크 (사진 크기 bool 배열) — SAM3 처럼 마스크를 주는 인식기만

    @property
    def center(self):
        return int(round((self.x1 + self.x2) / 2)), int(round((self.y1 + self.y2) / 2))


class Detector:
    """Grounding DINO 인식기 (config.DETECTOR = "gdino")"""

    def __init__(self):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        torch.set_num_threads(config.DINO_THREADS)
        print(f"[detector] Grounding DINO 불러오는 중 ({config.DINO_MODEL}) ...")
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(config.DINO_MODEL)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(config.DINO_MODEL).eval()

    def detect(self, bgr, prompt):
        """사진(BGR)에서 prompt 를 찾아 점수 높은 순서의 Box 목록을 돌려준다."""
        from PIL import Image

        image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        text = prompt.strip().lower().rstrip(".") + "."      # Grounding DINO 는 소문자 + 마침표 형식
        inputs = self.processor(images=image, text=text, return_tensors="pt")
        with self.torch.no_grad():
            outputs = self.model(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=config.BOX_THRESHOLD, text_threshold=config.TEXT_THRESHOLD,
            target_sizes=[image.size[::-1]])[0]
        labels = result.get("text_labels", result.get("labels"))
        boxes = [Box(str(label), float(score), *[float(v) for v in box])
                 for label, score, box in zip(labels, result["scores"], result["boxes"])]
        return sorted(boxes, key=lambda b: b.score, reverse=True)

    def find(self, bgr, target):
        """모든 상품 이름 + 상품이 아닌 것(사람·손)을 함께 주고 경쟁시켜서, 이름이 target 인 상자만 고른다.
        → (target 상자 목록, 전체 상자 목록)
        target 하나만 주면 사진에 없어도 비슷한 것을 억지로 맞추기 때문 (예: 사람 → 'tuna' 0.37)."""
        phrases = list(dict.fromkeys(config.all_prompts() + config.DINO_DISTRACTORS + [target]))
        boxes = self.detect(bgr, ". ".join(phrases))
        # DINO 가 붙인 이름(label)이 target 에만 들어 있는 상자만 인정 (예: 'tuna' → 'tuna can' 하나뿐이면 인정)
        mine = [b for b in boxes if b.label and [p for p in phrases if b.label in p] == [target]]
        return mine, boxes

    def find_all(self, bgr, targets):
        """여러 상품을 한 번에 찾는다 (우선순위 집기용) → ({target: [Box, 점수 높은 순]}, 전체 상자 목록)

        find() 처럼 모든 상품 이름 + 상품이 아닌 것을 경쟁시키되, DINO 가 붙인 라벨 글자 대신
        상자마다 '이름별 점수'(그 이름의 단어 토큰 점수 평균)를 직접 비교해서 1등 이름으로 정한다.
          - 여러 이름에 함께 있는 단어(can, box …)는 빼고, 그 이름에만 있는 단어(coca, cola, ramen …)로 비교
          - 1등이 TEXT_THRESHOLD 이상이고 2등보다 LABEL_MARGIN 이상 높아야 인정, 아니면 라벨 '?'
        (실측: DINO 라벨은 'coca cola ramen' 처럼 이름이 섞여 나와 라벨 글자로는 콜라·컵라면을 놓침)"""
        import numpy as np
        from PIL import Image

        phrases = list(dict.fromkeys(config.all_prompts() + config.DINO_DISTRACTORS + list(targets)))
        text = ". ".join(phrases) + "."
        image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        inputs = self.processor(images=image, text=text, return_tensors="pt")
        with self.torch.no_grad():
            outputs = self.model(**inputs)
        prob = outputs.logits.sigmoid()[0].numpy()             # (상자 후보, 토큰)
        tokens = _phrase_tokens(self.processor.tokenizer, text, phrases)
        h, w = bgr.shape[:2]
        boxes = []
        for q in np.where(prob.max(axis=1) >= config.BOX_THRESHOLD)[0]:
            scores = [float(prob[q, idx].mean()) for idx in tokens]
            order = np.argsort(scores)[::-1]
            best, second = scores[order[0]], scores[order[1]] if len(order) > 1 else 0.0
            ok = best >= config.TEXT_THRESHOLD and best - second >= config.LABEL_MARGIN
            cx, cy, bw, bh = (float(v) for v in outputs.pred_boxes[0, q])
            boxes.append(Box(phrases[order[0]] if ok else "?", float(prob[q].max()),
                             (cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h,
                             dict(zip(phrases, scores))))
        boxes = _nms(sorted(boxes, key=lambda b: b.score, reverse=True), config.NMS_IOU)
        return {t: [b for b in boxes if b.label == t] for t in targets}, boxes


def _phrase_tokens(tokenizer, text, phrases):
    """이름마다 비교에 쓸 토큰 위치 목록 — 그 이름에만 있는 단어의 토큰 (없으면 이름 전체 토큰)"""
    import numpy as np

    offsets = tokenizer(text, return_offsets_mapping=True)["offset_mapping"]
    count = {}
    for p in phrases:
        for word in set(p.split()):
            count[word] = count.get(word, 0) + 1
    result, pos = [], 0
    for p in phrases:
        start = text.index(p, pos)
        pos = start + len(p)
        unique, every, w0 = [], [], start
        for word in p.split():
            w0 = text.index(word, w0)
            span = [k for k, (a, b) in enumerate(offsets) if b > a and a >= w0 and b <= w0 + len(word)]
            every += span
            if count[word] == 1:
                unique += span
            w0 += len(word)
        result.append(np.array(unique or every, dtype=int))
    return result


def _nms(boxes, iou_limit):
    """점수 높은 순서의 상자에서, 이미 고른 상자와 많이 겹치는 상자를 뺀다"""
    def iou(a, b):
        ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
        iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
        inter = ix * iy
        union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
        return inter / union if union > 0 else 0.0
    kept = []
    for b in boxes:
        if all(iou(b, k) < iou_limit for k in kept):
            kept.append(b)
    return kept


def draw(bgr, boxes, chosen=None, targets=(), path=config.LAST_DETECTION_IMAGE):
    """찾은 상자를 그려서 저장한다 (고른 상자 초록 / 찾는 상품 노랑 / 그 밖의 것 회색)."""
    img = bgr.copy()
    for box in boxes:
        color = (0, 200, 0) if box is chosen else (0, 200, 255) if box in targets else (160, 160, 160)
        p1, p2 = (int(box.x1), int(box.y1)), (int(box.x2), int(box.y2))
        if box.mask is not None:                     # SAM3 마스크 테두리
            contours, _ = cv2.findContours(box.mask.astype("uint8"), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(img, contours, -1, color, 1)
        cv2.rectangle(img, p1, p2, color, 2)
        cv2.putText(img, f"{box.label} {box.score:.2f}", (p1[0], max(15, p1[1] - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        if box is chosen:
            cv2.circle(img, box.center, 5, color, -1)
    cv2.imwrite(path, img)
    return path
