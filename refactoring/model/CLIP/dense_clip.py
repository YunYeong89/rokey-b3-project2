"""CLIP ViT의 patch token을 텍스트 임베딩과 같은 공간에서 꺼냅니다 (MaskCLIP 방식).
위치별 이미지-이미지 유사도와 문장-patch 유사도를 같은 모델로 계산할 때 사용합니다."""

from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

import clip

CLIP_DIR = Path(__file__).resolve().parent


class DenseClip(nn.Module):
    """마지막 블록을 value 경로만 남겨(q·k 어텐션·residual·FFN 제거) patch token을 텍스트 공간에 둡니다.
    위치 임베딩을 입력 격자에 맞게 보간해 224 외 해상도도 받습니다. 가중치는 첫 사용 때 checkpoints/로 받습니다."""

    MEAN, STD = (0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)

    def __init__(self, name="ViT-B/16", device="cpu"):
        super().__init__()
        model, _ = clip.load(name, device=device, download_root=str(CLIP_DIR / "checkpoints"))
        self.model = model.float()
        self.patch = self.model.visual.conv1.kernel_size[0]

    def forward(self, x):
        """MEAN/STD로 정규화된 (1, 3, H, W) → patch token (H/patch · W/patch, D)."""
        visual = self.model.visual
        x = visual.conv1(x)  # (1, width, gh, gw)
        pos = visual.positional_embedding
        side = round((len(pos) - 1) ** 0.5)
        grid_pos = F.interpolate(pos[1:].T.reshape(1, -1, side, side), size=x.shape[-2:], mode="bicubic")
        x = torch.cat([visual.class_embedding + pos[:1], (x + grid_pos).flatten(2)[0].T])  # (1 + N, width)
        x = visual.ln_pre(x)[:, None]  # (L, 1, width)
        *blocks, last = visual.transformer.resblocks
        for block in blocks:
            x = block(x)
        width = x.shape[-1]
        x = last.attn.out_proj(F.linear(last.ln_1(x), last.attn.in_proj_weight[-width:], last.attn.in_proj_bias[-width:]))
        return visual.ln_post(x[1:, 0]) @ visual.proj

    @torch.inference_mode()
    def text(self, instruction):
        """문장 → L2 정규화 텍스트 임베딩 (D,)."""
        tokens = clip.tokenize(instruction).to(self.model.positional_embedding.device)
        return F.normalize(self.model.encode_text(tokens).float(), dim=-1)[0]
