"""vision_project detection(docker)의 /get_grasp_positions로 장면을 받아 0번 물품을 잡아 HOME_J로 가져와 그리퍼를 엽니다 (호스트 실행).
1. SCAN_J로 movej 후 검출 요청. 선택된 묶음(cluster)이 정확히 1개일 때만 진행하고, 아니면 RETRY_DELAY 뒤 재시도합니다.
   그리퍼 배향은 스캔 자세 그대로 끝까지 유지하고(축 정렬 없음), 모든 이동은 위치만 바꿉니다.
2. 가장 가까운 목표(0번)에서 TCP 방향 반대로 APPROACH만큼 물러난 곳에 잡기 직전 위치(waypoint1)를 두고,
   스캔 위치 → waypoint0(스캔 위치와 waypoint1의 중간점) → waypoint1을 movesx 스플라인 한 번으로 지나갑니다.
   두 구간과 앞으로 들어가는 구간을 그리퍼 상자로 장애물 점(base)과 검사해, 막히면 재시도합니다.
3. 보정: 잡기 직전 위치에서 다시 검출해 0번 segmentation 무게중심을 최종 grasp point로 정합니다 (1차 좌표와
   CORRECTION_LIMIT 이상 다르면 거절). 보정된 좌표까지 앞으로 들어가는 구간도 새 장애물 점으로 다시 검사합니다.
4. 그리퍼는 시작할 때 건드리지 않고, 잡기 직전에만 가로(TCP 방향에 수직인 수평축)로 물체 가로 폭 + GRIP_MARGIN만큼
   벌립니다 (MAX_OPEN 초과면 거절). 그리퍼는 GripperWorker 스레드에서 돌아 로봇 이동과 분리됩니다: 벌리기를 시작하면서
   동시에 TCP 방향으로 movel해 들어가고, grasp point에 도착하면 벌리기 완료를 기다린 뒤 잡습니다.
5. 잡으면 LIFT만큼 위로 올린 뒤 같은 높이로 잡기 직전 위치 위까지 movel로 빠지고, HOME_J로 movej해 그리퍼를 엽니다.
고정 자세(SCAN_J, HOME_J)는 movej, 집는 동작은 movel·movesx이며, 이동마다 실제로 도착했는지 확인합니다.
동작은 DRL API(DSR_ROBOT2), 그리퍼는 Modbus TCP(onrobot.RG)이며, 그리퍼가 안전 정지하거나 GRIPPER_TIMEOUT을 넘기면 중단합니다."""

import json
from threading import Thread
import time

import DR_init
import numpy as np
import rclpy
from rclpy.node import Node
from scipy.spatial.transform import Rotation
from std_srvs.srv import Trigger

from vision_project.onrobot import RG

