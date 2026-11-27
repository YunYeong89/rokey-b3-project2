"""vision_project detection(docker)의 /get_grasp_positions로 장면을 받아 0번 물품을 잡아 HOME_J로 가져와 그리퍼를 엽니다 (호스트 실행).
1. SCAN_J로 movej 후 검출 요청. 선택된 묶음(cluster)이 정확히 1개일 때만 진행하고, 아니면 RETRY_DELAY 뒤 재시도합니다.
   스캔 자세는 테이블을 내려다보도록 기울어도 되고, 집는 자세는 스캔 자세의 TCP 방향을 base -y로 맞추는 최소 회전
   (+ GRASP_ROLL)으로 계산합니다. 축 정렬(집는 자세로 회전)은 잡기 직전 위치에서만 합니다.
2. motion 1 (waypoint1): 가장 가까운 목표(0번) 앞(base +y로 APPROACH)의 잡기 직전 위치로 스캔 자세 그대로 movel합니다.
   이 경로(스캔 자세 그리퍼)와 motion 3 구간(집는 자세 그리퍼)을 장애물 점(base)으로 검사해, 막히면 재시도합니다.
3. 보정: 잡기 직전 위치에서 다시 검출해 0번 segmentation 무게중심을 최종 grasp point로 정합니다 (1차 좌표와
   CORRECTION_LIMIT 이상 다르면 거절). 보정된 좌표까지 앞으로 들어가는 구간도 새 장애물 점으로 다시 검사합니다.
4. motion 2: waypoint1 높이에서 제자리로 축 정렬(집는 자세, TCP ∥ -y)합니다. 그리퍼는 가로(TCP 방향에 수직인 수평축)로
   열고 닫도록 고정하고, 물체 점의 가로 폭 + GRIP_MARGIN만큼 벌립니다 (MAX_OPEN 초과면 거절).
   motion 3: 앞으로 movel해 grasp point에 도착하면, TCP 방향(툴 z축)이 base -y와 AXIS_TOLERANCE 이내로
   평행한지 확인하고 잡습니다.
5. 잡으면 LIFT만큼 위로 올린 뒤 같은 높이로 잡기 직전 위치 위까지 movel로 빠지고, HOME_J로 movej해 그리퍼를 엽니다.
고정 자세(SCAN_J, HOME_J)는 movej, 집는 동작은 movel입니다.
동작은 DRL API(DSR_ROBOT2), 그리퍼는 Modbus TCP(onrobot.RG)이며, 그리퍼가 안전 정지하거나 GRIPPER_TIMEOUT을 넘기면 중단합니다."""

import json
import time

import DR_init
import numpy as np
import rclpy
from rclpy.node import Node
from scipy.spatial.transform import Rotation
from std_srvs.srv import Trigger

from vision_project.onrobot import RG

ROBOT_ID, ROBOT_MODEL = "dsr01", "m0609"
VELOCITY, ACC = 12, 12  # movej deg/s·deg/s², movel mm/s·mm/s²
SCAN_J = [-90.0, -54.91, 85.55, 0.0, 90.0, 90.0]  # 검출 자세 (movej), 테이블을 내려다봐도 됨
HOME_J = [0, 0, 90, 0, 90, 90]                     # 잡은 물품을 가져와 그리퍼를 여는 자세
GRASP_ROLL = 0.0                    # deg, 집는 자세를 TCP 축으로 더 돌리는 각도 (손가락이 세로로 벌어지면 90)
GRIPPER_TIMEOUT = 10.0              # s, 그리퍼 동작을 기다리는 최대 시간
APPROACH = 100.0                    # mm, 물품에서 잡기 직전 위치까지 거리 (TCP 방향 반대)
LIFT = 30.0                         # mm, 잡은 뒤 위로 올리는 높이
CORRECTION_LIMIT = 50.0             # mm, 잡기 직전 위치에서 보정한 좌표가 1차 좌표와 달라도 되는 최대 거리
AXIS_TOLERANCE = 5.0                # deg, 집을 때 TCP 방향(툴 z축)과 base -y 사이 허용 각도
GRIP_MARGIN = 20.0                  # mm, 물체 가로 폭보다 더 벌리는 여유 (양쪽 합)
GRASP_DEPTH = 15.0                  # mm, 손가락 끝(TCP)을 물체 중심(앞면 + 가로 폭/2)보다 더 넣는 깊이
MAX_OPEN = 110.0                    # mm, RG2 최대 벌림
GRIPPER_BOX = (120.0, 70.0, 180.0)  # mm, 그리퍼 상자: 가로 폭(손가락이 벌어지는 수평축), 세로 두께, TCP(손가락 끝)→손목 길이 — 실측해 조정
MARGIN = 10.0                       # mm, 상자 여유
STEP = 10.0                         # mm, 경로를 따라 상자를 검사하는 간격
RETRIES, RETRY_DELAY = 10, 2.0      # 재시도 횟수, 간격(s)
GRIPPER_NAME, TOOLCHARGER_IP, TOOLCHARGER_PORT = "rg2", "192.168.1.1", "502"
UP = np.array([0.0, 0.0, 1.0])

