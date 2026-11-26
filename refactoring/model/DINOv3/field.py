"""열원(--point 또는 CLIP으로 찾은 --instruction 위치)과의 patch token 유사도를 의미 그래프로 확산시켜 열화상처럼 표시합니다.
DINOv3(이미지 SSL), V-JEPA 2.1(비디오 SSL), CLIP(이미지-텍스트) 백본을 같은 열원·같은 고정 스케일로 나란히 비교합니다."""

import argparse
import os
from pathlib import Path
import sys

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
import numpy as np
import torch
import torch.nn.functional as F

DINO_DIR = Path(__file__).resolve().parent
sys.path[:0] = [str(DINO_DIR.parent / "CLIP"), str(DINO_DIR.parent / "V-JEPA2")]
from dense_clip import DenseClip
from dinov3.hub.backbones import dinov3_vits16
from vjepa2 import vjepa2_1_vit_base_384

CHECKPOINT = DINO_DIR / "checkpoints" / "dinov3_vits16_pretrain_lvd1689m-08c60483.pth"
IMAGE_DIR = DINO_DIR / "images"
IMAGES = sorted(path.name for path in IMAGE_DIR.iterdir() if path.suffix.lower() in (".jpg", ".jpeg", ".png"))
FONT = cv2.FONT_HERSHEY_SIMPLEX


class PatchEncoder:
    """RGB 이미지 → (h, w, D) patch token. 짧은 변을 size로 맞춰 PATCH 배수로 리사이즈합니다.
    백본마다 코사인 기준선이 달라 이미지 평균 token을 빼고 L2 정규화합니다 (0 = 평균 patch만큼 비슷함)."""

    PATCH = 16
    MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)

    def __init__(self, size, device):
        self.size, self.device = size, device
        self.mean = torch.tensor(self.MEAN, device=device).view(1, 3, 1, 1)
        self.std = torch.tensor(self.STD, device=device).view(1, 3, 1, 1)
        self.model = self.load().eval().to(device)

    @torch.inference_mode()
    def __call__(self, rgb):
        tokens = self.encode(rgb)
        return F.normalize(tokens - tokens.mean((0, 1)), dim=-1).cpu().numpy()

    @torch.inference_mode()
    def encode(self, rgb):
        """평균 제거 전 원시 patch token (gh, gw, D) torch."""
        h, w = rgb.shape[:2]
        scale = self.size / min(h, w)
        gh, gw = round(h * scale / self.PATCH), round(w * scale / self.PATCH)
        image = cv2.resize(rgb, (gw * self.PATCH, gh * self.PATCH), interpolation=cv2.INTER_AREA)
        x = torch.from_numpy(image).to(self.device).permute(2, 0, 1)[None].float() / 255
        return self.tokens((x - self.mean) / self.std).float().reshape(gh, gw, -1)

    def load(self):
        raise NotImplementedError

    def tokens(self, x):
        """정규화된 (1, 3, H, W) → (H/PATCH · W/PATCH, D)."""
        raise NotImplementedError


class DinoV3Encoder(PatchEncoder):
    """DINOv3 ViT-S/16 (LVD-1689M, 이미지 SSL)."""

    def load(self):
        model = dinov3_vits16(pretrained=False)
        model.load_state_dict(torch.load(CHECKPOINT, map_location="cpu", weights_only=True))
        return model

    def tokens(self, x):
        return self.model.forward_features(x)["x_norm_patchtokens"][0]


class VJepaEncoder(PatchEncoder):
    """V-JEPA 2.1 ViT-B/16 (../V-JEPA2, ViT-G 증류, 비디오+이미지 SSL).
    (B, C, 1, H, W)로 넣으면 이미지 토크나이저 경로를 타고, RoPE 보간으로 384 외 해상도도 받습니다."""

    def load(self):
        return vjepa2_1_vit_base_384()

    def tokens(self, x):
        return self.model(x.unsqueeze(2))[0]


class ClipEncoder(PatchEncoder):
    """CLIP ViT-B/16 (../CLIP/dense_clip.py, 이미지-텍스트 대조 학습).
    patch token이 문장 임베딩과 같은 공간이라 문장으로 열원을 찾고(locate) 문장 기준 열(text)도 그립니다."""

    MEAN, STD = DenseClip.MEAN, DenseClip.STD

    def load(self):
        return DenseClip(device=self.device)

    def tokens(self, x):
        return self.model(x)

    def text(self, instruction):
        return self.model.text(instruction)

    def locate(self, rgb, instruction):
        """instruction 텍스트 임베딩과 3×3 평균 유사도가 가장 높은 patch의 원본 픽셀 중심 (x, y)."""
        sim = cv2.blur((F.normalize(self.encode(rgb), dim=-1) @ self.text(instruction)).cpu().numpy(), (3, 3))
        gy, gx = np.unravel_index(sim.argmax(), sim.shape)
        (gh, gw), (h, w) = sim.shape, rgb.shape[:2]
        return int((gx + 0.5) / gw * w), int((gy + 0.5) / gh * h)


