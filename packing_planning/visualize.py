"""세 실험의 Packing Plan을 동일 공간·좌표계·상품 색상으로 시각화 (AABB 기준)."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from packing_validator import oriented_dims

COLORS = {
    'coca_cola': '#d62728', 'pepsi': '#1f4e9c', 'narangd_cider': '#2ca02c', 'tuna_can': '#7f7f7f',
    'spam': '#1f77b4', 'shin_ramyun': '#ff7f0e', 'almond_pepero': '#8c564b', 'jjolbyeong': '#e6b800',
}


def _faces(lo, hi):
    x0, y0, z0 = lo; x1, y1, z1 = hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    idx = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    return [[v[i] for i in f] for f in idx]


def _boxes(plan, items, db):
    by_id = {x['item_id']: x['product_id'] for x in items}
    out = []
    for s in plan['steps']:
        pid = by_id.get(s['item_id'])
        if pid is None:
            continue
        d = oriented_dims(db[pid], s['orientation'], s['yaw_deg'])
        p = s['position_mm']; c = [p['x'], p['y'], p['z']]
        out.append((s, pid, [c[k]-d[k]/2 for k in range(3)], [c[k]+d[k]/2 for k in range(3)]))
    return out


def _bad_items(ev):
    bad = set()
    for msgs in (ev or {}).get('violations', {}).values():
        for m in msgs:
            for tok in m.replace('/', ' ').replace(':', ' ').replace('→', ' ').replace('(', ' ').split():
                bad.add(tok)
    return bad


def render_all(out, rows, items, db, space):
    X, Y, Z = space['size_mm']; M = space['wall_clearance_mm']
    rows = [r for r in rows if r.get('repeat', 1) == 1]
    fig = plt.figure(figsize=(6.2*len(rows), 10))
    for col, r in enumerate(rows):
        ax3 = fig.add_subplot(2, len(rows), col+1, projection='3d')
        ax2 = fig.add_subplot(2, len(rows), len(rows)+col+1)
        title = r['label']
        for ax in (ax3,):
            ax.set_xlim(0, X); ax.set_ylim(0, Y); ax.set_zlim(0, Z)
            ax.set_box_aspect((X, Y, Z)); ax.set_xlabel('x (mm)'); ax.set_ylabel('y (mm)'); ax.set_zlabel('z (mm)')
            ax.view_init(elev=24, azim=-58)
        ax2.set_xlim(0, X); ax2.set_ylim(0, Y); ax2.set_aspect('equal'); ax2.set_xlabel('x (mm)'); ax2.set_ylabel('y (mm)')
        ax2.add_patch(Rectangle((M, M), X-2*M, Y-2*M, fill=False, ls='--', ec='k', lw=0.8))
        plan = r.get('plan')
        if r.get('run_status') != 'ok' or not plan or plan.get('status') != 'planned':
            msg = {'not_run': 'NOT RUN\n(no product photo)', 'api_error': 'API ERROR'}.get(r.get('run_status'), 'NO PLAN')
            ax3.text2D(0.5, 0.5, msg, transform=ax3.transAxes, ha='center', fontsize=16, color='gray')
            ax2.text(X/2, Y/2, msg, ha='center', va='center', fontsize=14, color='gray')
            ax3.set_title(title); continue
        ev = r.get('evaluation') or {}
        bad = _bad_items(ev)
        boxes = _boxes(plan, items, db)
        for s, pid, lo, hi in boxes:
            col_ = COLORS.get(pid, '#999999'); edge = 'red' if s['item_id'] in bad else 'k'
            ax3.add_collection3d(Poly3DCollection(_faces(lo, hi), facecolor=col_, edgecolor=edge, alpha=0.55, lw=1.2 if edge == 'red' else 0.5))
        # 위층을 나중에 그려 top view에서 보이게
        for s, pid, lo, hi in sorted(boxes, key=lambda b: b[2][2]):
            col_ = COLORS.get(pid, '#999999'); edge = 'red' if s['item_id'] in bad else 'k'
            ax2.add_patch(Rectangle((lo[0], lo[1]), hi[0]-lo[0], hi[1]-lo[1], fc=col_, ec=edge, alpha=0.5,
                                    lw=2 if edge == 'red' else 0.8, hatch='//' if lo[2] > 0.5 else None))
            ax2.text((lo[0]+hi[0])/2, (lo[1]+hi[1])/2, f"{s['sequence']}:{s['item_id']}\nL{s['layer']} z0={lo[2]:.0f}",
                     ha='center', va='center', fontsize=6.5)
        nv = sum(ev.get('violation_counts', {}).values())
        ax3.set_title(f"{title}\nvalid={ev.get('valid')}  violations={nv}  maxH={ev.get('metrics', {}).get('max_height_mm')}mm")
        ax2.set_title('Top view (hatched = bottom above floor z0>0, red edge = violation)', fontsize=9)
    used = [p for p in COLORS if p in {x['product_id'] for x in items}]
    handles = [Rectangle((0, 0), 1, 1, fc=COLORS[p], alpha=0.6) for p in used]
    fig.legend(handles, used, loc='lower center', ncol=len(used), fontsize=9)
    fig.suptitle(f'Packing plans (AABB, same space {X}x{Y}x{Z} mm, wall clearance {M} mm)', fontsize=13)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    fig.savefig(out / 'packing_plans_3d.png', dpi=130)
    plt.close(fig)


if __name__ == '__main__':
    # 저장된 결과 디렉터리로 다시 그리기: python visualize.py results/<run>
    run = Path(sys.argv[1])
    cfg = json.loads((run / 'config.json').read_text(encoding='utf-8'))
    db = json.loads((Path(__file__).parent / 'data' / 'product_properties.json').read_text(encoding='utf-8'))
    rows = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(run.glob('experiment_*.json'))]
    render_all(run, rows, cfg['items'], db, cfg['packing_space'])
