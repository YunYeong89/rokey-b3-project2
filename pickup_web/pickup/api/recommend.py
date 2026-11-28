"""추천 (Switching Hybrid) + 추천 반응 기록 + 고객 구매 이력 조회"""
import time

from fastapi import APIRouter, Depends, HTTPException

from ..catalog import NAMES
from ..history import clean_customer_id
from ..orders import order_uid
from .auth import current_user, require_admin
from .schemas import RecEventReq

REC_TITLE = {"personal": "평소 자주 구매하시는 상품", "popular": "인기 상품 추천", "cart": "함께 구매하면 좋은 상품"}


def _ids(csv):
    return [b for b in csv.split(",") if b]


def _cid(user):
    """로그인한 사용자의 구매 이력 id (= 로그인 ID). 비회원이면 None"""
    return clean_customer_id(user["username"]) if user else None


def build(svc):
    r = APIRouter()
    rec = svc.rec

    @r.get("/api/recommend")
    def recommend(basket: str = "", user=Depends(current_user)):
        """장바구니 '함께 구매하면 좋은 상품'. basket 은 담은 순서 (마지막 = 방금 담은 상품 -> 그 상품 기준 2개 + 전체 기준 1개)"""
        ids = _ids(basket)
        cid = _cid(user)
        items, rec_id = svc.serve_rec("cart", cid, lambda: rec.for_basket(ids))
        return {"items": items, "rec_id": rec_id, "title": REC_TITLE["cart"]}

    @r.get("/api/recommend/home")
    def recommend_home(basket: str = "", user=Depends(current_user)):
        """첫 화면: 구매 이력 있는 고객 R(재구매) / 신규·비회원 인기 상품 (Switching)"""
        cid = _cid(user)
        stats = (svc.hist("stats", cid) if cid else None) or {"n_orders": 0}
        exclude = _ids(basket)
        items, rec_id = svc.serve_rec("home", cid, lambda: rec.home(history=stats, exclude=exclude))
        mode = "personal" if any(it["algo"] == "R" for it in items) else "popular"
        return {"items": items, "rec_id": rec_id, "mode": mode, "title": REC_TITLE[mode],
                "customer_id": cid, "n_orders": stats["n_orders"]}

    @r.post("/api/rec_event")
    def rec_event(req: RecEventReq, user=Depends(current_user)):
        """추천 클릭 기록 (손님 화면에서 추천 카드를 눌러 담을 때)"""
        if req.event != "click" or req.slot not in ("home", "cart"):
            raise HTTPException(400, "event 는 click, slot 은 home / cart")
        row = {k: getattr(req, k) for k in ("event", "slot", "product_id", "algo", "rec_id", "rank")}
        svc.hist("log_events", [{**row, "customer_id": _cid(user)}])
        return {"ok": True}

    @r.get("/api/rec_stats", dependencies=[Depends(require_admin)])
    def rec_stats():
        """추천 영역·알고리즘별 노출 / 클릭 / 구매 수 (추천 방식 비교용)"""
        return {"rows": svc.hist("event_summary") or []}

    @r.get("/api/customer")
    def customer(user=Depends(current_user)):
        """로그인한 사용자 본인의 구매 이력 (비회원이면 401)"""
        cid = _cid(user)
        if not cid:
            raise HTTPException(401, "로그인이 필요해요")
        stats = svc.hist("stats", cid) or {"n_orders": 0, "counts": {}}
        n = stats["n_orders"]
        items = sorted(({"id": pid, "name": NAMES.get(pid, pid), "orders": c, "r": round(c / n, 4)}
                        for pid, c in stats.get("counts", {}).items()), key=lambda x: -x["r"])
        hist_orders = svc.hist("orders", cid) or []
        # 수령 대기 중인 주문은 QR 을 다시 열 수 있게 pickup 정보를 붙임 (본인 주문일 때만 client_secret 포함)
        with svc.store.lock:
            svc.orders.sweep(time.time())
            for h in hist_orders:
                o = svc.orders.get(h["order_id"])
                if (o and o["status"] == "ready_for_pickup" and o.get("customer_id") == cid
                        and order_uid(o) == h.get("order_uid")):
                    h["pickup"] = {"order_id": o["order_id"], "client_secret": o["client_secret"],
                                   "pickup_code": o["pickup_code"], "expires_at": o["expires_at"]}
        for h in hist_orders:
            h.pop("order_uid", None)
        return {"customer_id": cid, "n_orders": n, "items": items, "orders": hist_orders}

    return r
