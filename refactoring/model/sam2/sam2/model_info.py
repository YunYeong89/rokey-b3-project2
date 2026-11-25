"""SAM 2 및 SAM 2.1 모델의 설정과 가중치 정보."""

from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL_NAME = "sam2.1_tiny"
CHECKPOINT_DIR = Path(__file__).resolve().parents[1] / "checkpoints"


@dataclass(frozen=True)
class SAM2ModelInfo:
    name: str
    config_name: str
    checkpoint_name: str
    hf_model_id: str

    @property
    def checkpoint_path(self) -> Path:
        return CHECKPOINT_DIR / self.checkpoint_name

    @property
    def checkpoint_url(self) -> str:
        release = "092824" if self.name.startswith("sam2.1_") else "072824"
        return f"https://dl.fbaipublicfiles.com/segment_anything_2/{release}/{self.checkpoint_name}"


MODEL_INFOS = {
    info.name: info
    for info in (
        SAM2ModelInfo(
            name="sam2_tiny",
            config_name="configs/sam2/sam2_hiera_t.yaml",
            checkpoint_name="sam2_hiera_tiny.pt",
            hf_model_id="facebook/sam2-hiera-tiny",
        ),
        SAM2ModelInfo(
            name="sam2_small",
            config_name="configs/sam2/sam2_hiera_s.yaml",
            checkpoint_name="sam2_hiera_small.pt",
            hf_model_id="facebook/sam2-hiera-small",
        ),
        SAM2ModelInfo(
            name="sam2_base_plus",
            config_name="configs/sam2/sam2_hiera_b+.yaml",
            checkpoint_name="sam2_hiera_base_plus.pt",
            hf_model_id="facebook/sam2-hiera-base-plus",
        ),
        SAM2ModelInfo(
            name="sam2_large",
            config_name="configs/sam2/sam2_hiera_l.yaml",
            checkpoint_name="sam2_hiera_large.pt",
            hf_model_id="facebook/sam2-hiera-large",
        ),
        SAM2ModelInfo(
            name="sam2.1_tiny",
            config_name="configs/sam2.1/sam2.1_hiera_t.yaml",
            checkpoint_name="sam2.1_hiera_tiny.pt",
            hf_model_id="facebook/sam2.1-hiera-tiny",
        ),
        SAM2ModelInfo(
            name="sam2.1_small",
            config_name="configs/sam2.1/sam2.1_hiera_s.yaml",
            checkpoint_name="sam2.1_hiera_small.pt",
            hf_model_id="facebook/sam2.1-hiera-small",
        ),
        SAM2ModelInfo(
            name="sam2.1_base_plus",
            config_name="configs/sam2.1/sam2.1_hiera_b+.yaml",
            checkpoint_name="sam2.1_hiera_base_plus.pt",
            hf_model_id="facebook/sam2.1-hiera-base-plus",
        ),
        SAM2ModelInfo(
            name="sam2.1_large",
            config_name="configs/sam2.1/sam2.1_hiera_l.yaml",
            checkpoint_name="sam2.1_hiera_large.pt",
            hf_model_id="facebook/sam2.1-hiera-large",
        ),
    )
}

HF_MODEL_ID_TO_FILENAMES = {
    info.hf_model_id: (info.config_name, info.checkpoint_name)
    for info in MODEL_INFOS.values()
}


def get_model_info(model_name: str = DEFAULT_MODEL_NAME) -> SAM2ModelInfo:
    try:
        return MODEL_INFOS[model_name]
    except KeyError:
        raise ValueError(
            f"Unknown SAM2 model: {model_name}. Available models: {', '.join(MODEL_INFOS)}"
        ) from None
