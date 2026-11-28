"""LLM 자체 평가와 독립적인 Packing Plan 검증기 (Bounding Box 기반 근사).

기존 run_experiment.py 의 validate() 로직(AABB 충돌, 경계, 원통 지지면의 내접 정사각형
근사, 30% 지지 면적, 볼록껍질 내부 판정, 상부 전체 하중을 각 지지 상품에 부과)을
유지하면서 항목별 위반 건수를 분리 집계하도록 확장했다.

주의: 마찰, 변형, 실제 무게중심, 봉투 들림, 그리퍼 접근 경로, 동역학은 고려하지 않는다.
결과가 '유효'여도 실제 물리적 안정성을 보장하지 않는다.
"""
import math
from collections import Counter, defaultdict

TOL = 0.5          # mm, 접촉/충돌/경계 판정 허용 오차
MIN_SUPPORT = 0.30  # 상부 상품 외접 사각형 면적 대비 최소 지지 비율

# 검증 항목 → 위반 카테고리. 순서는 요구사항 1~11 과 동일.
CATEGORIES = [
    ('missing', '1. 주문 상품/수량 누락'),
    ('unknown_item', '1. 주문에 없는 item_id'),
    ('duplicate', '2. 상품 중복 배치'),
    ('boundary', '3. 포장 공간 경계 위반'),
    ('collision', '4. 상품 간 충돌'),
    ('orientation', '5. 허용 배치 방향 위반'),
    ('stack_forbidden', '6. 상부 적재 금지 상품 위 적재'),
    ('load_exceeded', '7. 허용 적재 하중 초과'),
    ('support_insufficient', '8. 지지면 부족(공중 부양 포함)'),
    ('stability', '9. 무게중심 투영점이 지지 볼록껍질 밖'),
    ('layer_limit', '10. 최대 적재 층수 초과'),
    ('sequence', '11. 담기 순서 위반(지지 상품보다 먼저 배치 / 순번 오류)'),
]
WARNINGS = [
    ('declared_layer_mismatch', 'LLM이 선언한 layer와 기하학적으로 계산된 층이 다름'),
    ('declared_support_mismatch', 'LLM이 선언한 supported_by와 기하학적 지지 상품이 다름'),
]
NOT_IMPLEMENTED = [
    '실제 무게중심(내용물 분포) — 외접 직육면체의 기하 중심으로 대체',
    '원통/원뿔대 형상의 정확한 충돌 — AABB로 보수적으로 근사(실제로는 닿지 않는 캔끼리도 충돌로 판정될 수 있음)',
    '신라면 컵 원뿔대 형상의 윗면/아랫면 크기 차이',
    '마찰, 상품 변형, 봉투 변형/들림, 동역학(운반 중 흔들림)',
    '그리퍼(RG2) 접근 경로 및 손가락 간섭(swept volume), 봉투 입구(300×240mm) 통과 경로',
    '로봇 도달 가능성/작업 시간',
]


def oriented_dims(prop, orientation, yaw_deg):
    """DB의 기본 dimensions_mm=[width(x), depth(y), height(z)] (upright 기준)을 자세에 맞게 변환.

    flat: 봉지류(flexible_pouch)는 이미 평평하므로 그대로, 그 외에는 depth와 height 교환.
    yaw 90: x, y 길이 교환.
    """
    w, d, h = prop['dimensions_mm']
    if orientation == 'flat' and prop['shape'] != 'flexible_pouch':
        d, h = h, d
    if yaw_deg == 90:
        w, d = d, w
    return [w, d, h]


def _hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lower, upper = [], []
    for seq, part in [(pts, lower), (list(reversed(pts)), upper)]:
        for pt in seq:
            while len(part) >= 2 and cross(part[-2], part[-1], pt) <= 0:
                part.pop()
            part.append(pt)
    return lower[:-1] + upper[:-1]


def _inside(poly, pt):
    return len(poly) >= 3 and all(
        (b[0]-a[0])*(pt[1]-a[1]) - (b[1]-a[1])*(pt[0]-a[0]) > 1e-6
        for a, b in zip(poly, poly[1:] + poly[:1]))


def _contact_patch(prop, lo, hi):
    """지지 상품 윗면의 보수적 접촉 영역. 원통은 원에 내접하는 정사각형."""
    l, h = list(lo[:2]), list(hi[:2])
    if prop['shape'] == 'cylinder':
        half = min(h[j]-l[j] for j in (0, 1)) / (2*math.sqrt(2))
        c = [(l[j]+h[j])/2 for j in (0, 1)]
        l, h = [v-half for v in c], [v+half for v in c]
    return l, h


