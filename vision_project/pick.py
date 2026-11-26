"""detection의 /get_grasp_positions로 가까운 순 base 좌표를 받아 0번 물품을 잡아 home으로 가져와 놓습니다.
TCP가 base -y를 향한 HOME_J 자세를 끝까지 고정한 채 물품 앞(+y) 접근점으로 간 뒤, 재검출한 0번이 처음 좌표와
TOLERANCE 이내면 처음 좌표까지 들어가 잡습니다. 동작은 DRL API(DSR_ROBOT2), 그리퍼는 Modbus TCP(onrobot.RG)입니다."""

import json
import time

import DR_init
import numpy as np
import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger

from vision_project.onrobot import RG

ROBOT_ID, ROBOT_MODEL = "dsr01", "m0609"
VELOCITY, ACC = 60, 60
HOME_J = [0, 0, 90, 0, 90, 90]  # 관찰·복귀 자세 (TCP가 base -y를 향함)
APPROACH = 100.0                # mm, 물품 좌표에서 +y로 떨어진 접근점 거리
TOLERANCE = 30.0                # mm, 접근점에서 재검출한 0번이 처음 좌표와 달라도 되는 거리
GRIPPER_NAME, TOOLCHARGER_IP, TOOLCHARGER_PORT = "rg2", "192.168.1.1", "502"

# DRL API(DSR_ROBOT2)는 import 전에 DR_init에 로봇 ID·모델·노드가 등록되어 있어야 합니다.
DR_init.__dsr__id, DR_init.__dsr__model = ROBOT_ID, ROBOT_MODEL
rclpy.init()
DR_init.__dsr__node = rclpy.create_node("vision_pick_dsr", namespace=ROBOT_ID)
from DSR_ROBOT2 import get_current_posx, movej, movel, mwait  # noqa: E402


class Picker(Node):
    def __init__(self):
        super().__init__("vision_pick")
        self.gripper = RG(GRIPPER_NAME, TOOLCHARGER_IP, TOOLCHARGER_PORT)
        self.client = self.create_client(Trigger, "/get_grasp_positions")
        while not self.client.wait_for_service(timeout_sec=3.0):
            self.get_logger().info("Waiting for /get_grasp_positions service...")

    def get_positions(self):
        """detection에서 {순번: [x, y, z]} (base, mm, 0 = 가장 가까움)를 받습니다."""
        future = self.client.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(self, future)
        positions = json.loads(future.result().message)
        self.get_logger().info(f"Grasp positions: {positions}")
        return positions

    def wait_gripper(self):
        while self.gripper.get_status()[0]:  # 그리퍼 동작 중
            time.sleep(0.5)

    def run(self):
        movej(HOME_J, vel=VELOCITY, acc=ACC)
        self.gripper.open_gripper()
        self.wait_gripper()

        positions = self.get_positions()
        if "0" not in positions:
            self.get_logger().warn("No target detected at home.")
            return
        target = np.array(positions["0"])
        orientation = list(get_current_posx()[0][3:])  # HOME_J의 TCP 자세(-y)를 끝까지 고정
        approach = target + (0, APPROACH, 0)

        movel(approach.tolist() + orientation, vel=VELOCITY, acc=ACC)
        mwait()
        check = self.get_positions().get("0")
        if check is None or np.linalg.norm(np.array(check) - target) > TOLERANCE:
            self.get_logger().warn(f"Re-detection failed: {check} vs {target.tolist()}, returning home.")
            movej(HOME_J, vel=VELOCITY, acc=ACC)
            return

        movel(target.tolist() + orientation, vel=VELOCITY, acc=ACC)
        mwait()
        self.gripper.close_gripper()
        self.wait_gripper()
        movel(approach.tolist() + orientation, vel=VELOCITY, acc=ACC)  # 잡은 채 +y로 빼고 복귀
        movej(HOME_J, vel=VELOCITY, acc=ACC)
        mwait()
        self.gripper.open_gripper()
        self.wait_gripper()
        self.get_logger().info("Released at home.")


def main():
    node = Picker()
    try:
        node.run()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
