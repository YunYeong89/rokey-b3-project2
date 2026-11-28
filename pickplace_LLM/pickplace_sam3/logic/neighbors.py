"""옆 물건 치우기 — 정면으로 집을 때 벌린 손가락이 옆 물건에 닿으면, 그 물건을 선반 위 옆자리로 잠깐 옮겼다가 되돌린다.

  1. estimate()     매대 사진에서 찾은 모든 상자(상품·펩시·사이다 …)의 위치를 잰다: 앞면 가운데(깊이) + 폭(지름) +
                    앞쪽 바닥(상자 아래 끝 광선 ∩ 앞면 수직면) + 윗면 → 원기둥 (중심, 반지름, 바닥, 위)
                    뒤에 가려진 상자는 깊이가 앞 물건 것이라 위치를 모름 → 빼고, '가려짐' 으로 따로 돌려줌
  2. finger_hits()  집으려는 물건 쪽으로 수평으로 다가갈 때 양쪽 손가락이 지나가는 띠 (다가가기 시작 ~ 정중앙)에 걸리는 물건
                    손가락 안쪽 면 = 벌린 폭/2, 두께 = 2 x FINGER_PAD
  3. temp_spot()    치울 물건을 잠깐 둘 자리: 원래 자리에서 가까운 순서로 옆(양쪽)·조금 안쪽을 시험 — 좁게 벌린 손가락이 다른 물건에
                    안 닿고, 집으려는 물건의 손가락에도 안 걸리고, 카메라 화면 안(= 보이는 선반 위)인 첫 자리
움직이는 동작은 logic/pick_place.py (clear_neighbors / restore_neighbors).
"""
import math
from dataclasses import dataclass

import numpy as np

import config
from vision import geometry


@dataclass
class Obstacle:
    label: str
    box: object
    center: np.ndarray        # 원기둥 중심 (base mm) — z 는 높이 가운데
    r: float                  # 반지름 (잰 폭 / 2)
    bottom: float
    top: float

    @property
    def height(self):
        return self.top - self.bottom


def on_front_plane(px, py, surface, direction, intrinsics, posx):
    """픽셀 (px, py) 의 광선이 '앞면 점 surface 를 지나고 다가가는 방향에 수직인 면' 과 만나는 점"""
    T = geometry.pose_matrix(posx) @ geometry._GRIPPER2CAM
    ray = T[:3, :3] @ np.array([(px - intrinsics["ppx"]) / intrinsics["fx"], (py - intrinsics["ppy"]) / intrinsics["fy"], 1.0])
    a = np.asarray(direction, float)
    denom = float(ray @ a)
    if abs(denom) < 1e-6:
        return np.asarray(surface, float)
    return T[:3, 3] + ray * (float((np.asarray(surface) - T[:3, 3]) @ a) / denom)


def occluded(box, boxes):
    """box 가 앞 물건에 가려졌나 (OCCLUDED_RATIO 넘게) — occluders() 참고"""
    if getattr(box, "mask", None) is not None and _masks_everywhere(box, boxes):
        return sum(c for _, c in _mask_covers(box, boxes)) > config.OCCLUDED_RATIO
    return bool(occluders(box, boxes))


def occluders(box, boxes):
    """box 를 앞에서 가린 상자들 (아래 끝이 더 아래 = 정면에서 내려다볼 때 더 앞).
    상자만 있으면 (Grounding DINO — 상자가 가려진 부분까지 덮음): 상자끼리 겹친 넓이가 OCCLUDED_RATIO 넘는 것
    마스크가 있으면 (SAM3 — 보이는 부분만): box 마스크 아래 끝 바로 밑이 그 물건 마스크인 비율이 큰 것
      (앞 물건이 아래쪽을 가리면 보이는 마스크의 아래 끝이 앞 물건 마스크에 닿음. 선반 위에 혼자 선 물건은 밑이 선반)"""
    if getattr(box, "mask", None) is not None and _masks_everywhere(box, boxes):
        return [o for o, c in _mask_covers(box, boxes) if c > 0.1]
    area = max((box.x2 - box.x1) * (box.y2 - box.y1), 1.0)
    found = []
    for o in _front_candidates(box, boxes):
        inter = max(0.0, min(o.x2, box.x2) - max(o.x1, box.x1)) * max(0.0, min(o.y2, box.y2) - max(o.y1, box.y1))
        if inter > config.OCCLUDED_RATIO * area:
            found.append(o)
    return found


