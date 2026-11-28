"""재고 최적화 (수요 예측 연동 준비)

계산 구조
  보호기간          = 입고 소요 기간(lead_time_days) + 재고 검토 주기(review_period_days)
  목표 재고량       = 보호기간의 예상 수요 + 안전재고
  재고 포지션       = 현재 재고(진열 + 창고) + 입고 예정 - 아직 담지 않은 확정 주문 수량
  보충 필요 수량    = max(0, 목표 재고량 - 재고 포지션)
  - 재고는 로봇 done 때 차감됨(Store.apply_picked). 그래서 '아직 담지 않은 확정 주문' = 로봇이 처리 중인(in_progress) 주문뿐이고,
    수령 대기(ready_for_pickup) 주문은 이미 차감되었으므로 다시 빼지 않음

목표 재고는 두 가지를 따로 보여 줌
  설정 목표   data/inventory_policy.json (관리자 설정, 없으면 products.json 의 처음 재고)
  예측 목표   자체 판매 데이터로 만든 예측 (min_history_days 일 이상 쌓이기 전에는 '참고'로만, 적용은 설정 목표)

예측 모델
  fixed_mean, moving_average   : 이 웹의 판매 기록으로 서버가 직접 계산 (하루 한 번 forecast_history 에 저장)
  나머지 (M5 연구 중)           : 구현돼 있지 않음. 외부 모듈이 AnalyticsDB.save_forecasts() 로 결과를 넣으면 '외부 결과 연동'으로 표시
  M5 는 상품명이 익명이라 이 편의점 상품과 매핑하지 않음 (모델·방법만 가져와 자체 판매 데이터로 다시 학습하는 구조)
"""
import json
import math
from collections import Counter, defaultdict
from statistics import mean, pstdev

from ..catalog import PRODUCTS
from ..config import INVENTORY_POLICY_FILE
from .common import collecting, day_start, days_between, kst_date
from .sales import first_order_ts, load_orders, pname, sold_orders

BUILTIN_MODELS = ("fixed_mean", "moving_average")
MODELS = [  # (id, 이름) - 재고 탭의 예측 모델 상태 표
    ("fixed_mean", "Fixed Mean"), ("moving_average", "Moving Average"), ("monthly_seasonal", "Monthly Seasonal"),
    ("seasonal_moving_average", "Seasonal Moving Average"), ("holt_winters", "Holt-Winters"),
    ("sarima", "SARIMA"), ("lightgbm", "LightGBM")]
DEFAULTS = {"lead_time_days": 2, "review_period_days": 1, "safety_stock": 2, "service_z": 1.65,
            "min_history_days": 14, "ma_window_days": 7}
INITIAL = {p["id"]: p.get("stock", 0) + p.get("warehouse", 0) for p in PRODUCTS}


def load_policy(path=INVENTORY_POLICY_FILE):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    d = {**DEFAULTS, **(raw.get("defaults") or {})}
    per = raw.get("products") or {}
    return d, {pid: {**d, "target_stock": INITIAL.get(pid, 0), "incoming": 0, **(per.get(pid) or {})}
               for pid in INITIAL}


def daily_sales(db, f, s, e):
    """{pid: {date: 판매 수량}} (판매 완료 주문, 한국 날짜)"""
    out = defaultdict(Counter)
    for o in sold_orders(load_orders(db, f, day_start(s), day_start(e) + 86400), lambda pid: True):
        d = kst_date(o["created_at"])
        for it in o["items"]:
            out[it["product_id"]][d] += it["sold_qty"]
    return out