def validate(plan, items, db, space):
    """plan: Structured Output 파싱 결과. items: [{item_id, product_id}]. db: 상품 DB.
    space: {'size_mm':[X,Y,Z], 'wall_clearance_mm':m, 'max_layers':n}
    """
    X, Y, Z = space['size_mm']; M = space['wall_clearance_mm']; max_layers = space['max_layers']
    v = defaultdict(list)  # category -> [message]
    w = defaultdict(list)
    result = {'plan_generated': plan is not None and plan.get('status') == 'planned'}
    if not result['plan_generated']:
        result.update(valid=False, violations={c: [] for c, _ in CATEGORIES},
                      violation_counts={c: 0 for c, _ in CATEGORIES}, warnings={}, metrics={},
                      note='계획이 생성되지 않음(status != planned)')
        return result

    by_id = {x['item_id']: x['product_id'] for x in items}
    steps = plan['steps']
    cnt = Counter(s['item_id'] for s in steps)
    for iid in by_id:
        if cnt[iid] == 0:
            v['missing'].append(iid)
    for iid, n in cnt.items():
        if iid not in by_id:
            v['unknown_item'].append(iid)
        elif n > 1:
            v['duplicate'].append(f'{iid} x{n}')

    # 순번 검증
    seqs = [s['sequence'] for s in steps]
    if sorted(seqs) != list(range(1, len(steps)+1)):
        v['sequence'].append(f'sequence 값이 1..{len(steps)}의 순열이 아님: {seqs}')

    boxes = {}  # item_id -> dict (중복은 첫 번째만 기하 판정)
    for s in steps:
        iid = s['item_id']; pid = by_id.get(iid)
        if pid is None or iid in boxes:
            continue
        prop = db[pid]
        if s['orientation'] not in prop['allowed_orientations']:
            v['orientation'].append(f"{iid}: {s['orientation']} (허용 {prop['allowed_orientations']})")
        dims = oriented_dims(prop, s['orientation'], s['yaw_deg'])
        p = s['position_mm']; pos = [p['x'], p['y'], p['z']]
        lo = [c - d/2 for c, d in zip(pos, dims)]; hi = [c + d/2 for c, d in zip(pos, dims)]
        if lo[0] < M-TOL or hi[0] > X-M+TOL or lo[1] < M-TOL or hi[1] > Y-M+TOL or lo[2] < -TOL or hi[2] > Z+TOL:
            v['boundary'].append(f'{iid}: x[{lo[0]:.1f},{hi[0]:.1f}] y[{lo[1]:.1f},{hi[1]:.1f}] z[{lo[2]:.1f},{hi[2]:.1f}]')
        boxes[iid] = dict(pid=pid, prop=prop, lo=lo, hi=hi, dims=dims, seq=s['sequence'], step=s)

    ids = list(boxes)
    for i, a in enumerate(ids):
        for b in ids[i+1:]:
            A, B = boxes[a], boxes[b]
            ov = [min(A['hi'][k], B['hi'][k]) - max(A['lo'][k], B['lo'][k]) for k in range(3)]
            if all(o > TOL for o in ov):
                v['collision'].append(f'{a} / {b} (겹침 {ov[0]:.1f}×{ov[1]:.1f}×{ov[2]:.1f}mm)')

    # 지지 관계: 아래에서 위로 계산
    supports = {}; layer = {}
    for iid in sorted(ids, key=lambda k: boxes[k]['lo'][2]):
        B = boxes[iid]
        if abs(B['lo'][2]) <= TOL:
            supports[iid] = []; layer[iid] = 1; continue
        sup = []; area = 0.0; corners = []
        for sid in ids:
            if sid == iid:
                continue
            S = boxes[sid]
            if abs(S['hi'][2] - B['lo'][2]) > TOL:
                continue
            l, h = _contact_patch(S['prop'], S['lo'], S['hi'])
            a = [max(l[j], B['lo'][j]) for j in (0, 1)]; b = [min(h[j], B['hi'][j]) for j in (0, 1)]
            if any(b[j]-a[j] <= 1e-6 for j in (0, 1)):
                continue
            sup.append(sid)
            area += (b[0]-a[0])*(b[1]-a[1])
            corners.extend((x, y) for x in (a[0], b[0]) for y in (a[1], b[1]))
        supports[iid] = sup
        footprint = (B['hi'][0]-B['lo'][0]) * (B['hi'][1]-B['lo'][1])
        center = ((B['hi'][0]+B['lo'][0])/2, (B['hi'][1]+B['lo'][1])/2)
        if not sup:
            # 바닥(z=0)에도, 다른 상품 윗면에도 닿지 않음 → 층 미정(None). z<0 이면 바닥 아래로 파고든 것.
            layer[iid] = None
            where = '바닥 아래로 파고듦' if B['lo'][2] < 0 else '공중 부양'
            v['support_insufficient'].append(f'{iid}: z_bottom={B["lo"][2]:.1f}mm, 바닥/지지 상품과 접촉 없음({where})')
            continue
        layer[iid] = 1 + max((layer.get(s) or 1) for s in sup)
        if area < MIN_SUPPORT*footprint - 1e-6:
            v['support_insufficient'].append(f'{iid}: 지지 면적 {area:.0f}mm² < 30% of {footprint:.0f}mm²')
        if not _inside(_hull(corners), center):
            v['stability'].append(f'{iid}: 중심 ({center[0]:.1f},{center[1]:.1f})이 지지 영역 볼록껍질 밖')
        for sid in sup:
            S = boxes[sid]
            if not S['prop']['stack_on_top_allowed'] or S['prop']['max_supported_load_g'] is None:
                v['stack_forbidden'].append(f'{iid} → {sid}({S["prop"]["name"]}) 위 적재 금지')
            if S['seq'] > B['seq']:
                v['sequence'].append(f'{iid}(순번 {B["seq"]})가 지지 상품 {sid}(순번 {S["seq"]})보다 먼저 배치')
        if layer[iid] > max_layers:
            v['layer_limit'].append(f'{iid}: {layer[iid]}층 (최대 {max_layers})')

    # 누적 하중: 상부 상품 무게(+그 위 하중 전체)를 각 지지 상품에 전부 부과(보수적)
    above = defaultdict(list)
    for iid, sup in supports.items():
        for sid in sup:
            above[sid].append(iid)
    memo = {}
    def load_on(sid, stack=()):
        if sid in memo:
            return memo[sid]
        if sid in stack:
            return 0.0
        total = sum(db[boxes[u]['pid']]['gross_weight_g'] + load_on(u, stack+(sid,)) for u in above[sid])
        memo[sid] = total
        return total
    loads = {}
    for sid in ids:
        L = load_on(sid)
        if L > 0:
            loads[sid] = L
            cap = boxes[sid]['prop']['max_supported_load_g']
            if cap is not None and boxes[sid]['prop']['stack_on_top_allowed'] and L > cap + 1e-6:
                v['load_exceeded'].append(f'{sid}: 부과 하중 {L:.0f}g > 허용 {cap}g')

    for iid in ids:
        st = boxes[iid]['step']
        if st['layer'] != layer[iid]:
            w['declared_layer_mismatch'].append(f'{iid}: 선언 {st["layer"]} / 계산 {layer[iid]}')
        if sorted(st['supported_by']) != sorted(supports[iid]):
            w['declared_support_mismatch'].append(f'{iid}: 선언 {sorted(st["supported_by"])} / 계산 {sorted(supports[iid])}')

    if boxes:
        lo = [min(b['lo'][k] for b in boxes.values()) for k in range(3)]
        hi = [max(b['hi'][k] for b in boxes.values()) for k in range(3)]
        bbox = [hi[k]-lo[k] for k in range(3)]
        metrics = dict(
            max_height_mm=round(hi[2], 1),
            layers_used=max((x for x in layer.values() if x is not None), default=0),
            unsupported_items=sum(1 for k in ids if layer[k] is None),
            used_bbox_mm=[round(x, 1) for x in bbox],
            used_bbox_volume_cm3=round(bbox[0]*bbox[1]*bbox[2]/1000, 1),
            floor_items=sum(1 for k in ids if layer[k] == 1),
            stacked_items=sum(1 for k in ids if (layer[k] or 0) > 1),
            computed_supports=supports, computed_layers=layer, carried_load_g=loads,
        )
    else:
        metrics = {}
    counts = {c: len(v[c]) for c, _ in CATEGORIES}
    result.update(valid=sum(counts.values()) == 0, violations={c: v[c] for c, _ in CATEGORIES},
                  violation_counts=counts, warnings={c: w[c] for c, _ in WARNINGS}, metrics=metrics)
    return result
