"""주문·픽업 기록 (data/runtime/orders.json)

status: in_progress(로봇 작업 중) -> ready_for_pickup -> picked_up / expired
        (로봇이 취소·오류를 보내면 cancelled / error)
QR 내용: QR_PREFIX + pickup_token  (order_id 는 넣지 않음)
"""
import hashlib
import json
import re
import secrets
import time

from .config import PICKUP_EXPIRE_MIN, PICKUP_LETTER


def check_code(token):
    """QR 이 안 될 때 쓰는 확인용 4자리 (pickup_token 에서 만듦)"""
    return f"{int(hashlib.sha256(token.encode()).hexdigest(), 16) % 10000:04d}"


def normalize_code(text):
    """'a17', 'A-017', ' A 17 ' -> 'A-017'"""
    m = re.fullmatch(r"\s*([A-Za-z])\s*-?\s*(\d{1,3})\s*", text or "")
    return f"{m.group(1).upper()}-{int(m.group(2)):03d}" if m else (text or "").strip().upper()


def order_uid(o):
    """구매 이력에서 주문 하나를 구분하는 값 (예전 기록에는 uid 가 없어서 만들어 씀)"""
    return o.get("uid") or f"{o['order_id']}-{int(o['created_at'])}"


class Orders:
    """항상 store.lock 을 잡은 상태에서 사용.
    on_expired(o), on_picked_up(o): 만료·수령 때 부르는 콜백 (직원 알림, 구매 이력 상태 반영)"""

    def __init__(self, path, expire_min=PICKUP_EXPIRE_MIN, on_expired=None, on_picked_up=None):
        self.path = path
        self.expire_min = expire_min
        self.on_expired = on_expired or (lambda o: None)
        self.on_picked_up = on_picked_up or (lambda o: None)
        self.next_id = 1
        self.next_code = 1
        self.orders = {}  # order_id(int) -> dict
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.next_id = saved.get("next_id", 1)
                self.next_code = saved.get("next_code", 1)
                self.orders = {int(k): v for k, v in saved.get("orders", {}).items()}
            except Exception as e:
                print(f"[web] {path.name} 읽기 실패 ({e}) -> 빈 기록으로 시작")
        self.next_id = max([self.next_id, *[oid + 1 for oid in self.orders]])
        for o in self.orders.values():  # 서버가 꺼질 때 담는 중이던 주문은 이어서 처리할 수 없음
            if o["status"] == "in_progress":
                o.update(status="error", message="서버가 다시 켜져서 주문이 중단되었어요")
        self.by_token = {o["pickup_token"]: o for o in self.orders.values() if o.get("pickup_token")}
        self.save()

    def save(self):
        data = {"next_id": self.next_id, "next_code": self.next_code,
                "orders": {str(k): v for k, v in self.orders.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # 쓰는 도중 꺼져도 파일이 깨지지 않게

    def get(self, oid):
        return self.orders.get(oid)

    def new(self, items, oid=None, customer_id=None, rec_attrib=None):
        if oid is None:
            oid = self.next_id
        self.next_id = max(self.next_id, oid + 1)
        self.orders[oid] = {
            "order_id": oid, "uid": secrets.token_hex(6), "client_secret": secrets.token_urlsafe(16),
            "customer_id": customer_id, "rec_attrib": rec_attrib or {},  # 추천에서 담은 상품 {pid: {slot, algo, rec_id, rank}}
            "pickup_code": None, "pickup_token": None, "items": items, "picked": {}, "staff_items": {},
            "status": "in_progress", "message": "", "created_at": time.time(),
            "ready_at": None, "picked_up_at": None, "expires_at": None}
        self.save()
        return self.orders[oid]

    def _new_code(self):
        in_use = {o["pickup_code"] for o in self.orders.values() if o["status"] == "ready_for_pickup"}
        for _ in range(999):
            code = f"{PICKUP_LETTER}-{self.next_code:03d}"
            self.next_code = self.next_code % 999 + 1
            if code not in in_use:
                return code
        raise RuntimeError("남은 픽업 번호가 없어요")

    def mark_ready(self, oid, picked, staff_items):
        """로봇 done -> 픽업 번호·token 발급, ready_for_pickup"""
        o = self.orders.get(oid)
        if o is None:  # 기록에 없는 주문(서버 재시작 전에 로봇이 받은 주문 등)도 픽업은 되게
            o = self.new({**staff_items, **picked}, oid)
        if o["status"] != "in_progress":
            return o
        now = time.time()
        o["pickup_token"] = secrets.token_urlsafe(16)
        o.update(pickup_code=self._new_code(), picked=picked, staff_items=staff_items,
                 status="ready_for_pickup", ready_at=now, expires_at=now + self.expire_min * 60)
        self.by_token[o["pickup_token"]] = o
        self.save()
        return o

    def finish(self, oid, status, message=""):
        o = self.orders.get(oid)
        if o and o["status"] == "in_progress":
            o.update(status=status, message=message)
            self.save()

    def sweep(self, now):
        """만료 시간이 지난 수령 대기 주문 -> expired"""
        changed = False
        for o in self.orders.values():
            if o["status"] == "ready_for_pickup" and now >= o["expires_at"]:
                o["status"] = "expired"
                self.on_expired(o)
                changed = True
        if changed:
            self.save()

    def find_code(self, code):
        """픽업 번호로 찾기 (번호는 돌려 쓰므로 수령 대기 중인 것 우선, 없으면 가장 최근 것)"""
        cands = [o for o in self.orders.values() if o.get("pickup_code") == code]
        cands.sort(key=lambda o: (o["status"] == "ready_for_pickup", o["ready_at"] or 0))
        return cands[-1] if cands else None

    def claim(self, o, now):
        """수령 처리. 성공하면 None, 실패하면 reason 코드"""
        if o is None or o["status"] in ("cancelled", "error"):
            return "unknown"
        if o["status"] == "picked_up":
            return "already_picked"
        if o["status"] == "expired" or (o["expires_at"] and now >= o["expires_at"]):
            return "expired"
        if o["status"] != "ready_for_pickup":
            return "not_ready"
        o.update(status="picked_up", picked_up_at=now)
        self.save()
        self.on_picked_up(o)
        return None

    def waiting(self, now, expired_show_min):
        """직원 화면용: 수령 대기 + 최근 expired_show_min 분 안에 만료된 주문"""
        rows = [o for o in self.orders.values()
                if o["status"] == "ready_for_pickup"
                or (o["status"] == "expired" and now - o["expires_at"] < expired_show_min * 60)]
        rows.sort(key=lambda o: (o["status"] != "ready_for_pickup", o["ready_at"]))
        return rows

    @staticmethod
    def view(o, now):
        """손님 / 키오스크에 보여 줄 내용 (pickup_token, client_secret 등은 빼고)"""
        out = {k: v for k, v in o.items() if k not in ("pickup_token", "client_secret", "uid", "rec_attrib")}
        out["check"] = check_code(o["pickup_token"]) if o.get("pickup_token") else None
        out["now"] = now
        return out
