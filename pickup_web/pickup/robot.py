"""웹 <-> 로봇 연결: RobotLink(ROS2 토픽) / MockRobot(로봇 없이 흉내)

둘 다 같은 메서드를 가짐: send_order, send_choice, request_scan, send_pickup
로봇이 보내는 상태는 svc.handle_status / svc.handle_vision 으로 넘김
"""
import json
import random
import threading
import time

from .catalog import IS_ROBOT, split_items


class RobotLink:
    """ROS2 토픽으로 로봇과 통신"""
    mode = "robot"

    def __init__(self, svc):
        import rclpy
        from rclpy.node import Node
        from std_msgs.msg import String
        self.svc = svc
        self.String = String
        rclpy.init()
        self.node = Node("pickup_web_bridge")
        self.pub_order = self.node.create_publisher(String, "/order", 10)
        self.pub_choice = self.node.create_publisher(String, "/order_choice", 10)
        self.pub_scan = self.node.create_publisher(String, "/scan_request", 10)
        self.pub_pickup = self.node.create_publisher(String, "/pickup", 10)
        self.node.create_subscription(String, "/order_status", lambda m: svc.handle_status(json.loads(m.data)), 10)
        self.node.create_subscription(String, "/vision_count", lambda m: svc.handle_vision(json.loads(m.data)), 10)
        threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True).start()
        print("[web] ROS2 로봇 모드")

    def _pub(self, pub, data):
        pub.publish(self.String(data=json.dumps(data, ensure_ascii=False)))

    def send_order(self, oid, items): self._pub(self.pub_order, {"order_id": oid, "items": items})
    def send_choice(self, data):      self._pub(self.pub_choice, data)
    def request_scan(self):           self._pub(self.pub_scan, {"reason": "manual"})
    def send_pickup(self, oid, code): self._pub(self.pub_pickup, {"order_id": oid, "pickup_code": code})


class MockRobot:
    """로봇 없이 동작을 흉내 내는 시뮬레이터 (발표 리허설, 웹 개발용)"""
    mode = "mock"

    def __init__(self, svc):
        self.svc = svc
        self.choice_evt = threading.Event()
        self.choice = None
        print("[web] 데모 모드 (로봇 없음)")

    def send_order(self, oid, items):
        threading.Thread(target=self._run, args=(oid, dict(items)), daemon=True).start()

    def send_choice(self, data):
        self.choice = data
        self.choice_evt.set()

    def request_scan(self):
        store = self.svc.store

        def scan():
            time.sleep(1.5)
            with store.lock:
                counts = {p["id"]: p["stock"] for p in store.products if p.get("robot")}  # 카메라는 실물 매대만
            if random.random() < 0.3:  # 가끔 불일치 상황을 만들어 시연
                pid = random.choice(list(counts))
                counts[pid] = max(0, counts[pid] - 1)
            self.svc.handle_vision(counts)
        threading.Thread(target=scan, daemon=True).start()

    def send_pickup(self, oid, code):
        print("[mock] /pickup " + json.dumps({"order_id": oid, "pickup_code": code}, ensure_ascii=False))

    def _st(self, **kw):
        self.svc.handle_status(kw)

    def _run(self, oid, items):
        store = self.svc.store
        # robot: true 상품만 로봇이 담고, 가상 상품은 직원 준비 (staff_items)
        items, staff = split_items(items)
        total = sum(items.values())
        self._st(order_id=oid, state="checking", message="매대에서 재고를 확인하고 있어요", done=0, total=total)
        time.sleep(1.5)
        # 재고 확인 (데모: 마지막 점검에서 카메라로 센 값, 없으면 장부 재고를 '보이는 개수'로 사용)
        with store.lock:
            seen = {pid: store.vision.get(pid, store.get(pid)["stock"]) for pid in items}
            store.on_vision(seen)
        for pid, want in list(items.items()):
            have = seen[pid]
            if have < want:
                self.choice_evt.clear()
                self._st(order_id=oid, state="need_choice", done=0, total=total,
                         message="재고가 부족해요", shortage={"id": pid, "want": want, "have": have},
                         substitute=None)  # handle_status 에서 추천기로 채움
                self.choice_evt.wait(timeout=120)
                c = (self.choice or {}).get("choice", "cancel")
                if c == "cancel":
                    self._st(order_id=oid, state="cancelled", message="주문이 취소되었어요")
                    return
                items[pid] = have
                if c == "substitute" and self.choice.get("substitute"):
                    sub = self.choice["substitute"]
                    dst = items if IS_ROBOT.get(sub) else staff  # 대체 상품이 가상이면 직원 준비
                    dst[sub] = dst.get(sub, 0) + (want - have)
                items = {k: v for k, v in items.items() if v > 0}
                total = sum(items.values())
        picked, done = {}, 0
        for pid, n in items.items():
            for _ in range(n):
                self._st(order_id=oid, state="picking", current=pid, done=done, total=total,
                         message="상품을 집어서 바구니에 담고 있어요", picked=picked)
                time.sleep(2.0)
                picked[pid] = picked.get(pid, 0) + 1
                done += 1
        self._st(order_id=oid, state="done", done=done, total=total, picked=picked, staff_items=staff,
                 message="담기 완료! 픽업대에서 가져가세요")


def connect_robot(svc, mock=False):
    """--mock 이면 MockRobot, 아니면 ROS2 연결 (rclpy 가 없거나 실패하면 MockRobot 으로)"""
    if not mock:
        try:
            return RobotLink(svc)
        except Exception as e:  # rclpy가 없거나 초기화 실패
            print(f"[web] ROS2 연결 실패 ({e}) -> 데모 모드로 전환")
    return MockRobot(svc)
