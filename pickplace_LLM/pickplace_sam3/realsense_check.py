"""RealSense 로 매대 확인 — 로봇은 움직이지 않고, 카메라 사진 한 장으로 상품을 어떻게 알아보는지 본다.

  # 터미널 1 — 카메라 (가상 로봇 + 실제 RealSense 도 됨)
  ros2 launch m0609_rg2_bringup bringup.launch.py camera:=true
  # 터미널 2
  cd ~/corecode/pickplace_LLM/pickplace_sam3 && python3 realsense_check.py                 # 모든 상품 기준
  python3 realsense_check.py 콜라 2 컵라면 1                            # 주문 기준 (집을 후보로 볼 상자)
  python3 realsense_check.py --loop                                     # Enter 마다 한 장씩 계속
  python3 realsense_check.py --robot                                    # 로봇 자세를 읽어 로봇 좌표도 표시
  python3 realsense_check.py --detector gdino                           # 인식기 바꿔 보기 (기본 config.DETECTOR)
  python3 realsense_check.py --save-crops cola --loop                   # 갤러리 사진 모으기 (SAM3 + DINOv3 용)
      → 매대에 그 상품만 올려 두고 찍으면, 찾은 물건 조각을 data/gallery/cola/ 에 저장
        (돌려 놓고·옮겨 놓고 여러 장, 상품마다 10~30장) → 다음 실행 때 인식기가 갤러리 특징을 알아서 다시 만듦

로봇 코드(logic/pick_place.look_shelf)와 같은 순서로 판단한다:
  DINO 이름별 점수 → 잰 폭(상자 폭 x 깊이)이 그 상품 폭과 맞는지 → 안 맞으면 폭이 맞는 상품으로 바로잡음 → 앞 물건에 가려졌는지
결과: 표 + data/realsense_check.jpg (초록 = 주문 상품 후보 / 노랑 = 다른 상품 / 빨강 = 가려짐 / 회색 = 모름)
"""
import argparse
import os
import sys
import time

import cv2

import config
from logic import neighbors
from logic.pick_place import find_product, item_spec, load_products
from vision import geometry
from vision.factory import add_detector_arg, make_detector

OUT = os.path.join(config.DATA_DIR, "realsense_check.jpg")


def classify(box, z, intrinsics, ordered):
    """상자 하나 → (상품 id 또는 None, 이유). ordered: 주문 상품 id 집합 (비면 모든 상품 기준)"""
    lo, hi = config.SIZE_CHECK
    all_names = {p: pid for pid in config.DINO_PROMPTS for p in config.prompts_of(pid)}
    own = all_names.get(box.label)
    width = (box.x2 - box.x1) * z / intrinsics["fx"]
    pool = ordered or set(config.DINO_PROMPTS)
    fits_own = own is not None and lo * item_spec(own).width <= width <= hi * item_spec(own).width
    if own and fits_own:
        return own, "이름·폭 맞음"
    cands = [(box.scores.get(p, 0.0), pid) for pid in pool for p in config.prompts_of(pid)
             if lo * item_spec(pid).width <= width <= hi * item_spec(pid).width
             and box.scores.get(p, 0.0) >= (config.SIZE_RELABEL_MIN_SCORE if own else config.SIZE_MIN_SCORE)]
    if own is None and box.label != "?":
        return None, "상품 아님"
    if cands:
        pid = max(cands)[1]
        return pid, f"폭으로 바로잡음 ({own or '?'} → {pid})" if pid != own else "폭 맞음"
    return None, f"폭 {width:.0f}mm 가 맞는 상품 없음"


