"""vision_project pick (호스트 실행): SCAN_J에서 검출한 0번 위치로 가서 잡고 들어 올립니다.
동작은 DRL API(DSR_ROBOT2), 그리퍼는 Modbus TCP(onrobot.RG).
1. HOME_J → SCAN_J (movej) 후 /get_grasp_positions로 0번(가장 가까운) 위치 target(화면 빨간 점의 depth 점)을 받습니다
   (선택된 묶음이 1개일 때만).
2. pre_grasp = target에서 base +y(앞쪽)로 APPROACH. 스캔 자세(J5 > 0)에서 TCP ∥ -y 자세(J5 < 0)로 가는 작업 공간 이동은
   J5 = 0(손목 특이점)을 지나 멈추므로, J1·J4·J6를 스캔 값으로 고정한 평면 팔에서 pre_grasp의 높이·거리에 TCP를 수평으로
   두는 J2·J3·J5를 구해 movej로 간 뒤, x만 movel로 맞춥니다.
3. 그리퍼를 OPEN_WIDTH로 벌리고, -y로 movel해 target보다 GRASP_DEPTH 더 들어간 grasp에서 잡은 뒤 LIFT만큼 들어 올립니다.
4. 같은 높이로 pre_grasp 위치까지 +y로 빼낸 뒤 HOME_J로 movej합니다 (그리퍼는 잡은 그대로)."""

import json
import time

import DR_init
import numpy as np
import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger

from vision_project.calibration import TCP_NAME, TOOL_NAME  # T_gripper2camera 보정 때의 TCP·공구 (detection과 같은 기준)
from vision_project.onrobot import RG

ROBOT_ID, ROBOT_MODEL = "dsr01", "m0609"
VELOCITY, ACC = 24, 24                             # movej deg/s·deg/s², movel mm/s·mm/s²
SCAN_J = [-90.0, -54.91, 85.55, 0.0, 90.0, 90.0]  # 검출 자세
HOME_J = [0, 0, 90, 0, 90, 90]                     # 홈 자세
APPROACH = 100.0                                   # mm, target에서 pre_grasp까지 (base +y)
GRASP_DEPTH = 30.0                                 # mm, target(보이는 앞면)보다 더 집어넣는 깊이 (base -y)
LIFT = 10.0                                        # mm, 잡은 뒤 들어 올리는 높이
OPEN_WIDTH = 100.0                                 # mm, 잡기 전에 벌리는 폭 (RG2 최대 110)
GRIPPER_TIMEOUT = 5.0                              # s, 그리퍼 동작을 기다리는 최대 시간
SHOULDER_Z, UPPER, FOREARM = 134.5, 411.0, 368.0  # mm, M0609 base→J2 높이, J2→J3(상완), J3→J5(전완) (URDF)
GRIPPER_NAME, TOOLCHARGER_IP, TOOLCHARGER_PORT = "rg2", "192.168.1.1", "502"

# DRL API(DSR_ROBOT2)는 import 전에 DR_init에 로봇 ID·모델·노드가 등록되어 있어야 합니다.
DR_init.__dsr__id, DR_init.__dsr__model = ROBOT_ID, ROBOT_MODEL
rclpy.init()
DR_init.__dsr__node = rclpy.create_node("vision_pick_dsr", namespace=ROBOT_ID)
from DSR_ROBOT2 import (DR_VAR_VEL, get_current_posj, get_current_posx, get_tcp, get_tool, movej, movel,  # noqa: E402
                        mwait, set_singularity_handling, set_tcp, set_tool)


class PlanarArm:
    """J1·J4·J6를 스캔 값으로 고정하면 팔은 J2·J3·J5만 움직이는 수직 평면 팔입니다 (J1 = -90이면 평면 = base y·z).
    평면 좌표 (r = 앞(-y) 거리, z) mm, 각도는 수직에서 앞으로: 상완 J2, 전완 J2 + J3, 툴 J2 + J3 + J5 (90 = 수평)."""

    def __init__(self, joints, pose):
        """스캔 관절·posx로 손목(J5 축)→TCP 오프셋을 (툴 방향, 그 위쪽 수직) 성분으로 잽니다 (컨트롤러 TCP 설정과 무관)."""
        self.joints = np.array(joints, dtype=float)
        along, up = self.axes(self.joints[1] + self.joints[2] + self.joints[4])
        offset = np.array([-pose[1], pose[2]]) - self.wrist(self.joints)
        self.offset = np.array([offset @ along, offset @ up])

    @staticmethod
    def axes(pitch):
        """툴 각도 → 평면 단위 벡터 (툴 방향, 그 위쪽 수직)."""
        angle = np.radians(pitch)
        return np.array([np.sin(angle), np.cos(angle)]), np.array([-np.cos(angle), np.sin(angle)])

    @staticmethod
    def wrist(joints):
        """J5 축 위치 (r, z)."""
        upper, forearm = np.radians([joints[1], joints[1] + joints[2]])
        return np.array([UPPER * np.sin(upper) + FOREARM * np.sin(forearm),
                         SHOULDER_Z + UPPER * np.cos(upper) + FOREARM * np.cos(forearm)])

    def level(self, position):
        """TCP를 수평(-y)으로 position의 (y, z)에 두는 관절 (x는 스캔 값 그대로, 팔꿈치는 스캔과 같은 J3 > 0 쪽).
        닿지 않으면 NaN이 들어갑니다."""
        r, z = np.array([-position[1], position[2]]) - [self.offset[0], self.offset[1] + SHOULDER_Z]
        j3 = np.arccos((r * r + z * z - UPPER ** 2 - FOREARM ** 2) / (2 * UPPER * FOREARM))
        j2 = np.arctan2(r, z) - np.arctan2(FOREARM * np.sin(j3), UPPER + FOREARM * np.cos(j3))
        joints = self.joints.copy()
        joints[1], joints[2] = np.degrees([j2, j3])
        joints[4] = 90.0 - joints[1] - joints[2]
        return joints


