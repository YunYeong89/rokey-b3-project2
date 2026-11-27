"""서버 상태를 한데 묶는 곳: 재고(Store) + 주문(Orders) + 구매 이력(CustomerHistory) + 추천(Recommender) + 로봇

API(pickup/api)와 로봇 연결(pickup/robot.py)은 이 PickupService 하나만 받아서 사용합니다.
"""
import secrets

from .config import HISTORY_FILE, ORDERS_FILE, PICKUP_EXPIRE_MIN, RUNTIME
from .history import CustomerHistory
from .inventory import Store
from .orders import Orders, order_uid
from .recommender import Recommender

ACTIVE_STATES = ("checking", "picking", "need_choice")   # 로봇이 주문을 처리 중인 상태


class PickupService:
    def __init__(self, expire_min=PICKUP_EXPIRE_MIN):
        RUNTIME.mkdir(parents=True, exist_ok=True)
        self.store = Store()
        # 고객 구매 이력 (SQLite). DB 문제가 생겨도 주문·추천은 계속 동작하도록 hist() 로만 호출
        try:
            self.history = CustomerHistory(HISTORY_FILE)
        except Exception as e:
            print(f"[web] 구매 이력 DB 열기 실패 ({e}) -> 개인화 추천 없이 동작")
            self.history = None
        self.orders = Orders(ORDERS_FILE, expire_min, on_expired=self._on_expired, on_picked_up=self._on_picked_up)
        # 추천: 재고는 store 의 실시간 값 사용 (store.lock 을 잡은 상태에서 호출)
        self.rec = Recommender(stock_fn=self.store.stock)
        self.robot = None   # robot.connect_robot() 이 채움 (RobotLink / MockRobot)

    @property
    def mode(self):
        return getattr(self.robot, "mode", "robot")

    # ------------------------------------------------------------ 구매 이력
    def hist(self, method, *args, **kw):
        """CustomerHistory 호출. DB 가 없거나 오류가 나면 None"""
        if self.history is None:
            return None
        try:
            return getattr(self.history, method)(*args, **kw)
        except Exception as e:
            print(f"[web] 구매 이력 {method} 실패: {e}")
            return None

    def _on_expired(self, o):
        self.store.alert("warn", f"픽업 {o['pickup_code']} 만료 - 바구니 정리 필요")
        self.hist("set_status", order_uid(o), "expired")  # 안 가져간 주문은 재구매(R) 계산에서 뺌

    def _on_picked_up(self, o):
        self.hist("set_status", order_uid(o), "picked_up")

    def record_purchase(self, o):
        """로봇 담기 완료 -> 고객 구매 이력 저장 + 추천에서 담은 상품이면 '구매' 기록 (store.lock 안에서)"""
        bought = {**o.get("staff_items", {}), **o.get("picked", {})}  # 실제로 준비된 상품 (대체 상품 포함)
        if o.get("customer_id"):
            self.hist("record_order", o["customer_id"], o["order_id"], order_uid(o), bought, o.get("ready_at"))
        attrib = o.get("rec_attrib") or {}
        self.hist("log_events", [{**a, "customer_id": o.get("customer_id"), "product_id": pid, "event": "purchase",
                                  "order_id": o["order_id"]} for pid, a in attrib.items() if pid in bought and a.get("slot")])

    # ------------------------------------------------------------ 추천
    def serve_rec(self, slot, customer_id, make):
        """추천 만들기 + 노출 기록. 추천에서 오류가 나도 빈 목록으로 (웹은 계속 동작)"""
        try:
            with self.store.lock:
                items = make()
        except Exception as e:
            print(f"[web] 추천 실패 ({slot}): {e}")
            items = []
        rec_id = secrets.token_hex(4)  # 이 추천 묶음 id (클릭·구매 기록과 연결)
        self.hist("log_events", [{"customer_id": customer_id, "rec_id": rec_id, "slot": slot, "algo": it["algo"],
                                  "product_id": it["id"], "rank": i + 1, "event": "impression"}
                                 for i, it in enumerate(items)])
        return items, rec_id

    # ------------------------------------------------------------ 로봇 -> 웹
    def handle_status(self, st):
        """로봇의 /order_status (RobotLink, MockRobot 이 호출)"""
        store, orders = self.store, self.orders
        with store.lock:
            prev = store.status
            store.status = st
            state = st.get("state")
            if state == "need_choice" and st.get("shortage"):
                # 대체 상품은 추천기(our_recs.json substitute + 현재 재고)로 결정
                subs = self.rec.substitute(st["shortage"]["id"])
                st["substitute"] = subs[0] if subs else None
            if state == "done" and prev.get("state") != "done":
                store.apply_picked({**st.get("picked", {}), **st.get("staff_items", {})})
            oid = st.get("order_id")
            if state == "done" and oid is not None:
                first = (orders.get(oid) or {}).get("status", "in_progress") == "in_progress"
                o = orders.mark_ready(oid, st.get("picked", {}), st.get("staff_items", {}))
                st["pickup_code"] = o.get("pickup_code")
                if first:  # 같은 done 이 또 와도 이력은 한 번만 (DB 에서도 order_uid 로 한 번 더 막음)
                    self.record_purchase(o)
            if state in ("cancelled", "error") and oid is not None:
                orders.finish(oid, state, st.get("message", ""))
            if state == "error":
                store.alert("error", f"로봇 오류: {st.get('message', '')}")

    def handle_vision(self, counts):
        """로봇의 /vision_count"""
        with self.store.lock:
            self.store.on_vision(counts)