def save_crops(color, boxes, pid):
    """찾은 물건 조각(마스크가 있으면 마스크 밖은 회색)을 갤러리 폴더 data/gallery/<pid>/ 에 저장"""
    from vision.sam3_dinov3 import masked_crop
    folder = os.path.join(config.GALLERY_DIR, pid)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    keep = [b for b in boxes if b.label not in ("person", "hand") and not neighbors.occluded(b, boxes)]
    for k, b in enumerate(keep, 1):
        crop = masked_crop(color, [b.x1, b.y1, b.x2, b.y2], b.mask)
        cv2.imwrite(os.path.join(folder, f"rs_{stamp}_{k}.jpg"), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    print(f"\n갤러리 {folder} 에 조각 {len(keep)}장 저장 (다른 물건이 섞였으면 그 사진만 지우기) "
          f"— 다음 실행 때 인식기가 특징을 알아서 다시 만듦")


def check(camera, detector, rclpy, ordered, posx=None, crops_pid=None):
    color, depth, intrinsics = camera.capture(rclpy)
    by, boxes = detector.find_all(color, config.all_prompts())
    if crops_pid:
        save_crops(color, boxes, crops_pid)
    img = color.copy()
    rows = []
    for k, b in enumerate(sorted(boxes, key=lambda b: b.center[0]), 1):
        if b.label in ("person", "hand"):
            continue
        u, v = b.center
        z = geometry.depth_at(depth, u, v)
        hidden = neighbors.occluded(b, boxes)
        if not z:
            pid, why, width = None, "깊이 없음", 0.0
        else:
            pid, why = classify(b, z, intrinsics, ordered)
            width = (b.x2 - b.x1) * z / intrinsics["fx"]
        cam = [(u - intrinsics["ppx"]) * (z or 0) / intrinsics["fx"], (v - intrinsics["ppy"]) * (z or 0) / intrinsics["fy"], z or 0]
        base = geometry.pixel_to_base(u, v, z, intrinsics, posx) if (posx and z) else None
        target = pid is not None and (not ordered or pid in ordered)
        color_box = (0, 0, 255) if hidden else (0, 200, 0) if target else (0, 200, 255) if pid else (160, 160, 160)
        if b.mask is not None:                                          # SAM3 마스크 테두리
            contours, _ = cv2.findContours(b.mask.astype("uint8"), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(img, contours, -1, color_box, 1)
        cv2.rectangle(img, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), color_box, 2)
        cv2.putText(img, f"{k}:{pid or b.label} {width:.0f}mm" + (" HIDDEN" if hidden else ""),
                    (int(b.x1), max(15, int(b.y1) - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color_box, 2)
        rows.append((k, b.label, b.score, pid, why, width, item_spec(pid).width if pid else None, z, hidden, cam, base))
    cv2.imwrite(OUT, img)
    print(f"\n상자 {len(rows)}개 (사진 {color.shape[1]}x{color.shape[0]}, fx {intrinsics['fx']:.0f}) — 그림: {OUT}")
    print(f"{'#':>2} {'DINO 이름':<18} {'점수':>4}  {'판단':<10} {'잰 폭':>6} {'표 폭':>5} {'깊이':>6}  가림  카메라 좌표 (mm)"
          + ("        로봇 좌표 (mm)" if posx else "") + "  이유")
    for k, label, score, pid, why, width, spec_w, z, hidden, cam, base in rows:
        print(f"{k:>2} {label:<18} {score:>4.2f}  {pid or '-':<10} {width:>6.0f} {spec_w or 0:>5.0f} {z or 0:>6.0f}  "
              f"{'예' if hidden else '  '}   ({cam[0]:5.0f}, {cam[1]:5.0f}, {cam[2]:5.0f})"
              + (f"  ({base[0]:5.0f}, {base[1]:5.0f}, {base[2]:5.0f})" if base is not None else ("  " + " " * 22 if posx else ""))
              + f"  {why}")
    if ordered:
        for pid in sorted(ordered):
            n = sum(1 for r in rows if r[3] == pid and not r[8])
            h = sum(1 for r in rows if r[3] == pid and r[8])
            print(f"  → {pid}: 집을 수 있는 후보 {n}개" + (f", 가려진 것 {h}개 (앞 물건을 치우면 집음)" if h else ""))


def main():
    ap = argparse.ArgumentParser(description="RealSense 사진 한 장으로 매대 상품 인식 확인 (로봇은 안 움직임)")
    ap.add_argument("order", nargs="*", help="주문 (예: 콜라 2 컵라면 1) — 없으면 모든 상품 기준")
    ap.add_argument("--loop", action="store_true", help="Enter 마다 한 장씩 계속 (q 로 끝)")
    ap.add_argument("--robot", action="store_true", help="로봇 자세를 읽어 로봇 좌표도 표시 (bringup 필요)")
    ap.add_argument("--wait", type=float, default=10.0, help="사진을 기다리는 시간(초)")
    ap.add_argument("--save-crops", metavar="상품", help="찾은 물건 조각을 갤러리 data/gallery/<상품 id>/ 에 저장 (예: cola, 콜라)")
    add_detector_arg(ap)
    args = ap.parse_args()
    config.CAMERA_TIMEOUT = args.wait

    from main import order_text, parse_order
    products = load_products()
    ordered = set()
    if args.order:
        items, unknown = parse_order(order_text(args.order), products)
        if unknown:
            sys.exit(f"주문을 알아듣지 못했습니다: {unknown}")
        ordered = set(items)
    crops_pid = None
    if args.save_crops:
        crops_pid = config.PRODUCT_ALIASES.get(args.save_crops, args.save_crops)
        if crops_pid not in config.DINO_PROMPTS:
            found = find_product(products, args.save_crops)
            crops_pid = found["id"] if found else None
        if crops_pid not in config.DINO_PROMPTS:
            sys.exit(f"모르는 상품: {args.save_crops} (상품 id: {', '.join(config.DINO_PROMPTS)})")

    import rclpy
    from vision.camera import Camera
    if not rclpy.ok():
        rclpy.init()
    arm = None
    if args.robot:
        from robot.robot_arm import DoosanArm
        arm = DoosanArm(gripper="ros")
    camera = Camera()
    detector = make_detector(args.detector)
    try:
        while True:
            check(camera, detector, rclpy, ordered, arm.current_posx() if arm else None, crops_pid)
            if not args.loop or input("\nEnter = 한 장 더, q = 끝 > ").strip().lower() == "q":
                break
    finally:
        camera.destroy_node()
        if arm:
            arm.shutdown()
        elif rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
