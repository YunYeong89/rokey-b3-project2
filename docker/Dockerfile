# vision_project 실행 이미지: ROS 2 Jazzy + 두산 DRL API(dsr_msgs2, dsr_common2의 DR_init·DSR_ROBOT2) + DINOv3/CLIP/FastSAM.
# conda 대신 ROS 시스템 파이썬(3.12)에 모델 의존성을 함께 설치해 detection·pick을 ros2 run으로 그대로 실행합니다.
# 소스(vision_project, model 가중치 포함)는 run.sh가 마운트해 컨테이너 안에서 symlink 빌드합니다.
#   ros2_ws/src/vision_project/docker/build.sh
FROM ros:jazzy-ros-base-noble

ARG TARGETARCH
ARG TORCH_CUDA=cu126
ARG DOOSAN_REPO=https://github.com/doosan-robotics/doosan-robot2.git
ARG DOOSAN_BRANCH=jazzy
ENV DEBIAN_FRONTEND=noninteractive PIP_BREAK_SYSTEM_PACKAGES=1 PIP_NO_CACHE_DIR=1

# cv_bridge, dsr_common2 빌드(poco), OpenCV 창(X11)
RUN apt-get update && apt-get install -y --no-install-recommends \
      git python3-pip libpoco-dev libyaml-cpp-dev ros-jazzy-cv-bridge ros-jazzy-rmw-zenoh-cpp \
      libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libxkbcommon-x11-0 libfontconfig1 \
 && rm -rf /var/lib/apt/lists/*

# torch: amd64는 CUDA 휠(드라이버만 있으면 됨, --gpus all), arm64(Mac Docker)는 CPU 휠.
# numpy<2: apt cv_bridge가 numpy 1.x로 빌드되어 있음 (opencv-python 4.12+는 numpy 2 필요).
# pymodbus<3.7: onrobot.RG가 쓰는 ModbusTcpClient(..., stopbits=...)·slave= 인자를 받는 마지막 계열.
RUN INDEX=$([ "$TARGETARCH" = arm64 ] && echo cpu || echo "$TORCH_CUDA") \
 && pip install --index-url "https://download.pytorch.org/whl/$INDEX" torch torchvision \
 && pip install "numpy<2" "opencv-python<4.12" pillow ftfy regex packaging tqdm hydra-core iopath \
      ultralytics scipy "pymodbus<3.7"

# DRL API: 메시지와 DR_init·DSR_ROBOT2(dsr_common2/imp)만 별도 underlay(/opt/dsr_ws)에 빌드합니다.
# /ros2_ws(마운트한 vision_project)에서 colcon build를 어떻게 돌려도 dsr 패키지를 다시 빌드하지 않도록 분리합니다
# (같은 워크스페이스면 --symlink-install 재빌드가 이미 설치된 dsr_msgs2와 충돌). bringup(roboton)은 호스트에서 띄웁니다.
WORKDIR /opt/dsr_ws
RUN git clone -b "$DOOSAN_BRANCH" --depth 1 "$DOOSAN_REPO" /tmp/doosan \
 && mkdir -p src && mv /tmp/doosan/dsr_msgs2 /tmp/doosan/dsr_common2 src/ && rm -rf /tmp/doosan \
 && . /opt/ros/jazzy/setup.sh && colcon build --packages-select dsr_msgs2 dsr_common2 && rm -rf build log

WORKDIR /ros2_ws
RUN mkdir -p src && echo 'source /opt/ros/jazzy/setup.bash && source /opt/dsr_ws/install/setup.bash && \
[ -f /ros2_ws/install/setup.bash ] && source /ros2_ws/install/setup.bash' >> /root/.bashrc
CMD ["sleep", "infinity"]
