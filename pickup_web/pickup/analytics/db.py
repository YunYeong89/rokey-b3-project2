"""분석용 기록 (SQLite). 고객 구매 이력과 같은 파일 data/runtime/customer_history.db 에 테이블만 추가

기존 테이블은 그대로 재사용
  purchases   회원 구매 이력 (R 추천용, pickup/history.py)  -> 분석은 아래 orders / order_items 를 씀 (비회원 주문 포함)
  rec_events  추천 노출 / 클릭 / 구매 (pickup/history.py)    -> 추천 성과 분석이 그대로 읽음

추가 테이블 (모두 시각은 유닉스 초, 화면 표시만 한국 시간)
  orders                 주문 1건 = 1행. order_uid 가 기본키라 같은 완료 이벤트가 두 번 와도 한 번만 집계
                         status: in_progress / completed / partial / cancelled / error
                         pickup_status: ready_for_pickup / picked_up / expired (완료된 주문만)
                         source: robot(실제 로봇) / mock(데모 로봇) -> 화면에서 나눠 볼 수 있음
  order_items            주문 상품. requested_qty(주문 수량) / sold_qty(실제로 담긴 수량) / unit_price(주문 시점 가격)
                         rec_slot, rec_algo, rec_id: 추천을 눌러 담은 상품이면 그 추천 정보
  inventory_transactions 재고 변경 (sale 판매 / move 창고->진열 / receive 입고 / adjust 수정 / snapshot 시작 시 기록)
  inventory_history      상품별 하루 마지막 재고 (date, product_id 기본키)
  stockout_events        품절·재고 부족 (shortage: 손님이 원한 만큼 없음 / shelf_empty: 판매 후 진열 0개)
  forecast_history       수요 예측 결과 (상품, 예측 대상일, 예측 수량, 모델, 모델 버전)
  packing_logs           포장 계획·실행 결과 (Packing Planning 모듈 연동용, 지금은 인터페이스만)
  robot_logs             로봇 상태 변화(kind=status) + 상품 집기(pick) / 놓기(place) / 경로 계획(motion_plan) / 주문(order) 결과

모든 기록 함수는 실패해도 예외를 밖으로 내지 않음 (분석 때문에 주문·로봇이 멈추면 안 됨) -> AnalyticsDB.safe()
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

from .common import kst_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_uid     TEXT PRIMARY KEY,
    order_id      INTEGER NOT NULL,
    customer_id   TEXT,
    source        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    finished_at   REAL,
    status        TEXT NOT NULL,
    pickup_status TEXT,
    requested_qty INTEGER NOT NULL DEFAULT 0,
    sold_qty      INTEGER NOT NULL DEFAULT 0,
    amount        INTEGER NOT NULL DEFAULT 0,
    message       TEXT,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE TABLE IF NOT EXISTS order_items (
    order_uid     TEXT NOT NULL,
    product_id    TEXT NOT NULL,
    requested_qty INTEGER NOT NULL DEFAULT 0,
    sold_qty      INTEGER NOT NULL DEFAULT 0,
    unit_price    INTEGER NOT NULL,
    handled_by    TEXT,
    rec_slot      TEXT,
    rec_algo      TEXT,
    rec_id        TEXT,
    PRIMARY KEY (order_uid, product_id)
);
CREATE TABLE IF NOT EXISTS inventory_transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    product_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    shelf_change  INTEGER NOT NULL DEFAULT 0,
    wh_change     INTEGER NOT NULL DEFAULT 0,
    stock_after   INTEGER,
    wh_after      INTEGER,
    order_uid     TEXT,
    source        TEXT,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_invtx_ts ON inventory_transactions(ts);
CREATE TABLE IF NOT EXISTS inventory_history (
    date          TEXT NOT NULL,
    product_id    TEXT NOT NULL,
    stock         INTEGER NOT NULL,
    warehouse     INTEGER NOT NULL,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (date, product_id)
);
CREATE TABLE IF NOT EXISTS stockout_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    product_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    unmet_qty     INTEGER NOT NULL DEFAULT 0,
    order_uid     TEXT,
    source        TEXT
);
CREATE TABLE IF NOT EXISTS forecast_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    REAL NOT NULL,
    product_id    TEXT NOT NULL,
    target_date   TEXT NOT NULL,
    qty           REAL NOT NULL,
    model         TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    UNIQUE (product_id, target_date, model, model_version)
);
CREATE TABLE IF NOT EXISTS packing_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL NOT NULL,
    order_uid         TEXT,
    order_id          INTEGER,
    planner           TEXT NOT NULL,
    input_mode        TEXT,
    plan_generated    INTEGER,
    validation_passed INTEGER,
    gen_seconds       REAL,
    total_tokens      INTEGER,
    cost_usd          REAL,
    regenerations     INTEGER,
    pack_seconds      REAL,
    robot_success     INTEGER,
    failure_reason    TEXT,
    damaged_items     TEXT,
    n_items           INTEGER,
    plan_json         TEXT,
    source            TEXT
);
CREATE TABLE IF NOT EXISTS robot_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    kind          TEXT NOT NULL,
    order_uid     TEXT,
    order_id      INTEGER,
    state         TEXT,
    product_id    TEXT,
    done          INTEGER,
    total         INTEGER,
    success       INTEGER,
    duration_sec  REAL,
    wait_sec      REAL,
    error_type    TEXT,
    message       TEXT,
    source        TEXT
);
CREATE INDEX IF NOT EXISTS idx_robot_ts ON robot_logs(ts, kind);
"""

