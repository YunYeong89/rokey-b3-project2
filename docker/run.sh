#!/usr/bin/env bash
# vision 컨테이너를 한 번 만들고, 마운트한 vision_project를 symlink 빌드합니다 (model/ 경로가 소스로 풀리도록).
# 이후: docker start vision && docker exec -it vision bash
#   ros2 run vision_project detection --object coke
#   ros2 run vision_project pick
set -e
SRC=$(cd "$(dirname "$0")/.." && pwd)
GPU=$(command -v nvidia-smi >/dev/null && echo "--gpus all")
xhost +local:docker >/dev/null  # OpenCV 창 (호스트 X11)
docker run -d --name vision $GPU --network host --ipc host \
  -e DISPLAY -e ROS_DOMAIN_ID -e RMW_IMPLEMENTATION -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$SRC":/ros2_ws/src/vision_project vision_project:jazzy
docker exec vision bash -c "source /ros2_ws/install/setup.bash && cd /ros2_ws && \
  colcon build --symlink-install --packages-select vision_project"
