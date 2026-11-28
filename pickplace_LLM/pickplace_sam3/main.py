"""pickplace_sam3 — 키보드로 주문하면 카메라(RealSense) + 물체 인식기로 상품을 찾아 집고 비닐봉투에 넣는다.
인식기: config.DETECTOR / --detector — sam3_dinov3 (SAM3 + DINOv3, 기본) / gdino (Grounding DINO) / external (팀원 함수)

  source ~/cobot2_ws/install/setup.bash
  python3 main.py --gripper ros --yes      # m0609_rg2_bringup (가상: 예시 좌표 확인 생략)
  python3 main.py --gripper ros            # 실제 로봇 (먼저 teach.py 로 위치 티칭)
  python3 main.py --gripper ros --detector gdino

입력:  2                     → 2번 상품 1개
       콜라 2, 컵라면 2       → 여러 개 주문 (쉼표로 구분) — 매대를 찍어 보고 ① 가까운 → ② 무거운 순서로 집어 넣음
                               (config.PICK_PRIORITY = "weight" 이면 ① 무거운 → ② 큰 → ③ 가볍고 작은 순서)
       c 2                   → 2번 상품 개수 세기     b → 봉투 확인     n → 새 봉투 (넣은 기록 지우기)     q → 종료
봉투 바닥이 차면 다른 물건 위에 쌓고(봉투 깊이 150mm 안), 그래도 자리가 없으면 다른 물품을 옮겨서 자리를 만든다.
그래도 안 되는 상품은 건너뛰고 다음 상품을 계속 넣는다 (마지막에 못 넣은 상품을 알려 줌).
"""
import argparse
import sys

import config
from logic.pick_place import PRIORITY_NAMES, PickPlace, find_product, item_spec, load_products, order_sequence, priority
from robot.robot_arm import DoosanArm, MotionError
from vision.camera import Camera
from vision.factory import add_detector_arg, make_detector


def show_menu(products):
    print("\n──────── 상품 (번호 / id / 이름) ────────")
    for i, p in enumerate(products, 1):
        it = item_spec(p["id"], p)
        tags = " · ".join(t for t, on in (("깨짐주의", p.get("fragile")), ("말랑", p.get("deformable"))) if on)
        print(f"  {i}) {p['name']:<12} {p['id']:<12} {it.weight:>4.0f}g {it.length:.0f}x{it.width:.0f}x{it.height:.0f}mm "
              f"[{PRIORITY_NAMES[priority(it)]}]" + (f" [{tags}]" if tags else ""))
    print("  입력 예) 2  /  콜라 2, 컵라면 2  /  c 2 (개수 세기)  /  b (봉투 확인)  /  n (새 봉투)  /  q (종료)")


def confirm_positions(args):
    """티칭하지 않은 예시 좌표로 실제 로봇을 움직이지 않도록 한 번 확인한다."""
    if args.yes or not config.UNTAUGHT_POSITIONS:
        return
    print(f"⚠️  아직 티칭하지 않아 예시 좌표를 쓰는 위치: {', '.join(config.UNTAUGHT_POSITIONS)}")
    print("   실제 로봇이라면 먼저 `python3 teach.py` 로 위치를 티칭하세요. (가상 로봇이면 계속해도 됩니다)")
    if input("   예시 좌표로 계속할까요? [y/N] > ").strip().lower() != "y":
        sys.exit(0)


def parse_one(text, products):
    """'spam 2' / '2' / '스팸' / '콜라2' / '콜라 2개' → (상품, 개수) 또는 None"""
    import re
    text = text.strip().strip("\"'“”‘’")
    m = re.match(r"^(.*?\D)\s*(\d+)\s*개?$", text)        # 이름 뒤 숫자 = 개수 (번호만 쓴 '2' 는 상품 번호)
    name, count = (m.group(1).strip(), int(m.group(2))) if m else (text.removesuffix("개").strip(), 1)
    product = find_product(products, name)
    return (product, count) if product and count > 0 else None


def order_text(words):
    """명령줄 주문 → 'A 1, B 2' 문자열. 따옴표 없이 '콜라 1 사이다 2' 처럼 써도 상품마다 쉼표로 나눔"""
    import re
    text = " ".join(words).replace("“", "").replace("”", "").replace('"', "").strip()
    if "," in text:
        return text
    return re.sub(r"(\d+)\s*개?\s+(?=\D)", r"\1, ", text)        # 숫자 뒤에 다음 이름이 오면 쉼표