def _front_candidates(box, boxes):
    return [o for o in boxes if o is not box and o.label not in ("person", "hand") and o.y2 > box.y2 + 5]


def _masks_everywhere(box, boxes):
    return all(getattr(o, "mask", None) is not None for o in _front_candidates(box, boxes))


def _mask_covers(box, boxes, band=6):
    """[(앞 물건, box 마스크 아래 끝 중 그 물건 마스크에 닿은 비율)]"""
    m = box.mask
    cols = np.flatnonzero(m.any(axis=0))
    if not cols.size:
        return []
    h = m.shape[0]
    bottoms = h - 1 - np.argmax(m[::-1][:, cols], axis=0)          # 열마다 마스크의 가장 아래 줄
    out = []
    for o in _front_candidates(box, boxes):
        hit = np.zeros(cols.size, bool)
        for d in range(1, band + 1):
            hit |= o.mask[np.minimum(bottoms + d, h - 1), cols]
        if hit.any():
            out.append((o, float(hit.mean())))
    return out


def depth_span(depth, u, v, z, rows=5, far=60.0, near=40.0):
    """깊이 사진에서 (u, v) 물건의 좌우 끝을 다시 찾는다 → (가운데 u, 폭 px) 또는 None.
    v 둘레 몇 줄에서 u 부터 좌우로 가며, 깊이가 z 보다 far 넘게 멀어지거나(뒤 배경·틈) near 넘게 가까워지면(앞 물건) 멈춤.
    DINO 상자는 느슨해서 가운데가 10~15mm 어긋남 (시뮬) → 3cm 간격 이웃에 손가락이 닿음. 깊이 끝으로 바로잡음"""
    h, w = depth.shape[:2]
    lefts, rights = [], []
    for dv in range(-(rows // 2), rows // 2 + 1):
        r = min(max(v + dv * 3, 0), h - 1)
        row = depth[r].astype(float)

        def inside(x):
            d = row[x]
            return d > 0 and z - near < d < z + far

        if not inside(min(max(u, 0), w - 1)):
            continue
        a = u
        while a - 1 >= 0 and (inside(a - 1) or (a - 2 >= 0 and inside(a - 2))):
            a -= 1
        b = u
        while b + 1 < w and (inside(b + 1) or (b + 2 < w and inside(b + 2))):
            b += 1
        if a > 0 and b < w - 1:                                    # 사진 끝에 잘린 줄은 안 씀
            lefts.append(a)
            rights.append(b)
    if len(lefts) < 2:
        return None
    a, b = float(np.median(lefts)), float(np.median(rights))
    return int(round((a + b) / 2)), b - a + 1


def mask_span(mask, v, rows=5):
    """물건 마스크(SAM3)의 v 둘레 몇 줄에서 좌우 끝 → (가운데 u, 폭 px) 또는 None (depth_span 과 같은 모양)"""
    h = mask.shape[0]
    lefts, rights = [], []
    for dv in range(-(rows // 2), rows // 2 + 1):
        cols = np.flatnonzero(mask[min(max(v + dv * 3, 0), h - 1)])
        if cols.size:
            lefts.append(cols[0])
            rights.append(cols[-1])
    if len(lefts) < 2:
        return None
    a, b = float(np.median(lefts)), float(np.median(rights))
    return int(round((a + b) / 2)), b - a + 1


def measure_span(box, depth, u, v, z, use_depth=True):
    """물건의 좌우 끝 → (가운데 u, 폭 px, 깊이 z).
    마스크가 있으면(SAM3) 마스크로, 없으면 깊이 끝(use_depth)으로, 그것도 안 되면 상자 폭 그대로"""
    span = mask_span(box.mask, v) if getattr(box, "mask", None) is not None else None
    if span is None and use_depth:
        span = depth_span(depth, u, v, z)
        if span and abs(span[0] - u) >= (box.x2 - box.x1) / 2:      # 깊이 끝이 상자 밖 = 다른 물건까지 이어짐
            span = None
    if span is None:
        return u, box.x2 - box.x1, z
    return span[0], span[1], geometry.depth_at(depth, span[0], v) or z


def estimate(boxes, depth, intrinsics, posx, direction):
    """상자들 → ([Obstacle], [가려져서 위치를 모르는 상자])"""
    a = np.asarray(direction, float)
    found, hidden = [], []
    for b in boxes:
        if b.label in ("person", "hand"):
            continue
        if occluded(b, boxes):
            hidden.append(b)
            continue
        u, v = b.center
        z = geometry.depth_at(depth, u, v)
        if not z:
            continue
        u, wpx, z = measure_span(b, depth, u, v, z)                  # 마스크·깊이로 잰 좌우 끝 (DINO 상자보다 정확)
        front = geometry.pixel_to_base(u, v, z, intrinsics, posx)
        r = wpx * z / intrinsics["fx"] / 2
        bottom = on_front_plane(u, b.y2, front, a, intrinsics, posx)[2]
        top = on_front_plane(u, b.y1, front, a, intrinsics, posx)[2] - 2 * r * 0.5   # 윗면 타원 먼 끝만큼 덜어냄 (대략)
        top = max(top, bottom + 2 * r)
        d = front - geometry.camera_position(posx)                  # 카메라 시선 방향(수평)으로 반지름만큼 = 축
        d[2] = 0.0
        d = d / max(float(np.linalg.norm(d)), 1e-6)
        center = np.array([front[0] + d[0] * r, front[1] + d[1] * r, (bottom + top) / 2])
        found.append(Obstacle(b.label, b, center, float(r), float(bottom), float(top)))
    return found, hidden


def _seg_dist(p, a, b):
    ab = b - a
    t = np.clip(float((p - a) @ ab) / max(float(ab @ ab), 1e-9), 0.0, 1.0)
    return float(np.linalg.norm(p - (a + t * ab)))


def finger_hits(center, direction, open_width, approach, obstacles, exclude=()):
    """center(쥐는 점)에 수평 방향 direction 으로 approach mm 뒤에서 다가갈 때, 벌린 손가락(안쪽 면 = open_width/2)이
    지나가는 띠에 걸리는 물건들"""
    a = np.array([direction[0], direction[1]], float)
    a /= np.linalg.norm(a)
    s = np.array([-a[1], a[0]])
    c = np.asarray(center[:2], float)
    mid = open_width / 2 + config.FINGER_PAD                          # 손가락 두께 가운데
    hits = []
    for o in obstacles:
        if any(o is e for e in exclude) or not (o.bottom - config.FINGER_PAD < center[2] < o.top + config.FINGER_PAD):
            continue
        for sgn in (-1, 1):
            p1 = c + s * sgn * mid
            if _seg_dist(np.asarray(o.center[:2]), p1 - a * approach, p1) < o.r + config.FINGER_PAD:
                hits.append(o)
                break
    return hits


def sweep_hits(center, r, direction, approach, obstacles, exclude=()):
    """반지름 r 물건을 center 에서 수평 방향 반대로 approach mm 뺄 때(또는 거기서 밀어 넣을 때) 그 몸통이 스치는 물건들"""
    a = np.array([direction[0], direction[1]], float)
    a /= np.linalg.norm(a)
    c = np.asarray(center[:2], float)
    return [o for o in obstacles if not any(o is e for e in exclude) and
            _seg_dist(np.asarray(o.center[:2]), c - a * approach, c) < r + o.r + 2]


def narrow_width(o):
    """치울 물건을 집을 때 벌리는 폭 — 그 폭 + 양쪽 NARROW_MARGIN (옆 물건에 닿지 않게)"""
    return min(2 * o.r + 2 * config.NARROW_MARGIN, config.GRIPPER_OPEN)


def spot_free_in_depth(c, o, depth, intrinsics, T_inv, margin=None, tol=15.0):
    """깊이 사진으로 c 자리(반지름 o.r + margin, 선반 높이)가 비었는지 — DINO 가 못 찾은 물건도 잡아냄.
    발자국 점들을 사진에 투영해서, 재야 할 선반 깊이보다 tol 넘게 가까운 점(= 그 자리에 뭔가 서 있거나 앞을 가림)이
    10% 넘으면 비지 않음. 옮길 물건 자신의 상자 안 픽셀은 (아직 거기 있으므로) 뺀다"""
    hgt, wid = depth.shape[:2]
    rr = o.r + (config.NEIGHBOR_MARGIN if margin is None else margin)
    total = busy = 0
    for dx in np.linspace(-rr, rr, 7):
        for dy in np.linspace(-rr, rr, 7):
            if dx * dx + dy * dy > rr * rr:
                continue
            P = T_inv @ np.array([c[0] + dx, c[1] + dy, o.bottom + 5.0, 1.0])
            if P[2] <= 0:
                continue
            u = int(round(intrinsics["fx"] * P[0] / P[2] + intrinsics["ppx"]))
            v = int(round(intrinsics["fy"] * P[1] / P[2] + intrinsics["ppy"]))
            if not (0 <= u < wid and 0 <= v < hgt) or (o.box is not None and o.box.x1 <= u <= o.box.x2 and o.box.y1 <= v <= o.box.y2):
                continue
            d = geometry.depth_at(depth, u, v, window=3)
            if not d:
                continue
            total += 1
            busy += d < P[2] - tol
    return total >= 5 and busy < 2


def temp_spot(o, target, direction, obstacles, approach, intrinsics, posx, hidden=(), depth=None):
    """o 를 잠깐 옮겨 둘 선반 위 자리 (중심 좌표) 또는 None.
    선반 밖으로 뺐다가 옆으로 가서 다시 넣으므로 집으려는 물건 건너편이나 조금 더 안쪽도 됨 → 원래 자리에서 가까운 순서로 시험:
      그 자리에서 좁게 벌린 손가락이 다른 물건에 안 닿고 / 다른 물건과 안 겹치고 / 집으려는 물건의 손가락 띠에 안 걸리고 /
      카메라 화면 안 (= 보이는 선반 위) / 뒤에 가려져 위치를 모르는 물건의 상자와 화면에서 안 겹침"""
    a = np.array([direction[0], direction[1], 0.0])
    a /= np.linalg.norm(a)
    s = np.array([-a[1], a[0], 0.0])
    others = [p for p in obstacles if p is not o]
    T_inv = np.linalg.inv(geometry.pose_matrix(posx) @ geometry._GRIPPER2CAM)
    w, h = 2 * intrinsics["ppx"], 2 * intrinsics["ppy"]
    steps = int(config.SHIFT_MAX // 10)
    cands = [(lat * 10.0, dep) for lat in range(-steps, steps + 1) for dep in (0.0, 25.0, 50.0, 75.0, 100.0) if lat or dep]
    cands.sort(key=lambda c: (c[1], abs(c[0])))     # 같은 깊이(옆으로만) 먼저 — 안쪽은 앞 물건에 가려 안 보여서 위험 (시뮬 확인)
    for lat, dep in cands:
        c = o.center + s * lat + a * dep
        moved = Obstacle(o.label, o.box, c, o.r, o.bottom, o.top)
        if any(np.linalg.norm((c - p.center)[:2]) < o.r + p.r + config.NEIGHBOR_MARGIN for p in others):  # 다른 물건과 겹침
            continue
        if finger_hits(c, direction, narrow_width(o), approach, others):              # 내려놓을 때 손가락
            continue
        if sweep_hits(c, o.r, direction, approach, others + [target]):                # 밀어 넣는 길에 다른 물건 (예: 바로 뒤)
            continue
        if finger_hits(target.center, direction, config.GRIPPER_OPEN, approach, [moved]):   # 집으려는 물건의 손가락
            continue
        ok, us, vs = True, [], []
        for q in (c + s * o.r, c - s * o.r, c - a * o.r + np.array([0, 0, o.top - c[2]]),
                  c - a * o.r + np.array([0, 0, o.bottom - c[2]])):                  # 화면 안 (= 보이는 선반 위)
            P = T_inv @ np.append(q, 1.0)
            if P[2] <= 0:
                ok = False
                break
            u = intrinsics["fx"] * P[0] / P[2] + intrinsics["ppx"]
            v = intrinsics["fy"] * P[1] / P[2] + intrinsics["ppy"]
            us.append(u)
            vs.append(v)
            if not (20 <= u <= w - 20 and 20 <= v <= h - 20):
                ok = False
                break
        if ok and any(min(max(us), b.x2) - max(min(us), b.x1) > 0 and min(max(vs), b.y2) - max(min(vs), b.y1) > 0
                      for b in hidden):                                                # 가려진 물건 자리일 수 있음
            ok = False
        if ok and depth is not None and not spot_free_in_depth(c, o, depth, intrinsics, T_inv):
            ok = False                                                                 # DINO 가 못 찾은 물건이 있음
        if ok:
            return c
    return None
