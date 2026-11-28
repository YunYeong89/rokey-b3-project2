"""고객 구매 행동 분석

고객 = 로그인 ID (customer_id). 비회원 주문은 고객을 이어서 알아볼 수 없으므로
고객별 지표(신규/재구매, 구매 빈도, 구매 주기, 상품별 재구매율)에서는 빼고, 장바구니·연관성 분석에만 넣음.
개인정보는 저장하지 않고 ID 별 집계만 반환 (원본 주문 목록은 내려보내지 않음).

추천 연구의 세 가지 패턴과 연결
  R (Repeat Purchase)          상품별 재구매율 = 그 상품을 2번 이상 주문한 고객 / 그 상품을 산 고객
  S (Same-aisle Association)   같은 코너(products.json aisle) 상품끼리의 동시 구매
  C (Cross-aisle Association)  다른 코너 상품끼리의 동시 구매
연관성: support = n(A,B)/N, confidence(A->B) = n(A,B)/n(A), lift = confidence / (n(B)/N)
"""
from collections import Counter, defaultdict
from itertools import combinations
from statistics import mean

from .common import collecting
from .sales import PMAP, first_order_ts, item_filter, load_orders, pname, sold_orders

MIN_CUSTOMERS = 2         # 고객 지표를 보여 주는 최소 회원 수
MIN_ASSOC_ORDERS = 10     # 연관성 분석 최소 주문 수 (적으면 lift 가 크게 흔들림)
MIN_PAIR_COUNT = 2        # 조합으로 보여 줄 최소 동시 구매 수
HEATMAP_TOP = 8


def pattern(a, b):
    aa, ab = PMAP.get(a, {}).get("aisle"), PMAP.get(b, {}).get("aisle")
    return "S" if aa and aa == ab else "C"


def customers(db, f):
    s_ts, e_ts, s, e = f.bounds(first_order_ts(db, f))
    keep = item_filter(f)
    period = sold_orders(load_orders(db, f, s_ts, e_ts), keep)
    before = sold_orders(load_orders(db, f, None, s_ts), keep)   # 기간 전 구매 (신규/기존 고객 구분용)
    out = {"period": {"label": f.label(s, e), "start": s, "end": e}, "empty": not period}

    # ---- 장바구니 (비회원 포함)
    n = len(period)
    out["basket"] = {"orders": n, "avg_items": round(mean(o["units"] for o in period), 2) if n else None,
                     "avg_value": round(mean(o["revenue"] for o in period)) if n else None,
                     "member_orders": sum(1 for o in period if o["customer_id"]),
                     "guest_orders": sum(1 for o in period if not o["customer_id"]),
                     "size_hist": _hist([o["units"] for o in period], [1, 2, 3, 4, 5], "5개+")}

    # ---- 고객별 (회원만)
    by_c = defaultdict(list)
    for o in before + period:
        if o["customer_id"]:
            by_c[o["customer_id"]].append(o)
    active = {o["customer_id"] for o in period if o["customer_id"]}
    prior = {o["customer_id"] for o in before if o["customer_id"]}
    if len(active) < MIN_CUSTOMERS:
        out["customers"] = collecting("고객을 이어서 알아볼 수 있는 회원 주문이 부족해요 (비회원 주문은 고객별 분석에서 제외)",
                                      len(active), MIN_CUSTOMERS)
    else:
        counts = {c: sum(1 for o in period if o["customer_id"] == c) for c in active}
        gaps = []
        for c in active:
            ts = sorted(o["created_at"] for o in by_c[c])
            gaps += [(b - a) / 86400 for a, b in zip(ts, ts[1:])]
        out["customers"] = {
            "status": "ok", "active": len(active),
            "new": len(active - prior), "returning": len(active & prior),
            "repeat_in_period": sum(1 for c in active if counts[c] >= 2),
            "avg_orders": round(mean(counts.values()), 2),
            "freq_hist": _hist(list(counts.values()), [1, 2, 3, 4, 5], "5회+"),
            "avg_interval_days": round(mean(gaps), 2) if gaps else None,
            "interval_note": None if gaps else "2번 이상 구매한 고객이 아직 없어요"}

    # ---- R: 상품별 재구매율 (회원, 기간 전 포함 전체 이력 기준)
    buyers, repeaters = defaultdict(set), defaultdict(set)
    for c, orders in by_c.items():
        cnt = Counter(it["product_id"] for o in orders for it in o["items"])
        for pid, k in cnt.items():
            buyers[pid].add(c)
            if k >= 2:
                repeaters[pid].add(c)
    if len(by_c) < MIN_CUSTOMERS:
        out["repurchase"] = collecting("회원 구매 이력이 부족해요", len(by_c), MIN_CUSTOMERS)
    else:
        out["repurchase"] = {"status": "ok", "rows": sorted(
            ({"id": pid, "name": pname(pid), "buyers": len(b), "repeaters": len(repeaters[pid]),
              "rate": round(len(repeaters[pid]) / len(b), 4)} for pid, b in buyers.items()),
            key=lambda r: (-r["rate"], -r["buyers"]))}

    # ---- S / C: 상품 조합 (비회원 포함, 기간 안 주문)
    if n < MIN_ASSOC_ORDERS:
        out["association"] = collecting("연관성(support · confidence · lift)은 주문이 더 쌓여야 의미가 있어요", n,
                                        MIN_ASSOC_ORDERS)
    else:
        single, pair = Counter(), Counter()
        for o in period:
            ids = sorted({it["product_id"] for it in o["items"]})
            single.update(ids)
            pair.update(combinations(ids, 2))
        rows = []
        for (a, b), c in pair.items():
            if c < MIN_PAIR_COUNT:
                continue
            rows.append({"a": a, "b": b, "a_name": pname(a), "b_name": pname(b), "count": c,
                         "support": round(c / n, 4), "conf_ab": round(c / single[a], 4),
                         "conf_ba": round(c / single[b], 4),
                         "lift": round(c * n / (single[a] * single[b]), 3), "pattern": pattern(a, b)})
        rows.sort(key=lambda r: (-r["count"], -r["lift"]))
        top = [pid for pid, _ in single.most_common(HEATMAP_TOP)]
        heat = [[None if a == b else round(pair[tuple(sorted((a, b)))] * n / (single[a] * single[b]), 2)
                 if pair[tuple(sorted((a, b)))] else 0 for b in top] for a in top]
        out["association"] = {"status": "ok", "orders": n, "pairs": rows[:20],
                              "pattern_count": dict(Counter(r["pattern"] for r in rows)),
                              "heatmap": {"ids": top, "names": [pname(p) for p in top], "lift": heat}}
    return out


def _hist(values, bins, last_label):
    c = Counter(min(v, bins[-1]) for v in values)
    return {"labels": [str(b) for b in bins[:-1]] + [last_label], "values": [c[b] for b in bins]}
