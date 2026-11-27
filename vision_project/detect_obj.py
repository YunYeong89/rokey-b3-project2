"""realsense2_camera 노드의 토픽(컬러·정렬 depth·camera_info)을 구독해 model/DINOv3/grasp_clustering.py 로직
(분할 → depth 조각 합치기 → DINOv3 그룹 → CLIP --object 선택 → depth 가까운 순 0, 1, 2 … 라벨)을 그대로 실행합니다.
로봇(DRL)에 의존하지 않는 검출 확인용 노드이며, 결과 화면을 OpenCV 창과 /detect_obj/image 토픽으로 내보냅니다.
  ros2 run vision_project detect_obj --object coke              # 창 + 토픽
  ros2 run vision_project detect_obj --object coke --headless   # 토픽만 (호스트에서 rqt_image_view /detect_obj/image)"""

import sys
from threading import Thread

from cv_bridge import CvBridge
import rclpy
from rclpy.utilities import remove_ros_args
from sensor_msgs.msg import Image

from vision_project.model_loader import load_grasp_clustering
from vision_project.realsense import RosCamera

grasp_clustering = load_grasp_clustering()
GraspStream, create_parser = grasp_clustering.GraspStream, grasp_clustering.create_parser


class ObjectDetector(GraspStream):
    """GraspStream(grasp_clustering)의 입력만 ROS realsense 토픽으로 바꾸고, 그린 결과를 Image 토픽으로도 발행합니다."""

    def __init__(self, args):
        super().__init__(args)
        self.node = rclpy.create_node("detect_obj")
        self.publisher = self.node.create_publisher(Image, "detect_obj/image", 1)
        self.bridge = CvBridge()
        self.name += " + ROS"

    def open_source(self):
        print("Source: ROS realsense topics.", flush=True)
        return RosCamera()

    def process(self, frame):
        overlay, status = super().process(frame)
        self.publisher.publish(self.bridge.cv2_to_imgmsg(overlay, encoding="bgr8"))
        return overlay, status

    def run(self):
        """--headless면 OpenCV 창 없이 추론만 돌립니다 (instruction은 터미널 입력으로 변경, Ctrl+C로 종료)."""
        if not self.args.headless:
            return super().run()
        self.source = self.open_source()
        Thread(target=self._read_input, name="instruction-input", daemon=True).start()
        print(f"{self.name} on {self.device}, publishing /detect_obj/image. Press Ctrl+C to exit.", flush=True)
        try:
            self._infer()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            self.source.close()
        if self.error is not None:
            raise RuntimeError(f"{self.name} inference failed.") from self.error


def parse_args():
    parser = create_parser()
    parser.add_argument("--object", dest="instruction",
                        help="Target item in English, e.g. coke (same as --instruction; type a new one at runtime).")
    parser.add_argument("--headless", action="store_true",
                        help="No OpenCV window; view /detect_obj/image instead, e.g. with rqt_image_view.")
    return parser.parse_args(remove_ros_args(sys.argv)[1:])


def main():
    args = parse_args()
    rclpy.init()
    ObjectDetector(args).run()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
