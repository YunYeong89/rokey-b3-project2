"""손님 주문: 상품 목록, 주문, 로봇 진행 상황, 재고 부족 시 선택"""
from fastapi import APIRouter, Depends, HTTPException

from ..catalog import split_items
from ..history import clean_customer_id
from ..service import ACTIVE_STATES
from .auth import current_user
from .schemas import ChoiceReq, OrderReq


def build(svc):
    r = APIRouter()
    store, orders = svc.store, svc.orders

    @r.get("/api/products")
    def products():
        with store.lock:
            return {"products": store.products, "mode": svc.mode}

    @r.post("/api/order")
    def order(req: OrderReq, user=Depends(current_user)):
        items = {k: v for k, v in req.items.items() if v > 0}
        if not items:
            raise HTTPException(400, "담은 상품이 없어요")
        customer_id = clean_customer_id(user["username"]) if user else None  # 비회원이면 None
        attrib = {pid: {k: a.get(k) for k in ("slot", "algo", "rec_id", "rank")}
                  for pid, a in (req.rec_attrib or {}).items() if pid in items and isinstance(a, dict)}
        with store.lock:
            if store.status.get("state") in ACTIVE_STATES:
                raise HTTPException(409, "로봇이 다른 주문을 처리 중이에요. 잠시 후 다시 시도해 주세요")
            for pid, n in items.items():
                p = store.get(pid)
                if not p:
                    raise HTTPException(400, f"없는 상품: {pid}")
                if not p.get("robot") and n > p["stock"]:  # 가상 상품은 카메라 확인이 없으므로 장부로 확인
                    raise HTTPException(400, f"{p['name']} 재고가 부족해요 (남은 {p['stock']}개)")
            o = orders.new(items, customer_id=customer_id, rec_attrib=attrib)
            oid = o["order_id"]
            svc.track("order_created", o)   # 분석: 주문 시점 가격·수량 기록
            store.status = {"order_id": oid, "state": "checking", "message": "주문을 로봇에게 보냈어요",
                            "done": 0, "total": sum(split_items(items)[0].values())}
        svc.robot.send_order(oid, items)
        # client_secret: 이 주문의 진행 상황·QR 을 볼 수 있는 열쇠 (손님 브라우저에만 저장)
        return {"order_id": oid, "client_secret": o["client_secret"]}

    @r.get("/api/status")
    def status():
        with store.lock:
            return store.status

    @r.post("/api/choice")
    def choice(req: ChoiceReq):
        with store.lock:
            oid = store.status.get("order_id")
        svc.robot.send_choice({"order_id": oid, "choice": req.choice, "substitute": req.substitute})
        return {"ok": True}

    return r