def ensure_forecasts(db, f, today):
    """오늘 날짜 예측이 없으면 서버 내장 모델(Fixed Mean, Moving Average)로 한 번 계산해 저장.
    재학습은 매 주문이 아니라 하루 한 번만 (같은 날 다시 불러도 UNIQUE 라 그대로)"""
    first = first_order_ts(db, f)
    if not first or kst_date(first) >= today:
        return
    have = db.query("SELECT 1 FROM forecast_history WHERE target_date=? AND model IN ('fixed_mean','moving_average')"
                    " LIMIT 1", (today,))
    if have:
        return
    pol, _ = load_policy()
    days = days_between(kst_date(first), today)[:-1]   # 오늘 전까지
    hist = daily_sales(db, f, days[0], days[-1])
    win = days[-int(pol["ma_window_days"]):]
    rows = []
    for pid in INITIAL:
        h = hist.get(pid, {})
        rows.append({"product_id": pid, "target_date": today, "model": "fixed_mean", "model_version": "builtin-1",
                     "qty": round(sum(h.get(d, 0) for d in days) / len(days), 3)})
        rows.append({"product_id": pid, "target_date": today, "model": "moving_average",
                     "model_version": f"builtin-ma{len(win)}", "qty": round(sum(h.get(d, 0) for d in win) / len(win), 3)})
    db.save_forecasts(rows)


def inventory(db, f, store_rows, reserved):
    """store_rows: Store.rows() (지금 재고), reserved: {pid: 로봇이 처리 중인 주문의 수량}"""
    pol, per = load_policy()
    today = kst_date(f.now)
    first = first_order_ts(db, f)
    start = kst_date(first) if first else today
    span_days = len(days_between(start, today))
    enough = first is not None and span_days - 1 >= pol["min_history_days"]   # 오늘 빼고 꽉 찬 날 수
    hist_days = days_between(start, today)
    hist = daily_sales(db, f, start, today)
    win = hist_days[-int(pol["ma_window_days"]) - 1:-1] or hist_days   # 최근 n일 (오늘 제외, 오늘만 있으면 오늘)

    latest = {}   # pid -> 오늘 날짜의 가장 최근 예측 (외부 모델 우선)
    for r in db.query("SELECT product_id, model, model_version, qty FROM forecast_history WHERE target_date=?"
                      " ORDER BY created_at", (today,)):
        latest.setdefault(r["product_id"], {})[r["model"]] = r

    rows = []
    for p in store_rows:
        pid = p["id"]
        cfg = per.get(pid, {**pol, "target_stock": 0, "incoming": 0})
        protect = cfg["lead_time_days"] + cfg["review_period_days"]
        h = hist.get(pid, {})
        series = [h.get(d, 0) for d in win]
        avg = round(mean(series), 3) if series else 0.0
        fc = latest.get(pid, {})
        ext = [m for m in fc if m not in BUILTIN_MODELS]
        model = ext[-1] if ext else "moving_average" if "moving_average" in fc else None
        daily_fc = fc[model]["qty"] if model else avg
        demand = round(daily_fc * protect, 2)
        sigma = pstdev(series) if len(series) > 1 else 0.0
        safety_fc = max(cfg["safety_stock"], math.ceil(cfg["service_z"] * sigma * math.sqrt(protect)))
        target_fc = math.ceil(demand + safety_fc)
        target_cfg = int(cfg["target_stock"])
        applied = target_fc if enough else target_cfg
        on_hand = p["stock"] + p["warehouse"]
        position = on_hand + int(cfg["incoming"]) - int(reserved.get(pid, 0))
        need = max(0, applied - position)
        state = "out" if on_hand == 0 else "reorder" if need > 0 else "ok"
        rows.append({"id": pid, "name": p["name"], "emoji": p.get("emoji"), "robot": p.get("robot"),
                     "stock": p["stock"], "warehouse": p["warehouse"], "on_hand": on_hand,
                     "avg_daily": avg, "forecast_daily": round(daily_fc, 3), "forecast_model": model or "recent_avg",
                     "forecast_protect": demand, "protect_days": protect,
                     "safety": cfg["safety_stock"], "safety_forecast": safety_fc,
                     "target_config": target_cfg, "target_forecast": target_fc, "target_applied": applied,
                     "target_basis": "forecast" if enough else "config",
                     "incoming": int(cfg["incoming"]), "reserved": int(reserved.get(pid, 0)),
                     "position": position, "reorder": need, "state": state})

    models = []
    ext_models = {r["model"] for r in db.query("SELECT DISTINCT model FROM forecast_history")}
    for mid, name in MODELS:
        if mid in BUILTIN_MODELS:
            st = "builtin"
        elif mid in ext_models:
            st = "external"
        else:
            st = "not_connected"
        models.append({"id": mid, "name": name, "status": st})
    return {"today": today, "policy": pol, "history_days": span_days, "enough_history": enough,
            "history_note": None if enough else
            f"판매 기록 {max(0, span_days - 1)}일 / 필요 {pol['min_history_days']}일 - 예측 목표는 참고값, 적용은 설정 목표",
            "rows": rows, "models": models,
            "low_count": sum(1 for r in rows if r["state"] != "ok")}


