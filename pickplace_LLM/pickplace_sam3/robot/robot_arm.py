"""로봇 동작 — 두산 m0609 + OnRobot RG2. (LLM robot_arm 기반 — 옛 proj2)

DRL_Tutorial(rokey_study.ipynb), Calibration_Tutorial(verify.py, onrobot.py) 실습 코드 기반.
- DoosanArm     : 실제 로봇 / 가상(에뮬레이터) 로봇
- ModbusGripper : 실제 RG2 Modbus 직결 (--gripper modbus)
- RosGripper    : /onrobot/sendCommand 서비스 (--gripper ros, m0609_rg2_bringup)
"""
import math
import time

import numpy as np

import config

POS_TOLERANCE = 3.0   # 이동 후 목표와의 허용 오차(mm)


class MotionError(RuntimeError):
    """로봇이 목표 위치로 이동하지 못함"""


def _fmt(pose):
    return "[" + ", ".join(f"{v:.1f}" for v in pose) + "]"


def _offset(pose, vector, dist):
    """pose 의 위치를 vector 방향으로 dist(mm) 옮긴 자세 (방향은 그대로)"""
    moved = list(pose)
    for i in range(3):
        moved[i] += float(vector[i]) * dist
    return moved


def _setup_dr_init(DR_init, node):
    # 주의: 클래스 안에서 DR_init.__dsr__id 처럼 쓰면 파이썬 name mangling 때문에
    # DR_init._DoosanArm__dsr__id 로 바뀌어 DSR_ROBOT2 가 노드를 못 찾는다 → 클래스 밖 함수에서 설정
    DR_init.__dsr__id = config.ROBOT_ID
    DR_init.__dsr__model = config.ROBOT_MODEL
    DR_init.__dsr__node = node


# ==================================================================== 그리퍼 (grip() → 잡았는지 bool)
class ModbusGripper:
    """실습 방식: onrobot.py 로 RG2 에 Modbus TCP 직접 연결 (실제 로봇)"""
    def __init__(self, check_grip=True):
        from robot.onrobot import RG
        self.rg = RG(config.GRIPPER_NAME, config.TOOLCHARGER_IP, config.TOOLCHARGER_PORT)
        if not self.rg.client.connected:
            raise RuntimeError(f"그리퍼({config.TOOLCHARGER_IP}:{config.TOOLCHARGER_PORT}) 연결 실패 — "
                               "bringup 으로 띄웠다면 --gripper ros 를 쓰세요")
        self.check_grip = check_grip

    def _wait(self):
        time.sleep(0.3)
        deadline = time.monotonic() + config.GRIPPER_TIMEOUT
        while self.rg.get_status()[0] and time.monotonic() < deadline:
            time.sleep(0.2)

    def grip(self, force=config.GRIPPER_FORCE):
        print(f"    [gripper] grip (modbus, 힘 {force})")
        self.rg.close_gripper(force_val=force)
        self._wait()
        return bool(self.rg.get_status()[1]) if self.check_grip else True   # 물체가 잡혔는지

    def release(self):
        print("    [gripper] release (modbus)")
        self.rg.open_gripper(force_val=config.GRIPPER_FORCE)
        self._wait()

    def open_to(self, width_mm):
        print(f"    [gripper] {width_mm:.0f}mm 로 벌림 (modbus)")
        self.rg.move_gripper(int(round(width_mm * 10)), force_val=config.GRIPPER_FORCE)
        self._wait()

    def close(self):
        self.rg.close_connection()


class RosGripper:
    """/onrobot/sendCommand 서비스 (onrobot_rg_msgs/SetCommand) 로 제어.

    m0609_rg2_bringup 을 띄우면 virtual 모드에선 gripper_virtual_node 가 RViz 에서 손가락을 움직이고,
    real 모드에선 OnRobot 드라이버가 같은 서비스를 제공한다. (가상에서는 '잡았는지' 판정은 불가 → 항상 성공)
    """
    def __init__(self, node, rclpy):
        from onrobot_rg_msgs.srv import SetCommand
        self.node, self.rclpy, self.SetCommand = node, rclpy, SetCommand
        self.client = node.create_client(SetCommand, "/onrobot/sendCommand")
        if not self.client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError("/onrobot/sendCommand 서비스가 없습니다 — "
                               "`ros2 launch m0609_rg2_bringup bringup.launch.py` 로 띄웠는지 확인하세요")

    def _send(self, command):
        req = self.SetCommand.Request()
        req.command = str(command)
        future = self.client.call_async(req)
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=config.GRIPPER_TIMEOUT + 5)
        result = future.result()
        if result is None or not result.success:
            raise MotionError(f"그리퍼 명령 {command!r} 실패: {result}")

    def grip(self, force=config.GRIPPER_FORCE):
        print(f"    [gripper] grip → 너비 {config.GRIP_WIDTH_DMM / 10:.0f}mm (ros)")
        self._send(config.GRIP_WIDTH_DMM)
        return True

    def release(self):
        print("    [gripper] release → 열기 (ros)")
        self._send("o")

    def open_to(self, width_mm):
        print(f"    [gripper] {width_mm:.0f}mm 로 벌림 (ros)")
        self._send(int(round(width_mm * 10)))

    def close(self):
        pass