def parse_order(line, products):
    """'스팸 2, 과자, 참치 1' → ({pid: 개수}, 못 알아들은 부분 목록)"""
    items, unknown = {}, []
    for part in [p.strip() for p in line.replace("，", ",").strip("\"'“”‘’").split(",") if p.strip()]:
        one = parse_one(part, products)
        if one is None:
            unknown.append(part)
            continue
        product, count = one
        items[product["id"]] = items.get(product["id"], 0) + count
    return items, unknown


def show_bag(pp):
    bm = pp.scan_basket()
    plan = bm.plan(item_spec("?"))          # 기본 크기(80mm) 상품 기준
    tallest = max((o.top for o in bm.objects), default=0.0)
    state = ("바로 넣을 수 있음" + (" (쌓기)" if plan.spot.stacked else "") if plan.spot and not plan.moves else
             f"물건 {len(plan.moves)}개를 옮기면 넣을 수 있음" if plan.spot else "자리 없음")
    print(f"  🛍 봉투: 물건 덩어리 {len(bm.objects)}개, 가장 높은 곳 {tallest:.0f}/{config.CONTAINER_DEPTH:.0f}mm (최대 {config.CONTAINER_MAX:.0f}), "
          f"넣은 기록 {len(pp.placed)}개 / 80mm 상품 기준 {state} (지도: {bm.draw(plan)})")


def main():
    ap = argparse.ArgumentParser(description="pickplace — 상품을 찾아 집어서 비닐봉투에 넣기")
    ap.add_argument("--gripper", choices=["modbus", "ros"], default="modbus",
                    help="modbus: 실제 RG2 직결(기본값) / ros: /onrobot/sendCommand (m0609_rg2_bringup 의 가상·실제 그리퍼)")
    ap.add_argument("--yes", action="store_true", help="예시 좌표 사용 확인을 건너뜀 (가상 로봇용)")
    add_detector_arg(ap)
    args = ap.parse_args()

    confirm_positions(args)
    products = load_products()
    info = {p["id"]: p for p in products}
    print(f"[main] 상품 목록: {config.products_file()} (로봇이 집는 실물 {len(products)}종)")

    arm = DoosanArm(gripper=args.gripper)
    camera = Camera()
    detector = make_detector(args.detector)
    pp = PickPlace(arm, camera, detector, products)
    try:
        arm.home()
        while True:
            show_menu(products)
            line = input("\n> ").strip()
            low = line.lower()
            if low in ("q", "quit", "exit"):
                break
            if low == "n":
                pp.new_bag()
                print("  🛍 새 봉투 — 넣은 물건 기록을 지웠습니다")
                continue
            try:
                if low == "b":
                    show_bag(pp)
                    arm.home()
                    continue
                if low.startswith("c "):
                    one = parse_one(line[2:], products)
                    if one is None:
                        print("  상품을 찾지 못했어요.")
                        continue
                    product = one[0]
                    pp.move_to_view(product.get("shelf", "upper"))
                    print(f"  📷 {product['name']}: {pp.count_items(product['id'])}개 보임 (그림: {config.LAST_DETECTION_IMAGE})")
                    arm.home()
                    continue

                items, unknown = parse_order(line, products)
                if unknown:
                    print(f"  못 알아들은 상품: {', '.join(unknown)} — 번호나 id 로 다시 입력해 주세요")
                    continue
                if not items:
                    continue
                if config.PICK_PRIORITY == "near_heavy":
                    print("  📋 집는 순서: 매대를 찍어 보고 ① 가까운 → ② 무거운 순서로 정합니다")
                else:
                    print("  📋 넣는 순서 (① 무거운 → ② 큰 → ③ 가볍고 작은): "
                          + " → ".join(f"{info[pid]['name']}" for pid in order_sequence(items, info)))
                done = pp.run_order(items)
                print("  🛍 결과: " + ", ".join(f"{info[pid]['name']} {done.get(pid, 0)}/{n}" for pid, n in items.items()))
                if pp.skipped:
                    print("  ⚠️ 못 넣은 상품 (직원 확인 필요): "
                          + ", ".join(f"{info[pid]['name']}({why})" for pid, why in pp.skipped))
            except (MotionError, RuntimeError) as e:
                print(f"  ⚠️ 작업 중 오류: {e}")
                try:
                    arm.home()
                except Exception as e2:
                    print(f"  ⚠️ 대기 자세 복귀 실패: {e2} — 로봇 상태를 확인하세요")
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        camera.destroy_node()
        arm.shutdown()


if __name__ == "__main__":
    main()
