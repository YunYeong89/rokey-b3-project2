#!/usr/bin/env python3
"""
로봇 쪽 주문 처리 노드 (뼈대)

web_server.py 가 보내는 /order 를 받아서
  재고 확인 -> (부족하면 손님 선택 대기) -> 집기/담기 -> 완료
순서로 처리하고, 단계마다 /order_status 를 발행합니다.

기존 robot_control.py 의 함수(이동, 3D 위치, pick, 그리퍼)를
아래 TODO 자리에 연결하면 됩니다.

products.json 의 robot: true 상품(실물)만 로봇이 처리하고,
robot: false 상품(가상)은 직원이 준비하도록 완료 메시지의 staff_items 로 넘깁니다.
"""
import json
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from recommender import PRODUCTS_FILE, Recommender

# 상품 정보는 web_server.py 와 같은 data/products.json 에서 읽음
_CATALOG = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
# 상품 id -> 속성 (shelf, fragile, deformable, registered). pick_one 에서 그립 방식 결정에 사용
PRODUCT_INFO = {p["id"]: p for p in _CATALOG["products"]}
ROBOT_ITEMS = {pid for pid, p in PRODUCT_INFO.items() if p.get("robot")}  # 로봇이 집는 실물 상품
# 대체 상품 후보 (our_recs.json). 최종 대체 상품은 web_server 가 실시간 재고로 다시 고름
RECOMMENDER = Recommender()
# 상품 id -> 매대 ("upper" / "lower"). 실물 상품만, products.json 의 "shelf" 값으로 만듦
SHELF_VIEW_POSE = {pid: PRODUCT_INFO[pid]["shelf"] for pid in ROBOT_ITEMS}
# 매대 이름 -> 촬영 위치 (posj 또는 posx). 직접 티칭해서 채우세요.
SHELF_POSE = {"upper": None, "lower": None}
BASKET_POSE = None  # TODO: 바구니 위 place 위치 posx


class OrderNode(Node):
    def __init__(self):
        super().__init__("robot_order_node")
        self.pub_status = self.create_publisher(String, "/order_status", 10)
        self.pub_vision = self.create_publisher(String, "/vision_count", 10)
        self.create_subscription(String, "/order", self.on_order, 10)
        self.create_subscription(String, "/order_choice", self.on_choice, 10)
        self.create_subscription(String, "/scan_request", self.on_scan, 10)
        self.choice_evt = threading.Event()
        self.choice = None
        self.busy = False
        self.get_logger().info("주문 대기 중...")

    # ------------------------------------------------------------ 발행 도우미
    def status(self, **kw):
        self.pub_status.publish(String(data=json.dumps(kw, ensure_ascii=False)))

    # ------------------------------------------------------------ TODO: 기존 코드 연결
    def move_to_view(self, shelf):
        """매대 촬영 위치로 이동 (movej). shelf 는 "upper"/"lower" -> SHELF_POSE[shelf]"""
        pass

    def count_items(self, pid):
        """YOLO로 해당 상품 개수 세기 -> int
        예: 탐지 결과에서 class 이름이 pid 인 박스 개수 (conf >= 0.5)"""
        return 0

    def pick_one(self, pid):
        """해당 상품 하나 집기 -> 성공 여부
        예: /get_3d_position 으로 좌표 -> transform_to_base -> 접근 -> 그립
        PRODUCT_INFO[pid] 의 fragile(약한 힘·천천히), deformable(모양이 변함), registered(처음 보는 상품) 참고"""
        return True

    def place_in_basket(self):
        """바구니에 놓기"""
        pass

    # ------------------------------------------------------------ 콜백
    def on_choice(self, msg):
        self.choice = json.loads(msg.data)
        self.choice_evt.set()

    def on_scan(self, msg):
        if self.busy:
            return
        threading.Thread(target=self.scan_all, daemon=True).start()

    def on_order(self, msg):
        if self.busy:
            self.get_logger().warn("이미 주문 처리 중")
            return
        data = json.loads(msg.data)
        # 콜백 안에서 오래 걸리는 동작을 하면 다른 콜백(손님 선택)을 못 받으므로 스레드로 처리
        threading.Thread(target=self.run_order, args=(data["order_id"], data["items"]), daemon=True).start()

    # ------------------------------------------------------------ 동작
    def scan_all(self):
        counts = {}
        for pid, shelf in SHELF_VIEW_POSE.items():
            self.move_to_view(shelf)
            counts[pid] = self.count_items(pid)
        self.pub_vision.publish(String(data=json.dumps(counts)))

    def run_order(self, oid, items):
        self.busy = True
        try:
            # 가상 상품(robot: false)은 직원 준비 -> 완료 메시지의 staff_items
            staff = {k: v for k, v in items.items() if v > 0 and k not in ROBOT_ITEMS}
            items = {k: v for k, v in items.items() if v > 0 and k in ROBOT_ITEMS}
            total = sum(items.values())
            self.status(order_id=oid, state="checking", message="매대에서 재고를 확인하고 있어요",
                        done=0, total=total)
            seen = {}
            for pid in items:
                self.move_to_view(SHELF_VIEW_POSE.get(pid))
                seen[pid] = self.count_items(pid)
            self.pub_vision.publish(String(data=json.dumps(seen)))

            for pid, want in list(items.items()):
                have = seen[pid]
                if have >= want:
                    continue
                self.choice_evt.clear()
                self.status(order_id=oid, state="need_choice", message="재고가 부족해요",
                            shortage={"id": pid, "want": want, "have": have},
                            substitute=next(iter(RECOMMENDER.substitute(pid)), None), done=0, total=total)
                if not self.choice_evt.wait(timeout=120) or self.choice["choice"] == "cancel":
                    self.status(order_id=oid, state="cancelled", message="주문이 취소되었어요")
                    return
                items[pid] = have
                sub = self.choice.get("substitute")
                if self.choice["choice"] == "substitute" and sub:
                    dst = items if sub in ROBOT_ITEMS else staff  # 대체 상품이 가상이면 직원 준비
                    dst[sub] = dst.get(sub, 0) + (want - have)
            items = {k: v for k, v in items.items() if v > 0}
            total = sum(items.values())

            picked, done = {}, 0
            for pid, n in items.items():
                self.move_to_view(SHELF_VIEW_POSE.get(pid))
                for _ in range(n):
                    self.status(order_id=oid, state="picking", current=pid, done=done, total=total,
                                picked=picked, message="상품을 집어서 바구니에 담고 있어요")
                    if not self.pick_one(pid):
                        self.status(order_id=oid, state="error", message=f"{pid} 집기 실패 - 직원 확인 필요",
                                    picked=picked, done=done, total=total)
                        return
                    self.place_in_basket()
                    picked[pid] = picked.get(pid, 0) + 1
                    done += 1
            self.status(order_id=oid, state="done", done=done, total=total, picked=picked,
                        staff_items=staff, message="담기 완료! 픽업대에서 가져가세요")
        finally:
            self.busy = False


def main():
    rclpy.init()
    node = OrderNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
