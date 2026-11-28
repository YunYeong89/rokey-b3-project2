"""SAM3 + DINOv3 인식기 (config.DETECTOR = "sam3_dinov3").

  1. SAM3 — 물건 나누기: 매대 사진 + 일반 단어 프롬프트(config.SAM3_PROMPTS: "can", "cup", "box" …)
     → 물건마다 마스크 · 상자 · 점수. 여러 프롬프트가 같은 물건을 잡으면 마스크가 많이 겹치는 것끼리 하나로 합친다.
  2. DINOv3 — 어느 상품인지: 물건마다 마스크 밖을 회색으로 칠한 조각 → 특징 벡터
     → 상품별 참조 사진(갤러리) 특징과 코사인 유사도 → 가장 닮은 상품.
     상품 점수 = softmax(유사도 / DINOV3_TEMPERATURE) — 0~1 로 맞춰 로봇 코드의 '크기로 바로잡기' 기준을 그대로 쓴다.
     가장 닮은 유사도가 DINOV3_MIN_SIM 보다 낮으면 모르는 물건 "?" (갤러리에 없는 물건).
  3. 공통 형식 Box 로 돌려줌 (vision/detector.py) — label = 상품 이름, scores = 상품별 점수, mask = SAM3 마스크

갤러리: data/gallery/<상품 id>/*.jpg|png (상품마다 여러 장, 실제 매대에서 찍은 것이 가장 좋음)
  → 인식기가 시작할 때 특징을 계산해 data/gallery/embeddings.npz 에 저장 (사진이 그 파일보다 새것이면 다시)
  사진 모으기: python3 realsense_check.py --save-crops <상품> --loop
모델 이름은 config.SAM3_MODEL / DINOV3_MODEL (Hugging Face 에서 사용 승인 필요 — 팀원이 쓰는 모델로 바꾸기)
"""
import os

import cv2
import numpy as np

import config
from vision.detector import Box

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")


def _device():
    if config.DEVICE != "auto":
        return config.DEVICE
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def mask_iou(a, b, step=4):
    a, b = a[::step, ::step], b[::step, ::step]
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter) / float(union) if union else 0.0


def merge_instances(instances, iou_limit):
    """[(점수, 상자, 마스크)] — 점수 높은 순서로, 이미 고른 것과 마스크가 iou_limit 넘게 겹치면 같은 물건으로 보고 버림"""
    kept = []
    for inst in sorted(instances, key=lambda t: -t[0]):
        if all(mask_iou(inst[2], k[2]) < iou_limit for k in kept):
            kept.append(inst)
    return kept


def keep_objects(instances, shape):
    """SAM3 결과 정리: 너무 큰 마스크(매대 판·벽)·빈 마스크를 빼고, 여러 단어가 같은 물건을 잡은 것은 하나로"""
    area = float(shape[0] * shape[1])
    ok = [t for t in instances if 0 < t[2].sum() <= config.SAM3_MAX_AREA * area]
    return merge_instances(ok, config.SAM3_MERGE_IOU)


def masked_crop(bgr, box, mask, pad=None):
    """상자 둘레 pad 만큼 자른 조각 (RGB) — 마스크 밖은 회색 (옆 물건이 특징에 섞이지 않게)"""
    pad = config.CROP_PAD if pad is None else pad
    h, w = bgr.shape[:2]
    x1, y1 = max(0, int(box[0]) - pad), max(0, int(box[1]) - pad)
    x2, y2 = min(w, int(box[2]) + pad), min(h, int(box[3]) + pad)
    crop = bgr[y1:y2, x1:x2].copy()
    if mask is not None:
        crop[~mask[y1:y2, x1:x2]] = (104, 116, 124)          # ImageNet 평균색 (BGR)
    return cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)


