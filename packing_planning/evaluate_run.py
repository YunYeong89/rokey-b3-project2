"""저장된 실험 결과 디렉터리를 (재)평가하고 comparison.csv, 시각화, experiment_report.md 를 생성한다.

API 를 다시 호출하지 않으며 LLM 이 생성한 plan 과 raw_responses 는 수정하지 않는다.
  .venv/bin/python evaluate_run.py results/<실행일시>

평가는 두 가지:
  - evaluation (주 평가): product_properties.json 의 치수·특성으로 검증. 세 실험 동일 기준.
  - self_consistency (보조): 치수만 LLM 이 스스로 가정한 assumed_dimensions_mm 으로 바꿔 검증.
    → '치수 지식 부족' 과 '기하 배치 추론 오류' 를 분리해 보기 위한 참고 지표이며 유효성 판정에 쓰지 않는다.
"""
import copy, csv, json, sys
from pathlib import Path

from packing_validator import validate, CATEGORIES, WARNINGS, NOT_IMPLEMENTED

ROOT = Path(__file__).resolve().parent
DB = json.loads((ROOT / 'data' / 'product_properties.json').read_text(encoding='utf-8'))
ORDER = ['experiment_1_text_only', 'experiment_2_text_vision', 'experiment_3_text_properties']


def self_consistency_db(plan):
    db = copy.deepcopy(DB)
    for s in plan['steps']:
        pid = s['item_id'].rsplit('_', 1)[0]
        a = s['assumed_dimensions_mm']
        if pid in db and min(a['width'], a['depth'], a['height']) > 0:
            db[pid]['dimensions_mm'] = [a['width'], a['depth'], a['height']]
    return db


def load_rows(run):
    rows = []
    for f in sorted(run.glob('experiment_*.json')):
        r = json.loads(f.read_text(encoding='utf-8')); r['_file'] = f; rows.append(r)
    rows.sort(key=lambda r: (r.get('repeat', 1), ORDER.index(r['experiment'])))
    return rows


def evaluate(run):
    cfg = json.loads((run / 'config.json').read_text(encoding='utf-8'))
    items, space = cfg['items'], cfg['packing_space']
    rows = load_rows(run)
    for r in rows:
        if r.get('run_status') != 'ok':
            continue
        plan = r.get('plan')
        r['evaluation'] = validate(plan, items, DB, space)
        if plan and plan.get('status') == 'planned':
            sc = validate(plan, items, self_consistency_db(plan), space)
            r['self_consistency'] = {'valid': sc['valid'], 'violation_counts': sc['violation_counts'],
                                     'violations': sc['violations']}
        f = r.pop('_file')
        f.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding='utf-8')
        r['_file'] = f
    return cfg, rows


def write_csv(run, rows):
    cols = ['experiment', 'label', 'repeat', 'run_status', 'response_model', 'plan_generated', 'plan_status', 'valid',
            'total_violations'] + [f'n_{c}' for c, _ in CATEGORIES] + [f'w_{c}' for c, _ in WARNINGS] + \
           ['stability_ok', 'max_height_mm', 'layers_used', 'unsupported_items', 'floor_items', 'stacked_items',
            'used_bbox_mm', 'used_bbox_volume_cm3', 'self_consistency_valid', 'self_consistency_violations',
            'seconds', 'input_tokens', 'output_tokens', 'total_tokens', 'estimated_cost_usd', 'note']
    with (run / 'comparison.csv').open('w', newline='', encoding='utf-8-sig') as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for r in rows:
            ev = r.get('evaluation') or {}; m = ev.get('metrics') or {}; vc = ev.get('violation_counts') or {}
            sc = r.get('self_consistency') or {}
            row = {k: r.get(k, '') for k in ('experiment', 'label', 'repeat', 'run_status', 'response_model', 'seconds',
                                              'input_tokens', 'output_tokens', 'total_tokens', 'estimated_cost_usd', 'note')}
            row.update(plan_generated=ev.get('plan_generated', ''), plan_status=(r.get('plan') or {}).get('status', ''),
                       valid=ev.get('valid', ''), total_violations=sum(vc.values()) if vc else '',
                       stability_ok=(vc.get('stability', 0) == 0 and vc.get('support_insufficient', 0) == 0) if vc else '',
                       self_consistency_valid=sc.get('valid', ''),
                       self_consistency_violations=sum(sc['violation_counts'].values()) if sc else '')
            for c, _ in CATEGORIES:
                row[f'n_{c}'] = vc.get(c, '')
            for c, _ in WARNINGS:
                row[f'w_{c}'] = len((ev.get('warnings') or {}).get(c, [])) if ev else ''
            for k in ('max_height_mm', 'layers_used', 'unsupported_items', 'floor_items', 'stacked_items',
                      'used_bbox_mm', 'used_bbox_volume_cm3'):
                row[k] = m.get(k, '')
            if r.get('run_status') == 'api_error':
                row['note'] = f"{r.get('error_type')}: {r.get('error')}"[:300]
            wr.writerow(row)