SALE_STATUSES = ("completed", "partial")      # 매출·판매량에 넣는 주문 상태 (만료 = 안 가져감은 뺌, sales.py)


class AnalyticsDB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        # 같은 파일을 CustomerHistory 도 열고 있으므로 잠깐 잠겨 있으면 기다림 (timeout)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.executescript(SCHEMA)
        self.version = 0          # 쓸 때마다 +1 -> 대시보드 캐시·자동 갱신 기준
        self.started = time.time()

    # ------------------------------------------------------------ 공통
    def _write(self, sql, rows=None, many=False):
        with self.lock, self.db:
            cur = self.db.executemany(sql, rows) if many else self.db.execute(sql, rows or ())
            self.version += 1
            return cur.rowcount

    def query(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def tag(self):
        """대시보드 갱신 확인용 값 (서버를 다시 켜도 바뀌게 시작 시각 포함)"""
        return f"{int(self.started)}-{self.version}"

    # ------------------------------------------------------------ 주문
    def order_created(self, o, prices, source, created_at=None, note=None):
        """주문 접수 (in_progress). prices = {pid: 주문 시점 가격}. 이미 있으면 그대로 (중복 방지)"""
        uid = o["uid"]
        items = {pid: int(n) for pid, n in (o.get("items") or {}).items() if int(n) > 0}
        attrib = o.get("rec_attrib") or {}
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM orders WHERE order_uid=?", (uid,)).fetchone():
                return False
            self.db.execute(
                "INSERT INTO orders (order_uid, order_id, customer_id, source, created_at, status, requested_qty, note)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (uid, o["order_id"], o.get("customer_id"), source, created_at or o.get("created_at") or time.time(),
                 "in_progress", sum(items.values()), note))
            self.db.executemany(
                "INSERT OR IGNORE INTO order_items (order_uid, product_id, requested_qty, sold_qty, unit_price,"
                " rec_slot, rec_algo, rec_id) VALUES (?,?,?,0,?,?,?,?)",
                [(uid, pid, n, int(prices.get(pid, 0)), (attrib.get(pid) or {}).get("slot"),
                  (attrib.get(pid) or {}).get("algo"), (attrib.get(pid) or {}).get("rec_id"))
                 for pid, n in items.items()])
            self.version += 1
        return True

    def order_done(self, uid, picked, staff_items, prices, finished_at=None):
        """로봇 done: 실제로 담긴 수량 반영. 이미 완료 처리된 주문이면 False (같은 done 이 또 와도 판매량 그대로)
        주문 수량보다 적게 담겼으면 partial (대체 상품은 requested_qty=0 인 행으로 추가)"""
        sold = {}
        for src, handled in ((picked or {}, "robot"), (staff_items or {}, "staff")):
            for pid, n in src.items():
                if int(n) > 0:
                    sold[pid] = (sold.get(pid, (0, handled))[0] + int(n), handled)
        with self.lock, self.db:
            row = self.db.execute("SELECT status FROM orders WHERE order_uid=?", (uid,)).fetchone()
            if row is None or row["status"] != "in_progress":
                return False
            req = {r["product_id"]: r for r in self.db.execute(
                "SELECT product_id, requested_qty, unit_price FROM order_items WHERE order_uid=?", (uid,))}
            for pid, (n, handled) in sold.items():
                if pid in req:
                    self.db.execute("UPDATE order_items SET sold_qty=?, handled_by=? WHERE order_uid=? AND product_id=?",
                                    (n, handled, uid, pid))
                else:  # 대체 상품: 주문 시점 가격이 없으므로 지금 가격
                    self.db.execute(
                        "INSERT OR IGNORE INTO order_items (order_uid, product_id, requested_qty, sold_qty, unit_price,"
                        " handled_by) VALUES (?,?,0,?,?,?)", (uid, pid, n, int(prices.get(pid, 0)), handled))
            partial = any(sold.get(pid, (0,))[0] < r["requested_qty"] for pid, r in req.items())
            amount = self.db.execute("SELECT COALESCE(SUM(sold_qty*unit_price),0) FROM order_items WHERE order_uid=?",
                                     (uid,)).fetchone()[0]
            self.db.execute(
                "UPDATE orders SET status=?, finished_at=?, sold_qty=?, amount=?, pickup_status='ready_for_pickup'"
                " WHERE order_uid=?",
                ("partial" if partial else "completed", finished_at or time.time(),
                 sum(n for n, _ in sold.values()), amount, uid))
            self.version += 1
        return True

    def order_failed(self, uid, status, message="", finished_at=None):
        """취소·오류: 판매 수량 0 (in_progress 일 때만 바꿈)"""
        return self._write("UPDATE orders SET status=?, message=?, finished_at=? WHERE order_uid=? AND status='in_progress'",
                           (status, message, finished_at or time.time(), uid))

    def pickup_status(self, uid, status):
        return self._write("UPDATE orders SET pickup_status=? WHERE order_uid=? AND status IN ('completed','partial')",
                           (status, uid))

    def order_row(self, uid):
        r = self.query("SELECT * FROM orders WHERE order_uid=?", (uid,))
        return r[0] if r else None

    # ------------------------------------------------------------ 재고
    def inventory_change(self, pid, kind, shelf_change, wh_change, stock_after, wh_after, order_uid=None,
                         source=None, note=None, ts=None):
        ts = ts or time.time()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO inventory_transactions (ts, product_id, kind, shelf_change, wh_change, stock_after, wh_after,"
                " order_uid, source, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, pid, kind, shelf_change, wh_change, stock_after, wh_after, order_uid, source, note))
            self.db.execute("INSERT OR REPLACE INTO inventory_history VALUES (?,?,?,?,?)",
                            (kst_date(ts), pid, stock_after, wh_after, ts))
            self.version += 1

    def snapshot(self, products, source=None):
        """서버 시작 시 오늘 재고 기록 (오늘 기록이 이미 있는 상품은 건너뜀)"""
        ts = time.time()
        today = kst_date(ts)
        with self.lock, self.db:
            have = {r[0] for r in self.db.execute("SELECT product_id FROM inventory_history WHERE date=?", (today,))}
            for p in products:
                if p["id"] not in have:
                    self.db.execute("INSERT INTO inventory_history VALUES (?,?,?,?,?)",
                                    (today, p["id"], p["stock"], p["warehouse"], ts))
            self.version += 1

    def stockout(self, pid, kind, unmet_qty=0, order_uid=None, source=None):
        return self._write("INSERT INTO stockout_events (ts, product_id, kind, unmet_qty, order_uid, source)"
                           " VALUES (?,?,?,?,?,?)", (time.time(), pid, kind, unmet_qty, order_uid, source))

    # ------------------------------------------------------------ 수요 예측
    def save_forecasts(self, rows):
        """rows: [{"product_id", "target_date": "YYYY-MM-DD", "qty", "model", "model_version"}]
        외부 예측 모듈(M5 연구 코드 등)도 이 함수로 결과를 넣으면 재고 탭에 바로 반영됨"""
        now = time.time()
        return self._write(
            "INSERT OR REPLACE INTO forecast_history (created_at, product_id, target_date, qty, model, model_version)"
            " VALUES (?,?,?,?,?,?)",
            [(now, r["product_id"], r["target_date"], float(r["qty"]), r["model"], r.get("model_version") or "")
             for r in rows], many=True)

    # ------------------------------------------------------------ 포장
    PACKING_FIELDS = ("order_uid", "order_id", "planner", "input_mode", "plan_generated", "validation_passed",
                      "gen_seconds", "total_tokens", "cost_usd", "regenerations", "pack_seconds", "robot_success",
                      "failure_reason", "damaged_items", "n_items", "plan_json", "source")

    def log_packing(self, **kw):
        """Packing Planning 모듈이 계획을 만들거나 로봇이 포장을 마쳤을 때 호출. planner 는 rule / llm / rule_guided_llm
        validation_passed(계획 검증 통과)와 robot_success(실제 포장 성공)는 서로 다른 값으로 따로 기록"""
        if not kw.get("planner"):
            raise ValueError("planner 가 필요해요")
        for k in ("plan_json", "damaged_items"):
            if k in kw and not isinstance(kw[k], (str, type(None))):
                kw[k] = json.dumps(kw[k], ensure_ascii=False)
        cols = [k for k in self.PACKING_FIELDS if k in kw]
        return self._write(f"INSERT INTO packing_logs (ts, {', '.join(cols)}) VALUES (?{',?' * len(cols)})",
                           (kw.get("ts") or time.time(), *[kw[k] for k in cols]))

    # ------------------------------------------------------------ 로봇
    ROBOT_FIELDS = ("order_uid", "order_id", "state", "product_id", "done", "total", "success", "duration_sec",
                    "wait_sec", "error_type", "message", "source")

    def log_robot(self, kind, ts=None, **kw):
        cols = [k for k in self.ROBOT_FIELDS if k in kw]
        return self._write(f"INSERT INTO robot_logs (ts, kind{''.join(', ' + c for c in cols)}) VALUES (?,?{',?' * len(cols)})",
                           (ts or time.time(), kind, *[kw[k] for k in cols]))
