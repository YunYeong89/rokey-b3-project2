"""
고객 구매 이력 + 추천 반응 기록 (SQLite, data/runtime/customer_history.db)

개인화 추천(R)은 Instacart 고객 데이터가 아니라 **이 웹에서 실제로 주문한 고객의 이력**만 사용합니다.
(Instacart 는 전체 인기도와 상품 연관성(S, C) 계산에만 사용)

테이블
  purchases   고객별 구매 이력. 주문이 완료(로봇 done)된 경우에만 저장, 취소·실패는 저장 안 함
      customer_id, order_id, order_uid, product_id, quantity, purchased_at, order_status
      - order_uid : 주문 하나를 구분하는 값 (orders.json 을 지워 order_id 가 다시 1부터 시작해도 안 겹치게)
      - (order_uid, product_id) 가 기본키 -> 같은 주문을 두 번 기록해도 한 번만 저장
      - order_status : completed(로봇 담기 완료) -> picked_up(수령) / expired(안 가져감)
  rec_events  추천 노출·클릭·구매 기록 (추천 영역 분리 방식이 실제로 효과가 있는지 나중에 확인용)
      ts, customer_id, rec_id, slot(home/cart), algo(R/S/C/popular), product_id, rank, event, order_id

R 점수 = 이 고객이 그 상품을 산 주문 수 / 이 고객의 구매 완료 주문 수
  (한 주문에서 같은 상품을 여러 개 사도 1번으로 셈, expired 주문은 빼고 계산)

실행 (시연용 샘플 고객 만들기):
    python3 -m pickup.history seed           # user1, user2, user3 (로그인 계정) 에 샘플 구매 이력
    python3 -m pickup.history show user2     # 고객 이력·R 점수 보기
    python3 -m pickup.history events         # 추천 노출/클릭/구매 집계
"""
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

from .config import HISTORY_FILE as DB_FILE

COUNTED_STATUSES = ("completed", "picked_up")   # R 계산에 넣는 주문 상태
CUSTOMER_ID_RE = re.compile(r"^[0-9A-Za-z가-힣_-]{1,20}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    customer_id  TEXT    NOT NULL,
    order_id     INTEGER NOT NULL,
    order_uid    TEXT    NOT NULL,
    product_id   TEXT    NOT NULL,
    quantity     INTEGER NOT NULL,
    purchased_at REAL    NOT NULL,
    order_status TEXT    NOT NULL,
    PRIMARY KEY (order_uid, product_id)
);
CREATE INDEX IF NOT EXISTS idx_purchases_customer ON purchases(customer_id);
CREATE TABLE IF NOT EXISTS rec_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    customer_id TEXT,
    rec_id      TEXT,
    slot        TEXT NOT NULL,
    algo        TEXT,
    product_id  TEXT NOT NULL,
    rank        INTEGER,
    event       TEXT NOT NULL,
    order_id    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_slot ON rec_events(slot, algo, event);
