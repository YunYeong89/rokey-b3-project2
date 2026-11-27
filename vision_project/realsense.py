import numpy as np
from rclpy.node import Node
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge


class ImgNode(Node):
    def __init__(self):
        super().__init__('img_node')
        self.bridge = CvBridge()
        self.color_frame = None
        self.color_frame_stamp = None
        self.depth_frame = None
        self.intrinsics = None
        # 큐 1: 추론 중 쌓인 옛 프레임 대신 spin_once 때 최신 프레임을 받습니다.
        self.color_subscription = self.create_subscription(
            Image, '/camera/color/image_raw', self.color_callback, 1)
        self.depth_subscription = self.create_subscription(
            Image, '/camera/aligned_depth_to_color/image_raw', self.depth_callback, 1)
        self.camera_info_subscription = self.create_subscription(
            CameraInfo, '/camera/color/camera_info', self.camera_info_callback, 1)
        self.get_logger().info("Waiting for client's call...")
        self._img_exec = SingleThreadedExecutor()
        self._img_exec.add_node(self)

    def spin_once(self, timeout_sec=0.1):
        self._img_exec.spin_once(timeout_sec=timeout_sec)

    def camera_info_callback(self, msg):
        self.intrinsics = {"fx": msg.k[0], "fy": msg.k[4], "ppx": msg.k[2], "ppy": msg.k[5]}

    def color_callback(self, msg):
        self.color_frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        self.color_frame_stamp = str(msg.header.stamp.sec) + str(msg.header.stamp.nanosec)

    def depth_callback(self, msg):
        self.depth_frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='passthrough')

    def get_color_frame(self):
        return self.color_frame

    def get_color_frame_stamp(self):
        return self.color_frame_stamp

    def get_depth_frame(self):
        return self.depth_frame

    def get_camera_intrinsic(self):
        return self.intrinsics


class RosCamera:
    """realsense 노드 토픽(컬러 BGR, 정렬 depth mm)을 GraspStream 소스 인터페이스(read/close/frame)로 감쌉니다.
    카메라는 realsense 노드가 점유하므로 grasp_clustering의 RealSenseCamera(pyrealsense2 직접 연결) 대신 씁니다."""

    def __init__(self):
        self.node = ImgNode()
        while self.node.color_frame is None or self.node.depth_frame is None or self.node.intrinsics is None:
            self.node.spin_once()
        self.frame = self.node.color_frame  # InstructionStream.run 대기 화면 크기
        self.intrinsics = self.node.intrinsics

    def read(self, timeout=0.1):
        """(frame_id, stamp, (BGR, depth m)). 컬러 프레임이 그대로면 같은 id라 InstructionStream._infer가 건너뜁니다."""
        self.node.spin_once(timeout)
        stamp = int(self.node.color_frame_stamp)
        return stamp, stamp, (self.node.color_frame, self.node.depth_frame.astype(np.float32) / 1000)

    def close(self):
        self.node.destroy_node()
