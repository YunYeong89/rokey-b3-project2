"""realsense 토픽 프레임으로 model/DINOv3/grasp_clustering.py(분할 → DINOv3 그룹 → CLIP --object 선택 → depth 가까운 순 라벨)를
화면에 띄우고, 잡을 점(segmentation 무게중심)과 장애물 점을 base 좌표(mm)로 바꿔 /get_grasp_positions 서비스로 제공합니다 (docker 실행).
응답: Trigger.message = JSON {
  "clusters": 선택된 DINOv3 묶음 수 (pick은 1일 때만 진행), "labels": ["A0", "A1", …] (화면 라벨, targets 순서),
  "targets": {"0": [x, y, z], …} (마스크 안 유효 depth 점들의 무게중심, 전체 가까운 순, 0 = 가장 가까움),
  "target_points": [[x, y, z], …] (0번 물체 점, 그리퍼 벌림 폭 계산용), "obstacles": [[x, y, z], …] (0번을 뺀 depth 점)}."""

import json
from pathlib import Path
import sys
from threading import Thread
import time

from ament_index_python.packages import get_package_share_directory
import cv2
from dsr_msgs2.srv import GetCurrentPosx
import numpy as np
import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.utilities import remove_ros_args
from scipy.spatial.transform import Rotation
from std_srvs.srv import Trigger

from vision_project.model_loader import load_grasp_clustering
from vision_project.realsense import RosCamera

