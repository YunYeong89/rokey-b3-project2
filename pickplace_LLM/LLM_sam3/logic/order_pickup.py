"""주문 확인(QR) → 장바구니 수령 — 음성 안내 부분.

카메라/QR 인식/수령 감지는 이 파일이 하지 않는다. 그쪽 코드가 아래 함수만 호출하면 된다.
  pickup.customer_detected()     # 카메라에 손님이 잡힘
  pickup.qr_scanned("ORDER-0001")  # QR 인식 결과 문자열
  pickup.item_taken(1)           # 1번 / 2번 장바구니에서 물품을 집어감
  pickup.reset()                 # 손님이 떠남 등 — 처음 상태로

흐름
  [대기] --손님 감지--> "화면 속 QR 코드를 보여주세요"            → [QR 대기]
  [QR 대기] --QR 맞음--> "N번 장바구니 물품을 가져가 주세요"       → [수령 대기]
  [QR 대기] --QR 틀림--> "확인되지 않는 QR 코드입니다…"
  [대기/QR 대기] --가져감--> ⚠️ "QR 코드를 먼저 확인해야…"        (QR 안 찍고 가져감)
  [수령 대기] --다른 장바구니--> ⚠️ "잘못 가져가셨습니다. N번…"
  [수령 대기] --맞는 장바구니--> "감사합니다, 안녕히 가십시오."      → [대기]

ROS2 토픽으로 연결하는 노드는 order_voice_node.py
"""
import config
from voice.speaker import Speaker

IDLE, WAIT_QR, WAIT_PICKUP = "대기", "QR 대기", "수령 대기"


class OrderPickupVoice:
    def __init__(self, speaker=None, orders=None):
        self.speaker = speaker or Speaker()
        self.orders = dict(config.ORDERS if orders is None else orders)
        self.state = IDLE
        self.basket = None      # 이번 손님이 가져가야 할 장바구니 번호

    def _say(self, key, **fmt):
        self.speaker.say(key, **fmt)

    def _set(self, state, basket=None):
        self.state, self.basket = state, basket
        print(f"  [상태] {state}" + (f" (장바구니 {basket}번)" if basket else ""))

    # -------------------------------------------------- 사건
    def customer_detected(self):
        if self.state != IDLE:      # 이미 응대 중이면 같은 안내를 반복하지 않는다
            return
        self._say("show_qr")
        self._set(WAIT_QR)

    def resolve_basket(self, qr_data):
        """QR 문자열 → 장바구니 번호 (없으면 None). 주문 시스템에 맞게 바꾸면 된다."""
        return self.orders.get(str(qr_data).strip())

    def qr_scanned(self, qr_data):
        if self.state == WAIT_PICKUP:   # 이미 확인됨 → 같은 안내 반복
            self._say("pickup", basket=self.basket)
            return True
        basket = self.resolve_basket(qr_data)
        if basket not in config.PICKUP_BASKETS:
            self._say("qr_invalid")
            if self.state == IDLE:
                self._set(WAIT_QR)
            return False
        self._say("pickup", basket=basket)
        self._set(WAIT_PICKUP, basket)
        return True

    def item_taken(self, basket):
        """basket 번 장바구니에서 물품을 집어갔을 때. 맞게 가져갔으면 True"""
        if self.state != WAIT_PICKUP:
            self._say("warn_no_qr")
            if self.state == IDLE:
                self._set(WAIT_QR)
            return False
        if int(basket) != self.basket:
            self._say("warn_wrong", basket=self.basket)
            return False
        self._say("goodbye")
        self._set(IDLE)
        return True

    def reset(self):
        self._set(IDLE)
