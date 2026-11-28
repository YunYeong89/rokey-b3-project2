"""상품을 찾아 집어서 비닐봉투(바구니)에 넣는 순서.

함수 이름은 pickup_web/robot_order_node.py 의 TODO 자리와 같다 → 그 노드에서 그대로 불러 쓰면 된다.
  move_to_view(shelf)   매대 촬영 자세로 이동 ("upper" / "lower")
  count_items(pid)      카메라로 그 상품 개수 세기 → int
  pick_one(pid)         그 상품 하나 집기 → 성공 여부
  place_in_basket()     봉투에 넣기 (prepare_basket 이 찾아 둔 자리 — 바닥 또는 다른 물건 위)

봉투 빈자리 · 쌓기 (logic/basket.py):
  prepare_basket(pid)   상품을 집기 '전에' 호출. 봉투를 찍어 바닥이나 다른 물건 위에 놓을 자리를 찾고,
                        없으면 다른 물품을 옮겨 자리를 만든다 (그리퍼가 비어 있어야 옮길 수 있으므로) → 자리 없으면 None
넣는 순서:
  order_sequence(items) 주문 {상품: 개수} → ① 무거운 물품 → ② 큰 물품 → ③ 가볍고 작은 물품 순서의 목록 (config.PICK_PRIORITY="weight")
  run_nearest(items)    매대를 찍어 보고 ① 가까운 물품 → ② 무거운 물품 순서로 하나씩 집어 넣기 (config.PICK_PRIORITY="near_heavy")
"""
import json
import math
import time
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation

import config
from logic import front_grasp, neighbors
from logic.basket import BasketMap, Item, Placed
from vision import geometry
from vision.detector import draw


def load_products():
    """products.json 에서 로봇이 집는 실물 상품(robot: true + config.EXTRA_ROBOT_PRODUCTS)만 → [dict, ...]"""
    with open(config.products_file(), encoding="utf-8") as f:
        products = json.load(f)["products"]
    extra = config.EXTRA_ROBOT_PRODUCTS
    have = {p["id"] for p in products}
    return ([dict(p, robot=True, **extra.get(p["id"], {})) for p in products if p.get("robot") or p["id"] in extra]
            + [dict(p) for p in config.NEW_PRODUCTS if p["id"] not in have])


def find_product(products, text):
    """키보드 입력(번호 / id / 이름 일부) → 상품 dict 또는 None"""
    text = text.strip()
    if text.isdigit() and 1 <= int(text) <= len(products):
        return products[int(text) - 1]
    alias = config.PRODUCT_ALIASES.get(text) or config.PRODUCT_ALIASES.get(text.lower())
    if alias:
        return next((p for p in products if p["id"] == alias), None)
    for p in products:
        if text.lower() == p["id"].lower() or text == p["name"]:
            return p
    matches = [p for p in products if text and (text in p["name"] or text.lower() in p["id"].lower())]
    return matches[0] if len(matches) == 1 else None


def item_spec(pid, info=None):
    """상품 무게·크기 (config.PRODUCT_SPECS, 없으면 DEFAULT_SPEC) + products.json 의 fragile → Item"""
    spec = config.PRODUCT_SPECS.get(pid, config.DEFAULT_SPEC)
    length, width, height = spec["size"]
    return Item(pid, float(spec["weight"]), float(max(length, width)), float(min(length, width)), float(height),
                bool((info or {}).get("fragile")), front_grasp.is_front(pid))


PRIORITY_NAMES = {0: "무거운 물품", 1: "큰 물품", 2: "가볍고 작은 물품"}


def priority(item):
    """0 = 무거운 물품, 1 = 큰 물품, 2 = 가볍고 작은 물품 (무겁고 큰 물품은 0)"""
    if item.weight >= config.HEAVY_WEIGHT_G:
        return 0
    volume_cm3 = item.length * item.width * item.height / 1000.0
    if volume_cm3 >= config.BIG_VOLUME_CM3 or item.length >= config.BIG_LENGTH_MM:
        return 1
    return 2


def order_sequence(items, info=None):
    """주문 {pid: 개수} → 넣을 순서대로 pid 목록. 같은 단계 안에서는 무거운 것 → 부피 큰 것 순서"""
    info = info or {}
    specs = {pid: item_spec(pid, info.get(pid)) for pid in items}
    ranked = sorted(items, key=lambda pid: (priority(specs[pid]), -specs[pid].weight,
                                            -specs[pid].length * specs[pid].width * specs[pid].height))
    return [pid for pid in ranked for _ in range(int(items[pid]))]


@dataclass
class Candidate:
    """매대에서 찾은 상품 하나 (우선순위 집기용) — 집을 때 다시 찍지 않도록 찍은 사진·자세도 함께 둔다"""
    pid: str
    shelf: str
    box: object
    u: int
    v: int
    z: float              # 카메라 깊이 (mm)
    xyz: np.ndarray       # 상품 겉면 base 좌표 (mm)
    dist: float           # 기준점(NEAR_ORIGIN)에서 수평 거리 (mm)
    weight: float         # 표의 무게 (g)
    color: np.ndarray     # 찍은 사진 (같은 매대 후보끼리 공유)
    boxes: list           # 그 사진에서 찾은 전체 상자 (그림용)
    depth: np.ndarray
    intrinsics: dict
    posx: list


def pick_first(candidates):
    """① 가장 가까운 것 (NEAR_TOL 안이면 똑같이 가깝다고 봄) → ② 그중 무거운 것 → ③ 그래도 같으면 더 가까운 것"""
    nearest = min(c.dist for c in candidates)
    near = [c for c in candidates if c.dist <= nearest + config.NEAR_TOL]
    return max(near, key=lambda c: (c.weight, -c.dist))


def rank_candidates(candidates):
    """pick_first 를 되풀이한 전체 순서 (로그·확인용)"""
    rest, ranked = list(candidates), []
    while rest:
        ranked.append(pick_first(rest))
        rest.remove(ranked[-1])
    return ranked


def close_axis_angle(posx):
    """그리퍼 손가락이 닫히는 방향의 base 수평 각도(deg)"""
    R = Rotation.from_euler("ZYZ", posx[3:6], degrees=True).as_matrix()
    close = R[:, 0 if config.GRIPPER_CLOSE_AXIS == "x" else 1]
    return math.degrees(math.atan2(close[1], close[0]))


def turn_to(posx, close_deg):
    """손가락 닫히는 방향이 close_deg(base 수평 각도) 가 되도록 base z축으로 돌린 자세 (손가락 대칭 → ±90° 안)"""
    turn = ((close_deg - close_axis_angle(posx)) + 90.0) % 180.0 - 90.0
    return geometry.yawed(posx, turn)