ROBOT_ID, ROBOT_MODEL = "dsr01", "m0609"
VELOCITY, ACC = 24, 24  # movej deg/s·deg/s², movel mm/s·mm/s²
SCAN_J = [-90.0, -54.91, 85.55, 0.0, 90.0, 90.0]  # 검출 자세 (movej), 이 그리퍼 배향을 끝까지 유지
HOME_J = [0, 0, 90, 0, 90, 90]                     # 잡은 물품을 가져와 그리퍼를 여는 자세
GRIPPER_TIMEOUT = 10.0              # s, 그리퍼 동작을 기다리는 최대 시간
WIDTH_TOLERANCE = 3.0               # mm, 벌리기 완료로 볼 목표 폭과의 차이 (동작 중 비트가 안 풀려도 폭으로 판정)
APPROACH = 100.0                    # mm, 물품에서 잡기 직전 위치까지 거리 (TCP 방향 반대)
LIFT = 30.0                         # mm, 잡은 뒤 위로 올리는 높이
CORRECTION_LIMIT = 50.0             # mm, 잡기 직전 위치에서 보정한 좌표가 1차 좌표와 달라도 되는 최대 거리
GRIP_MARGIN = 20.0                  # mm, 물체 가로 폭보다 더 벌리는 여유 (양쪽 합)
GRASP_DEPTH = 25.0                  # mm, 손가락 끝(TCP)을 물체 중심(앞면 + 가로 폭/2)보다 더 넣는 깊이
MAX_OPEN = 110.0                    # mm, RG2 최대 벌림
GRIPPER_BOX = (100.0, 50.0, 150.0)  # mm, 그리퍼 상자: 가로 폭(손가락이 벌어지는 수평축), 세로 두께, TCP(손가락 끝)→손목 길이 — 실측해 조정
MARGIN = 5.0                        # mm, 상자 여유
MIN_HITS = 10                       # 상자 안 장애물 점이 이 개수 이상일 때만 막힘으로 판정 (depth 잡음 무시)
STEP = 10.0                         # mm, 경로를 따라 상자를 검사하는 간격
RETRIES, RETRY_DELAY = 10, 2.0      # 재시도 횟수, 간격(s)
ARRIVAL_POSITION, ARRIVAL_ANGLE = 5.0, 2.0  # mm, deg: movel·movesx 뒤 목표와 이만큼 넘게 다르면 도중에 멈춘 것으로 봄
ARRIVAL_JOINT = 1.0                 # deg: movej 뒤 각 관절이 목표와 이만큼 넘게 다르면 도중에 멈춘 것으로 봄
GRIPPER_NAME, TOOLCHARGER_IP, TOOLCHARGER_PORT = "rg2", "192.168.1.1", "502"
UP = np.array([0.0, 0.0, 1.0])

# DRL API(DSR_ROBOT2)는 import 전에 DR_init에 로봇 ID·모델·노드가 등록되어 있어야 합니다.
DR_init.__dsr__id, DR_init.__dsr__model = ROBOT_ID, ROBOT_MODEL
rclpy.init()
DR_init.__dsr__node = rclpy.create_node("vision_pick_dsr", namespace=ROBOT_ID)
from DSR_ROBOT2 import get_current_posj, get_current_posx, movej, movel, movesx, mwait, posx  # noqa: E402


def grasp_frame(pose):
    """posx → 그리퍼 좌표계 (3, 3), 열 = [가로(손가락이 벌어지는 수평축), 세로, TCP 방향(툴 z축)]의 base 방향.
    가로는 TCP 방향에 수직인 수평축으로 고정합니다 (TCP가 기울어 있어도 손가락은 수평으로 벌어짐)."""
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
                if hit.sum() >= MIN_HITS:
                    low, high = local[hit].min(0), local[hit].max(0)
                    return (f"{hit.sum()} obstacle points near TCP {np.round(position).astype(int).tolist()}: "
                            f"across {low[0]:.0f}~{high[0]:.0f}, vertical {low[1]:.0f}~{high[1]:.0f}, "
                            f"along {low[2]:.0f}~{high[2]:.0f} mm")
        return None


class GripperWorker:
    """RG2 명령과 완료 확인을 별도 스레드에서 실행해 로봇 이동(메인 스레드)과 분리합니다. 그리퍼 결과가 필요한 시점에만
    wait()로 기다립니다. 완료는 상태 레지스터(268)의 동작 중 비트가 풀리거나, 벌리기면 실제 폭(275)이 목표 ±WIDTH_TOLERANCE,
    잡기면 잡힘 비트로 판정하고, 상태는 출력 없이 직접 읽습니다. 안전 회로 작동이나 GRIPPER_TIMEOUT이면 wait()에서 오류를 냅니다."""

    BUSY, GRIPPED, SAFETY = 0b1, 0b10, 0b101000  # 동작 중, 잡힘, 안전 회로 1·2 작동

    def __init__(self, gripper):
        self.gripper = gripper
        self.thread, self.error = None, None

    def start(self, width=None):
        """width(mm)로 벌리기, None이면 잡기를 백그라운드로 시작합니다 (이전 명령은 먼저 끝까지 기다림)."""
        self.wait()
        self.thread = Thread(target=self._run, args=(width,), name="gripper", daemon=True)
        self.thread.start()

    def wait(self):
        if self.thread:
            self.thread.join()
            self.thread = None
        error, self.error = self.error, None
        if error:
            raise error

    def status(self):
        return self.gripper.client.read_holding_registers(address=268, count=1, slave=65).registers[0]

    def _run(self, width):
        try:
            if width is None:
                self.gripper.close_gripper()
            else:
                self.gripper.move_gripper(int(round(width * 10)))  # RG 단위 0.1 mm
            time.sleep(0.3)  # 명령이 반영돼 동작 중 비트가 켜질 시간
            deadline = time.monotonic() + GRIPPER_TIMEOUT
            while True:
                status = self.status()
                if status & self.SAFETY:
                    raise RuntimeError("Gripper safety circuit is activated; remove the cause and power-cycle the gripper.")
                if not status & self.BUSY:
                    return
                if width is None and status & self.GRIPPED:
                    return
                if width is not None and abs(self.gripper.get_width_with_offset() - width) <= WIDTH_TOLERANCE:
                    return
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Gripper did not finish within {GRIPPER_TIMEOUT:.0f} s "
                                       f"(status {status:07b}, width {self.gripper.get_width_with_offset():.0f} mm).")
                time.sleep(0.1)
        except Exception as error:
            self.error = error


