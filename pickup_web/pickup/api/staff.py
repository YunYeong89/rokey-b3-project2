"""직원 화면: 재고 표, 카메라 점검, 진열·입고·수정, 픽업 대기 목록"""
import time

from fastapi import APIRouter, HTTPException

from ..config import EXPIRED_SHOW_MIN
from .schemas import RestockReq

PICKUP_FIELDS = ("order_id", "pickup_code", "status", "ready_at", "expires_at", "picked", "staff_items")


def build(svc):
    r = APIRouter()
    store, orders = svc.store, svc.orders

    @r.get("/api/inventory")
    def inventory():
        with store.lock:
            return {"rows": store.rows(), "vision_time": store.vision_time, "alerts": store.alerts,
                    "robot": store.status}

    @r.post("/api/scan")
    def scan():
        svc.robot.request_scan()
        return {"ok": True}

    @r.post("/api/restock")
    def restock(req: RestockReq):
        with store.lock:
            try:
                store.restock(req.id, req.qty, req.mode)
            except KeyError:
                raise HTTPException(404, "없는 상품")
            except ValueError as e:
                raise HTTPException(400, str(e))
        return {"ok": True}

    @r.get("/api/pickups")
    def pickups():
        """수령 대기 주문 + 최근 만료된 주문"""
        now = time.time()
        with store.lock:
            orders.sweep(now)
            return {"now": now, "expire_min": orders.expire_min, "orders": [
                {k: o[k] for k in PICKUP_FIELDS}
                | {"count": sum(o["picked"].values()) + sum(o["staff_items"].values())}
                for o in orders.waiting(now, EXPIRED_SHOW_MIN)]}

    return r