# DRL API(DSR_ROBOT2)는 import 전에 DR_init에 로봇 ID·모델·노드가 등록되어 있어야 합니다.
DR_init.__dsr__id, DR_init.__dsr__model = ROBOT_ID, ROBOT_MODEL
rclpy.init()
DR_init.__dsr__node = rclpy.create_node("vision_pick_dsr", namespace=ROBOT_ID)
from DSR_ROBOT2 import get_current_posx, movej, movel, mwait  # noqa: E402


def angle_to_minus_y(pose):
    """posx (mm, ZYZ deg) → TCP 방향(툴 z축)과 base -y 사이 각도 (deg). 0이면 TCP가 정확히 -y를 향합니다."""
    axis = Rotation.from_euler("ZYZ", pose[3:], degrees=True).as_matrix()[:, 2]
    return float(np.degrees(np.arccos(np.clip(-axis[1], -1.0, 1.0))))


def grasp_orientation(pose):
    """스캔 자세 posx → 집는 자세 [rx, ry, rz] (ZYZ deg). TCP 방향(툴 z축)을 base -y로 맞추는 최소 회전이라
    스캔 때 가로로 벌어지던 손가락은 가로로 유지되고, GRASP_ROLL만큼 TCP 축으로 더 돌립니다."""
    rotation = Rotation.from_euler("ZYZ", pose[3:], degrees=True)
    approach, target = rotation.as_matrix()[:, 2], np.array([0.0, -1.0, 0.0])
    axis = np.cross(approach, target)  # 회전축 (길이 = sin(사잇각))
    angle = np.arctan2(np.linalg.norm(axis), approach @ target)
    align = Rotation.from_rotvec(axis / np.linalg.norm(axis) * angle) if angle > 1e-6 else Rotation.identity()
    grasp = align * rotation * Rotation.from_euler("z", GRASP_ROLL, degrees=True)
    return grasp.as_euler("ZYZ", degrees=True).tolist()


def grasp_frame(pose):
    """posx → 그리퍼 좌표계 (3, 3), 열 = [가로(손가락이 벌어지는 수평축), 세로(+ = 위), TCP 방향(툴 z축)]의 base 방향.
    가로는 TCP 방향에 수직인 수평축으로 고정합니다 (TCP가 -y면 base x)."""
    approach = Rotation.from_euler("ZYZ", pose[3:], degrees=True).as_matrix()[:, 2]
    across = np.cross(approach, UP)
    across /= np.linalg.norm(across)
    return np.stack([across, np.cross(across, approach), approach], axis=1)


