"""QR 픽업: 손님 화면(내 주문, QR 이미지) + 키오스크(QR / 번호로 수령 처리)"""
import io
import secrets
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..config import FAIL_MAX, FAIL_WINDOW_SEC, QR_PREFIX
from ..orders import Orders, check_code, normalize_code
from .schemas import ManualReq, VerifyReq

REASON_MSG = {"already_picked": "이미 수령한 주문이에요", "expired": "만료된 주문이에요",
              "not_ready": "아직 준비 중인 주문이에요", "unknown": "알 수 없는 QR이에요",
              "too_many": "실패가 너무 많아요. 잠시 후 다시 시도해 주세요"}


def qr_png(data):
    try:
        import qrcode
    except ImportError:
        raise HTTPException(503, 'qrcode 패키지가 없어요: pip install "qrcode[pil]"')
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(data)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()


def build(svc):
    r = APIRouter()
    store, orders = svc.store, svc.orders
    fails = defaultdict(deque)  # IP -> 실패 시각들 (verify / manual 무차별 대입 막기)

    def my_order(order_id, secret):
        """client_secret 이 맞을 때만 주문 반환 (store.lock 안에서)"""
        o = orders.get(order_id)
        if o is None:
            raise HTTPException(404, "주문을 찾을 수 없어요")
        if not secrets.compare_digest(secret.encode(), o["client_secret"].encode()):
            raise HTTPException(403, "이 주문을 볼 권한이 없어요")
        return o

    # ------------------------------------------------------------ 손님 화면
    @r.get("/api/pickup/order/{order_id}")
    def pickup_order(order_id: int, secret: str = ""):
        """내 주문 상태 (+ 로봇 작업 중이면 로봇 진행 상황)"""
        now = time.time()
        with store.lock:
            orders.sweep(now)
            o = my_order(order_id, secret)
            out = Orders.view(o, now)
            if o["status"] == "in_progress" and store.status.get("order_id") == order_id:
                out["robot"] = store.status
            return out

    @r.get("/api/pickup/qr/{order_id}.png")
    def pickup_qr(order_id: int, secret: str = ""):
        now = time.time()
        with store.lock:
            orders.sweep(now)
            o = my_order(order_id, secret)
            if o["status"] != "ready_for_pickup":
                raise HTTPException(409, "픽업 준비가 된 주문이 아니에요")
            data = QR_PREFIX + o["pickup_token"]
        return Response(qr_png(data), media_type="image/png", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ 키오스크
    def claim(request, find):
        """verify / manual 공통: 찾기 -> 수령 처리 -> /pickup 발행"""
        ip = request.client.host if request.client else "?"
        now = time.time()
        with store.lock:
            q = fails[ip]
            while q and now - q[0] > FAIL_WINDOW_SEC:
                q.popleft()
            if len(q) >= FAIL_MAX:
                wait = int(FAIL_WINDOW_SEC - (now - q[0])) + 1
                return JSONResponse({"ok": False, "reason": "too_many", "message": REASON_MSG["too_many"],
                                     "retry_after": wait}, status_code=429)
            orders.sweep(now)
            o = find()
            reason = orders.claim(o, now)
            if reason:
                if reason == "unknown":  # 맞히려고 시도한 것만 셈 (키오스크가 수령한 QR 을 또 읽어도 막히지 않게)
                    q.append(now)
                return JSONResponse({"ok": False, "reason": reason, "message": REASON_MSG[reason]}, status_code=400)
            store.alert("info", f"픽업 {o['pickup_code']} 수령 완료")
            out = Orders.view(o, now)
        svc.robot.send_pickup(o["order_id"], o["pickup_code"])
        return {"ok": True, "order": out}

    @r.post("/api/pickup/verify")
    def pickup_verify(req: VerifyReq, request: Request):
        token = req.token.strip()
        if token.startswith(QR_PREFIX):  # QR 내용을 통째로 보내도 됨
            token = token[len(QR_PREFIX):]
        return claim(request, lambda: orders.by_token.get(token) if token else None)

    @r.post("/api/pickup/manual")
    def pickup_manual(req: ManualReq, request: Request):
        def find():
            o = orders.find_code(normalize_code(req.pickup_code))
            if o and o.get("pickup_token") and secrets.compare_digest(
                    check_code(o["pickup_token"]).encode(), req.check.strip().encode()):
                return o
            return None
        return claim(request, find)

    return r