def inventory_trends(db, f, pid):
    """선택한 상품(없으면 전체 합) 의 재고 변화, 실제 vs 예측 판매, 월·계절 패턴, 품절 추이, 평균 재고, 회전율"""
    s_ts, e_ts, s, e = f.bounds(first_order_ts(db, f))
    days = days_between(s, e)
    pids = [pid] if pid else list(INITIAL)

    # 재고 변화: 하루 마지막 기록, 기록 없는 날은 전날 값 이어 쓰기
    snap = defaultdict(dict)
    for r in db.query("SELECT date, product_id, stock, warehouse FROM inventory_history WHERE date <= ?", (e,)):
        snap[r["product_id"]][r["date"]] = r["stock"] + r["warehouse"]
    level = []
    for d in days:
        tot, any_ = 0, False
        for p in pids:
            past = [x for x in snap.get(p, {}) if x <= d]
            if past:
                tot += snap[p][max(past)]
                any_ = True
        level.append(tot if any_ else None)
    held = [x for x in level if x is not None]
    avg_held = round(mean(held), 2) if held else None

    hist = daily_sales(db, f, s, e)
    actual = [sum(hist.get(p, {}).get(d, 0) for p in pids) for d in days]
    fc = defaultdict(lambda: defaultdict(float))
    for r in db.query("SELECT product_id, target_date, model, qty FROM forecast_history WHERE target_date BETWEEN ? AND ?",
                      (s, e)):
        if r["product_id"] in pids:
            fc[r["model"]][r["target_date"]] += r["qty"]
    forecast = {m: [round(v[d], 2) if d in v else None for d in days] for m, v in fc.items()}

    so = Counter()
    so_rows = db.query("SELECT ts, product_id, unmet_qty FROM stockout_events WHERE ts >= ? AND ts < ?", (s_ts, e_ts))
    for r in so_rows:
        if r["product_id"] in pids:
            so[kst_date(r["ts"])] += 1
    sold_total = sum(actual)
    return {"product": pid, "name": pname(pid) if pid else "전체 상품", "labels": days, "level": level,
            "actual": actual, "forecast": forecast,
            "stockouts": [so[d] for d in days], "stockout_total": sum(so.values()),
            "unmet_total": sum(r["unmet_qty"] for r in so_rows if r["product_id"] in pids),
            "avg_held": avg_held,
            "turnover": round(sold_total / avg_held, 3) if avg_held else None,
            "turnover_note": None if avg_held else "재고 기록이 쌓이면 계산돼요 (서버 시작·재고 변경 때 기록)",
            "sold_total": sold_total}


def held_by_product(db, f):
    """상품별 평균 보유 재고와 회전율 (기간 안 하루 마지막 재고 평균)"""
    s_ts, e_ts, s, e = f.bounds(first_order_ts(db, f))
    rows = db.query("SELECT product_id, AVG(stock + warehouse) AS avg FROM inventory_history WHERE date BETWEEN ? AND ?"
                    " GROUP BY product_id", (s, e))
    hist = daily_sales(db, f, s, e)
    out = []
    for r in rows:
        sold = sum(hist.get(r["product_id"], {}).values())
        out.append({"id": r["product_id"], "name": pname(r["product_id"]), "avg_held": round(r["avg"], 2),
                    "sold": sold, "turnover": round(sold / r["avg"], 3) if r["avg"] else None})
    if not out:
        return collecting("재고 기록이 아직 없어요 (서버 시작·재고 변경 때 기록돼요)")
    return {"status": "ok", "rows": sorted(out, key=lambda x: -(x["turnover"] or 0))}