class PathChecker:
    """장애물 점(base, mm)과 그리퍼를 그리퍼 좌표계의 상자(가로·세로로 GRIPPER_BOX 폭, TCP 방향으로 손가락 끝에서
    손목 쪽 길이)로 보고, 자세를 고정한 직선 경유점 경로를 STEP 간격으로 따라가며 상자 안에 장애물 점이 있는지 검사합니다."""

    def __init__(self, obstacles, frame):
        self.points = np.asarray(obstacles, dtype=float).reshape(-1, 3)
        self.frame = frame  # grasp_frame()
        across, height, length = GRIPPER_BOX
        self.low = np.array([-across / 2, -height / 2, -length]) - MARGIN
        self.high = np.array([across / 2, height / 2, 0.0]) + MARGIN

    def blocked(self, *waypoints):
        """경로에서 처음 겹치는 위치의 장애물 점 정보(TCP 기준 가로·세로·TCP 방향 범위, mm)를 문자열로, 안 겹치면 None.
        세로가 음수면 TCP 아래(테이블 등), 가로가 크면 옆 물체, TCP 방향이 0 근처면 물체 자신의 가장자리일 가능성이 큽니다."""
        for start, end in zip(waypoints, waypoints[1:]):
            for position in np.linspace(start, end, int(np.linalg.norm(end - start) // STEP) + 2):
                local = (self.points - position) @ self.frame  # base → 그리퍼 좌표
                hit = ((local > self.low) & (local < self.high)).all(1)
                if hit.any():
                    low, high = local[hit].min(0), local[hit].max(0)
                    return (f"{hit.sum()} obstacle points near TCP {np.round(position).astype(int).tolist()}: "
                            f"across {low[0]:.0f}~{high[0]:.0f}, vertical {low[1]:.0f}~{high[1]:.0f}, "
                            f"along {low[2]:.0f}~{high[2]:.0f} mm")
        return None


class Picker(Node):
    def __init__(self):
        super().__init__("vision_pick")
        self.gripper = RG(GRIPPER_NAME, TOOLCHARGER_IP, TOOLCHARGER_PORT)
        self.client = self.create_client(Trigger, "/get_grasp_positions")
        while not self.client.wait_for_service(timeout_sec=3.0):
            self.get_logger().info("Waiting for /get_grasp_positions service...")
        self.scan_orientation, self.scan_frame = None, None  # 스캔 자세 (posx rx, ry, rz)와 그 grasp_frame (접근 경로용)
        self.orientation, self.frame = None, None            # 집는 자세 (TCP ∥ -y)와 그 grasp_frame (잡기·빠지기용)

    def request(self):
        """detection 결과 {clusters, labels, targets{순번: base xyz}, target_points, obstacles} (mm)."""
        future = self.client.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(self, future)
        return json.loads(future.result().message)

    def target(self, result):
        """선택된 묶음이 정확히 1개면 (0번 무게중심, None), 아니면 (None, 거절 사유)."""
        if result["clusters"] != 1:
            return None, f"{result['clusters']} clusters selected ({' '.join(result['labels']) or 'none'}), need exactly 1"
        return np.array(result["targets"]["0"]), None

    def plan(self, scan):
        """스캔 위치에서 검출해 1차 좌표와 잡기 직전 위치까지의 충돌 없는 경유점을 고릅니다.
        ((1차 좌표, 잡기 직전 위치, 경유점 목록), None) 또는 (None, 거절 사유)."""
        result = self.request()
        target, reason = self.target(result)
        if target is None:
            return None, reason
        pregrasp = target - APPROACH * self.frame[:, 2]  # 물품 앞
        checker = PathChecker(result["obstacles"], self.frame)
        hit = checker.blocked(pregrasp, target)
        if hit:
            return None, f"moving forward from the pre-grasp point to the target {target.round(1).tolist()} is blocked: {hit}"
        route = [pregrasp]  # motion 1: 스캔 위치 → waypoint1 (스캔 자세 유지)
        hit = PathChecker(result["obstacles"], self.scan_frame).blocked(scan, *route)
        if hit:
            return None, f"motion 1 to waypoint1 (pre-grasp) is blocked: {hit}"
        self.get_logger().info(f"Target {target.round(1).tolist()}, waypoint1 {pregrasp.round(1).tolist()}")
        return (target, pregrasp, route), None

    def correct(self, coarse, pregrasp):
        """잡기 직전 위치에서 다시 검출해 최종 grasp point와 그리퍼 벌림 폭을 정합니다.
        정면에서 본 segmentation 무게중심은 물체 앞면이라, TCP 방향으로 (가로 폭/2 + GRASP_DEPTH)만큼 더 들어가
        손가락이 물체 중심을 감싸게 합니다. ((grasp point, 벌림 폭 mm), None) 또는 (None, 거절 사유)."""
        result = self.request()
        target, reason = self.target(result)
        if target is None:
            return None, reason
        shift = np.linalg.norm(target - coarse)
        if shift > CORRECTION_LIMIT:
            return None, f"corrected target moved {shift:.0f} mm from the first estimate (limit {CORRECTION_LIMIT:.0f})"
        across = np.asarray(result["target_points"], dtype=float).reshape(-1, 3) @ self.frame[:, 0]
        width = float(np.subtract(*np.percentile(across, (95, 5))))  # 가로 폭 (5~95 백분위)
        opening = width + GRIP_MARGIN
        if opening > MAX_OPEN:
            return None, f"object is {width:.0f} mm wide, needs {opening:.0f} mm opening (max {MAX_OPEN:.0f})"
        grasp = target + (width / 2 + GRASP_DEPTH) * self.frame[:, 2]  # 앞면 → 물체 중심 너머
        hit = PathChecker(result["obstacles"], self.frame).blocked(pregrasp, grasp)
        if hit:
            return None, f"moving forward to the grasp point is blocked: {hit}"
        self.get_logger().info(f"Front surface {target.round(1).tolist()} (corrected by {shift:.0f} mm), "
                               f"width {width:.0f} mm → grasp point {grasp.round(1).tolist()}, opening {opening:.0f} mm")
        return (grasp, opening), None

    def move(self, position, orientation=None):
        """position (base mm)으로 movel. orientation을 주지 않으면 집는 자세(TCP ∥ -y)."""
        movel(np.asarray(position).tolist() + (orientation or self.orientation), vel=VELOCITY, acc=ACC)
        mwait()

    def scan_pose(self):
        movej(SCAN_J, vel=VELOCITY, acc=ACC)
        mwait()

    def wait_gripper(self):
        """그리퍼 동작이 끝날 때까지 기다립니다. 안전 회로가 작동했거나 GRIPPER_TIMEOUT을 넘으면 중단합니다."""
        deadline = time.monotonic() + GRIPPER_TIMEOUT
        while True:
            status = self.gripper.get_status()  # [동작 중, 잡힘, S1 눌림, S1 작동, S2 눌림, S2 작동, 안전 오류]
            if status[3] or status[5]:
                raise RuntimeError("Gripper safety circuit is activated; remove the cause and power-cycle the gripper.")
            if not status[0]:
                return
            if time.monotonic() > deadline:
                raise RuntimeError(f"Gripper did not finish within {GRIPPER_TIMEOUT:.0f} s.")
            time.sleep(0.5)

    def run(self):
        self.gripper.open_gripper()
        self.wait_gripper()
        for attempt in range(1, RETRIES + 1):
            self.scan_pose()
            pose = get_current_posx()[0]
            scan, self.scan_orientation, self.scan_frame = np.array(pose[:3]), list(pose[3:]), grasp_frame(pose)
            self.orientation = grasp_orientation(pose)
            self.frame = grasp_frame(list(scan) + self.orientation)

            plan, reason = self.plan(scan)
            if plan is None:
                self.get_logger().warn(f"Attempt {attempt}/{RETRIES} rejected at scan pose: {reason}")
                time.sleep(RETRY_DELAY)
                continue
            coarse, pregrasp, route = plan
            for waypoint in route:  # motion 1: 스캔 자세 그대로 waypoint1(잡기 직전 위치)에 도착
                self.move(waypoint, self.scan_orientation)

            final, reason = self.correct(coarse, pregrasp)  # 최종 grasp point·벌림 폭 보정
            if final is None:
                self.get_logger().warn(f"Attempt {attempt}/{RETRIES} rejected at pre-grasp: {reason}")
                self.scan_pose()
                time.sleep(RETRY_DELAY)
                continue
            grasp, opening = final
            self.move(pregrasp)  # motion 2: waypoint1 높이에서 제자리 축 정렬 (집는 자세, TCP ∥ -y)
            self.gripper.move_gripper(int(round(opening * 10)))  # RG 단위 0.1 mm
            self.wait_gripper()

            self.move(grasp)  # motion 3: 앞으로 들어가며 잡기
            angle = angle_to_minus_y(get_current_posx()[0])  # 실제 TCP가 -y와 평행한지 잡기 직전에 확인
            if angle > AXIS_TOLERANCE:
                self.get_logger().warn(f"Attempt {attempt}/{RETRIES} rejected at grasp: "
                                       f"TCP is {angle:.1f} deg off -y (limit {AXIS_TOLERANCE}).")
                self.move(pregrasp)
                self.scan_pose()
                continue
            self.gripper.close_gripper()
            self.wait_gripper()

            self.move(grasp + LIFT * UP)  # 살짝 들어 올리고
            self.move(pregrasp + LIFT * UP)  # 같은 높이로 잡기 직전 위치까지 빠짐
            movej(HOME_J, vel=VELOCITY, acc=ACC)
            mwait()
            self.gripper.open_gripper()
            self.wait_gripper()
            self.get_logger().info("Released at home.")
            return
        self.get_logger().error(f"Gave up after {RETRIES} attempts.")


def main():
    node = Picker()
    try:
        node.run()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