ENCODERS = {"dinov3": DinoV3Encoder, "vjepa": VJepaEncoder, "clip": ClipEncoder}


class HeatField:
    """직접 열: 기준 벡터(열원 patch 평균 token, 또는 CLIP 문장 임베딩)와의 코사인 유사도 e.
    확산 열: h = (1−α)(I − αP)⁻¹ e. P는 공간 반경 밖 top-k 이웃을 잇는 kNN 그래프의 전이행렬이라
    h(p)는 p에서 의미 그래프를 따라 걸으며 만나는 직접 열의 가중 평균입니다. 행 합이 1이라 e와 같은 스케일이고 α=0이면 e."""

    def __init__(self, alpha, knn, tau, radius):
        self.alpha = alpha    # 확산 정도 (재시작 확률 1−α)
        self.knn = knn        # patch당 연결할 의미 이웃 수
        self.tau = tau        # 이웃 가중치 온도
        self.radius = radius  # kNN에서 뺄 공간 반경 (patch, Chebyshev): 옆 patch로 열이 새는 것 방지

    def __call__(self, tokens, anchor):
        """tokens (h, w, D), anchor (D,) → (직접 열, 확산 열) 각 (h, w)."""
        gh, gw, _ = tokens.shape
        f = tokens.reshape(gh * gw, -1)
        direct = f @ (anchor / np.linalg.norm(anchor))
        diffused = np.linalg.solve(np.eye(len(f)) - self.alpha * self.graph(f, gh, gw), (1 - self.alpha) * direct)
        return direct.reshape(gh, gw), diffused.reshape(gh, gw)

    def graph(self, f, gh, gw):
        """대칭 kNN 그래프의 행 정규화 전이행렬 P (N, N)."""
        sim = f @ f.T
        y, x = np.divmod(np.arange(gh * gw), gw)
        sim[np.maximum(np.abs(y[:, None] - y), np.abs(x[:, None] - x)) <= self.radius] = -np.inf
        rows = np.arange(len(f))[:, None]
        top = np.argpartition(-sim, self.knn, axis=1)[:, :self.knn]
        weight = np.zeros_like(sim)
        weight[rows, top] = np.exp((sim[rows, top] - 1) / self.tau)
        weight = np.maximum(weight, weight.T)
        return weight / weight.sum(1, keepdims=True)

    @staticmethod
    def source(grid, shape, point):
        """원본 픽셀 좌표 point가 속한 patch와 주변 3×3 patch를 열원으로 표시한 (h, w) bool."""
        (gh, gw), (h, w), (x, y) = grid, shape, point
        gx, gy = int(x / w * gw), int(y / h * gh)
        source = np.zeros(grid, dtype=bool)
        source[max(gy - 1, 0):gy + 2, max(gx - 1, 0):gx + 2] = True
        return source


