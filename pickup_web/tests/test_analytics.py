"""Analytics 테스트 (운영 데이터와 분리된 임시 폴더 사용, 로봇 명령 없음)

실행: cd pickup_web && python3 -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="pickup_analytics_test_")
os.environ["PICKUP_RUNTIME_DIR"] = TMP            # pickup 패키지를 import 하기 전에 정해야 함
os.environ.setdefault("PACKING_RESULTS_DIR", str(Path(TMP) / "no_packing_results"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pickup.analytics.common import Filters  # noqa: E402
from pickup.config import RUNTIME  # noqa: E402
from pickup.service import PickupService  # noqa: E402


class FakeRobot:
    """로봇 명령을 보내지 않는 테스트용 (보낸 것만 기록)"""
    mode = "mock"

    def __init__(self):
        self.sent = []

    def send_order(self, oid, items): self.sent.append(("order", oid, items))
    def send_choice(self, data): self.sent.append(("choice", data))
    def request_scan(self): pass
    def send_pickup(self, oid, code): self.sent.append(("pickup", oid, code))


def new_service():
    svc = PickupService(expire_min=30)
    svc.robot = FakeRobot()
    return svc


def place(svc, items, customer=None, rec_attrib=None):
    """api/shop.py order() 와 같은 순서 (HTTP 없이)"""
    with svc.store.lock:
        o = svc.orders.new(items, customer_id=customer, rec_attrib=rec_attrib or {})
        svc.track("order_created", o)
    return o


def status(svc, **st):
    svc.handle_status(st)


def run_robot(svc, oid, picked, staff=None, step=0.0):
    """MockRobot 과 같은 순서로 상태를 보냄"""
    total = sum(picked.values())
    status(svc, order_id=oid, state="checking", done=0, total=total)
    done, got = 0, {}
    for pid, n in picked.items():
        for _ in range(n):
            status(svc, order_id=oid, state="picking", current=pid, done=done, total=total, picked=dict(got))
            time.sleep(step)
            got[pid] = got.get(pid, 0) + 1
            done += 1
    status(svc, order_id=oid, state="done", done=done, total=total, picked=got, staff_items=staff or {})


def F(**kw):
    return Filters.parse(**{"range": "all", **kw})


class AnalyticsTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        for f in Path(TMP).glob("*"):
            f.unlink()
        self.svc = new_service()
        self.an = self.svc.analytics

    def price(self, pid):
        return self.svc.store.get(pid)["price"]

    # 1, 3
    def test_runtime_dir_is_isolated(self):
        self.assertEqual(RUNTIME, Path(TMP).resolve())
        self.assertNotIn("data/runtime", str(self.an.db.path))

    def test_empty_db_all_tabs(self):
        rows, reserved = self.svc.store.rows(), {}
        f = F()
        ov = self.an.overview(f, rows, reserved)
        self.assertTrue(ov["empty"])
        self.assertEqual(ov["kpi"]["total_revenue"], 0)
        self.assertIsNone(ov["kpi"]["robot_success"])
        self.assertIsNone(ov["kpi"]["avg_pack_sec"])
        self.assertTrue(self.an.sales(f)["empty"])
        self.assertEqual(self.an.customers(f)["customers"]["status"], "collecting")
        inv = self.an.inventory(f, rows, reserved)
        self.assertEqual(len(inv["rows"]), len(rows))
        self.assertFalse(inv["enough_history"])
        self.assertEqual(self.an.packing(f)["live"]["status"], "not_collected")
        self.assertEqual(self.an.packing(f)["offline"]["status"], "collecting")
        self.assertTrue(self.an.robot(f)["empty"])
        self.assertTrue(self.an.recommendations(f)["empty"])

    # 4, 5, 6
    def test_completed_order_revenue_and_refresh(self):
        v0 = self.an.version()
        o = place(self.svc, {"tuna_can": 2, "cola": 1}, customer="user1")
        run_robot(self.svc, o["order_id"], {"tuna_can": 2}, {"cola": 1})
        self.assertNotEqual(v0, self.an.version())        # 데이터 버전이 바뀌어 대시보드가 다시 불러옴
        s = self.an.sales(F())
        expect = 2 * self.price("tuna_can") + self.price("cola")
        self.assertEqual(s["kpi"]["revenue"], expect)
        self.assertEqual(s["kpi"]["orders"], 1)
        self.assertEqual(s["kpi"]["units"], 3)
        row = self.an.db.order_row(o["uid"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["source"], "mock")
        tx = self.an.db.query("SELECT product_id, shelf_change FROM inventory_transactions WHERE kind='sale'")
        self.assertEqual({r["product_id"]: r["shelf_change"] for r in tx}, {"tuna_can": -2, "cola": -1})

    # 7
    def test_cancelled_and_error_excluded(self):
        a = place(self.svc, {"spam_can": 1})
        status(self.svc, order_id=a["order_id"], state="checking", done=0, total=1)
        status(self.svc, order_id=a["order_id"], state="cancelled", message="주문이 취소되었어요")
        b = place(self.svc, {"tuna_can": 1})
        status(self.svc, order_id=b["order_id"], state="picking", current="tuna_can", done=0, total=1)
        status(self.svc, order_id=b["order_id"], state="error", message="tuna_can 집기 실패 - 직원 확인 필요")
        s = self.an.sales(F())
        self.assertEqual(s["kpi"]["revenue"], 0)
        self.assertEqual(s["kpi"]["cancelled"], 1)
        self.assertEqual(s["kpi"]["error"], 1)
        rb = self.an.robot(F())
        self.assertEqual(rb["kpi"]["error"], 1)
        self.assertEqual(rb["grasp"]["rows"][0]["rate"], 0.0)
        self.assertEqual(rb["error_types"][0]["type"], "grasp_fail")

    # 8
    def test_partial_and_substitute(self):
        o = place(self.svc, {"tuna_can": 2})
        status(self.svc, order_id=o["order_id"], state="need_choice", shortage={"id": "tuna_can", "want": 2, "have": 1})
        status(self.svc, order_id=o["order_id"], state="need_choice", shortage={"id": "tuna_can", "want": 2, "have": 1})
        run_robot(self.svc, o["order_id"], {"tuna_can": 1, "spam_can": 1})   # 1개 + 대체 상품 1개
        row = self.an.db.order_row(o["uid"])
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["sold_qty"], 2)
        self.assertEqual(row["amount"], self.price("tuna_can") + self.price("spam_can"))
        so = self.an.db.query("SELECT * FROM stockout_events WHERE kind='shortage'")
        self.assertEqual(len(so), 1)                        # 같은 부족 알림이 두 번 와도 한 번
        self.assertEqual(so[0]["unmet_qty"], 1)

    # 9
    def test_duplicate_done_not_double_counted(self):
        o = place(self.svc, {"milk_pack": 1})
        run_robot(self.svc, o["order_id"], {"milk_pack": 1})
        stock_after = self.svc.store.get("milk_pack")["stock"]
        status(self.svc, order_id=o["order_id"], state="done", done=1, total=1, picked={"milk_pack": 1}, staff_items={})
        self.svc.store.status = {"state": "idle"}            # done 사이에 다른 상태가 끼어도
        status(self.svc, order_id=o["order_id"], state="done", done=1, total=1, picked={"milk_pack": 1}, staff_items={})
        s = self.an.sales(F())
        self.assertEqual(s["kpi"]["units"], 1)
        self.assertEqual(s["kpi"]["revenue"], self.price("milk_pack"))
        self.assertEqual(len(self.an.db.query("SELECT * FROM robot_logs WHERE kind='order'")), 1)
        self.assertEqual(self.svc.store.get("milk_pack")["stock"], stock_after)   # 재고도 한 번만 차감
        self.assertEqual(len(self.an.db.query("SELECT * FROM inventory_transactions WHERE kind='sale'")), 1)

    def test_price_snapshot(self):
        o = place(self.svc, {"cola": 2})
        old = self.price("cola")
        self.svc.store.get("cola")["price"] = old + 1000    # 주문 뒤 가격이 바뀌어도
        run_robot(self.svc, o["order_id"], {}, {"cola": 2})
        self.assertEqual(self.an.sales(F())["kpi"]["revenue"], 2 * old)
        self.svc.store.get("cola")["price"] = old

    def test_expired_excluded(self):
        o = place(self.svc, {"bread": 1})
        run_robot(self.svc, o["order_id"], {}, {"bread": 1})
        self.assertEqual(self.an.sales(F())["kpi"]["orders"], 1)
        with self.svc.store.lock:
            self.svc.orders.sweep(time.time() + 31 * 60)
        self.assertEqual(self.an.sales(F())["kpi"]["orders"], 0)
        self.assertEqual(self.an.db.order_row(o["uid"])["pickup_status"], "expired")

    # 10
    def test_restart_keeps_data_and_reconciles(self):
        o = place(self.svc, {"water": 3})
        run_robot(self.svc, o["order_id"], {}, {"water": 3})
        p = place(self.svc, {"tuna_can": 1})                 # 담는 중에 서버 종료
        status(self.svc, order_id=p["order_id"], state="checking", done=0, total=1)
        rev = self.an.sales(F())["kpi"]["revenue"]
        self.an.db.db.close()
        self.svc.history.db.close()
        svc2 = new_service()
        self.assertEqual(svc2.analytics.sales(F())["kpi"]["revenue"], rev)
        self.assertEqual(svc2.analytics.db.order_row(p["uid"])["status"], "error")

    # 11
    def test_filters(self):
        a = place(self.svc, {"cola": 1, "cookie": 2})
        run_robot(self.svc, a["order_id"], {}, {"cola": 1, "cookie": 2})
        s = self.an.sales(F(product="cookie"))
        self.assertEqual(s["kpi"]["units"], 2)
        self.assertEqual(s["kpi"]["revenue"], 2 * self.price("cookie"))
        self.assertEqual(self.an.sales(F(category="음료"))["kpi"]["units"], 1)
        self.assertEqual(self.an.sales(F(range="today"))["kpi"]["orders"], 1)
        self.assertEqual(self.an.sales(F(source="robot"))["kpi"]["orders"], 0)   # mock 데이터와 실제 데이터 구분
        old = self.an.sales(F(range="custom", start="2020-01-01", end="2020-01-31"))
        self.assertEqual(old["kpi"]["orders"], 0)
        self.assertEqual(len(old["daily"]["labels"]), 31)
        with self.assertRaises(ValueError):
            F(range="custom", start="2020-13-01")

    def test_recommendation_attribution(self):
        o = place(self.svc, {"cola": 1, "water": 1}, rec_attrib={"water": {"slot": "cart", "algo": "S", "rec_id": "x", "rank": 1}})
        run_robot(self.svc, o["order_id"], {}, {"cola": 1, "water": 1})
        r = self.an.recommendations(F())
        self.assertEqual(r["kpi"]["rec_revenue"], self.price("water"))
        self.assertEqual(r["kpi"]["purchase"], 1)
        algo = {x["key"]: x for x in r["by_algo"]}
        self.assertEqual(algo["S"]["revenue"], self.price("water"))

    def test_customers_and_inventory(self):
        for cid, items in (("user1", {"cola": 1, "cookie": 1}), ("user1", {"cola": 1}), ("user2", {"cookie": 1})):
            o = place(self.svc, items, customer=cid)
            run_robot(self.svc, o["order_id"], {}, items)
        c = self.an.customers(F())
        self.assertEqual(c["customers"]["active"], 2)
        rep = {x["id"]: x for x in c["repurchase"]["rows"]}
        self.assertEqual(rep["cola"]["rate"], 1.0)           # user1 이 콜라를 2번
        self.assertEqual(rep["cookie"]["rate"], 0.0)
        self.assertEqual(c["association"]["status"], "collecting")   # 주문 3건 < 10건
        reserved = {"cola": 1}
        inv = self.an.inventory(F(), self.svc.store.rows(), reserved)
        cola = next(r for r in inv["rows"] if r["id"] == "cola")
        self.assertEqual(cola["position"], cola["on_hand"] + cola["incoming"] - 1)
        self.assertEqual(cola["reorder"], max(0, cola["target_applied"] - cola["position"]))
        self.assertEqual(cola["target_basis"], "config")
        models = {m["id"]: m["status"] for m in inv["models"]}
        self.assertEqual(models["lightgbm"], "not_connected")
        self.assertEqual(models["moving_average"], "builtin")

    def test_external_forecast_interface(self):
        self.an.db.save_forecasts([{"product_id": "cola", "target_date": "2030-01-01", "qty": 3, "model": "lightgbm",
                                    "model_version": "test"}])
        inv = self.an.inventory(F(), self.svc.store.rows(), {})
        self.assertEqual({m["id"]: m["status"] for m in inv["models"]}["lightgbm"], "external")

    def test_packing_interface(self):
        self.an.db.log_packing(planner="llm", input_mode="text_properties", plan_generated=1, validation_passed=0,
                               robot_success=None, pack_seconds=30, n_items=4, failure_reason="collision")
        p = self.an.packing(F())
        self.assertEqual(p["live"]["status"], "ok")
        llm = p["live"]["by_planner"][0]
        self.assertEqual(llm["validation_rate"], 0.0)
        self.assertIsNone(llm["robot_success_rate"])         # 검증 통과율과 실제 포장 성공률은 따로

    def test_robot_pick_timing_and_events(self):
        o = place(self.svc, {"tuna_can": 2})
        oid = o["order_id"]
        status(self.svc, order_id=oid, state="checking", done=0, total=2)
        status(self.svc, order_id=oid, state="picking", current="tuna_can", done=0, total=2,
               event={"type": "motion_plan", "success": False, "error_type": "PLANNING_FAILED"})
        time.sleep(0.05)
        status(self.svc, order_id=oid, state="picking", current="tuna_can", done=1, total=2)
        status(self.svc, order_id=oid, state="done", done=2, total=2, picked={"tuna_can": 2}, staff_items={})
        r = self.an.robot(F())
        self.assertEqual(r["kpi"]["success_rate"], 1.0)
        self.assertEqual(r["grasp"]["rows"][0]["attempts"], 2)
        self.assertGreaterEqual(r["kpi"]["avg_pick_sec"], 0.0)
        self.assertEqual(r["motion_plan"]["fail_rate"], 1.0)
        self.assertEqual(r["place"]["status"], "not_collected")


if __name__ == "__main__":
    unittest.main()
