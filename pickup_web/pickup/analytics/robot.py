"""로봇 작업 기록·분석

ROS2 토픽은 바꾸지 않음. 웹 서버가 이미 받는 /order_status 를 그대로 보고 robot_logs 에 남김 (RobotTracker)
  status       상태가 바뀔 때마다 (checking / need_choice / picking / done / cancelled / error)
  pick         상품 하나 집어 담기: picking(done=n, current=pid) 다음 done 이 n+1 이 되면 성공, 그 사이 error 면 실패
               소요 시간 = picking 메시지부터 다음 진행까지 (Pick & Place 합계)
  order        주문 하나 처리: 첫 상태부터 done / error / cancelled 까지 (손님 선택 대기 시간은 wait_sec 로 따로)
로봇이 지금 보내지 않는 값 (놓기 성공, MoveIt 2 경로 계획 실패, 가동·대기 시간)은 '미수집'으로 표시.
나중에 로봇 노드가 /order_status 에 아래처럼 event 를 덧붙이면 (선택 필드, 기존 필드는 그대로) 바로 집계됨
  "event": {"type": "place" | "motion_plan" | "pick", "product_id": "tuna_can", "success": false,
            "duration_sec": 3.2, "error_type": "PLANNING_FAILED"}
"""
import threading
import time
from collections import Counter, defaultdict
from statistics import mean

from .common import collecting, days_between, kst_date, not_collected
from .sales import pname

EVENT_KINDS = ("pick", "place", "motion_plan")


def classify_error(msg):
    m = (msg or "").lower()
    if "집기" in m or "grasp" in m or "pick" in m:
        return "grasp_fail"
    if "경로" in m or "plan" in m or "moveit" in m:
        return "motion_plan"
    if "놓기" in m or "place" in m:
        return "place_fail"
    if "카메라" in m or "vision" in m or "인식" in m:
        return "vision"
    return "other"


ERROR_NAME = {"grasp_fail": "집기 실패", "motion_plan": "경로 계획 실패", "place_fail": "놓기 실패",
              "vision": "인식 실패", "other": "기타"}


class RobotTracker:
    """handle_status 가 받은 상태를 robot_logs 로 (store.lock 안에서 호출되지만 자체 lock 도 가짐)"""

    def __init__(self, db):
        self.db = db
        self.lock = threading.Lock()
        self.jobs = {}   # order_id -> {"uid", "start", "pick": (pid, done, ts) | None, "wait", "wait_from", "last"}
        self.finished = {}   # order_id -> 끝난 상태 (같은 done / error 가 또 와도 다시 기록하지 않게)

    def on_status(self, st, order_uid=None, source=None, now=None):
        now = now or time.time()
        oid, state = st.get("order_id"), st.get("state")
        if oid is None or not state:
            return
        with self.lock:
            if state in ("done", "error", "cancelled") and oid in self.finished:
                return   # 이미 끝난 주문의 상태가 또 옴
            self.finished.pop(oid, None)   # 같은 번호로 새 주문이 시작됨 (orders.json 을 지운 경우 등)
            j = self.jobs.setdefault(oid, {"uid": order_uid, "start": now, "pick": None, "wait": 0.0,
                                           "wait_from": None, "last": None})
            j["uid"] = j["uid"] or order_uid
            key = (state, st.get("done"), st.get("current"))
            if key == j["last"]:
                return   # 같은 상태가 또 옴 (중복)
            log = lambda kind, **kw: self.db.log_robot(kind, ts=now, order_uid=j["uid"], order_id=oid,
                                                       source=source, **kw)
            if j["last"] is None or j["last"][0] != state:
                log("status", state=state, done=st.get("done"), total=st.get("total"), message=st.get("message"))
            j["last"] = key
            if j["wait_from"] is not None and state != "need_choice":
                j["wait"] += now - j["wait_from"]
                j["wait_from"] = None
            if state == "need_choice":
                j["wait_from"] = now

            pick = j["pick"]
            if state == "picking":
                if pick and (st.get("done") or 0) > pick[1]:
                    log("pick", product_id=pick[0], success=1, duration_sec=round(now - pick[2], 3))
                    pick = None
                if pick is None or pick[0] != st.get("current"):
                    pick = (st.get("current"), st.get("done") or 0, now)
                j["pick"] = pick
            elif state in ("done", "error", "cancelled"):
                if pick and state == "done":
                    log("pick", product_id=pick[0], success=1, duration_sec=round(now - pick[2], 3))
                elif pick and state == "error":
                    log("pick", product_id=pick[0], success=0, duration_sec=round(now - pick[2], 3),
                        error_type=classify_error(st.get("message")), message=st.get("message"))
                log("order", state=state, done=st.get("done"), total=st.get("total"),
                    success=1 if state == "done" else 0 if state == "error" else None,
                    duration_sec=round(now - j["start"], 3), wait_sec=round(j["wait"], 3),
                    error_type=classify_error(st.get("message")) if state == "error" else None,
                    message=st.get("message"))
                self.jobs.pop(oid, None)
                self.finished[oid] = state
                if len(self.finished) > 1000:
                    self.finished.pop(next(iter(self.finished)))
            # 선택 필드: 로봇이 직접 보내는 세부 결과
            evs = st.get("event")
            for ev in (evs if isinstance(evs, list) else [evs] if isinstance(evs, dict) else []):
                if ev.get("type") in EVENT_KINDS:
                    s = ev.get("success")
                    log(ev["type"], product_id=ev.get("product_id"), success=None if s is None else int(bool(s)),
                        duration_sec=ev.get("duration_sec"), error_type=ev.get("error_type"), message=ev.get("message"))