class ThermalRenderer:
    """흑백 원본 위에 고정 범위 [vmin, vmax]의 INFERNO 열 지도를 겹칩니다. 자동 범위를 쓰지 않아 약한 결합은 차갑게 남습니다."""

    def __init__(self, vmin, vmax, opacity):
        self.vmin, self.vmax, self.opacity = vmin, vmax, opacity

    def __call__(self, bgr, heat, point, label):
        h, w = bgr.shape[:2]
        gray = cv2.cvtColor(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        level = cv2.resize(self.level(heat), (w, h), interpolation=cv2.INTER_CUBIC).clip(0, 1)
        color = cv2.applyColorMap((level * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        image = cv2.addWeighted(gray, 1 - self.opacity, color, self.opacity, 0)
        cv2.drawMarker(image, point, (255, 255, 255), cv2.MARKER_TILTED_CROSS, 24, 2)
        return self.label(image, label)

    def level(self, heat):
        return ((heat - self.vmin) / (self.vmax - self.vmin)).astype(np.float32)

    def row(self, bgr, direct, diffused, point, name, alpha):
        """[직접 | 확산 | 이 행 스케일의 컬러바] 한 행."""
        return np.hstack([self(bgr, direct, point, f"{name} direct"),
                          self(bgr, diffused, point, f"{name} diffused a={alpha}"),
                          self.colorbar(bgr.shape[0])])

    def colorbar(self, height, pad=20):
        """오른쪽에 붙일 세로 컬러바 (vmin·중간·vmax 눈금)."""
        bar = np.zeros((height, 90, 3), dtype=np.uint8)
        ramp = np.linspace(255, 0, height - 2 * pad).astype(np.uint8)[:, None].repeat(20, 1)
        bar[pad:height - pad, 10:30] = cv2.applyColorMap(ramp, cv2.COLORMAP_INFERNO)
        for ratio in (0, 0.5, 1):
            y = round(pad + (1 - ratio) * (height - 2 * pad - 1))
            cv2.putText(bar, f"{self.vmin + ratio * (self.vmax - self.vmin):.2f}", (36, y + 5), FONT,
                        0.45, (255, 255, 255), 1, cv2.LINE_AA)
        return bar

    @staticmethod
    def label(image, text):
        """왼쪽 위 검은 상자에 흰 글씨로 text를 씁니다."""
        (tw, th), _ = cv2.getTextSize(text, FONT, 0.7, 2)
        cv2.rectangle(image, (0, 0), (tw + 20, th + 20), (0, 0, 0), -1)
        cv2.putText(image, text, (10, th + 10), FONT, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        return image


def parse_args():
    parser = argparse.ArgumentParser(
        description="Heat map of patch-token affinity to a source (a pixel, or the patch CLIP matches to a sentence), "
                    "direct cosine vs. kNN-graph diffusion, for each backbone. Rows: backbones, columns: direct | diffused.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--point", type=lambda s: tuple(map(int, s.split(","))),
                        help="Heat source pixel 'x,y' in the input image (e.g. center of the trash bin).")
    source.add_argument("--instruction",
                        help="Sentence; the patch whose CLIP embedding best matches it becomes the heat source, "
                             "e.g. 'throw the can into the trash bin'.")
    parser.add_argument("--backbones", nargs="+", choices=tuple(ENCODERS), default=list(ENCODERS),
                        help="Backbones to compare, one row each.")
    parser.add_argument("--size", type=int, default=448, help="Short side fed to the backbone (multiple of 16).")
    parser.add_argument("--alpha", type=float, default=0.7, help="Diffusion strength in [0, 1).")
    parser.add_argument("--knn", type=int, default=10, help="Semantic neighbors per patch in the diffusion graph.")
    parser.add_argument("--tau", type=float, default=0.1, help="Temperature of neighbor weights exp((cos - 1) / tau).")
    parser.add_argument("--radius", type=int, default=2, help="Spatial radius (patches) excluded from kNN.")
    parser.add_argument("--vmin", type=float, default=0.0, help="Heat mapped to the coldest color.")
    parser.add_argument("--vmax", type=float, default=0.8, help="Heat mapped to the hottest color.")
    parser.add_argument("--text-vmin", type=float, default=0.0,
                        help="Coldest heat of the 'clip text' row (sentence-to-patch cosine is lower than image-to-image).")
    parser.add_argument("--text-vmax", type=float, default=0.3, help="Hottest heat of the 'clip text' row.")
    parser.add_argument("--opacity", type=float, default=0.6, help="Heat overlay opacity over the grayscale image.")
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto", help="Inference device.")
    parser.add_argument("--input", choices=IMAGES, default="before.jpg", help="Input image file name in images/.")
    parser.add_argument("--output", type=Path, default=DINO_DIR / "results" / "after.jpg", help="Output image.")
    return parser.parse_args()


def main():
    args = parse_args()
    device = args.device if args.device != "auto" else (
        "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    bgr = cv2.imread(str(IMAGE_DIR / args.input))
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    encoders = {name: ENCODERS[name](args.size, device) for name in args.backbones}
    field = HeatField(args.alpha, args.knn, args.tau, args.radius)
    renderer = ThermalRenderer(args.vmin, args.vmax, args.opacity)

    point, title = args.point, f"source: point {args.point}"
    if args.instruction:
        text_encoder = encoders["clip"] if "clip" in encoders else ClipEncoder(args.size, device)
        point = text_encoder.locate(rgb, args.instruction)
        title = f"source: '{args.instruction}' -> CLIP {point}"
    print(title)

    rows = []

    def add(name, tokens, anchor, painter):
        source = field.source(tokens.shape[:2], bgr.shape[:2], point)
        direct, diffused = field(tokens, tokens[source].mean(0) if anchor is None else anchor)
        rows.append(painter.row(bgr, direct, diffused, point, name, args.alpha))
        print(f"{name}: grid {tokens.shape[1]}x{tokens.shape[0]} | source {direct[source].mean():.2f} | "
              f"max {direct.max():.2f} | mean {direct.mean():.2f} | diffused mean {diffused.mean():.2f}")

    for name, encoder in encoders.items():  # 이미지 기준: 열원 patch 평균 token
        add(name, encoder(rgb), None, renderer)
    if args.instruction:  # 문장 기준: CLIP 문장 임베딩 자체, 교차 모달 유사도라 별도 스케일
        add("clip text", text_encoder(rgb), text_encoder.text(args.instruction).cpu().numpy(),
            ThermalRenderer(args.text_vmin, args.text_vmax, args.opacity))

    grid = np.vstack(rows)
    image = renderer.label(cv2.copyMakeBorder(grid, 44, 0, 0, 0, cv2.BORDER_CONSTANT), title)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output), image)
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
