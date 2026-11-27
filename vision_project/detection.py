"""realsense 토픽 프레임으로 model/DINOv3/grasp_clustering.py(분할 → DINOv3 그룹 → CLIP --object 선택 → depth 가까운 순 라벨)를
화면에 띄우고, 라벨 순번별 잡을 점(마스크 중심)을 base 좌표(mm)로 바꿔 /get_grasp_positions 서비스로 제공합니다.
응답: Trigger.message = JSON {"0": [x, y, z], "1": [...], ...} (0 = 카메라에서 가장 가까운 물품)."""

import json
from pathlib import Path
import sys
from threading import Thread
import time

from ament_index_python.packages import get_package_share_directory
import cv2
import DR_init
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.utilities import remove_ros_args
from scipy.spatial.transform import Rotation
from std_srvs.srv import Trigger

from vision_project.model_loader import load_grasp_clustering
from vision_project.realsense import RosCamera

ROBOT_ID, ROBOT_MODEL = "dsr01", "m0609"
GRIPPER2CAM = Path(get_package_share_directory("vision_project")) / "resource" / "T_gripper2camera.npy"

# DRL API(DSR_ROBOT2)는 import 전에 DR_init에 로봇 ID·모델·노드가 등록되어 있어야 합니다.
DR_init.__dsr__id, DR_init.__dsr__model = ROBOT_ID, ROBOT_MODEL
rclpy.init()
DR_init.__dsr__node = rclpy.create_node("grasp_detection_dsr", namespace=ROBOT_ID)
from DSR_ROBOT2 import get_current_posx  # noqa: E402

grasp_clustering = load_grasp_clustering()
GraspStream, create_parser = grasp_clustering.GraspStream, grasp_clustering.create_parser


def pose_matrix(x, y, z, rx, ry, rz):
    """DRL posx (mm, ZYZ 오일러 deg) → base 기준 4x4 변환 행렬."""
    matrix = np.eye(4)
    matrix[:3, :3] = Rotation.from_euler("ZYZ", [rx, ry, rz], degrees=True).as_matrix()
    matrix[:3, 3] = x, y, z
    return matrix


class GraspDetector(GraspStream):
    """grasp_clustering 화면(세그멘테이션·가까운 순 라벨)에 잡을 점을 더하고, 라벨별 base 좌표를 서비스로 제공합니다.
    스레드: 메인 = OpenCV 화면, 추론 = 분할·base 변환(DRL get_current_posx), 서비스 = 별도 executor."""

    def __init__(self, args):
        super().__init__(args)
        self.gripper2cam = np.load(GRIPPER2CAM)
        self.positions, self.updated = {}, 0.0  # 최신 {순번: base [x, y, z] mm}, 그 추론을 시작한 시각
        self.node = rclpy.create_node("grasp_detection")
        self.node.create_service(Trigger, "get_grasp_positions", self.get_positions)
        executor = SingleThreadedExecutor()
        executor.add_node(self.node)
        Thread(target=executor.spin, name="grasp-service", daemon=True).start()
        self.name += " + base"

    def open_source(self):
        print("Source: ROS realsense topics.", flush=True)
        return RosCamera()

    def process(self, frame):
        """가까운 순 마스크 → 마스크 중심 픽셀 + 마스크 depth → 카메라 좌표 → T_gripper2camera·현재 posx로 base 좌표."""
        start = time.monotonic()
        base2cam = pose_matrix(*get_current_posx()[0]) @ self.gripper2cam
        masks, boxes, groups, depths, total = self.rank(frame)

        k = self.source.intrinsics
        points = np.array([np.argwhere(mask).mean(0)[::-1] for mask in masks]).reshape(-1, 2)  # (u, v)
        z = depths * 1000  # m → mm (T_gripper2camera 단위)
        camera = np.stack([(points[:, 0] - k["ppx"]) * z / k["fx"], (points[:, 1] - k["ppy"]) * z / k["fy"],
                           z, np.ones_like(z)])
        base = (base2cam @ camera)[:3].T
        self.positions = {str(rank): xyz.round(1).tolist() for rank, xyz in enumerate(base)}
        self.updated = start

        overlay = self.render(frame[0], masks, boxes, groups, depths)
        for u, v in points.astype(int):
            cv2.circle(overlay, (u, v), 5, (0, 0, 255), -1)
        # 서비스 키(전체 가까운 순 0, 1, …)와 화면 라벨(A0, B0 …)을 함께 표시합니다.
        coords = " ".join(f"{rank}={label}:({x:.0f},{y:.0f},{h:.0f})"
                          for rank, (label, (x, y, h)) in enumerate(zip(self.labels(groups)[1], base)))
        return overlay, f"Grasp {len(base)}/{total} | base mm {coords}"

    def get_positions(self, request, response):
        """요청 이후 시작한 추론 결과를 기다려 {순번: base [x, y, z] mm}를 JSON으로 돌려줍니다 (로봇 정지 후 프레임 보장)."""
        start = time.monotonic()
        while self.updated < start:
            time.sleep(0.05)
        response.success, response.message = bool(self.positions), json.dumps(self.positions)
        return response


def parse_args():
    parser = create_parser()
    parser.add_argument("--object", dest="instruction",
                        help="Target item in English, e.g. coke (same as --instruction; type a new one at runtime).")
    return parser.parse_args(remove_ros_args(sys.argv)[1:])


def main():
    GraspDetector(parse_args()).run()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
