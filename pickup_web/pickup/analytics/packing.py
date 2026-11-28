"""포장 계획 분석

1) 실제 운영 결과   packing_logs 테이블 (Packing Planning 모듈이 AnalyticsDB.log_packing() 으로 기록)
                    아직 웹 서버와 연결된 포장 모듈이 없어 지금은 비어 있음 -> '미수집'
2) 오프라인 실험    packing_planning/results/<실행>/comparison.csv 를 그대로 읽음 (값을 만들거나 고치지 않음)
                    v1: Validator 로 검증한 결과 / v2: 검증 없이 LLM 응답만 저장 -> 검증 통과율은 '검증 안 함'
계획 검증 통과율(validation_passed)과 실제 로봇 포장 성공률(robot_success)은 다른 지표로 따로 계산
실험하지 않은 Planner(Rule-based, Rule-guided LLM)는 '실험 결과 없음'으로만 표시
"""
import csv
import json
from collections import Counter, defaultdict
from statistics import mean

from ..config import PACKING_RESULTS_DIR
from .common import collecting, not_collected

PLANNERS = [("rule", "Rule-based Planner"), ("llm", "LLM-based Planner"), ("rule_guided_llm", "Rule-guided LLM Planner")]
MODES = [("Text-only", "text_only"), ("Text + Vision", "text_vision"), ("Text + Physical Properties", "text_properties")]
VIOLATION_COLS = {"n_missing": "상품 누락", "n_unknown_item": "모르는 상품", "n_duplicate": "중복 배치",
                  "n_boundary": "공간 경계 초과", "n_collision": "상품 충돌", "n_orientation": "자세 오류",
                  "n_stack_forbidden": "적재 금지 위반", "n_load_exceeded": "하중 초과",
                  "n_support_insufficient": "지지면 부족", "n_stability": "무게중심 불안정",
                  "n_layer_limit": "층수 초과", "n_sequence": "담는 순서 오류"}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _bool(x):
    return {"true": True, "false": False}.get(str(x).strip().lower())


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(mean(xs), 4) if xs else None


def _rate(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(1 for x in xs if x) / len(xs), 4) if xs else None


def offline_results(root=PACKING_RESULTS_DIR):
    """comparison.csv 들을 읽어 실행별 / 입력 방식별 요약"""
    if not root.is_dir():
        return collecting(f"오프라인 실험 결과 폴더가 없어요 ({root.name}). packing_planning 브랜치를 합치거나 "
                          "PACKING_RESULTS_DIR 환경 변수로 위치를 알려 주세요")
    runs = []
    for f in sorted(root.glob("*/comparison.csv")):
        with f.open(encoding="utf-8-sig", newline="") as fp:
            rows = list(csv.DictReader(fp))
        if not rows:
            continue
        validated = "valid" in rows[0]
        runs.append({"run": f.parent.name, "version": "v2" if f.parent.name.startswith("v2_") else "v1",
                     "validated": validated, "model": rows[0].get("response_model"),
                     "rows": [{"mode": r.get("label"), "experiment": r.get("experiment"),
                               "status": r.get("run_status"),
                               "generated": _bool(r.get("plan_generated")) if "plan_generated" in r
                               else r.get("plan_status") == "planned",
                               "valid": _bool(r.get("valid")) if validated else None,
                               "violations": _num(r.get("total_violations")) if validated else None,
                               "seconds": _num(r.get("seconds")), "tokens": _num(r.get("total_tokens")),
                               "cost_usd": _num(r.get("estimated_cost_usd")),
                               "causes": {k: int(_num(r.get(k)) or 0) for k in VIOLATION_COLS if k in r}}
                              for r in rows]})
    if not runs:
        return collecting("comparison.csv 가 있는 실험 결과가 없어요")

    by_mode = []
    for label, key in MODES:
        rs = [(run, r) for run in runs for r in run["rows"] if r["mode"] == label]
        if not rs:
            continue
        val = [r for run, r in rs if run["validated"]]
        causes = Counter()
        for r in val:
            causes.update(r["causes"])
        by_mode.append({"mode": label, "key": key, "runs": len(rs), "validated_runs": len(val),
                        "generation_rate": _rate([r["generated"] for _, r in rs]),
                        "validation_rate": _rate([r["valid"] for r in val]) if val else None,
                        "avg_violations": _avg([r["violations"] for r in val]),
                        "avg_seconds": _avg([r["seconds"] for _, r in rs]),
                        "avg_tokens": _avg([r["tokens"] for _, r in rs]),
                        "avg_cost_usd": _avg([r["cost_usd"] for _, r in rs]),
                        "causes": {VIOLATION_COLS[k]: v for k, v in causes.items() if v}})
    return {"status": "ok", "source": str(root), "runs": runs, "by_mode": by_mode,
            "models": sorted({r["model"] for r in runs if r["model"]})}


def packing(db, f):
    s_ts, e_ts, s, e = f.bounds(db.query("SELECT MIN(ts) AS t FROM packing_logs")[0]["t"])
    src, args = f.source_sql()
    logs = db.query(f"SELECT * FROM packing_logs WHERE ts >= ? AND ts < ?{src}", (s_ts, e_ts, *args))
    offline = offline_results()
    planners = []
    for key, name in PLANNERS:
        live = [r for r in logs if r["planner"] == key]
        has_offline = key == "llm" and offline.get("status") == "ok"
        planners.append({"key": key, "name": name, "live_count": len(live),
                         "offline": "있음" if has_offline else "실험 결과 없음"})

    if not logs:
        live = not_collected("웹 서버와 연결된 포장 모듈이 아직 없어요. Packing Planning 모듈이 "
                             "AnalyticsDB.log_packing() 으로 기록하면 여기에 표시돼요")
    else:
        by_n = defaultdict(list)
        damage, fails = Counter(), Counter()
        for r in logs:
            if r["n_items"] and r["pack_seconds"] is not None:
                by_n[r["n_items"]].append(r["pack_seconds"])
            if r["failure_reason"]:
                fails[r["failure_reason"]] += 1
            if r["damaged_items"]:
                try:
                    damage.update(json.loads(r["damaged_items"]))
                except ValueError:
                    pass
        live = {"status": "ok", "count": len(logs), "by_planner": [{
            "planner": key, "name": name, "count": len(rs),
            "generation_rate": _rate([r["plan_generated"] for r in rs]),
            "validation_rate": _rate([r["validation_passed"] for r in rs]),
            "robot_success_rate": _rate([r["robot_success"] for r in rs]),
            "avg_gen_seconds": _avg([r["gen_seconds"] for r in rs]),
            "avg_tokens": _avg([r["total_tokens"] for r in rs]),
            "avg_cost_usd": _avg([r["cost_usd"] for r in rs]),
            "avg_regenerations": _avg([r["regenerations"] for r in rs]),
            "avg_pack_seconds": _avg([r["pack_seconds"] for r in rs])}
            for key, name in PLANNERS if (rs := [r for r in logs if r["planner"] == key])],
            "pack_time_by_items": [{"n_items": n, "avg_seconds": _avg(v), "count": len(v)} for n, v in sorted(by_n.items())],
            "failure_reasons": dict(fails.most_common()), "damaged": dict(damage.most_common())}
    return {"period": {"label": f.label(s, e)}, "planners": planners, "live": live, "offline": offline}


def avg_pack_seconds(db, f):
    src, args = f.source_sql()
    r = db.query(f"SELECT AVG(pack_seconds) AS a, COUNT(pack_seconds) AS n FROM packing_logs WHERE 1=1{src}", args)[0]
    return round(r["a"], 1) if r["n"] else None
