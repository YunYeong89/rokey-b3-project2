"""CLIP instruction으로 고른 물품 마스크를 DINOv3 임베딩으로 같은 물품끼리 묶어 그룹별 색으로 표시합니다."""

import os
from pathlib import Path
import sys

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
import numpy as np
import torch
import torch.nn.functional as F

DINO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DINO_DIR.parent / "CLIP"))
from instruction import InstructionStream, create_parser as create_clip_parser, masked_crop
from dinov3.hub.backbones import dinov3_vits16

CHECKPOINT = DINO_DIR / "checkpoints" / "dinov3_vits16_pretrain_lvd1689m-08c60483.pth"


class DinoEmbedder:
    """마스크 조각(배경 회색, 정사각 패딩)을 DINOv3 ViT-S/16에 배치로 넣어 CLS 임베딩을 만듭니다.
    프레임 patch token을 마스크로 평균하면 캔처럼 모양이 같은 물품끼리 유사도가 모두 높아져 조각 단위로 봅니다."""

    def __init__(self, size, device):
        self.size, self.device = size, device
        self.model = dinov3_vits16(pretrained=False).eval().to(device)
        self.model.load_state_dict(torch.load(CHECKPOINT, map_location="cpu", weights_only=True))
        self.mean = torch.tensor((0.485, 0.456, 0.406), device=device).view(3, 1, 1)
        self.std = torch.tensor((0.229, 0.224, 0.225), device=device).view(3, 1, 1)

    @torch.inference_mode()
    def __call__(self, rgb, masks, boxes):
        """마스크 (N, H, W)와 박스 (N, 4) → L2 정규화 CLS 임베딩 (N, 384)."""
        crops = np.stack([np.asarray(masked_crop(rgb, mask, box, self.size)) for mask, box in zip(masks, boxes)])
        batch = torch.from_numpy(crops).to(self.device).permute(0, 3, 1, 2).float() / 255
        cls = self.model.forward_features((batch - self.mean) / self.std)["x_norm_clstoken"]
        return F.normalize(cls, dim=1).cpu().numpy()


class InstanceGrouper:
    """임베딩을 평균 연결 군집으로 같은 물품끼리 묶고, 그룹 중심을 이전 프레임과 매칭해 그룹 ID를 유지합니다."""

    def __init__(self, threshold, momentum=0.8):
        self.threshold = threshold  # 같은 물품으로 볼 최소 코사인 유사도
        self.momentum = momentum    # 그룹 중심 갱신 시 이전 중심 가중치
        self.centers = {}           # 그룹 ID → 정규화된 중심 임베딩

    def __call__(self, features):
        """L2 정규화 임베딩 (N, D) → 프레임 간 유지되는 그룹 ID (N,)."""
        clusters = self.cluster(features)
        centers = np.stack([features[members].mean(0) for members in clusters])
        centers /= np.linalg.norm(centers, axis=1, keepdims=True)

        match = {}
        if self.centers:
            ids = list(self.centers)
            sim = centers @ np.stack(list(self.centers.values())).T
            for row, col in zip(*np.unravel_index(np.argsort(-sim, axis=None), sim.shape)):  # 유사도 높은 쌍부터
                if sim[row, col] < self.threshold:
                    break
                if row not in match and ids[col] not in match.values():
                    match[row] = ids[col]

        groups = np.empty(len(features), dtype=int)
        for row, (members, center) in enumerate(zip(clusters, centers)):
            if row in match:
                center = self.centers[match[row]] * self.momentum + center * (1 - self.momentum)
                center /= np.linalg.norm(center)
            else:
                match[row] = len(self.centers)
            self.centers[match[row]] = center
            groups[members] = match[row]
        return groups

    def cluster(self, features):
        """평균 유사도가 가장 높은 두 군집을 기준 이상인 동안 합쳐 군집별 인덱스 목록을 반환합니다."""
        sim = features @ features.T
        clusters = [[index] for index in range(len(features))]
        while len(clusters) > 1:
            link = np.full((len(clusters), len(clusters)), -1.0)
            for i, a in enumerate(clusters):
                for j in range(i + 1, len(clusters)):
                    link[i, j] = sim[np.ix_(a, clusters[j])].mean()
            i, j = np.unravel_index(link.argmax(), link.shape)
            if link[i, j] < self.threshold:
                break
            clusters[i] += clusters.pop(j)  # i < j라 pop 후에도 i 위치는 그대로
        return clusters