def _fmt(x):
    return '–' if x is None or x == '' else x


def write_report(run, cfg, rows):
    L = []
    ok = [r for r in rows if r.get('run_status') == 'ok']
    models = sorted({r.get('response_model') for r in ok if r.get('response_model')})
    L += [f'# Packing Planning 비교 실험 보고서 — {run.name}', '',
          '> 자동 생성(evaluate_run.py). 수치는 저장된 원본 응답과 독립 Validator 결과에서 계산. '
          '상품 치수·무게·허용 하중은 **개발용 근사값**이며 검증 결과가 실제 물리적 안정성을 보장하지 않는다.', '',
          '## 1. 실험 조건', '',
          f"- 요청 모델: `{cfg['model_requested']}` / 실제 응답 모델: {', '.join(f'`{m}`' for m in models) or '–'}",
          f"- 생성 설정: `{json.dumps(cfg['generation_settings'])}` (Structured Outputs `json_schema`, strict=true, store=false, 자동 재시도 0회)",
          f"- 주문 `{cfg['order_id']}`: " + ', '.join(f"{l['display_name']}×{l['quantity']}" for l in cfg['order']['items'])
          + f" (총 {len(cfg['items'])}개)",
          f"- item_id: {', '.join('`'+i['item_id']+'`' for i in cfg['items'])}",
          f"- 포장 공간: {cfg['packing_space']['size_mm']} mm (x,y,안전 적재 높이), 벽 여유 {cfg['packing_space']['wall_clearance_mm']} mm, 최대 {cfg['packing_space']['max_layers']}층",
          f"- 사진: {('`'+Path(cfg['image']['path']).name+'` sha256 '+cfg['image']['sha256'][:12]+'…') if cfg.get('image') else '없음'}",
          f"- 반복: {cfg['repeats']}회", '']
    L += ['| 실험 | 입력 정보 | 실행 상태 |', '|---|---|---|']
    desc = {'text_only': '상품명·수량·공간', 'text_vision': '상품명·수량·공간 + 실제 사진',
            'text_properties': '상품명·수량·공간 + 주문 상품 물리 특성(6종)'}
    for r in rows:
        L.append(f"| {r['label']} (r{r.get('repeat', 1)}) | {desc[r['mode']]} | {r['run_status']}"
                 + (f" — {r.get('note') or r.get('error', '')}" if r['run_status'] != 'ok' else '') + ' |')
    L.append('')

    L += ['## 2. 평가 지표 비교', '']
    hdr = ['지표'] + [f"{r['label']} r{r.get('repeat', 1)}" for r in rows]
    L += ['| ' + ' | '.join(hdr) + ' |', '|' + '---|' * len(hdr)]
    def line(name, fn):
        vals = []
        for r in rows:
            try:
                vals.append(str(_fmt(fn(r))) if r.get('run_status') == 'ok' else '미실행' if r['run_status'] == 'not_run' else '오류')
            except Exception:
                vals.append('–')
        L.append(f'| {name} | ' + ' | '.join(vals) + ' |')
    E = lambda r: r['evaluation']; C = lambda r, c: r['evaluation']['violation_counts'][c]; M = lambda r, k: r['evaluation']['metrics'].get(k)
    line('계획 생성 성공(status=planned)', lambda r: E(r)['plan_generated'])
    line('계획 유효(위반 0건)', lambda r: E(r)['valid'])
    line('총 위반 건수', lambda r: sum(E(r)['violation_counts'].values()))
    line('상품 누락', lambda r: C(r, 'missing'))
    line('중복 배치', lambda r: C(r, 'duplicate'))
    line('상품 간 충돌(AABB)', lambda r: C(r, 'collision'))
    line('공간 경계 위반', lambda r: C(r, 'boundary'))
    line('방향 위반', lambda r: C(r, 'orientation'))
    line('적재 금지 상품 위 적재', lambda r: C(r, 'stack_forbidden'))
    line('적재 하중 초과', lambda r: C(r, 'load_exceeded'))
    line('지지면 부족(공중부양/파고듦 포함)', lambda r: C(r, 'support_insufficient'))
    line('무게중심 안정성 위반', lambda r: C(r, 'stability'))
    line('최대 층수 초과', lambda r: C(r, 'layer_limit'))
    line('담기 순서 위반', lambda r: C(r, 'sequence'))
    line('적재 안정성(근사) 통과', lambda r: C(r, 'stability') == 0 and C(r, 'support_insufficient') == 0)
    line('최대 적재 높이(mm)', lambda r: M(r, 'max_height_mm'))
    line('실제 사용 층수(지지 확인된 상품 기준)', lambda r: M(r, 'layers_used'))
    line('지지 없는 상품 수', lambda r: M(r, 'unsupported_items'))
    line('사용 공간 외접 박스(mm)', lambda r: '×'.join(str(x) for x in M(r, 'used_bbox_mm')))
    line('사용 공간 외접 부피(cm³)', lambda r: M(r, 'used_bbox_volume_cm3'))
    line('LLM 선언 layer 불일치(경고)', lambda r: len(E(r)['warnings']['declared_layer_mismatch']))
    line('LLM 선언 supported_by 불일치(경고)', lambda r: len(E(r)['warnings']['declared_support_mismatch']))
    line('[보조] 자체 가정 치수 기준 유효', lambda r: r['self_consistency']['valid'])
    line('[보조] 자체 가정 치수 기준 위반 건수', lambda r: sum(r['self_consistency']['violation_counts'].values()))
    line('API 응답 시간(s)', lambda r: r['seconds'])
    line('Input tokens', lambda r: r['input_tokens'])
    line('Output tokens', lambda r: r['output_tokens'])
    line('Total tokens', lambda r: r['total_tokens'])
    line('추정 비용(USD)', lambda r: r['estimated_cost_usd'])
    L += ['', f"비용은 {next((r.get('cost_note') for r in ok if r.get('cost_note')), '–')}. "
          '세 실험 모두 동일 상품·동일 용기이므로 (상품 총부피/용기 부피) 비율은 같아 비교 지표로 쓰지 않았다.', '']

    L += ['## 3. 실험별 담기 순서 및 배치 계획', '']
    for r in rows:
        L.append(f"### {r['label']} (r{r.get('repeat', 1)})")
        if r.get('run_status') != 'ok' or not r.get('plan'):
            L += ['', f"실행 상태: {r['run_status']} {r.get('note') or r.get('error') or ''}", '']; continue
        p = r['plan']; met = r['evaluation']['metrics']
        L += ['', f"- status: `{p['status']}` — {p['reason']}", '- assumptions:'] + [f'  - {a}' for a in p['assumptions']] + ['']
        L += ['| 순서 | item_id | 위치 x,y,z (mm) | 자세 | yaw | 선언 층 | 계산 층 | 선언 지지 | 계산 지지 | LLM 가정 치수 W×D×H | DB 치수 | LLM 가정 무게 / DB | 이유 |',
              '|---:|---|---|---|---:|---:|---:|---|---|---|---|---|---|']
        for s in sorted(p['steps'], key=lambda s: s['sequence']):
            pid = s['item_id'].rsplit('_', 1)[0]; a = s['assumed_dimensions_mm']; pos = s['position_mm']
            dbd = DB.get(pid, {}).get('dimensions_mm', '–')
            L.append(f"| {s['sequence']} | `{s['item_id']}` | {pos['x']:g}, {pos['y']:g}, {pos['z']:g} | {s['orientation']} | {s['yaw_deg']} | "
                     f"{s['layer']} | {_fmt(met.get('computed_layers', {}).get(s['item_id']))} | {', '.join(s['supported_by']) or '바닥'} | "
                     f"{', '.join(met.get('computed_supports', {}).get(s['item_id'], [])) or '바닥/없음'} | "
                     f"{a['width']:g}×{a['depth']:g}×{a['height']:g} | {'×'.join(str(x) for x in dbd)} | "
                     f"{s['assumed_weight_g']:g} / {DB.get(pid, {}).get('gross_weight_g', '–')} | {s['reason']} |")
        L += ['', '**Validator 위반 내역**', '']
        any_v = False
        for c, name in CATEGORIES:
            for m in r['evaluation']['violations'][c]:
                L.append(f'- [{name}] {m}'); any_v = True
        if not any_v:
            L.append('- 없음')
        for c, name in WARNINGS:
            for m in r['evaluation']['warnings'][c]:
                L.append(f'- (경고: {name}) {m}')
        L.append('')
    L += ['## 4. Validator 구현 범위', '',
          '구현됨(AABB 근사): ' + ', '.join(n for _, n in CATEGORIES), '',
          '미구현/근사 한계:'] + [f'- {x}' for x in NOT_IMPLEMENTED] + ['']
    L += ['## 5. 결과 파일', '', '- `experiment_*.json`: 요청 입력(이미지 base64 생략), 파싱된 plan, Validator 결과, 토큰/시간',
          '- `raw_responses/`: OpenAI Responses API 응답 원문', '- `comparison.csv`: 지표 비교표',
          '- `packing_plans_3d.png`: 동일 공간·좌표계·색상의 3D/Top view 시각화(DB 치수 기준 AABB)',
          '- `config.json`: 프롬프트, 스키마, 생성 설정, 주문, 공간', '']
    notes = run / 'analysis_notes.md'
    if notes.exists():
        L += [notes.read_text(encoding='utf-8')]
    (run / 'experiment_report.md').write_text('\n'.join(L), encoding='utf-8')


def finalize(run):
    cfg, rows = evaluate(run)
    for r in rows:
        r.pop('_file', None)
    write_csv(run, rows)
    from visualize import render_all
    render_all(run, rows, cfg['items'], DB, cfg['packing_space'])
    write_report(run, cfg, rows)
    return rows


if __name__ == '__main__':
    finalize(Path(sys.argv[1]).resolve())
    print('updated', sys.argv[1])
