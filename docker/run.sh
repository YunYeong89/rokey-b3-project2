#!/usr/bin/env bash
# vision 컨테이너를 한 번 만들고, 마운트한 vision_project를 symlink 빌드합니다 (model/ 경로가 소스로 풀리도록).
# 이후: docker start vision && docker exec -it vision bash
#   ros2 run vision_project detection --object coke
#   ros2 run vision_project pick
set -e
SRC=$(cd "$(dirname "$0")/.." && pwd)
GPU=$(command -v nvidia-smi >/dev/null && echo "--gpus all")
# 호스트(roboton·realsense)와 같은 조건으로 통신하도록 ROS 통신 변수를 그대로 넘깁니다 (호스트에 있는 값만 넘어감).
ROS_ENV="-e ROS_DOMAIN_ID -e RMW_IMPLEMENTATION -e ROS_LOCALHOST_ONLY -e ROS_AUTOMATIC_DISCOVERY_RANGE \
-e ROS_STATIC_PEERS -e CYCLONEDDS_URI -e FASTRTPS_DEFAULT_PROFILES_FILE"
CYCLONE=${CYCLONEDDS_URI#file://}  # CYCLONEDDS_URI가 파일 경로면 같은 경로로 마운트
CYCLONE_MOUNT=$([ -f "$CYCLONE" ] && echo "-v $CYCLONE:$CYCLONE:ro" || true)
xhost +local:docker >/dev/null  # OpenCV 창 (호스트 X11)
docker run -d --name vision $GPU --network host --ipc host $ROS_ENV $CYCLONE_MOUNT \
  -e DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$SRC":/ros2_ws/src/vision_project vision_project:jazzy
docker exec vision bash -c "source /opt/dsr_ws/install/setup.bash && cd /ros2_ws && colcon build --symlink-install"