def _rate(ok, n):
    return round(ok / n, 4) if n else None


def robot(db, f):
    first = db.query("SELECT MIN(ts) AS t FROM robot_logs")[0]["t"]
    s_ts, e_ts, s, e = f.bounds(first)
    src, args = f.source_sql()
    rows = db.query(f"SELECT * FROM robot_logs WHERE ts >= ? AND ts < ?{src} ORDER BY ts", (s_ts, e_ts, *args))
    kinds = defaultdict(list)
    for r in rows:
        kinds[r["kind"]].append(r)
    orders = kinds["order"]
    out = {"period": {"label": f.label(s, e), "start": s, "end": e}, "empty": not orders}

    done = [r for r in orders if r["state"] == "done"]
    err = [r for r in orders if r["state"] == "error"]
    canc = [r for r in orders if r["state"] == "cancelled"]
    out["kpi"] = {"tasks": len(orders), "done": len(done), "error": len(err), "cancelled": len(canc),
                  "success_rate": _rate(len(done), len(done) + len(err)),
                  "avg_order_sec": round(mean(r["duration_sec"] for r in done), 2) if done else None,
                  "avg_robot_sec": round(mean(r["duration_sec"] - (r["wait_sec"] or 0) for r in done), 2) if done else None,
                  "avg_pick_sec": None, "busy_sec": round(sum(r["duration_sec"] or 0 for r in orders), 1),
                  "errors": len(err) + sum(1 for k in EVENT_KINDS for r in kinds[k] if r["success"] == 0
                                           and k != "pick")}

    picks = kinds["pick"]
    ok_picks = [r for r in picks if r["success"] == 1 and r["duration_sec"] is not None]
    out["kpi"]["avg_pick_sec"] = round(mean(r["duration_sec"] for r in ok_picks), 2) if ok_picks else None
    by_p = defaultdict(lambda: [0, 0, []])
    for r in picks:
        b = by_p[r["product_id"]]
        b[0] += 1
        b[1] += r["success"] == 1
        if r["success"] == 1 and r["duration_sec"] is not None:
            b[2].append(r["duration_sec"])
    out["grasp"] = ({"status": "ok", "rows": sorted(({"id": pid, "name": pname(pid), "attempts": n, "success": ok,
                                                      "rate": _rate(ok, n), "avg_sec": round(mean(d), 2) if d else None}
                                                     for pid, (n, ok, d) in by_p.items()), key=lambda x: -x["attempts"])}
                    if picks else collecting("집기 기록이 없어요 (주문을 로봇이 처리하면 쌓여요)"))

    def detail(kind, name):
        rs = kinds[kind]
        if not rs:
            return not_collected(f"로봇이 {name} 결과를 보내지 않아요 (/order_status 의 event 필드로 연동 가능)")
        by = defaultdict(lambda: [0, 0])
        for r in rs:
            by[r["product_id"]][0] += 1
            by[r["product_id"]][1] += r["success"] == 1
        n, ok = len(rs), sum(1 for r in rs if r["success"] == 1)
        return {"status": "ok", "attempts": n, "success": ok, "rate": _rate(ok, n), "fail_rate": _rate(n - ok, n),
                "rows": [{"id": pid, "name": pname(pid) if pid else "-", "attempts": a, "rate": _rate(o, a)}
                         for pid, (a, o) in by.items()]}

    out["place"] = detail("place", "놓기(배치)")
    out["motion_plan"] = detail("motion_plan", "MoveIt 2 경로 계획")
    etypes = Counter(r["error_type"] or "other" for r in err)
    etypes.update(r["error_type"] or "other" for k in ("place", "motion_plan") for r in kinds[k] if r["success"] == 0)
    out["error_types"] = [{"type": t, "name": ERROR_NAME.get(t, t), "count": c} for t, c in etypes.most_common()]
    out["uptime"] = not_collected("로봇 가동·대기 시간은 로봇이 보내지 않아요. 주문 처리 시간 합계(작업 시간)만 표시")

    days = days_between(s, e)
    tasks, succ = Counter(), Counter()
    for r in orders:
        d = kst_date(r["ts"])
        tasks[d] += 1
        succ[d] += r["state"] == "done"
    out["daily"] = {"labels": days, "tasks": [tasks[d] for d in days], "done": [succ[d] for d in days]}
    out["recent_errors"] = [{"ts": r["ts"], "order_id": r["order_id"], "type": ERROR_NAME.get(r["error_type"], "기타"),
                             "message": r["message"]} for r in err[-10:]][::-1]
    return out


def success_rate(db, f):
    src, args = f.source_sql()
    r = db.query(f"SELECT SUM(state='done') AS d, SUM(state='error') AS e FROM robot_logs WHERE kind='order'{src}",
                 args)[0]
    d, e = r["d"] or 0, r["e"] or 0
    return _rate(d, d + e)
