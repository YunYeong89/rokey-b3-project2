#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PLATFORM="$(uname -s)"
ARCH="$(uname -m)"
ENV_NAME="DINOv3"

case "$PLATFORM:$ARCH" in
    Darwin:arm64|Linux:x86_64|Linux:aarch64) ;;
    *) echo "Unsupported platform: $PLATFORM $ARCH. Use Apple Silicon macOS or Linux x86_64/aarch64." >&2; exit 1 ;;
esac

CONDA_BIN="${CONDA_EXE:-$(command -v conda || true)}"
for prefix in "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3"; do
    [[ -x "$CONDA_BIN" ]] || CONDA_BIN="$prefix/bin/conda"
done
[[ -x "$CONDA_BIN" ]] || { echo "Conda not found. Install Miniforge or Miniconda first." >&2; exit 1; }

CONDA_BASE="$("$CONDA_BIN" info --base)"
BASE_PYTHON="$CONDA_BASE/bin/python"
if "$CONDA_BIN" env list --json | "$BASE_PYTHON" -c '
import json, os, sys
sys.exit(not any(os.path.basename(path) == sys.argv[1] for path in json.load(sys.stdin)["envs"]))
' "$ENV_NAME"; then
    echo "Reusing Conda environment: $ENV_NAME (Python will not be changed)."
else
    echo "Creating Conda environment: $ENV_NAME with Python 3.12."
    "$CONDA_BIN" create --yes --name "$ENV_NAME" --override-channels -c conda-forge python=3.12 pip
fi

"$CONDA_BIN" env config vars set --name "$ENV_NAME" PYTHONPATH=
if [[ "$PLATFORM" == "Darwin" ]]; then
    "$CONDA_BIN" env config vars set --name "$ENV_NAME" DYLD_LIBRARY_PATH= PYTORCH_ENABLE_MPS_FALLBACK=1
fi

run_python() {
    "$CONDA_BIN" run --no-capture-output --name "$ENV_NAME" python "$@"
}

run_python -m pip install --upgrade pip setuptools wheel

# Linux: 드라이버 CUDA 버전에 맞는 PyTorch 휠 (컴파일할 확장이 없어 툴킷은 불필요), macOS: 기본 PyPI 휠(MPS)
torch_index="https://pypi.org/simple"
if [[ "$PLATFORM" == "Linux" ]]; then
    driver_cuda="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \([0-9]*\.[0-9]*\).*/\1/p' || true)"
    cuda_tag="$("$BASE_PYTHON" - "$driver_cuda" <<'PY'
import sys

driver = tuple(map(int, sys.argv[1].split("."))) if sys.argv[1] else (0, 0)
supported = [(13, 0), (12, 8), (12, 6), (12, 4), (12, 1), (11, 8)]
selected = next((item for item in supported if item <= driver), None)
print("cu" + "".join(map(str, selected)) if selected else "cpu")
PY
)"
    torch_index="https://download.pytorch.org/whl/$cuda_tag"
    echo "PyTorch build: $cuda_tag (driver CUDA: ${driver_cuda:-none})."
fi

echo "Installing PyTorch from $torch_index."
run_python -m pip install --upgrade 'torch>=2.5.1' 'torchvision>=0.20.1' --index-url "$torch_index"
run_python -m pip install -r requirements.txt
run_python -m pip check
run_python -c '
import torch
import ultralytics
import group
from sam2.build_sam import build_sam2

device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
print("DINOv3 + CLIP + FastSAM/SAM2 imports: OK")
print("Available device:", device)
print("ViT-S/16 checkpoint:", "found" if group.CHECKPOINT.is_file() else f"missing, place it at {group.CHECKPOINT}")
'

echo "Setup complete. Activate the environment with:"
printf '  source %q\n' "$CONDA_BASE/etc/profile.d/conda.sh"
printf '  conda activate %q\n' "$ENV_NAME"
