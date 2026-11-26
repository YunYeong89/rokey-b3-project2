"""V-JEPA 2.1 인코더 생성 + 공식 가중치 로드 (upstream src/hub/backbones.py의 2.1 부분).
upstream은 VJEPA_BASE_URL이 localhost로 커밋돼 있어 공식 URL을 직접 씁니다. 예측기(predictor)는 만들지 않습니다."""

from pathlib import Path

import torch

from .vision_transformer import vit_base

CHECKPOINT_DIR = Path(__file__).resolve().parents[1] / "checkpoints"
BASE_URL = "https://dl.fbaipublicfiles.com/vjepa2"


def vjepa2_1_vit_base_384(pretrained=True):
    """ViT-B/16 (ViT-G 증류, 384) 인코더. 가중치는 첫 호출 때 checkpoints/로 받습니다.
    입력 (B, C, 1, H, W)는 이미지 토크나이저, (B, C, T, H, W)는 비디오 토크나이저 경로를 탑니다. 출력은 (B, N, 768)."""
    encoder = vit_base(
        img_size=(384, 384), num_frames=64, tubelet_size=2, use_sdpa=True, uniform_power=False,
        use_rope=True, img_temporal_dim_size=1, interpolate_rope=True,
    )
    if pretrained:
        state = torch.hub.load_state_dict_from_url(f"{BASE_URL}/vjepa2_1_vitb_dist_vitG_384.pt",
                                                   model_dir=str(CHECKPOINT_DIR), map_location="cpu")["ema_encoder"]
        encoder.load_state_dict({key.replace("module.", "").replace("backbone.", ""): value
                                 for key, value in state.items()})
    return encoder
