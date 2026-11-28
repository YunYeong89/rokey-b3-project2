"""매출·판매 분석 (orders / order_items)

매출 = 실제로 담긴 수량(sold_qty) x 주문 시점 가격(unit_price)
집계 대상 주문 = status 가 completed / partial 이고 픽업 만료(안 가져감)가 아닌 주문
  - 취소·오류 주문은 sold_qty 가 0 이라 매출에 안 들어감
  - 부분 완료는 실제로 담긴 수량만 들어감
원가 데이터가 없으므로 이익·수익률은 계산하지 않음
"""
from collections import Counter, defaultdict

from ..catalog import PRODUCTS
from .common import SEASONS, WEEKDAYS, days_between, kst, kst_date, week_key
from .db import SALE_STATUSES

PMAP = {p["id"]: p for p in PRODUCTS}
CATEGORIES = sorted({p.get("category") or "기타" for p in PRODUCTS})


def pname(pid):
    return PMAP.get(pid, {}).get("name", pid)


def pcat(pid):
    return PMAP.get(pid, {}).get("category") or "기타"


def is_sale(o):
    return o["status"] in SALE_STATUSES and o.get("pickup_status") != "expired"


def first_order_ts(db, f):
    src, args = f.source_sql()
    r = db.query(f"SELECT MIN(created_at) AS t FROM orders WHERE 1=1{src}", args)
    return r[0]["t"] if r and r[0]["t"] else None


def load_orders(db, f, start_ts=None, end_ts=None):
    """기간 안 주문을 {order_uid: {..., "items": [...]}} 로 (모든 상태)"""
    src, args = f.source_sql("o.source")
    cond, cargs = "", []
    if start_ts is not None:
        cond += " AND o.created_at >= ?"
        cargs.append(start_ts)
    if end_ts is not None:
        cond += " AND o.created_at < ?"
        cargs.append(end_ts)
    rows = db.query(
        "SELECT o.order_uid, o.order_id, o.customer_id, o.source, o.created_at, o.finished_at, o.status,"
        " o.pickup_status, i.product_id, i.requested_qty, i.sold_qty, i.unit_price, i.handled_by,"
        " i.rec_slot, i.rec_algo FROM orders o LEFT JOIN order_items i ON i.order_uid = o.order_uid"
        f" WHERE 1=1{cond}{src} ORDER BY o.created_at", (*cargs, *args))
    out = {}
    for r in rows:
        o = out.setdefault(r["order_uid"], {k: r[k] for k in (
            "order_uid", "order_id", "customer_id", "source", "created_at", "finished_at", "status", "pickup_status")}
            | {"items": []})
        if r["product_id"]:
            o["items"].append({k: r[k] for k in ("product_id", "requested_qty", "sold_qty", "unit_price",
                                                 "handled_by", "rec_slot", "rec_algo")})
    return out


def item_filter(f):
    prods = set(f.products)
    return lambda pid: (not prods or pid in prods) and (not f.category or pcat(pid) == f.category)


def sold_orders(orders, keep):
    """매출 집계용: 판매된 주문만, 필터에 맞는 판매 상품만 남김. 남는 상품이 없는 주문은 뺌"""
    out = []
    for o in orders.values():
        if not is_sale(o):
            continue
        items = [it for it in o["items"] if it["sold_qty"] > 0 and keep(it["product_id"])]
        if items:
            out.append({**o, "items": items, "revenue": sum(it["sold_qty"] * it["unit_price"] for it in items),
                        "units": sum(it["sold_qty"] for it in items)})
    return out


def kpis(sold, orders):
    rev = sum(o["revenue"] for o in sold)
    units = sum(o["units"] for o in sold)
    n = len(sold)
    statuses = Counter(o["status"] for o in orders.values())
    closed = sum(statuses[s] for s in ("completed", "partial", "cancelled", "error"))
    return {"revenue": rev, "orders": n, "units": units,
            "aov": round(rev / n) if n else None, "items_per_order": round(units / n, 2) if n else None,
            "customers": len({o["customer_id"] for o in sold if o["customer_id"]}),
            "guest_orders": sum(1 for o in sold if not o["customer_id"]),
            "partial": statuses["partial"], "cancelled": statuses["cancelled"], "error": statuses["error"],
            "in_progress": statuses["in_progress"],
            "expired": sum(1 for o in orders.values() if o.get("pickup_status") == "expired"),
            "cancel_rate": round((statuses["cancelled"] + statuses["error"]) / closed, 4) if closed else None}


