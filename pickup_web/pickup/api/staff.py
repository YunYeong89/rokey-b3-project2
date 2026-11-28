"""직원 화면: 재고 표, 카메라 점검, 진열·입고·수정, 픽업 대기 목록, 회원 목록 (모두 관리자 로그인 필요)"""
import time

from fastapi import APIRouter, Depends, HTTPException

from ..catalog import NAMES
from ..config import EXPIRED_SHOW_MIN
from .auth import require_admin
from .schemas import RestockReq

PICKUP_FIELDS = ("order_id", "pickup_code", "status", "ready_at", "expires_at", "picked", "staff_items")


def build(svc):
    r = APIRouter(dependencies=[Depends(require_admin)])
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

    @r.get("/api/members")
    def members():
        """회원 탭: 계정별 구매 완료 주문 수, 마지막 구매, 자주 산 상품 Top 3"""
        rows = []
        for u in svc.accounts.list_users():
            st = svc.hist("stats", u["username"]) or {"n_orders": 0, "counts": {}, "last": {}}
            top = sorted(st["counts"].items(), key=lambda kv: (-kv[1], -st["last"].get(kv[0], 0)))[:3]
            rows.append({**u, "n_orders": st["n_orders"], "last_purchase": max(st["last"].values(), default=None),
                         "top": [{"id": pid, "name": NAMES.get(pid, pid), "orders": c} for pid, c in top]})
        rows.sort(key=lambda x: (x["role"] != "admin", x["username"]))
        return {"members": rows}

    return r
