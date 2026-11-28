"""추천 성과 분석 (기존 rec_events 테이블 + order_items 의 추천 표시)

이벤트 (pickup/history.py rec_events)
  impression  서버가 추천을 내려줄 때 (rec_id 로 한 묶음)
  click       손님이 추천 카드를 눌러 장바구니에 담음  -> 지금 화면은 '누르면 바로 담기'라 클릭 = 장바구니 추가
  purchase    추천에서 담은 상품이 실제로 로봇 담기까지 완료 (order_id 포함)
연결 키: rec_id(추천 묶음), customer_id(로그인 ID), product_id, order_id. 별도 session ID 는 없음 (rec_id 로 묶음).

추천 매출 = 추천에서 담아 실제로 판매된 상품의 금액 (order_items.rec_algo 가 있는 판매 수량 x 판매 단가)
  A/B 테스트가 없으므로 이 금액이 '추천 때문에 늘어난 매출'이라는 뜻은 아님 (추천이 없어도 샀을 수 있음)
"""
from collections import defaultdict

from .common import collecting
from .sales import first_order_ts, item_filter, load_orders, pname, sold_orders

ALGO_NAME = {"R": "R · 재구매", "S": "S · 같은 코너", "C": "C · 다른 코너", "popular": "인기 상품", "-": "기타"}
SLOT_NAME = {"home": "첫 화면", "cart": "장바구니"}


def _rate(a, b):
    return round(a / b, 4) if b else None


def recommendations(db, f):
    firsts = [t for t in (first_order_ts(db, f), db.query("SELECT MIN(ts) AS t FROM rec_events")[0]["t"]) if t]
    s_ts, e_ts, s, e = f.bounds(min(firsts) if firsts else None)
    keep = item_filter(f)
    ev = db.query("SELECT slot, COALESCE(algo,'-') AS algo, product_id, event, COUNT(*) AS n FROM rec_events"
                  " WHERE ts >= ? AND ts < ? GROUP BY slot, algo, product_id, event", (s_ts, e_ts))
    ev = [r for r in ev if keep(r["product_id"])]

    def group(key):
        g = defaultdict(lambda: {"impression": 0, "click": 0, "purchase": 0, "revenue": 0, "units": 0})
        for r in ev:
            g[key(r)][r["event"]] += r["n"]
        return g

    # 판매된 추천 상품 (매출)
    sold = sold_orders(load_orders(db, f, s_ts, e_ts), keep)
    rec_rev, total_rev = defaultdict(int), 0
    by_algo, by_slot, by_prod = group(lambda r: r["algo"]), group(lambda r: r["slot"]), group(lambda r: r["product_id"])
    for o in sold:
        for it in o["items"]:
            amt = it["sold_qty"] * it["unit_price"]
            total_rev += amt
            if it["rec_algo"] or it["rec_slot"]:
                a = it["rec_algo"] or "-"
                by_algo[a]["revenue"] += amt
                by_algo[a]["units"] += it["sold_qty"]
                if it["rec_slot"]:
                    by_slot[it["rec_slot"]]["revenue"] += amt
                by_prod[it["product_id"]]["revenue"] += amt
                rec_rev[a] += amt

    def rows(g, names):
        return [{"key": k, "name": names.get(k, k), **v, "ctr": _rate(v["click"], v["impression"]),
                 "cart_rate": _rate(v["click"], v["impression"]),
                 "conversion": _rate(v["purchase"], v["impression"]),
                 "click_to_purchase": _rate(v["purchase"], v["click"])} for k, v in g.items()]

    tot = {k: sum(r["n"] for r in ev if r["event"] == k) for k in ("impression", "click", "purchase")}
    out = {"period": {"label": f.label(s, e), "start": s, "end": e},
           "empty": not tot["impression"],
           "kpi": {**tot, "ctr": _rate(tot["click"], tot["impression"]),
                   "cart_rate": _rate(tot["click"], tot["impression"]),
                   "conversion": _rate(tot["purchase"], tot["impression"]),
                   "click_to_purchase": _rate(tot["purchase"], tot["click"]),
                   "rec_revenue": sum(rec_rev.values()), "total_revenue": total_rev,
                   "rec_revenue_share": _rate(sum(rec_rev.values()), total_rev)},
           "by_algo": sorted(rows(by_algo, ALGO_NAME), key=lambda r: ["R", "S", "C", "popular", "-"].index(r["key"])
                             if r["key"] in ("R", "S", "C", "popular", "-") else 9),
           "by_slot": rows(by_slot, SLOT_NAME),
           "by_product": sorted(rows(by_prod, {pid: pname(pid) for pid in by_prod}),
                                key=lambda r: (-r["impression"], -r["purchase"]))[:20],
           "notes": ["클릭 = 장바구니 추가 (추천 카드를 누르면 바로 담김)",
                     "추천 매출은 추천에서 담아 판매된 금액이며, A/B 테스트 없이 추천 때문에 늘어난 매출로 볼 수 없어요",
                     "rec_events 에는 mock / 실제 구분이 없어 데이터 구분 필터가 적용되지 않아요 (매출은 적용)"]}
    if out["empty"]:
        out["status"] = collecting("이 기간에 추천 노출 기록이 없어요")
    return out