def bucket(d, gran):
    return d if gran == "day" else week_key(d) if gran == "week" else d[:7]


def trend(sold, s, e, gran):
    days = days_between(s, e)
    keys = list(dict.fromkeys(bucket(d, gran) for d in days))
    rev, cnt, units = Counter(), Counter(), Counter()
    for o in sold:
        k = bucket(kst_date(o["created_at"]), gran)
        rev[k] += o["revenue"]
        cnt[k] += 1
        units[k] += o["units"]
    return {"gran": gran, "labels": keys, "revenue": [rev[k] for k in keys], "orders": [cnt[k] for k in keys],
            "aov": [round(rev[k] / cnt[k]) if cnt[k] else None for k in keys],
            "items_per_order": [round(units[k] / cnt[k], 2) if cnt[k] else None for k in keys]}


def product_table(sold):
    qty, rev, n_orders = Counter(), Counter(), Counter()
    for o in sold:
        for it in o["items"]:
            qty[it["product_id"]] += it["sold_qty"]
            rev[it["product_id"]] += it["sold_qty"] * it["unit_price"]
            n_orders[it["product_id"]] += 1
    return [{"id": pid, "name": pname(pid), "category": pcat(pid), "qty": qty[pid], "revenue": rev[pid],
             "orders": n_orders[pid]} for pid in qty]


def time_patterns(sold):
    hours, wdays = [0] * 24, [0] * 7
    heat = [[0] * 24 for _ in range(7)]
    months, seasons = Counter(), Counter()
    for o in sold:
        t = kst(o["created_at"])
        hours[t.hour] += o["units"]
        wdays[t.weekday()] += o["units"]
        heat[t.weekday()][t.hour] += o["units"]
        months[t.strftime("%Y-%m")] += o["units"]
        seasons[SEASONS[t.month]] += o["units"]
    mk = sorted(months)
    return {"hours": hours, "weekdays": {"labels": WEEKDAYS, "values": wdays},
            "heatmap": {"rows": WEEKDAYS, "cols": list(range(24)), "values": heat},
            "monthly": {"labels": mk, "values": [months[m] for m in mk]},
            "seasons": {"labels": ["봄", "여름", "가을", "겨울"], "values": [seasons[x] for x in ("봄", "여름", "가을", "겨울")]}}


def sales(db, f):
    s_ts, e_ts, s, e = f.bounds(first_order_ts(db, f))
    orders = load_orders(db, f, s_ts, e_ts)
    keep = item_filter(f)
    sold = sold_orders(orders, keep)
    prods = product_table(sold)
    cats = defaultdict(int)
    for p in prods:
        cats[p["category"]] += p["revenue"]
    return {"period": {"label": f.label(s, e), "start": s, "end": e, "days": len(days_between(s, e))},
            "filters": {"products": list(f.products), "category": f.category, "source": f.source},
            "empty": not sold,
            "kpi": kpis(sold, orders),
            "trend": trend(sold, s, e, f.gran),
            "daily": trend(sold, s, e, "day"),
            "by_qty": sorted(prods, key=lambda p: (-p["qty"], -p["revenue"])),
            "by_revenue": sorted(prods, key=lambda p: (-p["revenue"], -p["qty"])),
            "categories": [{"name": c, "revenue": v} for c, v in sorted(cats.items(), key=lambda kv: -kv[1])],
            **time_patterns(sold),
            "options": {"products": [{"id": p["id"], "name": p["name"], "category": p.get("category") or "기타"}
                                     for p in PRODUCTS], "categories": CATEGORIES}}