"""


def clean_customer_id(cid):
    """고객번호 정리. 형식이 틀리거나 비어 있으면 None (= 비회원, 인기 상품 추천)"""
    cid = (cid or "").strip()
    return cid if CUSTOMER_ID_RE.match(cid) else None


class CustomerHistory:
    def __init__(self, path=DB_FILE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.executescript(SCHEMA)

    # ------------------------------------------------------------ 구매 이력
    def record_order(self, customer_id, order_id, order_uid, items, purchased_at=None, status="completed"):
        """완료된 주문 저장. 이미 저장된 주문이면 False (중복 방지)"""
        customer_id = clean_customer_id(customer_id)
        items = {pid: int(n) for pid, n in (items or {}).items() if int(n) > 0}
        if not customer_id or not items:
            return False
        ts = purchased_at or time.time()
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM purchases WHERE order_uid=? LIMIT 1", (order_uid,)).fetchone():
                return False
            self.db.executemany(
                "INSERT OR IGNORE INTO purchases VALUES (?,?,?,?,?,?,?)",
                [(customer_id, order_id, order_uid, pid, n, ts, status) for pid, n in items.items()])
        return True

    def set_status(self, order_uid, status):
        """수령(picked_up) / 만료(expired) 반영"""
        with self.lock, self.db:
            self.db.execute("UPDATE purchases SET order_status=? WHERE order_uid=?", (status, order_uid))

    def stats(self, customer_id):
        """R 계산용 요약 {"n_orders", "counts": {pid: 산 주문 수}, "last": {pid: 마지막 구매 시각}}"""
        customer_id = clean_customer_id(customer_id)
        empty = {"n_orders": 0, "counts": {}, "last": {}}
        if not customer_id:
            return empty
        marks = ",".join("?" * len(COUNTED_STATUSES))
        with self.lock:
            n = self.db.execute(
                f"SELECT COUNT(DISTINCT order_uid) FROM purchases WHERE customer_id=? AND order_status IN ({marks})",
                (customer_id, *COUNTED_STATUSES)).fetchone()[0]
            rows = self.db.execute(
                f"""SELECT product_id, COUNT(DISTINCT order_uid) AS n, MAX(purchased_at) AS last
                    FROM purchases WHERE customer_id=? AND order_status IN ({marks}) GROUP BY product_id""",
                (customer_id, *COUNTED_STATUSES)).fetchall()
        if not n:
            return empty
        return {"n_orders": n, "counts": {r["product_id"]: r["n"] for r in rows},
                "last": {r["product_id"]: r["last"] for r in rows}}

    def repurchase_scores(self, customer_id):
        """{pid: R} R = 그 상품을 산 주문 수 / 전체 구매 완료 주문 수"""
        s = self.stats(customer_id)
        return {pid: c / s["n_orders"] for pid, c in s["counts"].items()} if s["n_orders"] else {}

    def orders(self, customer_id, limit=20):
        """최근 주문 목록 (조회 API 용)"""
        customer_id = clean_customer_id(customer_id)
        if not customer_id:
            return []
        with self.lock:
            rows = self.db.execute(
                """SELECT order_id, order_uid, order_status, MAX(purchased_at) AS purchased_at,
                          GROUP_CONCAT(product_id || ':' || quantity) AS items
                   FROM purchases WHERE customer_id=? GROUP BY order_uid ORDER BY purchased_at DESC LIMIT ?""",
                (customer_id, limit)).fetchall()
        return [{"order_id": r["order_id"], "order_uid": r["order_uid"], "order_status": r["order_status"],
                 "purchased_at": r["purchased_at"],
                 "items": {k: int(v) for k, v in (x.split(":") for x in r["items"].split(","))}} for r in rows]

    # ------------------------------------------------------------ 추천 반응 기록
    def log_events(self, rows):
        """rows: [{"customer_id","rec_id","slot","algo","product_id","rank","event","order_id"}]"""
        if not rows:
            return
        now = time.time()
        with self.lock, self.db:
            self.db.executemany(
                "INSERT INTO rec_events (ts, customer_id, rec_id, slot, algo, product_id, rank, event, order_id)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                [(now, clean_customer_id(r.get("customer_id")), r.get("rec_id"), r["slot"], r.get("algo"),
                  r["product_id"], r.get("rank"), r["event"], r.get("order_id")) for r in rows])

    def event_summary(self):
        """slot·algo 별 노출 / 클릭 / 구매 수와 비율"""
        with self.lock:
            rows = self.db.execute(
                "SELECT slot, COALESCE(algo,'-') AS algo, event, COUNT(*) AS n FROM rec_events GROUP BY slot, algo, event"
            ).fetchall()
        out = {}
        for r in rows:
            d = out.setdefault((r["slot"], r["algo"]), {"slot": r["slot"], "algo": r["algo"],
                                                        "impression": 0, "click": 0, "purchase": 0})
            d[r["event"]] = r["n"]
        for d in out.values():
            d["ctr"] = round(d["click"] / d["impression"], 4) if d["impression"] else None
            d["purchase_rate"] = round(d["purchase"] / d["impression"], 4) if d["impression"] else None
        return sorted(out.values(), key=lambda d: (d["slot"], d["algo"]))


# ---------------------------------------------------------------- 시연용 샘플 고객
SEED = {
    # 고객 id(= 로그인 ID): [주문마다 산 상품]  -> user1·user2 는 우유·시리얼을 자주, user3 은 콜라·과자를 자주
    # (user2, user3 은 예전 샘플 고객번호 1234, 5678 의 이력을 옮긴 것)
    "user1": [{"milk_pack": 1, "cereal": 1}, {"milk_pack": 2, "bread": 1}, {"milk_pack": 1, "yogurt": 1, "cereal": 1}],
    "user2": [{"milk_pack": 1, "cereal": 1}, {"milk_pack": 2, "bread": 1}, {"milk_pack": 1, "yogurt": 1, "cereal": 1}],
    "user3": [{"cola": 2, "snack_bag": 1}, {"cola": 1, "cup_ramen": 1}, {"snack_bag": 1, "cola": 1, "nuts": 1}],
}


def seed(h):
    now = time.time()
    for cid, orders in SEED.items():
        for i, items in enumerate(orders):
            uid = f"seed-{cid}-{i}"
            ok = h.record_order(cid, -(i + 1), uid, items, purchased_at=now - (len(orders) - i) * 86400)
            print(f"{cid} 주문 {i + 1}: {items} {'저장' if ok else '이미 있음'}")


if __name__ == "__main__":
    h = CustomerHistory()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "seed":
        seed(h)
    elif cmd == "show" and len(sys.argv) > 2:
        cid = sys.argv[2]
        print("주문:", *h.orders(cid), sep="\n  ")
        print("R 점수:", dict(sorted(h.repurchase_scores(cid).items(), key=lambda kv: -kv[1])))
    elif cmd == "events":
        for d in h.event_summary():
            print(d)
    else:
        print(__doc__)