# ==================================================================== 로봇팔
class DoosanArm:
    def __init__(self, gripper="modbus", check_grip=True):
        """gripper: 'modbus'(실습 방식, 실제 RG2) / 'ros'(/onrobot/sendCommand, RViz 가상·실제 그리퍼)"""
        try:
            import rclpy
            import DR_init
        except ImportError as e:
            raise RuntimeError("ROS2/두산 패키지 import 실패 — 먼저 `source ~/cobot2_ws/install/setup.bash` 하세요") from e

        self.rclpy = rclpy
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node("pickplace_robot", namespace=config.ROBOT_ID)
        _setup_dr_init(DR_init, self.node)   # DSR_ROBOT2 import 전에 노드를 지정해야 한다

        try:
            import DSR_ROBOT2 as dr
        except ImportError as e:
            raise RuntimeError(f"DSR_ROBOT2 import 실패: {e}") from e
        self.dr = dr
        self.gripper = ModbusGripper(check_grip) if gripper == "modbus" else RosGripper(self.node, rclpy)

    # ---------------------------------------------------------------- 기본 동작
    def current_posx(self):
        return [float(v) for v in self.dr.get_current_posx()[0]]

    def movej(self, joints, slow=False):
        vel, acc = (config.FRAGILE_VELOCITY, config.FRAGILE_ACC) if slow else (config.VELOCITY, config.ACC)
        ret = self.dr.movej(list(joints), vel=vel, acc=acc)
        self.dr.mwait()
        if ret != 0:
            raise MotionError(f"movej 실패 (ret={ret}) 목표 {list(joints)}")

    def movel(self, pose, slow=False):
        # DSR movel 은 실패해도(도달 불가여도) 예외 없이 0/-1 을 돌려주므로 실제 위치를 직접 확인한다
        vel, acc = (config.FRAGILE_VELOCITY, config.FRAGILE_ACC) if slow else (config.VELOCITY, config.ACC)
        ret = self.dr.movel(list(pose), vel=vel, acc=acc)
        self.dr.mwait()
        if ret != 0:
            raise MotionError(f"movel 실패 (ret={ret}) 목표 {_fmt(pose)}")
        current = self.current_posx()
        error = max(abs(c - p) for c, p in zip(current[:3], pose[:3]))
        if POS_TOLERANCE < error < 10.0:                    # 팔을 많이 뻗은 자리에서 조금 못 미침 → 한 번 더
            self.dr.movel(list(pose), vel=vel, acc=acc)
            self.dr.mwait()
            current = self.current_posx()
            error = max(abs(c - p) for c, p in zip(current[:3], pose[:3]))
        if error > POS_TOLERANCE:
            raise MotionError(f"목표 {_fmt(pose)} 에 도달하지 못함 (현재 {_fmt(current)}, 오차 {error:.1f}mm)")

    def ik_nearest(self, pose):
        """작업 좌표 목표의 관절 해 8개(팔 모양) 중 지금 관절과 가장 가까운 것 → 관절 목록 또는 None"""
        now = [float(v) for v in self.dr.get_current_posj()]
        best = None
        for sol in range(8):
            try:
                q = self.dr.ikin(list(pose), sol)
            except Exception:
                continue
            q = q[0] if isinstance(q, tuple) else q
            q = [float(v) for v in q] if q is not None else []
            if len(q) != 6 or any(abs(v) > 360 for v in q):
                continue
            cost = sum(abs(a - b) for a, b in zip(q, now))
            # 특이점 근처 해는 뒤로 미룸: 팔꿈치를 거의 다 편 자세(|J3| 작음)·손목 J5≈0 — 거기서 직선 이동하면 멈춤
            #   (시뮬: 봉투 위 (118, 602) 에서 가장 가까운 해가 J3=3° 라 수직으로 내려가다 43mm 못 미쳐 멈춤)
            if abs(q[2]) < config.IK_MIN_ELBOW or abs(q[4]) < config.IK_MIN_WRIST:
                cost += 10000.0
            # 보통 팔 모양을 먼저: 받침(J1)이 목표 쪽을 보고, 팔꿈치가 위로 굽은 해 (뒤로 넘겨 뻗거나 팔꿈치가 아래인 해는 나중에)
            azim = math.degrees(math.atan2(pose[1], pose[0]))
            if abs((q[0] - azim + 180.0) % 360.0 - 180.0) > 90.0:
                cost += 1000.0
            if q[2] < 0:
                cost += 500.0
            if best is None or cost < best[0]:
                best = (cost, q)
        return None if best is None else best[1]

    def movejx(self, pose, slow=False):
        """작업 좌표 목표로 '관절 보간' 이동. 먼 거리를 옮길 때 movel 의 특이점을 피함.
        목표의 팔 모양 8가지 중 지금과 가장 가까운 관절 해로 movej (지금 팔 모양을 고집하면 못 가는 자리가 있음 — 시뮬 확인)"""
        q = self.ik_nearest(pose)
        if q is None:
            raise MotionError(f"목표 {_fmt(pose)} 의 관절 해가 없음 (작업 범위 밖)")
        self.movej(q, slow=slow)
        current = self.current_posx()
        error = max(abs(c - p) for c, p in zip(current[:3], pose[:3]))
        if POS_TOLERANCE < error < 50.0:              # 역기구학 해가 조금 어긋남 (그리퍼가 정확히 아래일 때) → 짧게 직선 보정
            self.movel(pose, slow=slow)
            return
        current = self.current_posx()
        error = max(abs(c - p) for c, p in zip(current[:3], pose[:3]))
        if error > POS_TOLERANCE:
            raise MotionError(f"목표 {_fmt(pose)} 에 도달하지 못함 (movejx, 현재 {_fmt(current)}, 오차 {error:.1f}mm)")

    # ---------------------------------------------------------------- 공개 동작
    def home(self):
        print(f"    [arm] movej 대기 자세 {config.JREADY}")
        self.movej(config.JREADY)
        self.gripper.release()

    def pick(self, pose, approach_dir, fragile=False, approach_dist=None, lift=0.0, joint_approach=False, open_width=None):
        """그리퍼 방향(approach_dir) 반대쪽 approach_dist 에서 다가가 pose 에서 잡고 다시 물러난다. 못 잡으면 False.
        lift: 쥔 뒤 물러나기 전에 위로 드는 높이 (정면으로 쥔 상품을 선반 위에서 끌지 않게)
        joint_approach: 다가가기 시작하는 자리까지 관절 보간(movejx) — 촬영 자세와 집는 자세의 방향이 많이 다를 때
                        (직선 이동은 도중에 특이점을 지나 멈춤, 시뮬 확인)
        open_width: 다 벌리지 않고 이 폭(mm)까지만 벌려서 다가감 (옆 물건이 가까울 때)"""
        print(f"    [arm] 집기 {_fmt(pose)}" + (" (fragile: 천천히·약하게)" if fragile else ""))
        back = _offset(pose, -np.asarray(approach_dir), approach_dist or config.APPROACH_DIST)
        if open_width:
            self.gripper.open_to(open_width)
        else:
            self.gripper.release()
        (self.movejx if joint_approach else self.movel)(back)
        self.movel(pose, slow=fragile)
        grabbed = self.gripper.grip(config.FRAGILE_GRIPPER_FORCE if fragile else config.GRIPPER_FORCE)
        if not grabbed:
            self.gripper.release()
            self.movel(back, slow=fragile)
            return False
        if lift:
            self.movel(_offset(pose, (0, 0, 1), lift), slow=fragile)
            back = _offset(back, (0, 0, 1), lift)
        self.movel(back, slow=fragile)
        return grabbed

    def place(self, pose, slow=False, above_z=None, joint_approach=False, via_z=None):
        """봉투 위로 가서 내려놓고(그리퍼 열기) 다시 올라온다.
        above_z: 내려오기 시작하는 높이 (기본: pose 위 BASKET_APPROACH) — 봉투 입구에 걸리지 않게 높일 때
        joint_approach: 봉투 위까지는 관절 보간(movejx)으로 (매대에서 수평으로 들고 먼 거리를 옮길 때)
        via_z: 관절 보간으로는 이 높이의 '자리 바로 위' 까지만 가고, 거기서 수직으로 내려옴
               (관절 보간은 중간 경로가 낮게 휘어 봉투 벽을 뚫고 지나갈 수 있음 — 시뮬 확인)"""
        print(f"    [arm] 봉투에 넣기 {_fmt(pose)}")
        above = _offset(pose, (0, 0, 1), config.BASKET_APPROACH)
        if above_z is not None:
            above[2] = max(float(above_z), float(pose[2]))
        if joint_approach:
            via = list(above)
            via[2] = max(above[2], float(via_z or 0.0))
            while via[2] - 20.0 >= above[2]:                        # 팔을 거의 다 펴야 닿는 높이면 낮춤 (특이점)
                q = self.ik_nearest(via)
                if q is not None and abs(q[2]) >= config.IK_MIN_ELBOW:
                    break
                via[2] -= 20.0
            now = self.current_posx()
            if now[2] < via[2]:                                     # 먼저 그 높이까지 수직으로 올림
                self.movel(now[:2] + [via[2]] + now[3:], slow=slow)
            self.movejx(via, slow=slow)
            if via[2] > above[2]:
                self.movel(above, slow=slow)
        else:
            self.movel(above, slow=slow)
        self.movel(pose, slow=slow)
        self.gripper.release()
        self.movel(above)

    def shutdown(self):
        self.gripper.close()
        self.node.destroy_node()
        if self.rclpy.ok():
            self.rclpy.shutdown()