class GroupStream(InstructionStream):
    """분할 → DINOv3로 같은 물품끼리 묶기 → 그룹 평균 CLIP 임베딩으로 instruction 그룹 선택 → 그룹 색 표시.
    마스크 단위 CLIP 점수는 브랜드 간 차이가 작아, 그룹 평균 후 최고 그룹 대비 margin 이내만 고릅니다."""

    COLORS = np.random.default_rng(0).integers(60, 256, size=(32, 3))

    def __init__(self, args):
        super().__init__(args)
        self.embedder = DinoEmbedder(args.dino_size, self.device)
        self.grouper = InstanceGrouper(args.group_sim)
        self.name += " + DINOv3"

    def process(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        masks, boxes = self.segmenter(rgb)
        hit_masks, hit_boxes, groups, scores, _ = self.select(rgb, masks, boxes)
        overlay = self.render(bgr, hit_masks, hit_boxes, scores, groups)
        return overlay, f"Groups: {len(np.unique(groups))} | Matches: {len(groups)}/{len(masks)}"

    def select(self, rgb, masks, boxes):
        """면적 필터 → DINOv3 그룹 → 그룹 평균 CLIP 점수가 min_score 이상이고 최고 그룹 대비 margin 이내인 그룹만 남깁니다.
        남은 (마스크, 박스, 그룹 ID, 그룹 CLIP 점수, 마스크 CLIP 점수)를 반환합니다."""
        keep = self.selector.keep(masks)
        if self.selector.text is None or len(keep) == 0:
            return masks[:0], boxes[:0], np.empty(0, dtype=int), np.empty(0), np.empty(0)
        masks, boxes = masks[keep], boxes[keep]

        groups = self.grouper(self.embedder(rgb, masks, boxes))
        image = self.selector.embed(rgb, masks, boxes)
        ids = np.unique(groups)
        centers = np.stack([image[groups == group].mean(0) for group in ids])
        scores = (centers @ self.selector.text / np.linalg.norm(centers, axis=1))[np.searchsorted(ids, groups)]
        hit = (scores >= self.args.min_score) & (scores >= scores.max() - self.args.margin)
        return masks[hit], boxes[hit], groups[hit], scores[hit], image[hit] @ self.selector.text

    @classmethod
    def paint(cls, bgr, masks, groups):
        """그룹 ID별 고정 색으로 마스크와 윤곽을 칠한 BGR 이미지와 마스크별 색 (N, 3)을 반환합니다."""
        overlay = bgr.copy()
        colors = cls.COLORS[groups % len(cls.COLORS)]
        for mask, color in zip(masks, colors):
            overlay[mask] = overlay[mask] * 0.5 + color * 0.5
            contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, color.tolist(), 2)
        return overlay, colors

    @classmethod
    def render(cls, bgr, masks, boxes, scores, groups):
        """그룹 색 마스크 위에 'G<ID> <CLIP 점수>'를 그린 BGR 이미지를 반환합니다."""
        overlay, colors = cls.paint(bgr, masks, groups)
        for (x1, y1, _, _), score, group, color in zip(boxes.astype(int), scores, groups, colors):
            cv2.putText(overlay, f"G{group} {score:.2f}", (x1 + 4, y1 + 20), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, color.tolist(), 2, cv2.LINE_AA)
        return overlay


def add_group_arguments(parser):
    """DINOv3 그룹·CLIP 그룹 선택 인자 (grasp_clustering.py도 공유합니다)."""
    parser.add_argument("--dino-size", type=int, default=224, help="DINOv3 input size of each square mask crop.")
    parser.add_argument("--group-sim", type=float, default=0.78,
                        help="Minimum DINOv3 cosine similarity for two masks to be the same item.")
    parser.add_argument("--margin", type=float, default=0.025,
                        help="Keep groups whose CLIP score is within this margin of the best group (and >= --min-score).")
    return parser


def create_parser():
    """instruction.py 인자에 DINOv3 그룹 인자를 더한 파서 (ROS dinov3.py는 parse_args([])로 기본값만 씁니다)."""
    parser = add_group_arguments(create_clip_parser(
        "Group identical items by DINOv3 crop features, then keep the groups whose mean CLIP embedding matches the "
        "instruction. Processes --input into --output, or the webcam with --stream."))
    parser.add_argument("--input", type=Path, default=DINO_DIR / "images" / "before.jpg", help="Input image.")
    parser.add_argument("--output", type=Path, default=DINO_DIR / "images" / "after.jpg", help="Masked output image.")
    parser.add_argument("--stream", action="store_true", help="Run on webcam shared memory instead of --input.")
    return parser


def parse_args():
    parser = create_parser()
    args = parser.parse_args()
    if not args.stream and not args.instruction:
        parser.error("--instruction is required for image mode, e.g. --instruction can.")
    return args


def main():
    args = parse_args()
    pipeline = GroupStream(args)
    if args.stream:
        pipeline.run()
        return
    pipeline.set_instruction(args.instruction)
    overlay, status = pipeline.process(cv2.imread(str(args.input)))
    cv2.imwrite(str(args.output), overlay)
    print(f"Saved {args.output} | {args.instruction} | {status}")


if __name__ == "__main__":
    main()
