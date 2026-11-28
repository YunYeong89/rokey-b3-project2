"""RealSense 카메라 (ROS2 토픽) — Calibration_Tutorial/realsense.py 기반.

컬러 / 컬러에 맞춘 깊이(mm) / 카메라 내부값(fx, fy, ppx, ppy)을 받는다.
capture() 는 '부른 뒤에 들어온' 새 사진을 돌려준다 (로봇이 움직이기 전 사진을 쓰지 않도록).
"""
import time

from cv_bridge import CvBridge
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image

import config


class Camera(Node):
    def __init__(self):
        super().__init__("pickplace_camera")
        self.bridge = CvBridge()
        self.color = self.depth = self.intrinsics = None
        self.color_time = self.depth_time = 0.0
        self.create_subscription(Image, config.COLOR_TOPIC, self._on_color, 10)
        self.create_subscription(Image, config.DEPTH_TOPIC, self._on_depth, 10)
        self.create_subscription(CameraInfo, config.CAMERA_INFO_TOPIC, self._on_info, 10)

    def _on_color(self, msg):
        self.color = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        self.color_time = time.monotonic()

    def _on_depth(self, msg):
        self.depth = self.bridge.imgmsg_to_cv2(msg, desired_encoding="passthrough")
        self.depth_time = time.monotonic()

    def _on_info(self, msg):
        self.intrinsics = {"fx": msg.k[0], "fy": msg.k[4], "ppx": msg.k[2], "ppy": msg.k[5]}

    def capture(self, rclpy):
        """(컬러 BGR, 깊이 mm, 내부값) — 이 함수를 부른 뒤에 들어온 사진"""
        since = time.monotonic()
        deadline = since + config.CAMERA_TIMEOUT
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
            if self.intrinsics and self.color_time > since and self.depth_time > since:
                return self.color.copy(), self.depth.copy(), dict(self.intrinsics)
        missing = [name for name, ok in (("컬러", self.color_time > since), ("깊이", self.depth_time > since),
                                         ("camera_info", self.intrinsics)) if not ok]
        raise RuntimeError(f"카메라 사진을 받지 못했습니다 ({', '.join(missing)}) — "
                           "bringup 을 camera:=true 로 띄웠는지, 토픽 이름(config.COLOR_TOPIC 등)을 확인하세요")
