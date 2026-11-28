"""Analytics: 기록(이벤트 받기) + 조회(탭별 집계, 캐시)를 묶은 것. PickupService 가 하나 만들어 씀

기록 쪽은 모든 예외를 삼킴 -> 분석 기록이 실패해도 주문·재고·로봇은 그대로 동작
조회 쪽은 탭마다 따로 계산 -> 한 탭(예: 포장 결과 파일)이 실패해도 다른 탭은 동작
캐시: (탭, 필터, 데이터 버전) 이 같으면 다시 계산하지 않음. 주문·재고·로봇 기록이 생기면 버전이 바뀌어 다음 조회 때 새로 계산
"""
import time
from collections import Counter

from ..orders import order_uid
from . import customers as customers_mod
from . import inventory as inventory_mod
from . import packing as packing_mod
from . import recommendations as rec_mod
from . import robot as robot_mod
from . import sales as sales_mod
from .common import Filters
from .db import AnalyticsDB
from .robot import RobotTracker

CACHE_MAX = 64
STATUS_NAME = {"in_progress": "처리 중", "ready_for_pickup": "수령 대기", "picked_up": "수령 완료", "partial": "부분 완료",
               "expired": "만료 (미수령)", "cancelled": "취소", "error": "오류"}


class Analytics:
    def __init__(self, path, store, source_fn=lambda: "mock"):
        self.db = AnalyticsDB(path)
        self.store = store
        self.source_fn = source_fn   # 지금 데이터가 실제 로봇(robot)인지 데모(mock)인지
        self.tracker = RobotTracker(self.db)
        self.cache = {}

    # ------------------------------------------------------------ 기록 (실패해도 조용히)
    def safe(self, what, fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except Exception as e:
            print(f"[analytics] {what} 기록 실패: {e}")
            return None

    def prices(self, pids):
        return {pid: (self.store.get(pid) or {}).get("price", 0) for pid in pids}

    def order_created(self, o, note=None):
        self.safe("주문 접수", self.db.order_created, o, self.prices(o.get("items") or {}), self.source_fn(), note=note)

    def order_done(self, o):
        """로봇 done (첫 번째만 반영, 두 번째부터는 db 가 무시)"""
        uid = order_uid(o)
        if self.db.order_row(uid) is None:   # 서버 재시작 전에 받은 주문 등 접수 기록이 없는 주문
            self.order_created({**o, "uid": uid}, note="접수 기록 없음 (완료 때 생성, 가격은 완료 시점)")
        bought = {**o.get("staff_items", {}), **o.get("picked", {})}
        self.safe("주문 완료", self.db.order_done, uid, o.get("picked"), o.get("staff_items"),
                  self.prices(bought), o.get("ready_at"))

    def order_failed(self, o, status, message=""):
        self.safe("주문 취소·오류", self.db.order_failed, order_uid(o), status, message)

    def pickup_status(self, o, status):
        self.safe("픽업 상태", self.db.pickup_status, order_uid(o), status)

    def inventory_change(self, p, kind, shelf_change, wh_change, order_uid=None, note=None):
        self.safe("재고 변경", self.db.inventory_change, p["id"], kind, shelf_change, wh_change, p["stock"],
                  p["warehouse"], order_uid, self.source_fn(), note)
        if kind == "sale" and p["stock"] == 0:
            self.safe("품절", self.db.stockout, p["id"], "shelf_empty", 0, order_uid, self.source_fn())

    def shortage(self, pid, want, have, order_uid=None):
        self.safe("재고 부족", self.db.stockout, pid, "shortage", max(0, want - have), order_uid, self.source_fn())

    def robot_status(self, st, uid=None):
        self.safe("로봇 상태", self.tracker.on_status, st, uid, self.source_fn())

    def startup(self, store_products, orders):
        """서버 시작: 오늘 재고 기록 + orders.json 과 맞추기 (재시작으로 error 가 된 주문, 분석 기록 전에 생긴 주문)"""
        self.safe("재고 스냅샷", self.db.snapshot, store_products)
        self.safe("주문 맞추기", self._reconcile, orders)

    def _reconcile(self, orders):
        for o in orders.values():
            uid = order_uid(o)
            row = self.db.order_row(uid)
            if row is None:
                if o["status"] == "in_progress":
                    continue
                # 분석 기능을 넣기 전의 주문: 가격은 지금 가격으로 (note 에 표시)
                self.db.order_created({**o, "uid": uid}, self.prices(o.get("items") or {}), "unknown",
                                      note="분석 기능 이전 주문 (가격은 기록 시점)")
                row = self.db.order_row(uid)
            if row["status"] != "in_progress":
                if row["status"] in ("completed", "partial") and o["status"] in ("picked_up", "expired") \
                        and row["pickup_status"] != o["status"]:
                    self.db.pickup_status(uid, o["status"])
                continue
            if o["status"] in ("ready_for_pickup", "picked_up", "expired"):
                self.db.order_done(uid, o.get("picked"), o.get("staff_items"),
                                   self.prices({**o.get("staff_items", {}), **o.get("picked", {})}), o.get("ready_at"))
                if o["status"] != "ready_for_pickup":
                    self.db.pickup_status(uid, o["status"])
            elif o["status"] in ("cancelled", "error"):
                self.db.order_failed(uid, o["status"], o.get("message", ""))

    # ------------------------------------------------------------ 조회
    def version(self):
        return self.db.tag()

    def _cached(self, key, fn):
        k = (key, self.db.tag())
        if k not in self.cache:
            if len(self.cache) >= CACHE_MAX:
                self.cache.clear()
            self.cache[k] = fn()
        return self.cache[k]

    def wrap(self, tab, f, fn, extra=()):
        def run():
            t0 = time.time()
            data = fn()
            return {"tab": tab, "generated_at": time.time(), "version": self.db.tag(),
                    "compute_ms": round((time.time() - t0) * 1000, 1), **data}
        return self._cached((tab, f.key(), extra), run)

    def sales(self, f):
        return self.wrap("sales", f, lambda: sales_mod.sales(self.db, f))

    def products(self, f):
        def run():
            d = sales_mod.sales(self.db, f)
            return {k: d[k] for k in ("period", "by_qty", "by_revenue", "categories", "options")}
        return self.wrap("products", f, run)

    def customers(self, f):
        return self.wrap("customers", f, lambda: customers_mod.customers(self.db, f))

    def recommendations(self, f):
        # 추천 노출은 주문과 상관없이 계속 쌓이므로 캐시 키에 분 단위 시각을 넣어 1분마다 새로 계산
        return self.wrap("recommendations", f, lambda: rec_mod.recommendations(self.db, f), (int(time.time() // 60),))

    def packing(self, f):
        return self.wrap("packing", f, lambda: packing_mod.packing(self.db, f))

    def robot(self, f):
        return self.wrap("robot", f, lambda: robot_mod.robot(self.db, f))

    def inventory(self, f, store_rows, reserved, product=None):
        inventory_mod.ensure_forecasts(self.db, f, inventory_mod.kst_date(f.now))

        def run():
            inv = inventory_mod.inventory(self.db, f, store_rows, reserved)
            pf = Filters.parse(range=f.range, start=f.start, end=f.end, product=product or "", source=f.source,
                               now=f.now)
            pat = sales_mod.time_patterns(sales_mod.sold_orders(
                sales_mod.load_orders(self.db, pf, *pf.bounds(sales_mod.first_order_ts(self.db, pf))[:2]),
                sales_mod.item_filter(pf)))
            return {**inv, "trend": inventory_mod.inventory_trends(self.db, f, product),
                    "held": inventory_mod.held_by_product(self.db, f),
                    "pattern": {"monthly": pat["monthly"], "seasons": pat["seasons"]}}
        # 재고는 지금 값(store)을 쓰므로 그 값도 키에 넣음
        snap = tuple((r["id"], r["stock"], r["warehouse"]) for r in store_rows) + tuple(sorted(reserved.items()))
        return self.wrap("inventory", f, run, (product, snap))

    def overview(self, f, store_rows, reserved):
        def run():
            today = Filters.parse(range="today", source=f.source, now=f.now)
            all_ = Filters.parse(range="all", source=f.source, now=f.now)
            week = Filters.parse(range="7d", source=f.source, now=f.now)
            st = sales_mod.sales(self.db, today)
            sa = sales_mod.sales(self.db, all_)
            sw = sales_mod.sales(self.db, week)
            inv = inventory_mod.inventory(self.db, all_, store_rows, reserved)
            orders = sales_mod.load_orders(self.db, all_)
            status = Counter()
            for o in orders.values():
                k = o["status"]
                if k == "completed":
                    k = o.get("pickup_status") or "ready_for_pickup"
                elif k == "partial" and o.get("pickup_status") == "expired":
                    k = "expired"
                status[k] += 1
            return {"empty": not orders,
                    "kpi": {"today_revenue": st["kpi"]["revenue"], "today_orders": st["kpi"]["orders"],
                            "today_units": st["kpi"]["units"], "aov": sa["kpi"]["aov"],
                            "total_revenue": sa["kpi"]["revenue"], "total_orders": sa["kpi"]["orders"],
                            "low_stock": inv["low_count"], "robot_success": robot_mod.success_rate(self.db, all_),
                            "avg_pack_sec": packing_mod.avg_pack_seconds(self.db, all_)},
                    "week": {"labels": sw["daily"]["labels"], "revenue": sw["daily"]["revenue"]},
                    "top5": sa["by_qty"][:5],
                    "stock": [{"id": r["id"], "name": r["name"], "on_hand": r["on_hand"], "target": r["target_applied"],
                               "basis": r["target_basis"]} for r in inv["rows"]],
                    "status": [{"key": k, "name": STATUS_NAME.get(k, k), "count": c} for k, c in status.most_common()]}
        snap = tuple((r["id"], r["stock"], r["warehouse"]) for r in store_rows) + tuple(sorted(reserved.items()))
        return self.wrap("overview", f, run, (snap,))