GRIPPER2CAM = Path(get_package_share_directory("vision_project")) / "resource" / "T_gripper2camera.npy"
# 두산 컨트롤러의 현재 TCP 자세 서비스. DRL(DSR_ROBOT2)을 쓰지 않고 직접 불러 이미지 안 DRL 버전·실행기와 무관하게 합니다.
POSX_SERVICE = "/dsr01/dsr_controller2/aux_control/get_current_posx"
POSX_TIMEOUT = 2.0              # s, 응답을 기다리는 최대 시간 (넘으면 그 프레임은 건너뜀)
OBSTACLE_STRIDE = 4             # px, 장애물 점을 뽑는 간격
OBSTACLE_VOXEL = 15             # mm, 장애물 점을 줄이는 격자 크기
OBSTACLE_RANGE = (0.2, 1.5)     # m, 그리퍼가 비치는 가까운 점과 먼 배경을 뺀 depth 범위
TARGET_PAD = 7                  # px, 장애물에서 뺄 0번 마스크를 넓히는 폭 (경계의 번진 depth 제외)
TARGET_STRIDE = 2               # px, 물체 점(무게중심·폭)을 뽑는 간격
TARGET_VOXEL = 5                # mm, 응답에 넣는 0번 물체 점을 줄이는 격자 크기

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
    스레드: 메인 = OpenCV 화면, 추론 = 분할·base 변환, 서비스 = 별도 executor (get_grasp_positions 제공, posx 응답 수신)."""

    def __init__(self, args):
        super().__init__(args)
        self.gripper2cam = np.load(GRIPPER2CAM)
        self.result = {"clusters": 0, "labels": [], "targets": {}, "target_points": [], "obstacles": []}  # 최신 응답
        self.updated = 0.0  # self.result를 만든 추론의 시작 시각
        self.node = rclpy.create_node("grasp_detection")
        # get_positions가 새 추론을 기다리는 동안에도 posx 응답을 받도록 재진입 그룹 + 스레드 2개로 돌립니다.
        group = ReentrantCallbackGroup()
        self.posx = self.node.create_client(GetCurrentPosx, POSX_SERVICE, callback_group=group)
        while not self.posx.wait_for_service(timeout_sec=3.0):
            print(f"Waiting for {POSX_SERVICE} (is the robot bringup running and visible from this container?)",
                  flush=True)
        self.node.create_service(Trigger, "get_grasp_positions", self.get_positions, callback_group=group)
        executor = MultiThreadedExecutor(num_threads=2)
        executor.add_node(self.node)
        Thread(target=executor.spin, name="grasp-service", daemon=True).start()
        self.name += " + base"

    def open_source(self):
        print("Source: ROS realsense topics.", flush=True)
        return RosCamera()

    def process(self, frame):
        """가까운 순 마스크 → 마스크 안 유효 depth 점들을 T_gripper2camera·현재 posx로 base 좌표(mm)로 바꿔 무게중심을 잡을 점으로 씁니다."""
        start = time.monotonic()
        pose = self.current_pose()
        if pose is None:
            return frame[0].copy(), f"No response from {POSX_SERVICE} within {POSX_TIMEOUT:.0f} s"
        base2cam = pose_matrix(*pose) @ self.gripper2cam
        masks, boxes, groups, depths, total = self.rank(frame)
        depth = frame[1]

        clouds = [self.mask_points(depth, mask, base2cam) for mask in masks]  # rank에서 유효 depth 없는 마스크는 이미 빠짐
        base = np.array([cloud.mean(0) for cloud in clouds]).reshape(-1, 3)
        points = np.array([np.argwhere(mask).mean(0)[::-1] for mask in masks]).reshape(-1, 2)  # 화면 표시용 (u, v)
        labels = self.labels(groups)[1]
        obstacles = self.obstacles(depth, masks[0] if len(masks) else None, base2cam)
        target_points = (np.unique(np.round(clouds[0] / TARGET_VOXEL), axis=0) * TARGET_VOXEL).astype(int) if clouds else []
        self.result = {"clusters": len(np.unique(groups)), "labels": labels,
                       "targets": {str(rank): xyz.round(1).tolist() for rank, xyz in enumerate(base)},
                       "target_points": np.asarray(target_points).tolist(), "obstacles": obstacles.tolist()}
        self.updated = start

        overlay = self.render(frame[0], masks, boxes, groups, depths)
        for u, v in points.astype(int):
            cv2.circle(overlay, (u, v), 5, (0, 0, 255), -1)
        # 서비스 키(전체 가까운 순 0, 1, …)와 화면 라벨(A0, B0 …)을 함께 표시합니다.
        coords = " ".join(f"{rank}={label}:({x:.0f},{y:.0f},{h:.0f})"
                          for rank, (label, (x, y, h)) in enumerate(zip(labels, base)))
        return overlay, (f"Clusters {self.result['clusters']} | Obstacles {len(obstacles)} | "
                         f"Grasp {len(base)}/{total} | base mm {coords}")

    def current_pose(self):
        """두산 컨트롤러의 현재 TCP posx [x, y, z, rx, ry, rz] (base, mm·ZYZ deg). POSX_TIMEOUT 안에 응답이 없으면 None.
        응답은 서비스 executor 스레드가 받고, 추론 스레드는 future가 끝나기만 기다립니다."""
        future = self.posx.call_async(GetCurrentPosx.Request(ref=0))
        deadline = time.monotonic() + POSX_TIMEOUT
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.005)
        return list(future.result().task_pos_info[0].data[:6]) if future.done() else None

    def to_base(self, u, v, z, base2cam):
        """픽셀 (u, v)와 depth z (m) → base 좌표 점 (N, 3) mm (T_gripper2camera 단위)."""
        k = self.source.intrinsics
        z = z * 1000
        camera = np.stack([(u - k["ppx"]) * z / k["fx"], (v - k["ppy"]) * z / k["fy"], z, np.ones_like(z)])
        return (base2cam @ camera)[:3].T

    def mask_points(self, depth, mask, base2cam):
        """마스크 안 유효 depth 픽셀(TARGET_STRIDE 간격) → base 좌표 점 (N, 3) mm. 무게중심과 그리퍼 벌림 폭 계산용."""
        v, u = np.nonzero(mask[::TARGET_STRIDE, ::TARGET_STRIDE])
        v, u = v * TARGET_STRIDE, u * TARGET_STRIDE
        z = depth[v, u]
        return self.to_base(u[z > 0], v[z > 0], z[z > 0], base2cam)

    def obstacles(self, depth, target, base2cam):
        """depth (m)에서 0번 마스크(TARGET_PAD px 넓힘)를 뺀 점을 OBSTACLE_STRIDE 간격으로 뽑아 base 좌표(mm)로 바꾸고
        OBSTACLE_VOXEL 격자로 줄인 장애물 점 (M, 3) int. 그리퍼가 비치는 가까운 점과 먼 배경은 OBSTACLE_RANGE로 뺍니다."""
        excluded = np.zeros(depth.shape, bool) if target is None else cv2.dilate(
            target.astype(np.uint8), np.ones((2 * TARGET_PAD + 1,) * 2, np.uint8)).astype(bool)
        v, u = np.mgrid[0:depth.shape[0]:OBSTACLE_STRIDE, 0:depth.shape[1]:OBSTACLE_STRIDE]
        z = depth[v, u]
        keep = (z > OBSTACLE_RANGE[0]) & (z < OBSTACLE_RANGE[1]) & ~excluded[v, u]
        base = self.to_base(u[keep], v[keep], z[keep], base2cam)
        return (np.unique(np.round(base / OBSTACLE_VOXEL), axis=0) * OBSTACLE_VOXEL).astype(int)

    def get_positions(self, request, response):
        """요청 이후 시작한 추론 결과를 기다려 JSON(모듈 docstring 형식)으로 돌려줍니다 (로봇 정지 후 프레임 보장).
        success는 선택된 묶음이 정확히 1개일 때만 True입니다."""
        start = time.monotonic()
        while self.updated < start:
            time.sleep(0.05)
        response.success, response.message = self.result["clusters"] == 1, json.dumps(self.result)
        return response


def parse_args():
    parser = create_parser()
    parser.add_argument("--object", dest="instruction",
                        help="Target item in English, e.g. coke (same as --instruction; type a new one at runtime).")
    return parser.parse_args(remove_ros_args(sys.argv)[1:])


def main():
    args = parse_args()
    rclpy.init()
    GraspDetector(args).run()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