class PickPlace:
    def __init__(self, arm, camera, detector, products):
        self.arm, self.camera, self.detector = arm, camera, detector
        self.info = {p["id"]: p for p in products}
        self.reserved_spot = None       # prepare_basket 이 찾아 둔 상품 자리
        self.placed = []                # 우리가 봉투에 넣은 물건 기록 [Placed] — 쌓기 규칙에 사용
        self.held_height = None         # 매대에서 잰 상품 높이 (mm) — 놓는 높이 계산에 사용
        self.held_oriented = False      # 손가락이 상품 짧은 변을 잡았는지 (봉투에서 방향을 맞출 수 있는지)
        self.held_front = False         # 정면(수평)으로 쥐고 있는지 → 봉투에도 수평 그대로 놓음
        self.basket_map = None          # prepare_basket 때 찍은 봉투 지도 (수평 그리퍼가 봉투 안 물건에 닿는지 검사)
        self.reserved_mode = "top"      # 찾아 둔 자리에 놓는 방법: top(위에서) / upright(수평, 세워서) / lying(눕혀서) / drop(입구 위에서)
        self.skipped = []               # 마지막 주문에서 못 넣은 상품 [(pid, 이유)]
        self.returned = []              # 봉투에서 꺼내 내려놓은 상품 [(pid, 자리 번호)] — 내려놓는 곳 자리 관리
        self.remove_note = ""           # 마지막 remove_from_bag 에서 다 못 꺼낸 이유

    def prompt(self, pid):
        return config.prompts_of(pid)[0]

    def item(self, pid):
        return item_spec(pid, self.info.get(pid))

    def bag_ways(self, pid):
        """봉투에 넣는 방법 후보 [(자리 계획에 쓸 상품 모양, 방법)] — 앞에 있는 것부터 시도
          top: 위에서 쥔 상품 / upright: 수평 그대로 입구 아래로 / lying: 눕혀서 (LAY_DOWN) / drop: 입구 위에서 놓기"""
        item = self.item(pid)
        if not item.side_grasp:
            return [(item, "top")]
        if front_grasp.upright_fits(item):
            return [(item, "upright")]
        ways = [(front_grasp.lying_item(item), "lying")] if pid in config.LAY_DOWN else []
        return ways + [(item, "drop")]

    def new_bag(self):
        """새 봉투로 바꿨을 때 — 넣은 물건 기록을 비운다"""
        self.placed = []
        self.reserved_spot = None

    # ------------------------------------------------------------ robot_order_node 와 같은 이름
    def move_to_view(self, shelf):
        joints = config.VIEW_POSES[shelf]
        print(f"    [arm] 매대 촬영 자세 ({shelf}) {joints}")
        self.arm.movej(joints)
        time.sleep(config.SETTLE_SEC)

    def look(self, pid):
        """사진을 찍고 상품을 찾는다 → (그 상품 상자, 전체 상자, 컬러, 깊이, 내부값, 찍을 때 로봇 자세)"""
        posx = self.arm.current_posx()
        color, depth, intrinsics = self.camera.capture(self.arm.rclpy)
        t = time.monotonic()
        mine, boxes = self.detector.find(color, self.prompt(pid))
        others = [f"{b.label} {b.score:.2f}" for b in boxes if b not in mine]
        print(f"    [vision] '{self.prompt(pid)}' {len(mine)}개 찾음 ({time.monotonic() - t:.1f}s)"
              + "".join(f"\n             - 점수 {b.score:.2f} 중심 {b.center}" for b in mine)
              + (f"\n             (상품이 아닌 것으로 분류: {', '.join(others)})" if others else ""))
        return mine, boxes, color, depth, intrinsics, posx

    def count_items(self, pid):
        mine, boxes, color, *_ = self.look(pid)
        draw(color, boxes, targets=mine)
        return len(mine)

    @staticmethod
    def _measure_height(box, depth, top_z, ring=15):
        """상품 높이 = (상자 둘레 선반 깊이) - (상품 윗면 깊이). 위에서 찍었을 때만 의미 있음 → mm 또는 None"""
        h, w = depth.shape[:2]
        x1, y1 = max(0, int(box.x1) - ring), max(0, int(box.y1) - ring)
        x2, y2 = min(w, int(box.x2) + ring), min(h, int(box.y2) + ring)
        region = depth[y1:y2, x1:x2].astype(float)
        inner = np.zeros(region.shape, bool)
        inner[int(box.y1) - y1:int(box.y2) - y1, int(box.x1) - x1:int(box.x2) - x1] = True
        around = region[~inner & (region > 0)]
        if around.size < 20:
            return None
        height = float(np.median(around)) - top_z
        return height if 10.0 <= height <= 250.0 else None

    def pick_one(self, pid):
        """매대 촬영 자세에서 호출. 상품을 찾아서 집는다 → 성공 여부"""
        mine, boxes, color, depth, intrinsics, posx = self.look(pid)
        target = None
        for box in mine:                                    # 점수 높은 순서로, 깊이를 잴 수 있는 첫 상자
            u, v = box.center
            z = geometry.depth_at(depth, u, v)
            if z:
                target = (box, u, v, z)
                break
        image = draw(color, boxes, chosen=target[0] if target else None, targets=mine)
        if target is None:
            print(f"    [vision] 집을 수 있는 '{self.prompt(pid)}' 이(가) 없습니다 (그림: {image})")
            return False

        box, u, v, z = target
        return self._grasp(pid, box, u, v, z, depth, intrinsics, posx, image)

    def _grasp(self, pid, box, u, v, z, depth, intrinsics, posx, image):
        """찍은 사진에서 고른 상자(box, 픽셀 u v, 깊이 z)를 집고 대기 자세로 → 성공 여부"""
        fragile = bool(self.info.get(pid, {}).get("fragile"))
        self.held_front = False
        if front_grasp.is_front(pid):
            direction = front_grasp.forward_dir(posx)
            if direction is not None:
                return self._grasp_front(pid, box, u, v, z, depth, intrinsics, posx, image, direction, fragile)
            print("    [vision] ⚠️ 카메라가 매대를 정면이 아니라 위에서 보고 있어 위에서 집습니다 (정면 집기는 촬영 자세를 수평으로 티칭)")
        self.held_height = self._measure_height(box, depth, z)
        spec_h = self.item(pid).height
        print("    [vision] 상품 높이: " + (f"RealSense 로 잰 값 {self.held_height:.0f}mm (표 {spec_h:.0f}mm)"
                                          if self.held_height else f"재지 못해 표의 값 {spec_h:.0f}mm 사용"))
        surface = geometry.pixel_to_base(u, v, z, intrinsics, posx)
        approach = geometry.tool_z_axis(posx)                # 그리퍼가 향하는 방향
        grasp = surface + approach * config.PICK_DEPTH        # 겉면에서 그리퍼 방향으로 더 들어감
        grasp[2] = max(grasp[2], config.MIN_PICK_Z)
        pose = [float(grasp[0]), float(grasp[1]), float(grasp[2])] + posx[3:]   # 방향은 촬영 자세에서 시작
        # 손가락이 상품의 짧은 변을 잡도록 돌린다 (DINO 상자의 가로·세로로 판단) → 봉투에 놓을 때 방향을 맞출 수 있음
        if approach[2] < -0.9:                               # 위에서 내려다보며 집을 때만
            R_cam = (geometry.pose_matrix(posx) @ geometry._GRIPPER2CAM)[:3, :3]
            short = R_cam[:, 1] if (box.x2 - box.x1) >= (box.y2 - box.y1) else R_cam[:, 0]   # 사진 세로/가로 방향
            pose = turn_to(pose, math.degrees(math.atan2(short[1], short[0])))
            self.held_oriented = True
        else:
            print("    [vision] ⚠️ 위에서 집는 자세가 아니라 상품 방향을 맞추지 않습니다 (봉투 안 방향이 계획과 다를 수 있음)")
            self.held_oriented = False
        print(f"    [vision] 고른 상자 점수 {box.score:.2f}, 픽셀 ({u}, {v}), 깊이 {z:.0f}mm "
              f"→ 로봇 좌표 [{pose[0]:.1f}, {pose[1]:.1f}, {pose[2]:.1f}] (그림: {image})")
        grabbed = self.arm.pick(pose, approach, fragile=fragile)
        if grabbed:
            self.arm.movej(config.JREADY, slow=fragile)     # 물건을 든 채 대기 자세를 거쳐 봉투로 (특이점 회피)
        return grabbed

    @staticmethod
    def _open_width(item):
        """상품을 집을 때 벌리는 폭 — 상품 폭 + 양쪽 TARGET_OPEN_MARGIN (최대 GRIPPER_OPEN)"""
        return min(config.GRIPPER_OPEN, item.width + 2 * config.TARGET_OPEN_MARGIN)

    def _grasp_front(self, pid, box, u, v, z, depth, intrinsics, posx, image, direction, fragile):
        """정면에서 정중앙을 수평으로 집는다 (logic/front_grasp.py). 집은 뒤에도 수평 그대로 → 봉투로"""
        item = self.item(pid)
        surface = geometry.pixel_to_base(u, v, z, intrinsics, posx)          # 상품 앞면 가운데 (상자 가운데)
        # 높이 정중앙: 내려다보면 상자 가운데가 윗면 쪽으로 올라감 → 상자 아래 끝(앞쪽 바닥)에서 상품 높이의 반만큼 위
        #   (그대로 쥐면 눕혀 넣을 때 캔이 축 방향으로 그만큼 밀림 — 시뮬에서 25mm 밀려 봉투 밖으로 14%)
        #   상자 아래 끝 픽셀의 광선이 '앞면을 지나는 수직면'(법선 = 다가가는 방향)과 만나는 점 = 앞쪽 바닥
        #   (그 픽셀을 가운데 깊이로 되돌리면 비스듬히 볼 때 바닥이 실제보다 높게 나옴 — 시뮬에서 +31mm)
        T = geometry.pose_matrix(posx) @ geometry._GRIPPER2CAM
        ray = T[:3, :3] @ np.array([(box.center[0] - intrinsics["ppx"]) / intrinsics["fx"],
                                    (box.y2 - intrinsics["ppy"]) / intrinsics["fy"], 1.0])
        a = np.asarray(direction, float)
        denom = float(ray @ a)
        bottom = T[:3, 3] + ray * (float((surface - T[:3, 3]) @ a) / denom) if abs(denom) > 1e-6 else surface
        mid_z = bottom[2] + item.height / 2
        if abs(mid_z - surface[2]) < item.height / 2:
            surface = np.array([surface[0], surface[1], mid_z])
        pose = front_grasp.grasp_pose(surface, direction, item, posx)
        self.held_height, self.held_oriented = None, False
        gap = self._shelf_edge_gap(box, depth, intrinsics, posx, direction, surface, item)
        # 다가가기 시작·빼낼 거리 (정중앙 기준): 상품 뒷면(정중앙 + 반지름)이 선반 앞 끝(정중앙 - 반지름 - gap)보다
        # SHELF_CLEARANCE 더 로봇 쪽에 오도록 = 지름 + gap + SHELF_CLEARANCE
        out = item.width + gap + config.SHELF_CLEARANCE
        print(f"    [vision] 정면 집기: 고른 상자 점수 {box.score:.2f}, 픽셀 ({u}, {v}), 깊이 {z:.0f}mm → 앞면 "
              f"[{surface[0]:.1f}, {surface[1]:.1f}, {surface[2]:.1f}] + 반지름 {item.width / 2:.0f}mm "
              f"→ 정중앙 [{pose[0]:.1f}, {pose[1]:.1f}, {pose[2]:.1f}] (그림: {image})")
        back = np.asarray(pose[:3]) - np.asarray(direction) * out
        wrist = back[:2] - np.asarray(direction[:2]) * config.GRIPPER_LENGTH
        if math.hypot(*(wrist - np.asarray(config.NEAR_ORIGIN))) < config.WRIST_REACH[0]:
            print(f"    [vision] ⚠️ 선반이 로봇에 가까워, 선반에서 {config.SHELF_CLEARANCE:.0f}mm 빼는 자리에서 손목이 로봇 축에 "
                  f"{math.hypot(*(wrist - np.asarray(config.NEAR_ORIGIN))):.0f}mm 까지 붙습니다 (도달 못 할 수 있음 — 매대를 더 멀리)")
        print(f"    [vision] 선반 앞 끝: 상품 앞면에서 {gap:.0f}mm → 정중앙에서 {out:.0f}mm 떨어진 곳에서 다가가고, "
              f"쥔 뒤 {config.PICK_LIFT:.0f}mm 들어 그곳까지 뺌 (상품 뒷면이 선반 끝에서 {config.SHELF_CLEARANCE:.0f}mm)")
        grabbed = self.arm.pick(pose, direction, fragile=fragile, approach_dist=out, lift=config.PICK_LIFT,
                                joint_approach=True, open_width=self._open_width(item))
        self.held_front = bool(grabbed)
        return grabbed

    @staticmethod
    def _shelf_edge_gap(box, depth, intrinsics, posx, direction, surface, item, band=150):
        """상품 앞면에서 선반 앞 끝까지 거리(mm, 정면 방향). 상자 아래 띠(선반 판)의 깊이 점 중 로봇 쪽으로 가장 나온 점.
        띠는 150px — 선반 안쪽 깊이 있는 물건은 40px 로는 선반 앞 끝까지 닿지 않아 짧게 잼 (시뮬: 11cm 안쪽 컵라면을 64mm 만 뺌)
        못 재면 SHELF_EDGE_GAP"""
        h, w = depth.shape[:2]
        x1, x2 = max(0, int(box.x1)), min(w, int(box.x2))
        y1, y2 = min(h, int(box.y2) + 3), min(h, int(box.y2) + 3 + band)
        region = depth[y1:y2, x1:x2].astype(float)
        vs, us = np.nonzero(region > 0)
        if len(vs) < 20:
            return config.SHELF_EDGE_GAP
        zs = region[vs, us]
        cam = np.stack([(us + x1 - intrinsics["ppx"]) * zs / intrinsics["fx"],
                        (vs + y1 - intrinsics["ppy"]) * zs / intrinsics["fy"], zs, np.ones_like(zs)])
        pts = ((geometry.pose_matrix(posx) @ geometry._GRIPPER2CAM) @ cam)[:3].T
        below = pts[:, 2] < surface[2] - item.height / 4                   # 상품 아래쪽 (선반 판)
        if below.sum() < 20:
            return config.SHELF_EDGE_GAP
        along = (pts[below] - surface) @ np.asarray(direction, float)       # 음수 = 상품 앞면보다 로봇 쪽
        gap = max(0.0, -float(np.percentile(along, 5)))
        return min(gap, config.SHELF_EDGE_GAP_MAX)

    # ------------------------------------------------------------ 옆 물건 치우기 (logic/neighbors.py)
    def _approach_out(self, box, depth, intrinsics, posx, direction, surface, width, height):
        """다가가기 시작·빼낼 거리 (정중앙 기준) = 지름 + 선반 앞 끝까지 + SHELF_CLEARANCE"""
        from types import SimpleNamespace
        gap = self._shelf_edge_gap(box, depth, intrinsics, posx, direction, surface, SimpleNamespace(height=height))
        return width + gap + config.SHELF_CLEARANCE

    def _shelf_move(self, o, src, dst, direction, out, width):
        """선반 위 물건 o 를 src 에서 집어 dst 에 내려놓는다 (정면·수평, width 만큼만 벌림). 성공 여부"""
        a = np.array([direction[0], direction[1], 0.0])
        up = np.array([0.0, 0.0, 1.0])
        print(f"    [arm] 옆 물건 {o.label}: ({src[0]:.0f}, {src[1]:.0f}) → ({dst[0]:.0f}, {dst[1]:.0f}), {width:.0f}mm 만 벌려서 집음")
        if not self.arm.pick(front_grasp.level_pose(src, a), a, approach_dist=out, lift=config.PICK_LIFT,
                             joint_approach=True, open_width=width):
            print(f"    [arm] ⚠️ {o.label} 을(를) 잡지 못했습니다")
            return False
        lift = up * config.PICK_LIFT
        self.arm.movel(front_grasp.level_pose(dst - a * out + lift, a))            # 선반 밖에서 옆으로
        self.arm.movel(front_grasp.level_pose(dst + lift, a))                      # 수평으로 넣고
        self.arm.movel(front_grasp.level_pose(dst + up * config.SHELF_PLACE_CLEARANCE, a), slow=True)
        self.arm.gripper.open_to(width)                                            # 다 벌리면 옆 물건에 닿음
        self.arm.movel(front_grasp.level_pose(dst - a * out + up * config.SHELF_PLACE_CLEARANCE, a))
        return True

    def clear_neighbors(self, c, gone=()):
        """c 를 집을 때 벌린 손가락에 닿는 옆 물건을 선반 위 옆자리로 옮긴다 → 되돌릴 목록 [(물건, 원래 중심, 임시 중심, 방향, 거리, 폭)]
        gone: 그 사진을 찍은 뒤 이미 집어 간 물건 자리 (사진에는 있지만 지금은 없음)"""
        a = front_grasp.forward_dir(c.posx)
        if a is None:
            return []
        obstacles, hidden = neighbors.estimate(c.boxes, c.depth, c.intrinsics, c.posx, a)
        obstacles = [o for o in obstacles if o.box is c.box or
                     not any(np.linalg.norm(np.asarray(g[:2]) - (o.center[:2] - np.asarray(a[:2]) * o.r)) < o.r + 20 for g in gone)]
        target = next((o for o in obstacles if o.box is c.box), None)
        if target is None:
            return []
        item = self.item(c.pid)
        target.r = item.width / 2                                                  # 집을 상품은 표의 크기
        out_t = self._approach_out(c.box, c.depth, c.intrinsics, c.posx, a, c.xyz, item.width, item.height)
        hits = neighbors.finger_hits(target.center, a, self._open_width(item), out_t, obstacles, exclude=[target])
        moved = []
        for o in hits:
            print(f"    [neighbor] {self.info[c.pid]['name']} 을(를) 집을 때 벌린 손가락이 옆 {o.label} 에 닿음 → 옆으로 치웁니다")
            rec = self._move_aside(o, target, obstacles, hidden, a, c.depth, c.intrinsics, c.posx)
            if rec:
                moved.append(rec)
        left = neighbors.finger_hits(target.center, a, self._open_width(item), out_t, obstacles, exclude=[target])
        if left:
            print(f"    [neighbor] ⚠️ 아직 손가락에 닿는 물건: {', '.join(o.label for o in left)} (그대로 집습니다)")
        if hidden and any(abs(b.center[0] - c.box.center[0]) < (c.box.x2 - c.box.x1) for b in hidden):
            print("    [neighbor] (뒤에 가려진 물건은 위치를 몰라 닿는지 확인 못 함)")
        return moved

    def _move_aside(self, o, target, obstacles, hidden, a, depth, intrinsics, posx):
        """선반 위 물건 o 를 좁게 벌려 집어 빈자리로 옮긴다 → 되돌릴 기록 (물건, 원래 중심, 임시 중심, 방향, 거리, 폭) 또는 None"""
        if 2 * o.r > config.GRIPPER_MAX_GRASP:
            print(f"    [neighbor] ⚠️ {o.label} 폭 {2 * o.r:.0f}mm — 그리퍼로 못 잡아 그대로 둡니다")
            return None
        width = neighbors.narrow_width(o)
        out_o = self._approach_out(o.box, depth, intrinsics, posx, a, o.center - a * o.r, 2 * o.r, o.height)
        blocked = (neighbors.finger_hits(o.center, a, width, out_o, obstacles, exclude=[o]) +
                   neighbors.sweep_hits(o.center, o.r, a, out_o, obstacles, exclude=[o]))
        if blocked:
            print(f"    [neighbor] ⚠️ {o.label} 을(를) {width:.0f}mm 로 좁게 벌려도 손가락이 "
                  f"{', '.join(b.label for b in blocked)} 에 닿아 못 치웁니다")
            return None
        spot = neighbors.temp_spot(o, target, a, obstacles, out_o, intrinsics, posx, hidden, depth)
        if spot is None:
            print(f"    [neighbor] ⚠️ {o.label} 을(를) 옮길 빈자리가 선반(화면) 안에 없습니다")
            return None
        orig = o.center.copy()
        if not self._shelf_move(o, orig, spot, a, out_o, width):
            return None
        o.center = spot
        return (o, orig, spot, a, out_o, width)

    def uncover(self, h):
        """뒤에 가려진 주문 상품 h 앞을 막은 물건을 옆으로 치운다 → 되돌릴 기록 목록 (못 치우면 빈 목록)"""
        pid, shelf, box, shot = h
        color, boxes, depth, intrinsics, posx = shot
        a = front_grasp.forward_dir(posx)
        if a is None:
            return []
        obstacles, hidden = neighbors.estimate(boxes, depth, intrinsics, posx, a)
        covering = neighbors.occluders(box, boxes)                      # 앞을 가린 상자 (상자 겹침 / SAM3 마스크)
        front = [o for o in obstacles if any(o.box is b for b in covering)]
        if not front:
            return []
        # 가려진 상품의 대략 자리: 상자 가운데 광선이 '앞 물건들 뒷면 + 상품 반지름' 깊이의 수직면과 만나는 점
        item = self.item(pid)
        back = max(float((o.center + a * o.r) @ a) for o in front) + item.width / 2 + 10
        T = geometry.pose_matrix(posx) @ geometry._GRIPPER2CAM
        ray = T[:3, :3] @ np.array([(box.center[0] - intrinsics["ppx"]) / intrinsics["fx"],
                                    (box.center[1] - intrinsics["ppy"]) / intrinsics["fy"], 1.0])
        t = (back - float(T[:3, 3] @ a)) / max(float(ray @ a), 1e-6)
        c = T[:3, 3] + ray * t
        bottom = min(o.bottom for o in front)
        target = neighbors.Obstacle(pid, box, np.array([c[0], c[1], bottom + item.height / 2]), item.width / 2,
                                    bottom, bottom + item.height)
        print(f"    [uncover] {self.info[pid]['name']} 이(가) {', '.join(o.label for o in front)} 뒤에 가려져 있음 "
              f"(대략 ({c[0]:.0f}, {c[1]:.0f})) → 앞 물건을 치우고 다시 찍습니다")
        moved = []
        for o in sorted(front, key=lambda o: -o.box.y2):                     # 가장 앞 물건부터
            rec = self._move_aside(o, target, obstacles + [target], hidden, a, depth, intrinsics, posx)
            if rec:
                moved.append(rec)
        return moved

    def restore_neighbors(self, moved):
        """clear_neighbors 로 옮겼던 물건을 제자리로 (옮긴 반대 순서)"""
        for o, orig, spot, a, out, width in reversed(moved):
            print(f"    [neighbor] 치웠던 {o.label} 을(를) 제자리로 되돌립니다")
            if self._shelf_move(o, spot, orig, a, out, width):
                o.center = orig

    def place_in_basket(self, pid=None):
        """찾아 둔 자리(바닥 또는 다른 물건 위)에 넣는다.
        (prepare_basket 없이 부르면 바로 찾고, 없으면 오류 — 물건을 든 채로는 정리 불가)"""
        item = self.item(pid) if pid else item_spec("?")
        spot = self.reserved_spot
        if spot is None:
            plan = self.scan_basket().plan(item)
            spot = plan.spot if plan.spot and not plan.moves else None
            if spot is None:
                raise RuntimeError("봉투에 빈자리가 없습니다 (물건을 든 채로는 정리할 수 없어요 — 집기 전에 prepare_basket)")
        self.reserved_spot = None
        if self.held_front:
            return self._place_front(pid, item, spot)
        height = self.held_height or item.height
        self.held_height = None
        if spot.base + height > config.CONTAINER_MAX:
            print(f"    [basket] ⚠️ 잰 높이 {height:.0f}mm 로는 최대 채움 높이를 {spot.base + height - config.CONTAINER_MAX:.0f}mm 넘습니다")
        # 상품 바닥이 받침면 DROP_CLEARANCE 위에 오도록: 그리퍼는 윗면에서 PICK_DEPTH 아래를 잡고 있음
        release_z = config.BASKET_CENTER[2] + spot.base + max(height - config.PICK_DEPTH, 0.0) + config.DROP_CLEARANCE
        where = f"{spot.base:.0f}mm 높이 물건 위" if spot.stacked else "바닥"
        print(f"    [basket] {self.info.get(pid, {}).get('name', pid)} 을(를) {where}에 놓습니다 ({spot.x:.0f}, {spot.y:.0f})")
        # 상품 긴 변이 계획한 방향(봉투 가로 u / 세로 v)이 되도록: 손가락(짧은 변)은 그와 수직
        pose = [spot.x, spot.y, release_z] + config.BASKET_CENTER[3:]
        if self.held_oriented:
            pose = turn_to(pose, config.BASKET_YAW + (0.0 if spot.rotated else 90.0))
        self.arm.place(pose, slow=item.fragile)
        self.placed.append(Placed(item.pid, spot.x, spot.y, spot.du, spot.dv, spot.base, spot.base + height,
                                  item.weight, item.fragile))

    def _via_z(self, below_tcp):
        """봉투로 옮길 때 관절 보간으로 가는 '자리 바로 위' 높이:
        봉투 입구(평소 채움 높이)와 지금 봉투 안 가장 높은 물건 중 높은 것 + 쥔 물건이 그리퍼 아래로 내려온 만큼 + 여유
        (최대 채움 높이 200mm 로 잡으면 봉투 먼 쪽에서는 팔을 다 펴야 닿아 특이점 — 시뮬 확인)"""
        bm = self.basket_map
        tallest = float(np.nanmax(bm.height_grid())) if bm is not None and bm.points_inside else config.CONTAINER_MAX
        wall = max(config.CONTAINER_DEPTH, tallest)
        return config.BASKET_CENTER[2] + wall + below_tcp + config.CARRY_CLEARANCE

    def _place_front(self, pid, item, spot):
        """수평으로 쥔 상품을 봉투 바닥(받침면) 위 FRONT_RELEASE_CLEARANCE 까지 내려 놓는다 (입구에 안 걸리게).
        reserved_mode 가 lying 이면 그리퍼를 아래로 돌려 캔을 눕혀서 놓는다."""
        mode, self.reserved_mode = self.reserved_mode, "top"
        name = self.info.get(pid, {}).get("name", pid)
        if mode == "lying":
            lying = front_grasp.lying_item(item)
            rel = front_grasp.lying_release(spot, lying, self.basket_map)
            print(f"    [basket] {name} 을(를) 그리퍼를 아래로 돌려 눕혀서, 받침면 위 {config.FRONT_RELEASE_CLEARANCE:.0f}mm 에 놓습니다 "
                  f"({spot.x:.0f}, {spot.y:.0f}, 캔 축 = 봉투 {'세로' if spot.rotated else '가로'})")
            self.arm.place(rel.pose, slow=item.fragile, above_z=rel.above_z, joint_approach=True,
                           via_z=self._via_z(lying.height / 2))
            self.held_front = False
            self.placed.append(Placed(item.pid, spot.x, spot.y, spot.du, spot.dv, spot.base, spot.base + lying.height,
                                      item.weight, item.fragile, stackable=False))
            return
        rel = front_grasp.bag_release(spot, item, self.basket_map)
        if rel.inside:
            print(f"    [basket] {name} 을(를) 수평 그대로 받침면 위 {config.FRONT_RELEASE_CLEARANCE:.0f}mm 에 놓습니다 "
                  f"({spot.x:.0f}, {spot.y:.0f})")
        else:
            print(f"    [basket] ⚠️ {name}: 그리퍼가 봉투 입구 아래로 못 들어갑니다 — {rel.why}\n"
                  f"             → 입구에 걸리지 않게 그리퍼 바닥을 테두리 위 {config.RIM_MARGIN:.0f}mm 에 두고 놓습니다 "
                  f"(받침면 위 {config.FRONT_RELEASE_CLEARANCE + rel.drop:.0f}mm 에서 떨어뜨림)")
        self.arm.place(rel.pose, slow=item.fragile, above_z=rel.above_z, joint_approach=True,
                       via_z=self._via_z(item.height / 2))
        self.held_front = False
        self.placed.append(Placed(item.pid, spot.x, spot.y, spot.du, spot.dv, spot.base, spot.base + item.height,
                                  item.weight, item.fragile))

    # ------------------------------------------------------------ 봉투 빈자리 · 쌓기 · 정리
    def scan_basket(self):
        """봉투 촬영 자세에서 깊이를 찍어 높이 지도를 만든다 → BasketMap"""
        print(f"    [arm] 봉투 촬영 자세 {config.BASKET_VIEW_POSE}")
        self.arm.movej(config.BASKET_VIEW_POSE)
        time.sleep(config.SETTLE_SEC)
        posx = self.arm.current_posx()
        _, depth, intrinsics = self.camera.capture(self.arm.rclpy)
        bm = BasketMap(geometry.depth_to_base_points(depth, intrinsics, posx),
                       camera=geometry.camera_position(posx), placed=self.placed)
        if bm.points_inside == 0:
            raise RuntimeError("카메라에 봉투가 보이지 않습니다 — 봉투 촬영 자세 / BASKET_CENTER·BASKET_SIZE 확인")
        if bm.shadow.any():
            print(f"    [basket] 물건에 가려 깊이가 없는 바닥 {int(bm.shadow.sum())}칸은 비어 있다고 봄 (가려진 곳 계산)")
        for o in bm.objects:
            print(f"    [basket] 물건 #{o.index}: 중심 ({o.x:.0f}, {o.y:.0f}) 높이 {o.top:.0f}mm "
                  f"크기 {o.length:.0f}x{o.width:.0f}mm" + ("" if o.movable else f" — 옮기지 않음: {o.reason}"))
        return bm

    def _grasp_pose(self, obj):
        """봉투 안 물건을 위에서, 손가락이 짧은 변을 잡도록 돌린 자세"""
        base = [obj.x, obj.y, 0.0] + config.BASKET_CENTER[3:]
        R = Rotation.from_euler("ZYZ", base[3:], degrees=True).as_matrix()
        close = R[:, 0 if config.GRIPPER_CLOSE_AXIS == "x" else 1]           # 손가락이 닫히는 방향 (base)
        turn = (obj.yaw + 90.0) - math.degrees(math.atan2(close[1], close[0]))
        turn = (turn + 90.0) % 180.0 - 90.0                                   # 손가락은 앞뒤 대칭 → ±90° 안으로
        z = config.BASKET_CENTER[2] + max(obj.top - config.BASKET_PICK_DEPTH, config.MIN_GRASP_ABOVE_FLOOR)
        pose = geometry.yawed(base, turn)
        pose[2] = z
        return pose

    def prepare_basket(self, pid=None):
        """pid 상품을 놓을 자리를 확보한다 (바닥 또는 다른 물건 위, 필요하면 다른 물품을 옮김) → Spot 또는 None"""
        ways = self.bag_ways(pid) if pid else [(item_spec("?"), "top")]
        bm = self.scan_basket()
        self.basket_map = bm
        tries = [(limit, k, item, mode) for limit in (config.CONTAINER_DEPTH, config.CONTAINER_MAX)
                 for k, (item, mode) in enumerate(ways)]                  # 평소 높이(150mm)에서 모든 방법 → 그다음 최대(200mm)
        for limit, k, item, mode in tries:
            bm.fill_limit = limit
            plan = bm.plan(item)
            if plan.spot is not None:
                if k:
                    print(f"    [basket] {ways[0][1]} 로 넣을 자리가 없어 {mode} 로 넣습니다")
                if limit > config.CONTAINER_DEPTH:
                    print(f"    [basket] {config.CONTAINER_DEPTH:.0f}mm 안에는 자리가 없어 최대 {limit:.0f}mm 까지 채웁니다")
                break
        image = bm.draw(plan)
        self.reserved_mode = mode
        if plan.spot is None:
            print(f"    [basket] 놓을 자리가 없고, 옮겨서 만들 수도 없습니다 (지도: {image})")
            return None
        for mv in plan.moves:
            print(f"    [basket] 자리가 없어 물건 #{mv.obj.index} 를 ({mv.obj.x:.0f}, {mv.obj.y:.0f}) → "
                  f"({mv.dest_x:.0f}, {mv.dest_y:.0f}) 로 옮깁니다 (지도: {image})")
            grasp = self._grasp_pose(mv.obj)
            if not self.arm.pick(grasp, geometry.tool_z_axis(grasp)):
                print(f"    [basket] 물건 #{mv.obj.index} 를 잡지 못했습니다")
                return None
            self.arm.place([mv.dest_x, mv.dest_y, grasp[2] + config.PLACE_CLEARANCE] + grasp[3:])
            for p in self.placed:                       # 우리가 넣었던 물건이면 기록 위치도 옮김
                if math.hypot(p.x - mv.obj.x, p.y - mv.obj.y) < min(p.du, p.dv) / 2:
                    p.x, p.y = p.x + mv.dest_x - mv.obj.x, p.y + mv.dest_y - mv.obj.y
        if plan.moves and config.RESCAN_AFTER_MOVE:
            self.basket_map = self.scan_basket()
            self.basket_map.fill_limit = bm.fill_limit
            again = self.basket_map.plan(item)
            if again.spot is None or again.moves:
                print("    [basket] 옮긴 뒤 다시 봤는데 자리가 없습니다 — 봉투를 확인해 주세요")
                return None
            plan.spot = again.spot
        s = plan.spot
        print(f"    [basket] 상품 자리: ({s.x:.0f}, {s.y:.0f}) " + (f"— {s.base:.0f}mm 높이 물건 위에 쌓기" if s.stacked else "— 바닥")
              + {"upright": ", 수평 그대로 세워서", "lying": ", 눕혀서", "drop": ", 입구 위에서 놓기"}.get(mode, ""))
        self.reserved_spot = s
        return s

    # ------------------------------------------------------------ 한 번에
    def run_sequence(self, sequence):
        """pid 목록을 순서대로 집어서 봉투에 넣는다 → {pid: 넣은 개수}
        자리가 없거나 매대에서 못 찾은 상품은 건너뛰고 다음 상품을 계속 넣는다 (큰 상품 하나 때문에 작은 상품까지 포기하지 않게).
        건너뛴 상품은 self.skipped 에 [(pid, 이유)] 로 남긴다. 로봇 동작 오류는 그대로 올려 보낸다(멈춤)."""
        done, self.skipped = {}, []
        for k, pid in enumerate(sequence):
            item = self.item(pid)
            print(f"  ({k + 1}/{len(sequence)}) {self.info[pid]['name']} ({pid}) — {PRIORITY_NAMES[priority(item)]}, "
                  f"{item.weight:.0f}g, {item.length:.0f}x{item.width:.0f}x{item.height:.0f}mm")
            if self.prepare_basket(pid) is None:            # 집기 전에 봉투 자리부터 (필요하면 정리)
                print(f"  🛍 {self.info[pid]['name']}: 봉투에 넣을 자리가 없어 건너뜁니다")
                self.skipped.append((pid, "봉투 자리 없음"))
                continue
            self.move_to_view(self.info[pid].get("shelf", "upper"))
            if not self.pick_one(pid):
                self.reserved_spot = None
                print(f"  🛍 {self.info[pid]['name']}: 매대에서 찾지 못해 건너뜁니다")
                self.skipped.append((pid, "매대에서 못 찾음"))
                continue
            self.place_in_basket(pid)
            done[pid] = done.get(pid, 0) + 1
        self.arm.home()
        return done

    def run_order(self, items):
        """주문 {pid: 개수} 를 config.PICK_PRIORITY 순서로 넣는다 → {pid: 넣은 개수}
        near_heavy: ① 가까운 → ② 무거운 (매대를 보고 정함) / weight: ① 무거운 → ② 큰 → ③ 가볍고 작은"""
        if config.PICK_PRIORITY == "near_heavy":
            return self.run_nearest(items)
        return self.run_sequence(order_sequence(items, self.info))

    # ------------------------------------------------------------ 봉투에서 꺼내기 (LLM "빼기")
    def in_bag(self, pid=None):
        """봉투에 넣은 기록 개수 (pid 없으면 전부)"""
        return sum(1 for p in self.placed if pid is None or p.pid == pid)

    def clear_returned(self):
        """직원이 내려놓는 곳을 치웠을 때"""
        self.returned = []

    def _covered(self, rec, min_overlap=15.0):
        """rec 위에 다른 물건이 쌓여 있나 (우리가 넣은 기록으로 판단)
        기록의 차지 길이(du, dv)에는 안전 여유가 들어 있어 옆에 붙여 놓은 것도 살짝 겹침 → 양쪽으로 min_overlap 넘게 겹쳐야 위에 있다고 봄"""
        return any(p is not rec and p.base >= rec.top - 5
                   and (p.du + rec.du) / 2 - abs(p.x - rec.x) > min_overlap
                   and (p.dv + rec.dv) / 2 - abs(p.y - rec.y) > min_overlap for p in self.placed)

    def remove_from_bag(self, pid, count=1):
        """봉투에 넣은 pid 상품을 count 개 꺼내 탁자 위 내려놓는 곳(config.RETURN_SPOT ~)에 놓는다 → 꺼낸 개수.
        어느 물건이 그 상품인지는 넣을 때 남긴 기록(self.placed)으로 안다 — 같은 실행 중에 넣은 것만 꺼낼 수 있음.
        위에 다른 물건이 쌓인 것은 꺼내지 않는다 (같은 상품이 다른 자리에 있으면 그것부터)."""
        done = 0
        name = self.info.get(pid, {}).get("name", pid)
        self.remove_note = ""                                              # 다 못 꺼냈을 때 이유 (LLM 이 말해 줌)

        def stop(note):
            self.remove_note = note
            print(f"    [remove] {note}")

        for _ in range(count):
            free = [k for k in range(config.RETURN_SLOTS) if k not in {s for _, s in self.returned}]
            mine = [p for p in self.placed if p.pid == pid]
            if not mine:
                stop(f"봉투에 넣은 {name} 기록이 없습니다")
                break
            if not free:
                stop(f"내려놓는 곳 {config.RETURN_SLOTS}자리가 다 찼습니다 — 치운 뒤 다시")
                break
            ready = [p for p in mine if not self._covered(p)]
            if not ready:
                stop(f"{name} 위에 다른 물건이 쌓여 있어 꺼내지 않았습니다")
                break
            rec = max(ready, key=lambda p: p.top)                       # 가장 위에 있는 것부터
            bm = self.scan_basket()
            self.basket_map = bm
            obj = min(bm.objects, key=lambda o: math.hypot(o.x - rec.x, o.y - rec.y), default=None)
            near = obj is not None and math.hypot(obj.x - rec.x, obj.y - rec.y) < max(rec.du, rec.dv) / 2 + 20
            if not near:
                print(f"    [remove] 봉투 사진에서 ({rec.x:.0f}, {rec.y:.0f}) 의 {name} 이(가) 보이지 않습니다 — 기록에서 뺍니다")
                self.placed.remove(rec)
                continue
            yaw = config.BASKET_YAW + (0.0 if rec.du >= rec.dv else 90.0)        # 긴 변 방향 (넣은 기록 기준)
            top = min(obj.top, rec.top + 10) if obj.top > rec.top - 30 else rec.top
            grasp = self._grasp_pose(SimpleNamespace(x=rec.x, y=rec.y, yaw=yaw, top=top))
            hold = grasp[2] - config.BASKET_CENTER[2] - rec.base               # 상품 바닥에서 쥔 높이
            carry_z = self._via_z(hold)                                         # 쥔 상품 바닥이 봉투 입구·다른 물건 위로
            print(f"    [remove] {name} 을(를) 봉투 ({rec.x:.0f}, {rec.y:.0f}) 에서 꺼냅니다 (윗면 {top:.0f}mm)")
            if not self.arm.pick(grasp, geometry.tool_z_axis(grasp), fragile=rec.fragile,
                                 approach_dist=max(config.APPROACH_DIST, carry_z - grasp[2]),
                                 open_width=self._open_width(self.item(pid))):     # 옆 물건에 안 닿게 폭 + 여유만
                stop(f"{name} 을(를) 잡지 못했습니다")
                break
            slot = free[0]
            sx = config.RETURN_SPOT[0] + config.RETURN_STEP[0] * slot
            sy = config.RETURN_SPOT[1] + config.RETURN_STEP[1] * slot
            pose = [sx, sy, config.RETURN_SPOT[2] + hold + config.DROP_CLEARANCE] + grasp[3:]
            print(f"    [remove] 내려놓는 곳 {slot + 1}번 ({sx:.0f}, {sy:.0f}) 에 놓습니다")
            # 그 높이로 내려놓는 곳 위까지 (관절 보간 — 직선으로는 손목 특이점에 걸려 멈춤, 시뮬 확인) → 수직으로 내려놓음
            self.arm.place(pose, slow=rec.fragile, above_z=carry_z, joint_approach=True, via_z=carry_z)
            self.placed.remove(rec)
            self.returned.append((pid, slot))
            done += 1
        self.arm.home()
        return done

    # ------------------------------------------------------------ 우선순위 집기 (가까운 → 무거운)
    def _shelf_of(self, pid):
        return self.info[pid].get("shelf", "upper")

    def look_shelf(self, shelf, pids):
        """(이미 그 매대 촬영 자세에 있을 때) 찍어서 pids 상품 후보를 찾는다 → [Candidate] (깊이를 못 잰 상자는 뺌)"""
        here = [pid for pid in pids if self._shelf_of(pid) == shelf]
        posx = self.arm.current_posx()
        color, depth, intrinsics = self.camera.capture(self.arm.rclpy)
        t = time.monotonic()
        prompts = {p: pid for pid in here for p in config.prompts_of(pid)}       # DINO 이름 → 주문 상품 (콜라 = 코카콜라·펩시)
        all_names = {p: pid for pid in config.DINO_PROMPTS for p in config.prompts_of(pid)}   # DINO 이름 → 모든 상품
        by_prompt, boxes = self.detector.find_all(color, list(prompts))
        found = []
        self.last_hidden = [h for h in getattr(self, "last_hidden", []) if h[1] != shelf]
        shot = (color, boxes, depth, intrinsics, posx)
        lo, hi = config.SIZE_CHECK
        zs = {id(b): geometry.depth_at(depth, *b.center) for b in boxes}
        for box in boxes:
            u, v = box.center
            z = zs[id(box)]
            own = all_names.get(box.label)                     # DINO 가 부른 상품 (주문 안 한 것일 수도)
            named = own if own in here else None
            if not z or (own is None and box.label != "?"):         # 상품 이름이 아닌 것(사람·손 …)은 제외
                continue
            if front_grasp.forward_dir(posx) is not None and neighbors.occluded(box, boxes):
                # 뒤에 가려진 물건: 가운데 깊이가 앞 물건 것이라 집을 자리를 모름 → 기록해 두고, 앞 물건을 치운 뒤 다시 찍어서 집음
                hp = named or max(((box.scores.get(p, 0.0), q) for p, q in prompts.items()), default=(0.0, None))[1]
                if hp and (named or box.scores.get(next(p for p, q in prompts.items() if q == hp), 0.0) >= config.SIZE_MIN_SCORE):
                    print(f"    [vision] {self.info[hp]['name']} 상자 ({u}, {v}) 는 앞 물건에 가려져 있음")
                    self.last_hidden.append((hp, shelf, box, shot))
                continue
            # 마스크(SAM3)나 깊이로 잰 좌우 끝 → 가운데·폭 바로잡음 (깊이 끝은 정면으로 볼 때만)
            u, wpx, z = neighbors.measure_span(box, depth, u, v, z, use_depth=front_grasp.forward_dir(posx) is not None)
            width = wpx * z / intrinsics["fx"]                               # 잰 실제 폭 (mm)
            if own and own not in here and lo * self.item(own).width <= width <= hi * self.item(own).width:
                continue                                         # 폭도 맞는 다른(주문 안 한) 상품
            # 폭이 맞는 주문 상품 중 이름 점수가 가장 높은 것 (DINO 1등 이름이 틀려도 크기로 바로잡음)
            min_score = config.SIZE_RELABEL_MIN_SCORE if own else config.SIZE_MIN_SCORE   # 다른 상품 이름 + 폭 안 맞음 = 크기가 강한 증거
            fits = [(box.scores.get(p, 0.0), pid) for p, pid in prompts.items()
                    if lo * self.item(pid).width <= width <= hi * self.item(pid).width
                    and box.scores.get(p, 0.0) >= min_score]
            if not fits:
                if named:
                    print(f"    [vision] '{box.label}' 상자 ({u}, {v}) 제외: 잰 폭 {width:.0f}mm 가 표의 폭 "
                          f"{self.item(named).width:.0f}mm 와 다르고 폭이 맞는 다른 주문 상품도 없음")
                continue
            pid = max(fits)[1]
            if named and pid != named:
                print(f"    [vision] '{box.label}' 상자 ({u}, {v}): 잰 폭 {width:.0f}mm → {self.info[pid]['name']} 으로 바로잡음")
            xyz = geometry.pixel_to_base(u, v, z, intrinsics, posx)
            dist = math.hypot(xyz[0] - config.NEAR_ORIGIN[0], xyz[1] - config.NEAR_ORIGIN[1])
            found.append(Candidate(pid, shelf, box, u, v, z, xyz, dist, self.item(pid).weight,
                                   color, boxes, depth, intrinsics, posx))
        image = draw(color, boxes, targets=[c.box for c in found])
        print(f"    [vision] 매대({shelf}) 에서 " + ", ".join(
            f"{self.info[pid]['name']} {sum(c.pid == pid for c in found)}개" for pid in here)
              + f" 찾음 ({time.monotonic() - t:.1f}s, 그림: {image})")
        return found

    def survey(self, pids):
        """pids 상품이 있는 매대마다 촬영 자세로 가서 찍는다 → [Candidate]"""
        found = []
        for shelf in dict.fromkeys(self._shelf_of(pid) for pid in pids):
            self.move_to_view(shelf)
            found += self.look_shelf(shelf, pids)
        return found

    def run_nearest(self, items):
        """주문 {pid: 개수} 를 ① 가까운 물품 → ② 무거운 물품 순서로 집어서 봉투에 넣는다 → {pid: 넣은 개수}

        매대를 '한 번' 찍어 순서표(queue)를 만들고, 그 사진의 좌표로 차례로 집는다 (상품마다 DINO 를 다시 돌리지 않음).
        다시 찍는 때 (집기 직전, 이미 그 촬영 자세에 와 있으므로 로봇 이동은 늘지 않음):
          - 다음 상품이 이번에 집은 자리에서 RELOOK_RADIUS 안 → 손가락에 밀렸을 수 있으므로 찍어서 순서를 다시 정함
          - RESURVEY_EACH_PICK = True → 매번
          - 순서표가 비었는데 남은 상품이 있음 → 매대를 다시 찍음 (가려져 있던 물건이 보일 수 있음)
        고른 상품은 봉투 자리를 먼저 확보한 뒤(prepare_basket) 같은 촬영 자세로 돌아와 집는다."""
        remaining = {pid: int(n) for pid, n in items.items() if int(n) > 0}
        done, self.skipped = {}, []
        queue, touched = [], []         # 순서표 / 찍은 뒤 집은(건드린) 자리 [(매대, xyz)]
        picked = []                     # 찍은 뒤 집어 간 물건의 앞면 자리 [(매대, xyz)] — 사진엔 있지만 지금은 없음
        uncovered = {}                  # 가려진 상품을 꺼내려고 치운 앞 물건 {상품: [되돌릴 기록]} — 그 상품을 넣은 뒤 되돌림

        def give_up(pid, why):
            self.skipped += [(pid, why)] * remaining.pop(pid)

        def show(ranked):
            print("  📋 집는 순서 (① 가까운 → ② 무거운): " + " → ".join(
                f"{self.info[c.pid]['name']}({c.dist:.0f}mm, {c.weight:.0f}g)" for c in ranked))

        while remaining:
            queue = [c for c in queue if c.pid in remaining]
            if not queue:                                   # 처음 / 찍어 둔 것을 다 썼는데 남은 상품이 있음
                queue, touched, picked = rank_candidates(self.survey(list(remaining))), [], []
                for pid in [p for p in remaining if not any(c.pid == p for c in queue)]:
                    # 보이는 건 없지만 앞 물건 뒤에 가려진 게 있으면: 앞 물건을 치우고 다시 찍음
                    hid = [h for h in getattr(self, "last_hidden", []) if h[0] == pid]
                    if not (config.UNCOVER_HIDDEN and hid and pid not in uncovered):
                        continue
                    moved = self.uncover(hid[0])
                    if not moved:
                        continue
                    uncovered[pid] = moved
                    shelf = hid[0][1]
                    self.move_to_view(shelf)
                    fresh = self.look_shelf(shelf, list(remaining))
                    queue = rank_candidates(fresh + [q for q in queue if q.shelf != shelf])
                    touched = [(s_, p_) for s_, p_ in touched if s_ != shelf] + [(shelf, r[1]) for r in moved]
                    picked = [(s_, p_) for s_, p_ in picked if s_ != shelf]
                for pid in [p for p in remaining if not any(c.pid == p for c in queue)]:
                    print(f"  🛍 {self.info[pid]['name']}: 매대에서 찾지 못해 남은 {remaining[pid]}개를 건너뜁니다")
                    give_up(pid, "매대에서 못 찾음")
                if not queue:
                    break
                show(queue)
            c = queue[0]
            item = self.item(c.pid)
            print(f"  ({sum(done.values()) + 1}) {self.info[c.pid]['name']} ({c.pid}) — 거리 {c.dist:.0f}mm, "
                  f"{item.weight:.0f}g, {item.length:.0f}x{item.width:.0f}x{item.height:.0f}mm")
            if self.prepare_basket(c.pid) is None:          # 집기 전에 봉투 자리부터 (필요하면 정리)
                print(f"  🛍 {self.info[c.pid]['name']}: 봉투에 넣을 자리가 없어 남은 {remaining[c.pid]}개를 건너뜁니다")
                give_up(c.pid, "봉투 자리 없음")            # 봉투는 더 비지 않으므로 같은 상품은 다시 시도하지 않음
                continue
            self.move_to_view(c.shelf)                      # 찍었던 자세 그대로 → 찍어 둔 사진의 좌표를 그대로 씀
            here = [p for s, p in touched if s == c.shelf]     # 이 매대를 찍은 뒤 집은 자리
            near = [p for p in here if np.linalg.norm(p[:2] - c.xyz[:2]) < config.RELOOK_RADIUS]
            if near or (config.RESURVEY_EACH_PICK and here):
                print("    [vision] " + ("매번 다시 찍기 설정" if config.RESURVEY_EACH_PICK else
                                         f"방금 집은 자리 {config.RELOOK_RADIUS:.0f}mm 안이라 밀렸을 수 있음") + " → 다시 찍어 확인")
                fresh = self.look_shelf(c.shelf, list(remaining))
                queue = rank_candidates(fresh + [q for q in queue if q.shelf != c.shelf])
                touched = [(s, p) for s, p in touched if s != c.shelf]
                picked = [(s, p) for s, p in picked if s != c.shelf]
                if not queue or (queue[0].pid, queue[0].shelf) != (c.pid, c.shelf):
                    self.reserved_spot = None               # 다른 상품이 1순위가 됨 → 그 상품으로 봉투 자리부터 다시
                    if queue:
                        show(queue)
                    continue
                c = queue[0]
            queue.remove(c)
            touched.append((c.shelf, c.xyz))
            gone = [p for s_, p in picked if s_ == c.shelf]                 # 이 사진 뒤에 집어 간 물건 (앞면 자리)
            moved = self.clear_neighbors(c, gone) if config.CLEAR_NEIGHBORS and front_grasp.is_front(c.pid) else []
            picked.append((c.shelf, c.xyz))
            touched += [(c.shelf, orig) for _, orig, *_ in moved]           # 옮겼던 물건 둘레도 다시 확인
            image = draw(c.color, c.boxes, chosen=c.box, targets=[o.box for o in queue if o.color is c.color])
            if self._grasp(c.pid, c.box, c.u, c.v, c.z, c.depth, c.intrinsics, c.posx, image):
                self.place_in_basket(c.pid)
                done[c.pid] = done.get(c.pid, 0) + 1
            else:
                self.reserved_spot = None
                print(f"  🛍 {self.info[c.pid]['name']}: 잡지 못했습니다")
                self.skipped.append((c.pid, "집기 실패"))
            if moved:
                self.restore_neighbors(moved)
            if c.pid in uncovered:                          # 가려진 상품을 꺼냈음 → 치웠던 앞 물건을 제자리로
                self.restore_neighbors(uncovered.pop(c.pid))
            remaining[c.pid] -= 1
            if remaining[c.pid] == 0:
                remaining.pop(c.pid)
        for moved in uncovered.values():                    # 끝내 못 집은 상품 때문에 치웠던 물건도 제자리로
            self.restore_neighbors(moved)
        self.arm.home()
        return done

    def pick_and_place(self, pid, count=1):
        """pid 상품 count 개 → 넣은 개수"""
        return self.run_sequence([pid] * count).get(pid, 0)
