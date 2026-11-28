"""봉투(바구니) 빈자리 · 쌓을 자리 찾기 + 자리가 없으면 다른 물품을 옮겨서 자리 만들기 — '계획'만 한다 (로봇은 안 움직임).

  1. RealSense 깊이 → base 좌표 점들 → 봉투 안을 5mm 격자로 나눠 칸마다 높이(바닥 기준)를 잰다
  2. 바닥보다 OCCUPIED_HEIGHT 이상 높은 칸 = 물건 있음 / 깊이를 못 읽은 칸 = 모름
     단, 모르는 칸 중 '카메라와 그 바닥 사이를 물건이 가로막는' 칸은 물건에 가려진 바닥(shadow)으로 보고 비었다고 취급
     (비스듬히 찍으면 높은 물건 뒤 바닥은 늘 깊이가 비기 때문 — 그 안에 낮은 물건이 숨어 있으면 못 봄)
  3. 상품 자리 (상품 크기 + 안전 여유의 직사각형, 긴 변을 봉투 가로/세로 두 방향으로) 를 찾는다 — 바닥이든 다른 물건 위든:
       받침면 = 자리 안 가장 높은 곳, 그 높이 근처 칸이 MIN_SUPPORT_RATIO 이상이어야 (평평하게 받쳐져야) 놓는다
       받침면 + 상품 높이 ≤ 봉투 깊이(CONTAINER_DEPTH)
       자리 둘레(손가락이 내려올 곳)는 손가락 끝보다 낮아야 한다
       우리가 넣은 물건 기록으로: 깨지기 쉬운 물건 위 / 더 가벼운 물건 위에는 안 쌓는다
     → 받침면이 낮은 자리(바닥)부터, 같은 높이면 구석부터 고른다
  4. 없으면: 옮겨도 되는 물건(그리퍼로 잡히는 폭, 직사각형 모양, 고른 높이 — 여러 물건이 붙은 덩어리·쌓인 물건은 제외)을
     작은 것부터 하나씩 '바닥의 다른 자리로 옮긴다고 가정'해 보고, 옮긴 뒤 상품 자리가 생기는 첫 경우를 계획으로 돌려준다
  5. 그래도 없으면 자리 없음 (Plan.spot = None)
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional

import cv2
import numpy as np
from scipy import ndimage

import config


@dataclass
class Item:
    """넣을 상품 (무게 g, 크기 mm)"""
    pid: str
    weight: float
    length: float
    width: float
    height: float
    fragile: bool = False
    side_grasp: bool = False   # 정면(수평)으로 쥐는 상품 — 손가락이 위에서 둘레로 내려오지 않음


@dataclass
class Placed:
    """우리가 봉투에 넣은 물건 기록 — 쌓을 때 '깨지기 쉬운 물건 위·더 가벼운 물건 위' 를 피하는 데 씀"""
    pid: str
    x: float
    y: float
    du: float         # 봉투 가로(u) 방향으로 차지한 길이 (mm)
    dv: float         # 봉투 세로(v) 방향으로 차지한 길이 (mm)
    base: float       # 받침면 높이 (바닥 기준 mm)
    top: float        # 윗면 높이
    weight: float
    fragile: bool
    stackable: bool = True   # 위에 쌓아도 되는지 (눕힌 캔은 굴러서 안 됨)


@dataclass
class Spot:
    """놓을 자리 — base 좌표 중심 (mm), 받침면 높이, 격자 위치(그림용)"""
    x: float
    y: float
    i0: int
    j0: int
    fu: int
    fv: int
    base: float = 0.0     # 받침면 높이 (바닥 기준 mm) — 0 이면 바닥
    du: float = 0.0       # 상품이 봉투 가로(u) 방향으로 차지할 길이 (mm, 안전 여유 포함)
    dv: float = 0.0       # 세로(v) 방향
    rotated: bool = False # False: 상품 긴 변이 봉투 가로(u) 방향 / True: 세로(v) 방향

    @property
    def stacked(self):
        return self.base > config.OCCUPIED_HEIGHT


@dataclass
class BasketObject:
    """봉투 안 물건 덩어리 (깊이로 찾은 것 — 무슨 상품인지는 모름)"""
    index: int
    mask: np.ndarray
    x: float          # 중심 base 좌표 (mm)
    y: float
    top: float        # 바닥에서 윗면까지 높이 (mm)
    yaw: float        # 긴 쪽 방향 (base 기준 deg)
    bbox: tuple       # (i0, i1, j0, j1) 격자, 끝 포함
    length: float = 0.0   # 긴 변 (mm)
    width: float = 0.0    # 짧은 변 (mm)
    movable: bool = True
    reason: str = ""      # 옮기지 않는 이유


@dataclass
class Move:
    obj: BasketObject
    dest_x: float
    dest_y: float
    dest_bbox: tuple


@dataclass
class Plan:
    spot: Optional[Spot]
    moves: List[Move] = field(default_factory=list)


class BasketMap:
    def __init__(self, points, camera=None, placed=()):
        """points: base 좌표 점들 (N, 3) mm — vision.geometry.depth_to_base_points 결과
        camera : 사진을 찍을 때 카메라 위치 (base 좌표 mm) — 가려진 바닥 판별에 사용 (없으면 판별 안 함)
        placed : 우리가 넣은 물건 기록 [Placed, ...] — 쌓기 규칙(깨지기 쉬운/더 가벼운 물건 위 금지)에 사용"""
        self.camera = None if camera is None else np.asarray(camera, dtype=float)
        self.placed = list(placed)
        self.cell = config.GRID_CELL
        self.length, self.width = config.BASKET_SIZE
        self.cx, self.cy, self.floor = config.BASKET_CENTER[:3]
        self.yaw = math.radians(config.BASKET_YAW)
        self.nu, self.nv = int(self.length // self.cell), int(self.width // self.cell)
        self.margin = int(math.ceil(config.WALL_MARGIN / self.cell))
        self.fill_limit = config.CONTAINER_DEPTH       # 채울 높이 — 자리가 없으면 CONTAINER_MAX 로 올려서 다시 계획
        self._build(np.asarray(points, dtype=float))

    # ------------------------------------------------------------ 좌표 변환 (봉투 격자 ↔ base)
    def _to_uv(self, xy):
        d = xy - np.array([self.cx, self.cy])
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return np.stack([d[:, 0] * c + d[:, 1] * s, -d[:, 0] * s + d[:, 1] * c], axis=1)

    def _to_xy(self, u, v):
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return self.cx + u * c - v * s, self.cy + u * s + v * c

    def _cell_center_xy(self, i, j):
        return self._to_xy((i + 0.5) * self.cell - self.length / 2, (j + 0.5) * self.cell - self.width / 2)

    # ------------------------------------------------------------ 높이 지도
    def _build(self, pts):
        uv = self._to_uv(pts[:, :2])
        i = np.floor((uv[:, 0] + self.length / 2) / self.cell).astype(int)
        j = np.floor((uv[:, 1] + self.width / 2) / self.cell).astype(int)
        inside = (i >= 0) & (i < self.nu) & (j >= 0) & (j < self.nv)
        i, j, h = i[inside], j[inside], pts[inside, 2] - self.floor
        flat = i * self.nv + j
        n = self.nu * self.nv
        total = np.bincount(flat, minlength=n)
        high = np.bincount(flat, weights=(h > config.OCCUPIED_HEIGHT).astype(float), minlength=n)
        hmax = np.full(n, -np.inf)
        np.maximum.at(hmax, flat, h)

        self.points_inside = int(inside.sum())
        self.unknown = (total == 0).reshape(self.nu, self.nv)
        with np.errstate(invalid="ignore", divide="ignore"):
            self.occupied = ((high / np.maximum(total, 1)) >= 0.5).reshape(self.nu, self.nv)
        self.height = np.where(np.isfinite(hmax), hmax, 0.0).reshape(self.nu, self.nv)
        # 벽 가장자리는 물건으로 세지 않는다 (벽 윗면·기울어진 점) — 놓을 자리에서도 제외
        self.edge = np.zeros_like(self.occupied)
        m = self.margin
        if m > 0:                                # (m=0 이면 [-0:] 이 배열 전체가 되므로 건너뜀)
            self.edge[:m, :] = self.edge[-m:, :] = self.edge[:, :m] = self.edge[:, -m:] = True
        self.occupied &= ~self.edge
        self.objects = self._find_objects()
        self.shadow = self._occluded_floor()
        self.unknown &= ~self.shadow
        self._mark_stacks()

    def _footprint_mask(self, x, y, du, dv):
        """base 좌표 중심 (x, y), 봉투 가로 du · 세로 dv 직사각형이 덮는 칸"""
        u, v = self._to_uv(np.array([[x, y]]))[0]
        i0 = int(np.floor((u - du / 2 + self.length / 2) / self.cell))
        j0 = int(np.floor((v - dv / 2 + self.width / 2) / self.cell))
        nu, nv = int(np.ceil(du / self.cell)), int(np.ceil(dv / self.cell))
        mask = np.zeros((self.nu, self.nv), bool)
        mask[max(0, i0):max(0, i0 + nu), max(0, j0):max(0, j0 + nv)] = True
        return mask

    def _mark_stacks(self):
        """우리가 쌓은 물건이 걸친 덩어리는 옮기지 않는다 (위 물건만 집히거나 아래가 무너질 수 있음)"""
        for p in self.placed:
            if p.base <= config.OCCUPIED_HEIGHT:
                continue
            area = self._footprint_mask(p.x, p.y, p.du, p.dv)
            for o in self.objects:
                if o.movable and (o.mask & area).any():
                    o.movable, o.reason = False, "쌓인 물건 (위아래로 겹쳐 있음)"

    def heights_at(self, xs, ys):
        """base 좌표 점들의 칸 높이 (바닥 기준 mm). 봉투 밖 점은 NaN, 모르는 칸은 0"""
        uv = self._to_uv(np.stack([np.ravel(xs), np.ravel(ys)], axis=1))
        i = np.floor((uv[:, 0] + self.length / 2) / self.cell).astype(int)
        j = np.floor((uv[:, 1] + self.width / 2) / self.cell).astype(int)
        ok = (i >= 0) & (i < self.nu) & (j >= 0) & (j < self.nv)
        H = np.nan_to_num(self.height_grid(), nan=0.0)
        out = np.full(len(i), np.nan)
        out[ok] = H[i[ok], j[ok]]
        return out

    def height_grid(self):
        """칸별 높이 (바닥 기준 mm). 물건 없는 칸 0, 모르는 칸 NaN, 벽 가장자리 0"""
        H = np.where(self.occupied, self.height, 0.0)
        H[self.unknown] = np.nan
        H[self.edge] = 0.0
        return H

    def _occluded_floor(self):
        """모르는 칸 중, 그 칸 바닥 → 카메라 직선이 지나는 곳에 그 직선보다 높은 물건이 있는 칸 (물건에 가려진 바닥)"""
        shadow = np.zeros_like(self.unknown)
        if self.camera is None or not self.occupied.any():
            return shadow
        ii, jj = np.nonzero(self.unknown & ~self.edge)
        if len(ii) == 0:
            return shadow
        x0, y0 = self._cell_center_xy(ii, jj)
        cam_h = self.camera[2] - self.floor
        tallest = float(self.height[self.occupied].max())
        dx, dy = self.camera[0] - x0, self.camera[1] - y0
        reach = np.hypot(dx, dy) * tallest / max(cam_h, 1.0)          # 이 거리 안에서만 가로막을 수 있음
        steps = int(np.ceil(reach.max() / (self.cell / 2))) + 1
        hit = np.zeros(len(ii), bool)
        for k in range(1, steps + 1):
            t = np.minimum(k * (self.cell / 2) / np.maximum(np.hypot(dx, dy), 1e-6), 1.0)
            uv = self._to_uv(np.stack([x0 + t * dx, y0 + t * dy], axis=1))
            ci = np.floor((uv[:, 0] + self.length / 2) / self.cell).astype(int)
            cj = np.floor((uv[:, 1] + self.width / 2) / self.cell).astype(int)
            ok = (ci >= 0) & (ci < self.nu) & (cj >= 0) & (cj < self.nv)
            ray_h = t * cam_h                                         # 그 지점에서 직선의 높이 (바닥 기준)
            blocked = np.zeros(len(ii), bool)
            blocked[ok] = self.occupied[ci[ok], cj[ok]] & (self.height[ci[ok], cj[ok]] >= ray_h[ok])
            hit |= blocked
        shadow[ii[hit], jj[hit]] = True
        return shadow

    def _find_objects(self):
        labels, n = ndimage.label(self.occupied, structure=np.ones((3, 3)))
        min_cells = config.MIN_OBJECT_AREA / self.cell ** 2
        objects = []
        for k in range(1, n + 1):
            mask = labels == k
            if mask.sum() < min_cells:
                self.occupied &= ~mask          # 잡음
                continue
            ii, jj = np.nonzero(mask)
            ci, cj = ii.mean(), jj.mean()
            x, y = self._to_xy((ci + 0.5) * self.cell - self.length / 2, (cj + 0.5) * self.cell - self.width / 2)
            # 방향과 크기: 덩어리를 가장 꼭 감싸는 직사각형 (cv2.minAreaRect)
            #   주성분(PCA)으로 재면 정사각형 물건은 방향이 45° 로 잡혀 크기가 √2 배로 커 보인다 (E2E 시험에서 확인)
            (_, _), (rw, rh), angle = cv2.minAreaRect(np.stack([ii, jj], axis=1).astype(np.float32))
            length = float(max(rw, rh) + 1) * self.cell
            width = float(min(rw, rh) + 1) * self.cell
            yaw = angle if rw >= rh else angle + 90.0             # 긴 변 방향 (봉투 가로 기준 deg)
            fill = mask.sum() * self.cell ** 2 / (length * width)
            heights = self.height[mask]
            spread = float(np.percentile(heights, 90) - np.percentile(heights, 10))
            reason = ("짧은 변 {:.0f}mm > 그리퍼 {:.0f}mm".format(width, config.GRIPPER_MAX_GRASP)
                      if width > config.GRIPPER_MAX_GRASP else
                      "모양이 직사각형이 아님 (채움 {:.0%}) — 여러 물건이 붙어 있을 수 있음".format(fill)
                      if fill < config.MIN_FILL_RATIO else
                      "윗면 높이 차이 {:.0f}mm — 높이가 다른 물건이 붙어 있을 수 있음".format(spread)
                      if spread > config.MAX_HEIGHT_SPREAD else "")
            objects.append(BasketObject(len(objects) + 1, mask, float(x), float(y),
                                        float(np.percentile(heights, 90)), yaw + config.BASKET_YAW,
                                        (int(ii.min()), int(ii.max()), int(jj.min()), int(jj.max())),
                                        length, width, not reason, reason))
        return objects

    # ------------------------------------------------------------ 빈자리 찾기
    def _candidates(self, occupied, fu, fv):
        """fu x fv 칸이 비어 있는 자리 (i0, j0) 배열 — 구석에 가까운 순서"""
        m = self.margin
        if fu > self.nu - 2 * m or fv > self.nv - 2 * m:
            return np.empty((0, 2), int)
        blocked = (occupied | self.edge).astype(np.int32)
        unknown = self.unknown.astype(np.int32)

        def window_sum(a):
            s = np.pad(a.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
            return s[fu:, fv:] - s[:-fu, fv:] - s[fu:, :-fv] + s[:-fu, :-fv]

        ok = (window_sum(blocked) == 0) & (window_sum(unknown) <= config.MAX_UNKNOWN_RATIO * fu * fv)
        i0, j0 = np.nonzero(ok)
        score = (np.minimum(i0 - m, self.nu - m - (i0 + fu)) + np.minimum(j0 - m, self.nv - m - (j0 + fv))) * 10000 \
            + i0 * 100 + j0
        order = np.argsort(score, kind="stable")
        return np.stack([i0[order], j0[order]], axis=1)

    def find_spot(self, item, H=None, lookahead=True):
        """item 을 놓을 자리 (바닥 또는 다른 물건 위) → Spot 또는 None. H: 높이 격자 (기본: 지금 지도)
        상품 긴 변을 봉투 가로 방향 / 세로 방향 두 가지로 놓아 보고 더 좋은 자리를 고른다 (놓을 때 그리퍼를 그 방향으로 돌림).
        lookahead: 두 방향 다 되면, 놓은 뒤에도 '같은 상품이 바닥에 하나 더' 들어가는 방향을 먼저 고른다
                   (구석부터 채우면 첫 상품이 자리를 막는 일이 있음 — 입구 20x15cm 에 눕힌 캔 2개)"""
        H = self.height_grid() if H is None else H
        found_list = []
        for rotated in (False, True):
            du, dv = (item.width, item.length) if rotated else (item.length, item.width)
            found = self._find_spot_oriented(item, H, du + 2 * config.SPOT_SAFETY, dv + 2 * config.SPOT_SAFETY, rotated)
            if found:
                found_list.append(found)
            if item.length == item.width:
                break
        if not found_list:
            return None
        if lookahead and len(found_list) > 1:
            def leaves_room(f):
                s = f[1]
                H2 = np.array(H, dtype=float, copy=True)
                H2[s.i0:s.i0 + s.fu, s.j0:s.j0 + s.fv] = s.base + item.height
                nxt = self.find_spot(item, H2, lookahead=False)
                return nxt is not None and not nxt.stacked
            return min(found_list, key=lambda f: (not leaves_room(f), f[0]))[1]
        return min(found_list, key=lambda f: f[0])[1]

    def _find_spot_oriented(self, item, H, du, dv, rotated):
        """봉투 가로 du · 세로 dv (mm) 자리 → (점수, Spot) 또는 None"""
        from numpy.lib.stride_tricks import sliding_window_view as windows

        cu, cv = int(math.ceil(du / self.cell)), int(math.ceil(dv / self.cell))   # 상품 자리 칸 수
        r = int(math.ceil(config.FINGER_CLEARANCE / self.cell))                   # 둘레 손가락 자리 칸 수
        if item.side_grasp:
            r = 0                  # 수평 그리퍼는 놓을 때 따로 검사 (logic/front_grasp.bag_release)
        m = self.margin
        if cu > self.nu - 2 * m or cv > self.nv - 2 * m:
            return None                                                           # 봉투보다 큼

        # 자리 안: 받침면(가장 높은 곳), 모르는 칸 수, 받쳐지는 비율
        core = windows(H, (cu, cv))
        unknown_cnt = np.isnan(core).sum(axis=(-2, -1))
        filled = np.where(np.isnan(core), 0.0, core)
        base = filled.max(axis=(-2, -1))
        floor_ok = base <= config.OCCUPIED_HEIGHT
        base = np.where(floor_ok, 0.0, base)
        support = (core >= (base - config.SUPPORT_TOL)[..., None, None]).sum(axis=(-2, -1)) / (cu * cv)
        support = np.where(floor_ok, 1.0, support)

        # 둘레 손가락 자리: 자리 바깥 r 칸 띠의 가장 높은 곳 (봉투 밖은 높이 0 — 비닐 벽은 밀림)
        if r:
            P = np.pad(np.where(np.isnan(H), 0.0, H), r)
            ni, nj = core.shape[:2]
            rows = windows(P, (r, cv + 2 * r))
            cols = windows(P, (cu, r))
            ring = np.maximum.reduce([rows[:ni, :nj].max(axis=(-2, -1)),                    # 위쪽 띠
                                      rows[cu + r:cu + r + ni, :nj].max(axis=(-2, -1)),     # 아래쪽 띠
                                      cols[r:r + ni, :nj].max(axis=(-2, -1)),               # 왼쪽 띠
                                      cols[r:r + ni, cv + r:cv + r + nj].max(axis=(-2, -1))])  # 오른쪽 띠
        else:
            ring = np.zeros_like(base)
        finger_tip = base + item.height - config.PICK_DEPTH + config.DROP_CLEARANCE    # 놓을 때 손가락 끝 높이

        ok = ((unknown_cnt <= config.MAX_UNKNOWN_RATIO * cu * cv)
              & (support >= config.MIN_SUPPORT_RATIO)
              & (base + item.height <= self.fill_limit)
              & (ring <= np.maximum(finger_tip - config.FINGER_SAFETY, 0.0) + 1e-6)
              & (floor_ok | config.STACKING))
        # 자리 전체가 봉투 안쪽(벽 여유 제외)에 있어야 함
        inside = np.zeros_like(ok)
        inside[m:self.nu - m - cu + 1, m:self.nv - m - cv + 1] = True
        ok &= inside

        i0, j0 = np.nonzero(ok)
        if len(i0) == 0:
            return None
        level = np.round(base[i0, j0] / 10.0)
        corner = np.minimum(i0 - m, self.nu - m - (i0 + cu)) + np.minimum(j0 - m, self.nv - m - (j0 + cv))
        sx, sy = self._to_xy((i0 + cu / 2) * self.cell - self.length / 2, (j0 + cv / 2) * self.cell - self.width / 2)
        near = np.round(np.hypot(sx - config.NEAR_ORIGIN[0], sy - config.NEAR_ORIGIN[1]) / 10.0)   # 로봇에서 거리 (1cm 단위)
        order = np.lexsort((j0, corner, i0, near, level))               # 낮은 받침면 → 로봇에 가까운 자리 → 한쪽 끝부터 → 구석
        # 로봇에 가까운 쪽부터 차례로 채움: 구석부터 고르면 두 번째 상품이 반대쪽 구석으로 가서 가운데가 남고,
        # 먼 쪽 끝은 팔을 거의 다 뻗어야 해서 도달 못 함 (시뮬: 로봇에서 69cm 자리)
        for k in order:
            a, b = int(i0[k]), int(j0[k])
            if not self._stack_allowed(item, a, b, cu, cv, float(base[a, b])):
                continue
            x, y = self._to_xy((a + cu / 2) * self.cell - self.length / 2, (b + cv / 2) * self.cell - self.width / 2)
            spot = Spot(float(x), float(y), a, b, cu, cv, float(base[a, b]), cu * self.cell, cv * self.cell, rotated)
            return (float(level[k]), float(near[k]), int(corner[k])), spot
        return None

    def _stack_allowed(self, item, a, b, cu, cv, base):
        """쌓을 때: 받침이 되는 '우리가 넣은 물건' 중 깨지기 쉬운 것 / 더 가벼운 것이 있으면 안 됨"""
        if base <= config.OCCUPIED_HEIGHT or not self.placed:
            return True
        area = np.zeros((self.nu, self.nv), bool)
        area[a:a + cu, b:b + cv] = True
        supports = []
        for p in self.placed:
            if abs(p.top - base) > config.SUPPORT_TOL + 10 or not (self._footprint_mask(p.x, p.y, p.du, p.dv) & area).any():
                continue                                                  # 이 물건 위에 얹히는 게 아님
            if config.NO_STACK_ON_FRAGILE and p.fragile:
                return False
            if config.NO_HEAVIER_ON_LIGHTER and p.weight < item.weight:
                return False
            supports.append(p)
        if any(not p.stackable for p in supports):                        # 눕힌 캔(굴러감) 위
            return config.BRIDGE_ON_LYING and len(supports) >= 2          # 나란히 누운 두 개 사이 홈에 걸칠 때만
        return True

    def plan(self, item):
        """item 을 넣을 계획 → Plan(spot, moves). 바로 넣을 수 있으면 moves 는 빈 목록, 방법이 없으면 spot=None"""
        H = self.height_grid()
        spot = self.find_spot(item, H)
        if spot:
            return Plan(spot)
        mc = int(math.ceil(config.GRIPPER_CLEARANCE / self.cell))
        movable = [o for o in self.objects if o.movable]
        for obj in sorted(movable, key=lambda o: int(o.mask.sum())):          # 작은 물건부터 옮겨 본다
            H_wo = H.copy()
            H_wo[obj.mask] = 0.0
            occupied_wo = self.occupied & ~obj.mask
            i0, i1, j0, j1 = obj.bbox
            bu, bv = i1 - i0 + 1, j1 - j0 + 1
            for wi, wj in self._candidates(occupied_wo, bu + 2 * mc, bv + 2 * mc):   # 옮길 곳은 바닥의 빈자리
                di, dj = int(wi) + mc, int(wj) + mc           # 손가락 여유를 뺀 물건 자리
                if math.hypot(di - i0, dj - j0) * self.cell < config.MIN_MOVE_DIST:
                    continue
                H_after = H_wo.copy()
                # 옮긴 물건은 가장자리가 칸에 걸치는 정도가 달라질 수 있어 한 칸 넓게 표시
                H_after[max(0, di - 1):di + bu + 1, max(0, dj - 1):dj + bv + 1] = obj.top
                spot = self.find_spot(item, H_after)
                if spot:
                    du, dv = (di - i0) * self.cell, (dj - j0) * self.cell      # 봉투 좌표에서의 평행이동
                    c, s = math.cos(self.yaw), math.sin(self.yaw)
                    move = Move(obj, obj.x + du * c - dv * s, obj.y + du * s + dv * c, (di, di + bu - 1, dj, dj + bv - 1))
                    return Plan(spot, [move])
        return Plan(None)

    # ------------------------------------------------------------ 그림 (위에서 내려다본 지도)
    def draw(self, plan=None, path=None, px=8):
        path = path or config.LAST_BASKET_IMAGE
        img = np.full((self.nv * px, self.nu * px, 3), 255, np.uint8)       # 가로 = 봉투 가로(u)
        for i in range(self.nu):
            for j in range(self.nv):
                if self.edge[i, j]:
                    color = (225, 225, 225)
                elif self.unknown[i, j]:
                    color = (170, 170, 170)
                elif self.shadow[i, j]:
                    color = (245, 225, 200)
                elif self.occupied[i, j]:
                    shade = int(max(40, 220 - self.height[i, j]))
                    color = (shade, shade, 255)
                else:
                    continue
                cv2.rectangle(img, (i * px, j * px), ((i + 1) * px - 1, (j + 1) * px - 1), color, -1)
        for obj in self.objects:
            i0, i1, j0, j1 = obj.bbox
            cv2.rectangle(img, (i0 * px, j0 * px), ((i1 + 1) * px, (j1 + 1) * px), (0, 0, 160), 1)
            cv2.putText(img, f"#{obj.index} {obj.top:.0f}mm" + ("" if obj.movable else " X"), (i0 * px + 2, j0 * px + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 120), 1)
        if plan:
            for mv in plan.moves:
                i0, i1, j0, j1 = mv.dest_bbox
                cv2.rectangle(img, (i0 * px, j0 * px), ((i1 + 1) * px, (j1 + 1) * px), (200, 120, 0), 2)
                a = ((mv.obj.bbox[0] + mv.obj.bbox[1] + 1) * px // 2, (mv.obj.bbox[2] + mv.obj.bbox[3] + 1) * px // 2)
                b = ((i0 + i1 + 1) * px // 2, (j0 + j1 + 1) * px // 2)
                cv2.arrowedLine(img, a, b, (200, 120, 0), 2, tipLength=0.2)
            if plan.spot:
                s = plan.spot
                cv2.rectangle(img, (s.i0 * px, s.j0 * px), ((s.i0 + s.fu) * px, (s.j0 + s.fv) * px), (0, 170, 0), 3)
                label = f"NEW on {s.base:.0f}mm" if s.stacked else "NEW"
                cv2.putText(img, label, (s.i0 * px + 4, (s.j0 + s.fv) * px - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 0), 2)
        cv2.imwrite(path, img)
        return path
