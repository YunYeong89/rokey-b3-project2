"""정면(수평) 집기 — 캔 음료·컵라면을 앞에서 정중앙을 수평으로 집고, 수평 그대로 봉투 바닥 가까이 놓는다.

  집기:  매대 촬영 자세에서 카메라가 보는 방향의 수평 성분 = 다가갈 방향 a
         DINO 상자 가운데 픽셀의 깊이 → 상품 앞면 가운데 → a 로 반지름(짧은 변/2)만큼 더 들어간 점 = 정중앙
         그리퍼는 a 를 향해 수평, 손가락은 좌우(수평)로 닫힘 → arm.pick 이 a 반대쪽 100mm 에서 수평으로 다가가 집고 물러남
  놓기:  bag_release() — 상품 바닥이 받침면 위 FRONT_RELEASE_CLEARANCE(5mm) 가 되는 높이까지 수평 그대로 내려서 놓는다.
         단, 그리퍼가 봉투 입구 아래로 내려가려면 손가락 끝~플랜지~손목이 전부 입구 안에 들어가고 봉투 안 물건보다 높아야 함.
         여러 방향(로봇에서 바깥쪽, 봉투 가로·세로)으로 넣어 보고 들어가는 방향을 고르고,
         어느 방향도 안 되면 그리퍼 바닥이 입구 테두리 위(RIM_MARGIN)에 오는 높이에서 놓는다 (떨어뜨리는 높이를 알려 줌).
"""
import math
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation

import config
from vision import geometry