class Picker(Node):
    def __init__(self):
        super().__init__("vision_pick")
        self.gripper = RG(GRIPPER_NAME, TOOLCHARGER_IP, TOOLCHARGER_PORT)
        self.client = self.create_client(Trigger, "/get_grasp_positions")
        while not self.client.wait_for_service(timeout_sec=3.0):
            self.get_logger().info("Waiting for /get_grasp_positions service...")

    def detect(self):
        """0번(가장 가까운) 위치 (base mm). 선택된 묶음이 1개가 아니면 None."""
        future = self.client.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(self, future)
        result = json.loads(future.result().message)
        if result["clusters"] != 1:
            self.get_logger().warn(f"{result['clusters']} clusters selected "
                                   f"({' '.join(result['labels']) or 'none'}), need exactly 1")
            return None
        return np.array(result["targets"]["0"])

    def move_joints(self, joints, name):
        self.get_logger().info(f"movej to {name} {np.round(joints, 1).tolist()}")
        movej(list(joints), vel=VELOCITY, acc=ACC)
        mwait()

    def move(self, position, name):
        """현재 자세(배향) 그대로 position (base mm)으로 movel."""
        target = np.asarray(position).tolist() + list(get_current_posx()[0])[3:]
        self.get_logger().info(f"movel to {name} {np.round(target, 1).tolist()}")
        movel(target, vel=VELOCITY, acc=ACC)
        mwait()

    def grip(self, width=None):
        """width (mm)로 벌리기, None이면 잡기. 동작 중 비트(268 bit0)가 풀리거나 GRIPPER_TIMEOUT이 지날 때까지 기다립니다."""
        if width is None:
            self.gripper.close_gripper()
        else:
            self.gripper.move_gripper(int(round(width * 10)))  # RG 단위 0.1 mm
        time.sleep(0.5)  # 명령이 반영돼 동작 중 비트가 켜질 시간
        deadline = time.monotonic() + GRIPPER_TIMEOUT
        while self.gripper.client.read_holding_registers(address=268, count=1, slave=65).registers[0] & 1:
            if time.monotonic() > deadline:
                self.get_logger().warn(f"Gripper still busy after {GRIPPER_TIMEOUT:.0f} s; continuing.")
                break
            time.sleep(0.1)

    def home_pose(self):
        self.move_joints(HOME_J, "HOME_J")

    def scan_pose(self):
        self.move_joints(SCAN_J, "SCAN_J")

    def run(self):
        set_tool(TOOL_NAME)  # 공구 무게 (충돌 감지)
        set_tcp(TCP_NAME)      # posx 기준점 = 보정 때 TCP
        self.get_logger().info(f"Tool '{get_tool()}', TCP '{get_tcp()}'")
        set_singularity_handling(DR_VAR_VEL)  # 작업 공간 이동이 특이점 근처에서 멈추지 않고 감속
        self.home_pose()
        self.scan_pose()
        target = self.detect()
        if target is None:
            return
        pre_grasp = target + [0.0, APPROACH, 0.0]  # target 앞쪽 (base +y)
        grasp = target - [0.0, GRASP_DEPTH, 0.0]   # target보다 GRASP_DEPTH 안쪽 (base -y)
        joints = PlanarArm(list(get_current_posj()), list(get_current_posx()[0])).level(pre_grasp)
        if np.isnan(joints).any():
            self.get_logger().error(f"pre_grasp {pre_grasp.round(1).tolist()} is out of reach.")
            return
        self.get_logger().info(f"target {target.round(1).tolist()}, pre_grasp {pre_grasp.round(1).tolist()}, "
                               f"grasp {grasp.round(1).tolist()}")

        self.move_joints(joints, "pre_grasp posture")  # J2·J3↑, J5↓로 TCP ∥ -y
        self.move(pre_grasp, "pre_grasp")              # x 맞춤
        self.grip(OPEN_WIDTH)
        self.move(grasp, "grasp")                      # -y로 APPROACH + GRASP_DEPTH 전진
        self.grip()
        self.move(grasp + [0.0, 0.0, LIFT], "lift")
        self.move(pre_grasp + [0.0, 0.0, LIFT], "retreat")  # 같은 높이로 +y로 빼냄
        self.home_pose()
        self.get_logger().info("Picked and moved to HOME_J.")


def main():
    node = Picker()
    try:
        node.run()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