class Picker(Node):
    def __init__(self):
        super().__init__("vision_pick")
        self.gripper = GripperWorker(RG(GRIPPER_NAME, TOOLCHARGER_IP, TOOLCHARGER_PORT))
        self.client = self.create_client(Trigger, "/get_grasp_positions")
        while not self.client.wait_for_service(timeout_sec=3.0):
            self.get_logger().info("Waiting for /get_grasp_positions service...")
        self.orientation, self.frame = None, None  # 스캔 자세의 그리퍼 배향 (posx rx, ry, rz)과 그 grasp_frame

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
        ((1차 좌표, 잡기 직전 위치, waypoint0), None) 또는 (None, 거절 사유)."""
        result = self.request()
        target, reason = self.target(result)
        if target is None:
            return None, reason
        pregrasp = target - APPROACH * self.frame[:, 2]  # TCP 방향 반대로 물러난 물품 앞
        checker = PathChecker(result["obstacles"], self.frame)
        hit = checker.blocked(pregrasp, target)
        if hit:
            return None, f"moving forward from the pre-grasp point to the target {target.round(1).tolist()} is blocked: {hit}"
        waypoint0 = (scan + pregrasp) / 2  # 스캔 위치 → waypoint1 중간점
        hit = checker.blocked(scan, waypoint0)
        if hit:
            return None, f"move to waypoint0 is blocked: {hit}"
        hit = checker.blocked(waypoint0, pregrasp)
        if hit:
            return None, f"move to waypoint1 (pre-grasp) is blocked: {hit}"
        self.get_logger().info(f"Target {target.round(1).tolist()}, waypoint0 {waypoint0.round(1).tolist()}, "
                               f"waypoint1 {pregrasp.round(1).tolist()}")
        return (target, pregrasp, waypoint0), None

    def correct(self, coarse, pregrasp):
        """잡기 직전 위치에서 다시 검출해 최종 grasp point와 그리퍼 벌림 폭을 정합니다.
        카메라에 보이는 segmentation 무게중심은 물체 앞면이라, TCP 방향으로 (가로 폭/2 + GRASP_DEPTH)만큼 더 들어가
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

    def move(self, position):
        """position (base mm)으로 스캔 자세 배향 그대로 movel. 거부되거나(반환 -1) 도중에 멈추면 중단합니다."""
        target = np.asarray(position).tolist() + self.orientation
        if movel(target, vel=VELOCITY, acc=ACC) != 0:
            raise RuntimeError(f"movel to {np.round(target, 1).tolist()} was rejected by the controller.")
        mwait()
        self.arrived("movel", target)

    def spline(self, *positions):
        """positions (base mm)를 스캔 자세 배향 그대로 movesx 스플라인 한 번으로 지나갑니다.
        거부되거나(반환 -1) 마지막 경유점에 닿기 전에 멈추면 중단합니다."""
        path = [posx(np.asarray(position).tolist() + self.orientation) for position in positions]
        if movesx(path, vel=VELOCITY, acc=ACC) != 0:
            raise RuntimeError(f"movesx through {[np.round(list(p), 1).tolist() for p in path]} "
                               "was rejected by the controller.")
        mwait()
        self.arrived("movesx", list(path[-1]))

    def arrived(self, name, target):
        """DRL 반환값 0은 '명령 접수'일 뿐이라 실행 중 특이점·관절 한계로 멈춰도 알 수 없으므로, 이동 뒤 실제 posx를
        목표와 비교해 위치 ARRIVAL_POSITION·자세 ARRIVAL_ANGLE 넘게 다르면 어디서 멈췄는지와 함께 중단합니다."""
        pose = list(get_current_posx()[0])
        position_error = float(np.linalg.norm(np.subtract(pose[:3], target[:3])))
        angle_error = (Rotation.from_euler("ZYZ", pose[3:], degrees=True).inv()
                       * Rotation.from_euler("ZYZ", target[3:], degrees=True)).magnitude() * 180 / np.pi
        if position_error > ARRIVAL_POSITION or angle_error > ARRIVAL_ANGLE:
            raise RuntimeError(f"{name} stopped before its target: now {np.round(pose, 1).tolist()}, "
                               f"target {np.round(target, 1).tolist()} ({position_error:.0f} mm, {angle_error:.1f} deg off). "
                               "Check the teach pendant alarm (singularity, joint limit, workspace).")

    def move_joints(self, joints, name):
        """고정 자세(SCAN_J, HOME_J)로 movej하고, 각 관절이 ARRIVAL_JOINT 넘게 다르면 중단합니다."""
        if movej(joints, vel=VELOCITY, acc=ACC) != 0:
            raise RuntimeError(f"movej to {name} {joints} was rejected by the controller.")
        mwait()
        error = np.abs(np.subtract(list(get_current_posj()), joints)).max()
        if error > ARRIVAL_JOINT:
            raise RuntimeError(f"movej to {name} stopped before its target: now "
                               f"{np.round(list(get_current_posj()), 1).tolist()}, target {joints} ({error:.1f} deg off). "
                               "Check the teach pendant alarm.")

    def scan_pose(self):
        self.move_joints(SCAN_J, "SCAN_J")

    def run(self):
        for attempt in range(1, RETRIES + 1):  # 그리퍼는 시작할 때 건드리지 않고 잡기 직전에만 벌림
            self.scan_pose()
            pose = list(get_current_posx()[0])
            scan, self.orientation, self.frame = np.array(pose[:3]), pose[3:], grasp_frame(pose)

            plan, reason = self.plan(scan)
            if plan is None:
                self.get_logger().warn(f"Attempt {attempt}/{RETRIES} rejected at scan pose: {reason}")
                time.sleep(RETRY_DELAY)
                continue
            coarse, pregrasp, waypoint0 = plan
            self.spline(waypoint0, pregrasp)  # 스캔 위치 → waypoint0 → waypoint1, 배향 유지

            final, reason = self.correct(coarse, pregrasp)  # 최종 grasp point·벌림 폭 보정
            if final is None:
                self.get_logger().warn(f"Attempt {attempt}/{RETRIES} rejected at pre-grasp: {reason}")
                self.scan_pose()
                time.sleep(RETRY_DELAY)
                continue
            grasp, opening = final
            self.gripper.start(opening)  # 그리퍼 스레드: 벌리기 시작
            self.move(grasp)  # 로봇: 동시에 TCP 방향으로 들어감 (벌리기는 1~2 s, 이동은 그보다 김)
            self.gripper.wait()  # 잡기 전에 벌리기 완료 확인
            self.gripper.start()  # 잡기
            self.gripper.wait()  # 잡은 뒤에 들어 올림

            self.move(grasp + LIFT * UP)  # 살짝 들어 올리고
            self.move(pregrasp + LIFT * UP)  # 같은 높이로 잡기 직전 위치까지 빠짐
            self.move_joints(HOME_J, "HOME_J")
            self.gripper.start(MAX_OPEN)  # 홈에서 놓기
            self.gripper.wait()
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