class Sam3Dinov3Detector:
    def __init__(self, load_models=True):
        self.device = _device()
        self.gallery = {}                 # {상품 id: (n, d) 정규화된 특징}
        self.warned = set()
        if load_models:
            self._load_models()
            self.gallery = load_gallery(self)

    # ---------------------------------------------------------------- 모델
    def _load_models(self):
        import torch
        from transformers import AutoImageProcessor, AutoModel, Sam3Model, Sam3Processor

        torch.set_num_threads(config.DINO_THREADS)
        self.torch = torch
        print(f"[detector] SAM3 불러오는 중 ({config.SAM3_MODEL}, {self.device}) ...")
        self.sam_proc = Sam3Processor.from_pretrained(config.SAM3_MODEL)
        self.sam = Sam3Model.from_pretrained(config.SAM3_MODEL).to(self.device).eval()
        print(f"[detector] DINOv3 불러오는 중 ({config.DINOV3_MODEL}) ...")
        self.dino_proc = AutoImageProcessor.from_pretrained(config.DINOV3_MODEL)
        self.dino = AutoModel.from_pretrained(config.DINOV3_MODEL).to(self.device).eval()

    # ---------------------------------------------------------------- 1. SAM3
    def segment(self, bgr):
        """사진 → [(점수, [x1, y1, x2, y2], 마스크)] (프롬프트마다 찾은 뒤 겹치는 것 합침)"""
        from PIL import Image
        image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        found = []
        with self.torch.no_grad():
            img = self.sam_proc(images=image, return_tensors="pt").to(self.device)
            sizes = img["original_sizes"]
            sizes = sizes.tolist() if hasattr(sizes, "tolist") else [list(v) for v in sizes]
            vision = self.sam.get_vision_features(pixel_values=img["pixel_values"])   # 사진 특징은 한 번만 → 단어마다 재사용
            for prompt in config.SAM3_PROMPTS:
                text = self.sam_proc(text=prompt, return_tensors="pt").to(self.device)
                outputs = self.sam(vision_embeds=vision, input_ids=text["input_ids"],
                                   attention_mask=text["attention_mask"])
                res = self.sam_proc.post_process_instance_segmentation(
                    outputs, threshold=config.SAM3_THRESHOLD, mask_threshold=0.5, target_sizes=sizes)[0]
                for score, box, mask in zip(res["scores"], res["boxes"], res["masks"]):
                    found.append((float(score), [float(v) for v in box], mask.cpu().numpy().astype(bool)))
        return keep_objects(found, bgr.shape)

    # ---------------------------------------------------------------- 2. DINOv3
    def embed(self, crops_rgb, batch=16):
        """RGB 조각들 → (n, d) 정규화된 특징 (갤러리 수백 장도 batch 장씩 나눠서)"""
        crops_rgb = list(crops_rgb)
        if not crops_rgb:
            return np.zeros((0, 1), np.float32)
        parts = []
        for k in range(0, len(crops_rgb), batch):
            inputs = self.dino_proc(images=crops_rgb[k:k + batch], return_tensors="pt").to(self.device)
            with self.torch.no_grad():
                out = self.dino(**inputs)
            feats = getattr(out, "pooler_output", None)
            if feats is None:
                feats = out.last_hidden_state[:, 0]          # CLS 토큰
            parts.append(feats.float().cpu().numpy())
        feats = np.concatenate(parts)
        return feats / np.maximum(np.linalg.norm(feats, axis=1, keepdims=True), 1e-9)

    def classify(self, emb):
        """특징 하나 → (상품 id 또는 None, 가장 높은 유사도, {상품 id: 0~1 점수})"""
        if not self.gallery:
            if "gallery" not in self.warned:
                print(f"[detector] ⚠️ 갤러리가 비어 있습니다 — {config.GALLERY_DIR}/<상품 id>/ 에 사진을 넣으세요 (realsense_check.py --save-crops)")
                self.warned.add("gallery")
            return None, 0.0, {}
        pids = list(self.gallery)
        sims = np.array([float(np.max(self.gallery[p] @ emb)) for p in pids])
        z = sims / config.DINOV3_TEMPERATURE
        prob = np.exp(z - z.max())
        prob /= prob.sum()
        k = int(np.argmax(sims))
        if sims[k] < config.DINOV3_MIN_SIM:
            return None, float(sims[k]), {}
        return pids[k], float(sims[k]), {p: float(q) for p, q in zip(pids, prob)}

    # ---------------------------------------------------------------- 3. 공통 형식
    def find_all(self, bgr, targets):
        """→ ({이름: [Box]}, 전체 Box 목록) — vision/detector.py 의 Detector.find_all 과 같은 모양"""
        instances = self.segment(bgr)
        embs = self.embed([masked_crop(bgr, box, mask) for _, box, mask in instances])
        boxes = []
        for (score, box, mask), emb in zip(instances, embs):
            pid, sim, prob = self.classify(emb)
            label = config.prompts_of(pid)[0] if pid else "?"
            scores = {name: p for q, p in prob.items() for name in config.prompts_of(q)}
            boxes.append(Box(label, score, *box, scores, mask))
        boxes.sort(key=lambda b: b.score, reverse=True)
        return {t: [b for b in boxes if b.label == t] for t in targets}, boxes

    def find(self, bgr, target):
        """(옛 방식 pick_one/count_items 용) target 이름의 상자들, 전체 상자"""
        by, boxes = self.find_all(bgr, [target])
        return by[target], boxes


# -------------------------------------------------------------------- 갤러리
def gallery_images(gallery_dir=None):
    """{상품 id: [사진 경로, ...]} — data/gallery/<상품 id>/ 아래 사진 ('_' 로 시작하는 폴더는 건너뜀)"""
    root = gallery_dir or config.GALLERY_DIR
    out = {}
    if not os.path.isdir(root):
        return out
    for pid in sorted(os.listdir(root)):
        d = os.path.join(root, pid)
        if pid.startswith("_") or not os.path.isdir(d):
            continue
        files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(IMAGE_EXT))
        if files:
            out[pid] = files
    return out


def build_gallery(detector, gallery_dir=None, save=True):
    """갤러리 사진 → {상품 id: (n, d) 특징} (save 면 GALLERY_FILE 에 저장)"""
    gallery = {}
    for pid, files in gallery_images(gallery_dir).items():
        imgs = [cv2.imread(f) for f in files]
        crops = [cv2.cvtColor(im, cv2.COLOR_BGR2RGB) for im in imgs if im is not None]
        if not crops:
            continue
        gallery[pid] = detector.embed(crops)
        print(f"[gallery] {pid}: 사진 {len(crops)}장")
    if save and gallery:
        np.savez(config.GALLERY_FILE, **gallery)
        print(f"[gallery] 저장: {config.GALLERY_FILE}")
    return gallery


def load_gallery(detector):
    """GALLERY_FILE 이 있고 사진보다 새것이면 읽고, 아니면 사진으로 새로 만든다"""
    files = [f for fs in gallery_images().values() for f in fs]
    if os.path.exists(config.GALLERY_FILE) and all(os.path.getmtime(f) <= os.path.getmtime(config.GALLERY_FILE) for f in files):
        data = np.load(config.GALLERY_FILE)
        print(f"[gallery] {config.GALLERY_FILE} — 상품 {len(data.files)}개")
        return {k: data[k] for k in data.files}
    return build_gallery(detector)
