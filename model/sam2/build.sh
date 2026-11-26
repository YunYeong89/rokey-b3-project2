#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PLATFORM="$(uname -s)"
ARCH="$(uname -m)"
ENV_NAME="sam2"

case "$PLATFORM:$ARCH" in
    Darwin:arm64|Linux:x86_64|Linux:aarch64) ;;
    *) echo "Unsupported platform: $PLATFORM $ARCH. Use Apple Silicon macOS or Linux x86_64/aarch64." >&2; exit 1 ;;
esac

CONDA_BIN="${CONDA_EXE:-$(command -v conda || true)}"
if [[ ! -x "$CONDA_BIN" ]]; then
    for prefix in "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3"; do
        if [[ -x "$prefix/bin/conda" ]]; then
            CONDA_BIN="$prefix/bin/conda"
            break
        fi
    done
fi

if [[ ! -x "$CONDA_BIN" ]]; then
    echo "Conda not found. Installing Miniforge into $HOME/miniforge3."
    installer_dir="$(mktemp -d)"
    trap 'rm -rf "$installer_dir"' EXIT
    installer_os="$PLATFORM"
    [[ "$PLATFORM" != "Darwin" ]] || installer_os="MacOSX"
    installer="Miniforge3-$installer_os-$ARCH.sh"
    url="https://github.com/conda-forge/miniforge/releases/latest/download/$installer"
    for suffix in "" ".sha256"; do
        if command -v curl >/dev/null 2>&1; then
            curl --fail --location --retry 3 "$url$suffix" -o "$installer_dir/$installer$suffix"
        elif command -v wget >/dev/null 2>&1; then
            wget -O "$installer_dir/$installer$suffix" "$url$suffix"
        else
            echo "Install curl or wget, then run this script again." >&2
            exit 1
        fi
    done
    (
        cd "$installer_dir"
        if command -v sha256sum >/dev/null 2>&1; then
            sha256sum -c "$installer.sha256"
        else
            shasum -a 256 -c "$installer.sha256"
        fi
    )
    bash "$installer_dir/$installer" -b -p "$HOME/miniforge3"
    CONDA_BIN="$HOME/miniforge3/bin/conda"
fi

CONDA_BASE="$("$CONDA_BIN" info --base)"
BASE_PYTHON="$CONDA_BASE/bin/python"
if "$CONDA_BIN" env list --json | "$BASE_PYTHON" -c '
import json, os, sys
sys.exit(not any(os.path.basename(path) == "sam2" for path in json.load(sys.stdin)["envs"]))
'; then
    echo "Reusing Conda environment: $ENV_NAME (Python will not be changed)."
else
    echo "Creating Conda environment: $ENV_NAME with Python 3.12."
    "$CONDA_BIN" create --yes --name "$ENV_NAME" --override-channels -c conda-forge python=3.12 pip
fi

"$CONDA_BIN" env config vars set --name "$ENV_NAME" PYTHONPATH=
if [[ "$PLATFORM" == "Darwin" ]]; then
    "$CONDA_BIN" env config vars set --name "$ENV_NAME" DYLD_LIBRARY_PATH=
fi

run_python() {
    "$CONDA_BIN" run --no-capture-output --name "$ENV_NAME" python "$@"
}

run_python -c 'import sys; assert sys.version_info >= (3, 10), "SAM2 requires Python >= 3.10"; print("Python:", sys.version.split()[0])'
run_python -m pip install --upgrade pip setuptools wheel ninja

torch_index="https://pypi.org/simple"
if [[ "$PLATFORM" == "Linux" ]]; then
    driver_cuda=""
    if command -v nvidia-smi >/dev/null 2>&1; then
        driver_cuda="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \([0-9]*\.[0-9]*\).*/\1/p' || true)"
    fi
    nvcc="$(command -v nvcc || true)"
    if [[ -z "$nvcc" && -x "${CUDA_HOME:-/usr/local/cuda}/bin/nvcc" ]]; then
        nvcc="${CUDA_HOME:-/usr/local/cuda}/bin/nvcc"
    fi
    toolkit_cuda=""
    if [[ -n "$nvcc" ]]; then
        toolkit_cuda="$("$nvcc" --version | sed -n 's/.*release \([0-9]*\.[0-9]*\).*/\1/p')"
    fi
    cuda_tag="$("$BASE_PYTHON" - "$driver_cuda" "$toolkit_cuda" <<'PY'
import sys

def version(value):
    return tuple(map(int, value.split("."))) if value else (0, 0)

driver, toolkit = map(version, sys.argv[1:])
supported = [(12, 8), (12, 6), (12, 4), (12, 1), (11, 8)]
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

export SAM2_BUILD_CUDA=0
if [[ "$PLATFORM" == "Darwin" ]]; then
    export PYTORCH_ENABLE_MPS_FALLBACK=1
    "$CONDA_BIN" env config vars set --name "$ENV_NAME" PYTORCH_ENABLE_MPS_FALLBACK=1
elif [[ -n "$nvcc" ]]; then
    export CUDA_HOME="$(dirname "$(dirname "$nvcc")")"
    torch_cuda="$(run_python -c 'import torch; print(torch.version.cuda or "none")')"
    if [[ "$torch_cuda" == "$toolkit_cuda" ]] && run_python -c 'import torch; raise SystemExit(not torch.cuda.is_available())'; then
        export SAM2_BUILD_CUDA=1
    fi
fi

echo "Building SAM2 (CUDA extension requested: $SAM2_BUILD_CUDA)."
run_python -m pip install --no-build-isolation --no-deps -e .
run_python -m pip check
run_python -c '
import importlib.util
import torch
import torchvision
from hydra import compose
from sam2.sam2_image_predictor import SAM2ImagePredictor

compose(config_name="configs/sam2.1/sam2.1_hiera_t.yaml")
device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
print("SAM2 import and Tiny config: OK")
print("Available device:", device)
print("CUDA extension:", "available" if importlib.util.find_spec("sam2._C") else "unavailable (optional)")
print(f"Pass device={device!r} when loading the model.")
'

echo "Setup complete. Activate the environment with:"
printf '  source %q\n' "$CONDA_BASE/etc/profile.d/conda.sh"
printf '  conda activate %q\n' "$ENV_NAME"