def _zyz(R):
    """회전 행렬 → ZYZ 각도. 그리퍼가 정확히 아래(ry=180)면 특이점이라 scipy 가 경고를 내지만 결과 자세는 맞다"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return [float(a) for a in Rotation.from_matrix(R).as_euler("ZYZ", degrees=True)]


def is_front(pid):
    return pid in config.FRONT_GRASP


def forward_dir(posx):
    """카메라가 보는 방향의 수평 성분 (base 단위 벡터). 거의 수직으로 내려다보면 None"""
    T = geometry.pose_matrix(posx) @ geometry._GRIPPER2CAM
    d = np.array([T[0, 2], T[1, 2], 0.0])
    n = float(np.linalg.norm(d))
    return d / n if n > 0.5 else None          # 수평에서 60° 넘게 내려다보면 정면이 아님


def level_pose(xyz, direction):
    """그리퍼가 direction(수평)을 향하고, 손가락이 수평으로 닫히는 자세 posx [x, y, z, rx, ry, rz]"""
    z = np.array([direction[0], direction[1], 0.0])
    z /= np.linalg.norm(z)
    side = np.cross([0.0, 0.0, 1.0], z)       # 수평이면서 z 에 수직 = 손가락이 닫히는 방향
    if config.GRIPPER_CLOSE_AXIS == "x":
        x = side
        y = np.cross(z, x)
    else:
        y = side
        x = np.cross(y, z)
    return [float(v) for v in xyz[:3]] + _zyz(np.column_stack([x, y, z]))


def into_center(surface, posx, r):
    """카메라에 보이는 둥근 물건 앞면 가운데 점 → 축(중심). 보이는 점은 '카메라 쪽'을 향한 곳이라
    카메라 → 그 점 방향(수평)으로 반지름만큼 더 가야 축 (다가가는 방향으로 가면 비스듬히 볼 때 r·sinθ 만큼 어긋남 — 시뮬 13mm)"""
    s = np.asarray(surface, float)
    d = s - geometry.camera_position(posx)
    d[2] = 0.0
    n = float(np.linalg.norm(d))
    return s + (d / n) * r if n > 1e-6 else s


def grasp_pose(surface, direction, item, posx=None):
    """앞면 가운데 점 surface → 정중앙을 집는 수평 자세 (posx 를 주면 카메라 시선 방향으로 반지름만큼 들어감)"""
    if posx is not None:
        center = into_center(surface, posx, item.width / 2.0)
    else:
        center = np.asarray(surface, float) + np.asarray(direction, float) * (item.width / 2.0)
    return level_pose(center, direction)


@dataclass
class Release:
    pose: list            # 놓는 자세 (그리퍼 = 상품 중심)
    above_z: float        # 봉투 위에서 내려오기 시작하는 높이 (입구에 안 걸리는 높이)
    inside: bool          # 그리퍼가 입구 아래로 들어가는지
    drop: float           # 원하는 높이(바닥 위 5mm)보다 높이 놓는 만큼(mm) — 0 이면 원하는 대로
    why: str = ""         # inside=False 인 이유


# ------------------------------------------------------------ 눕혀서 넣기 (캔 음료)
def upright_fits(item):
    """세운 채 수평 그리퍼가 입구 아래로 들어갈 수 있는 자리·방향이 하나라도 있는지 (봉투 크기만으로, 빈 봉투 기준)"""
    L, W = config.BASKET_SIZE
    c, s = math.cos(math.radians(config.BASKET_YAW)), math.sin(math.radians(config.BASKET_YAW))
    for u in np.arange(-L / 2, L / 2 + 1, 20.0):
        for v in np.arange(-W / 2, W / 2 + 1, 20.0):
            x, y = config.BASKET_CENTER[0] + u * c - v * s, config.BASKET_CENTER[1] + u * s + v * c
            if any(fits_inside(x, y, d, item, 0.0)[0] for d in reachable_dirs(x, y)):
                return True
    return False


def lying_item(item):
    """눕힌 캔의 모양 — 길이 = 캔 높이, 폭·높이 = 지름 (봉투 자리 계획용, 위에서 쥔 상품처럼 손가락 자리 검사)"""
    from logic.basket import Item
    return Item(item.pid, item.weight, max(item.height, item.width), item.width, item.width, item.fragile, False)


def lying_release(spot, lying, bm=None):
    """정중앙을 쥔 캔을 그리퍼를 아래로 돌려 눕힌 채, 캔 옆면(바닥)이 받침면 위 FRONT_RELEASE_CLEARANCE 가 되게 놓는 자세.
    캔 축(= 세워 쥐었을 때 위쪽)은 자리의 긴 변 방향 (spot.rotated 이면 봉투 세로)."""
    a = math.radians(config.BASKET_YAW + (90.0 if spot.rotated else 0.0))
    axis = np.array([math.cos(a), math.sin(a), 0.0])
    z = np.array([0.0, 0.0, -1.0])
    if config.GRIPPER_CLOSE_AXIS == "x":       # 수평으로 쥘 때 도구 y = 위 = 캔 축 (level_pose)
        y = axis
        x = np.cross(y, z)
    else:                                      # 도구 x = 아래 = 캔 축 반대 (캔은 앞뒤 대칭)
        x = axis
        y = np.cross(z, x)
    angles = _zyz(np.column_stack([x, y, z]))
    floor = config.BASKET_CENTER[2]
    zc = floor + spot.base + config.FRONT_RELEASE_CLEARANCE + lying.height / 2
    pose = [float(spot.x), float(spot.y), float(zc)] + angles
    # 내려오기 시작하는 높이: 봉투 입구(평소 채움 높이)와 지금 봉투 안 가장 높은 물건 위로 (최대 높이 200mm 로 잡으면 팔을 너무 뻗음)
    tallest = float(np.nanmax(bm.height_grid())) if bm is not None and bm.points_inside else config.CONTAINER_MAX
    wall = max(config.CONTAINER_DEPTH, tallest)
    above = max(zc + config.BASKET_APPROACH, floor + wall + lying.height / 2 + config.RIM_MARGIN)
    return Release(pose, above, True, 0.0)


def _bag_uv(xs, ys):
    c, s = math.cos(math.radians(config.BASKET_YAW)), math.sin(math.radians(config.BASKET_YAW))
    dx, dy = np.asarray(xs) - config.BASKET_CENTER[0], np.asarray(ys) - config.BASKET_CENTER[1]
    return dx * c + dy * s, -dx * s + dy * c


def gripper_points(x, y, direction, item_width, step=10.0):
    """수평 그리퍼를 위에서 본 자리 (손가락 끝 ~ 플랜지 직사각형 + 손목 원) 의 점들 (xs, ys)"""
    d = np.array([direction[0], direction[1]]) / np.hypot(direction[0], direction[1])
    side = np.array([-d[1], d[0]])
    front = max(item_width / 2.0, 15.0)
    half = max(config.GRIPPER_HALF_WIDTH, item_width / 2.0 + 15.0)
    along = np.arange(-config.GRIPPER_LENGTH, front + 1e-6, step)
    across = np.arange(-half, half + 1e-6, step)
    A, S = np.meshgrid(along, across)
    pts = [np.stack([A.ravel(), S.ravel()], 1)]
    ang = np.linspace(0, 2 * math.pi, 24, endpoint=False)
    for r in (config.WRIST_RADIUS, config.WRIST_RADIUS / 2):
        pts.append(np.stack([-config.GRIPPER_LENGTH + r * np.cos(ang), r * np.sin(ang)], 1))
    P = np.concatenate(pts)
    return x + P[:, 0] * d[0] + P[:, 1] * side[0], y + P[:, 0] * d[1] + P[:, 1] * side[1]


def fits_inside(x, y, direction, item, axis_z, bm=None):
    """그리퍼 축 높이 axis_z (base mm) 로 봉투 입구 아래에 들어갈 수 있는지 → (bool, 이유)"""
    xs, ys = gripper_points(x, y, direction, item.width)
    u, v = _bag_uv(xs, ys)
    lu = config.BASKET_SIZE[0] / 2 - config.RIM_MARGIN
    lv = config.BASKET_SIZE[1] / 2 - config.RIM_MARGIN
    out = (np.abs(u) > lu) | (np.abs(v) > lv)
    if out.any():
        need = config.GRIPPER_LENGTH + max(item.width / 2, 15.0) + config.WRIST_RADIUS
        return False, (f"손가락 끝~손목 길이 {need:.0f}mm 가 입구 {config.BASKET_SIZE[0]:.0f}x{config.BASKET_SIZE[1]:.0f}mm "
                       f"(테두리 여유 {config.RIM_MARGIN:.0f}mm) 안에 안 들어감")
    if bm is not None:
        bottom = axis_z - config.GRIPPER_BELOW - config.BASKET_CENTER[2]      # 그리퍼 바닥 (봉투 바닥 기준)
        own = np.hypot(xs - x, ys - y) <= item.width / 2 + 5                  # 상품이 놓일 자리 자체는 제외
        h = bm.heights_at(xs[~own], ys[~own])
        h = h[np.isfinite(h)]
        if h.size and h.max() > bottom - config.RIM_MARGIN:
            return False, f"봉투 안 물건(높이 {h.max():.0f}mm)이 그리퍼 바닥({bottom:.0f}mm)에 닿음"
    return True, ""


def reachable_dirs(x, y):
    """(x, y) 에 수평 그리퍼를 둘 수 있는 방향들 — 플랜지(손목)가 로봇 축에서 WRIST_REACH 범위 안.
    로봇에서 바깥쪽을 향하는 방향부터 (팔이 편함). 범위 안이 하나도 없으면 바깥쪽 방향 하나."""
    radial = np.array([x - config.NEAR_ORIGIN[0], y - config.NEAR_ORIGIN[1]])
    radial = radial / max(np.linalg.norm(radial), 1e-6)
    c, s = math.cos(math.radians(config.BASKET_YAW)), math.sin(math.radians(config.BASKET_YAW))
    cands = [radial] + [np.array(v) for v in ((c, s), (-c, -s), (-s, c), (s, -c))]
    cands += [np.array([math.cos(a), math.sin(a)]) for a in np.radians(np.arange(0, 360, 45))]
    lo, hi = config.WRIST_REACH

    def ok(d):
        fx, fy = x - d[0] * config.GRIPPER_LENGTH, y - d[1] * config.GRIPPER_LENGTH
        return lo <= math.hypot(fx - config.NEAR_ORIGIN[0], fy - config.NEAR_ORIGIN[1]) <= hi

    good = sorted([d for d in cands if ok(d)], key=lambda d: -float(np.dot(d, radial)))
    return good or [radial]


def bag_release(spot, item, bm=None):
    """봉투 자리 spot 에 수평 그리퍼로 놓는 방법 → Release"""
    floor = config.BASKET_CENTER[2]
    rim = floor + config.CONTAINER_DEPTH
    want = floor + spot.base + config.FRONT_RELEASE_CLEARANCE + item.height / 2     # 상품 중심(=그리퍼 축) 높이
    dirs = reachable_dirs(spot.x, spot.y)
    radial = dirs[0]
    first_why = ""
    for d in dirs:
        ok, why = fits_inside(spot.x, spot.y, d, item, want, bm)
        if ok:
            pose = level_pose([spot.x, spot.y, want], d)
            top = floor + config.CONTAINER_MAX                                  # 봉투 안 물건이 최대 이 높이까지
            above = max(want + config.BASKET_APPROACH, top + item.height / 2 + config.RIM_MARGIN,
                        top + config.GRIPPER_BELOW + config.RIM_MARGIN)
            return Release(pose, above, True, 0.0)
        first_why = first_why or why
    z = max(want, rim + config.GRIPPER_BELOW + config.RIM_MARGIN)                   # 그리퍼 바닥이 입구 테두리 위
    pose = level_pose([spot.x, spot.y, z], radial)
    above = max(z + 50.0, floor + config.CONTAINER_MAX + item.height / 2 + config.RIM_MARGIN)
    return Release(pose, above, False, z - want, first_why)
