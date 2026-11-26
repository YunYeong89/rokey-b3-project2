#!/usr/bin/env bash
# vision_project 이미지를 빌드합니다. CUDA 휠 태그는 TORCH_CUDA로 바꿉니다 (예: TORCH_CUDA=cu128 ./build.sh).
set -e
docker build --build-arg TORCH_CUDA="${TORCH_CUDA:-cu126}" -t vision_project:jazzy "$(dirname "$0")"
