"""추천 (Switching Hybrid) + 추천 반응 기록 + 고객 구매 이력 조회"""
from fastapi import APIRouter, HTTPException

from ..catalog import NAMES
from ..history import BAD_CUSTOMER_ID, clean_customer_id
from .schemas import RecEventReq

REC_TITLE = {"personal": "평소 자주 구매하시는 상품", "popular": "인기 상품 추천", "cart": "함께 구매하면 좋은 상품"}


def _ids(csv):
    return [b for b in csv.split(",") if b]


def build(svc):
    r = APIRouter()
    rec = svc.rec

    @r.get("/api/recommend")
    def recommend(basket: str = "", customer_id: str = ""):
        """장바구니 '함께 구매하면 좋은 상품' (S -> C -> 인기). 장바구니가 비면 빈 목록"""
        ids = _ids(basket)
        cid = clean_customer_id(customer_id)
        items, rec_id = svc.serve_rec("cart", cid, lambda: rec.for_basket(ids))
        return {"items": items, "rec_id": rec_id, "title": REC_TITLE["cart"]}

    @r.get("/api/recommend/home")
    def recommend_home(customer_id: str = "", basket: str = ""):
        """첫 화면: 구매 이력 있는 고객 R(재구매) / 신규·비회원 인기 상품 (Switching)"""
        cid = clean_customer_id(customer_id)
        stats = (svc.hist("stats", cid) if cid else None) or {"n_orders": 0}
        exclude = _ids(basket)
        items, rec_id = svc.serve_rec("home", cid, lambda: rec.home(history=stats, exclude=exclude))
        mode = "personal" if any(it["algo"] == "R" for it in items) else "popular"
        return {"items": items, "rec_id": rec_id, "mode": mode, "title": REC_TITLE[mode],
                "customer_id": cid, "n_orders": stats["n_orders"]}

    @r.post("/api/rec_event")
    def rec_event(req: RecEventReq):
        """추천 클릭 기록 (손님 화면에서 추천 카드를 눌러 담을 때)"""
        if req.event != "click" or req.slot not in ("home", "cart"):
            raise HTTPException(400, "event 는 click, slot 은 home / cart")
        row = {k: getattr(req, k) for k in ("event", "slot", "product_id", "algo", "rec_id", "rank")}
        svc.hist("log_events", [{**row, "customer_id": clean_customer_id(req.customer_id)}])
        return {"ok": True}

    @r.get("/api/rec_stats")
    def rec_stats():
        """추천 영역·알고리즘별 노출 / 클릭 / 구매 수 (추천 방식 비교용)"""
        return {"rows": svc.hist("event_summary") or []}

    @r.get("/api/customer")
    def customer(customer_id: str = ""):
        """고객 구매 이력 조회 (시연용: 고객번호만 알면 조회 가능)"""
        cid = clean_customer_id(customer_id)
        if not cid:
            raise HTTPException(400, BAD_CUSTOMER_ID)
        stats = svc.hist("stats", cid) or {"n_orders": 0, "counts": {}}
        n = stats["n_orders"]
        items = sorted(({"id": pid, "name": NAMES.get(pid, pid), "orders": c, "r": round(c / n, 4)}
                        for pid, c in stats.get("counts", {}).items()), key=lambda x: -x["r"])
        return {"customer_id": cid, "n_orders": n, "items": items, "orders": svc.hist("orders", cid) or []}

    return r
