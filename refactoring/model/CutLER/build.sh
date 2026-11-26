#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PLATFORM="$(uname -s)"
ARCH="$(uname -m)"
ENV_NAME="cutler"  # sam2 환경과 iopath 버전이 충돌해 별도 환경 사용
DETECTRON2_DIR="$SCRIPT_DIR/../detectron2"
OPS_DIR="$SCRIPT_DIR/mask2former/modeling/pixel_decoder/ops"

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

run_python -m pip install --upgrade pip setuptools wheel ninja

# Linux: 드라이버·툴킷 CUDA 버전에 맞는 PyTorch 휠 선택, macOS: 기본 PyPI 휠(MPS)
torch_index="https://pypi.org/simple"
nvcc=""
toolkit_cuda=""
if [[ "$PLATFORM" == "Linux" ]]; then
    driver_cuda="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \([0-9]*\.[0-9]*\).*/\1/p' || true)"
    nvcc="$(command -v nvcc || true)"
    if [[ -z "$nvcc" && -x "${CUDA_HOME:-/usr/local/cuda}/bin/nvcc" ]]; then
        nvcc="${CUDA_HOME:-/usr/local/cuda}/bin/nvcc"
    fi
    if [[ -n "$nvcc" ]]; then
        toolkit_cuda="$("$nvcc" --version | sed -n 's/.*release \([0-9]*\.[0-9]*\).*/\1/p')"
    fi
    cuda_tag="$("$BASE_PYTHON" - "$driver_cuda" "$toolkit_cuda" <<'PY'
import sys

def version(value):
    return tuple(map(int, value.split("."))) if value else (0, 0)

driver, toolkit = map(version, sys.argv[1:])
supported = [(13, 0), (12, 8), (12, 6), (12, 4), (12, 1), (11, 8)]
choices = [item for item in supported if item <= driver]
selected = toolkit if toolkit in choices else next(iter(choices), None)
print("cu" + "".join(map(str, selected)) if selected else "cpu")
PY
)"
    torch_index="https://download.pytorch.org/whl/$cuda_tag"
    echo "PyTorch build: $cuda_tag (driver CUDA: ${driver_cuda:-none}, toolkit: ${toolkit_cuda:-none})."
fi

echo "Installing PyTorch from $torch_index."
run_python -m pip install --upgrade 'torch>=2.5.1' 'torchvision>=0.20.1' --index-url "$torch_index"
run_python -m pip install -r requirements.txt

build_cuda=0
if [[ -n "$nvcc" ]]; then
    export CUDA_HOME="$(dirname "$(dirname "$nvcc")")"
    torch_cuda="$(run_python -c 'import torch; print(torch.version.cuda or "none")')"
    if [[ "$torch_cuda" == "$toolkit_cuda" ]] && run_python -c 'import torch; raise SystemExit(not torch.cuda.is_available())'; then
        build_cuda=1
    fi
fi

echo "Building detectron2 (CUDA ops: $build_cuda)."
run_python -m pip install --no-build-isolation -e "$DETECTRON2_DIR"
if [[ "$build_cuda" == 1 ]]; then
    echo "Building MultiScaleDeformableAttention CUDA op."
    run_python -m pip install --no-build-isolation "$OPS_DIR"
fi
run_python -m pip check
run_python -c '
import importlib.util
import torch
import detectron2
from detectron2.projects.deeplab import add_deeplab_config
from detectron2.projects.point_rend.point_features import point_sample
import mask2former
import mask2former_video

device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
print("detectron2", detectron2.__version__, "and VideoCutLER modules: OK")
print("Available device:", device)
print("MSDeformAttn CUDA op:", "available" if importlib.util.find_spec("MultiScaleDeformableAttention") else "unavailable (PyTorch fallback)")
'

echo "Setup complete. Activate the environment with:"
printf '  source %q\n' "$CONDA_BASE/etc/profile.d/conda.sh"
printf '  conda activate %q\n' "$ENV_NAME"
