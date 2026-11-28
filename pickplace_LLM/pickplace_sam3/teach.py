"""매대 촬영 자세 / 봉투 위치 티칭 → positions.json 저장 (main.py 실행 때 자동 적용).

로봇을 직접교시(수동 모드)로 옮긴 뒤 Enter 를 누르면 현재 값을 기록한다.
  - upper / lower : 카메라에 그 매대 상품들이 잘 보이는 자세 → joint 값(get_current_posj)
  - 봉투촬영      : 카메라가 봉투 바로 위에서 안쪽 전체를 보는 자세 → joint 값
  - 봉투바닥      : 그리퍼를 아래로 향하게 하고 끝을 봉투 바닥 '중심'에 댄 자세 → 좌표(get_current_posx)
                    (z = 바닥 높이, 그리퍼 방향 = 봉투 안에서 집고 놓을 방향)
  - 내려놓는곳    : (LLM "빼기") 봉투에서 꺼낸 상품을 놓을 첫째 자리 — 그리퍼를 아래로, 끝을 탁자 면에 댄 자세 → 좌표
  봉투 안쪽 크기는 config.BASKET_SIZE, 깊이는 CONTAINER_DEPTH(150mm), 기울어짐은 BASKET_YAW 에 직접 적는다.

  source ~/cobot2_ws/install/setup.bash
  python3 teach.py                  # 전체
  python3 teach.py upper 봉투바닥     # 일부만 다시
"""
import json
import os
import sys

import rclpy
import DR_init

import config

BASKET_VIEW, BASKET_FLOOR, RETURN = "봉투촬영", "봉투바닥", "내려놓는곳"


def main():
    DR_init.__dsr__id = config.ROBOT_ID
    DR_init.__dsr__model = config.ROBOT_MODEL
    rclpy.init()
    node = rclpy.create_node("pickplace_teach", namespace=config.ROBOT_ID)
    DR_init.__dsr__node = node
    from DSR_ROBOT2 import get_current_posj, get_current_posx

    data = {"view_poses": {}, "basket_view": None, "basket_center": None}
    if os.path.exists(config.POSITIONS_FILE):
        with open(config.POSITIONS_FILE, encoding="utf-8") as f:
            data.update(json.load(f))

    labels = {**{s: f"'{s}' 매대 촬영 자세" for s in config.VIEW_POSES},
              BASKET_VIEW: "봉투 촬영 자세 (봉투 바로 위에서 안쪽 전체가 보이게)",
              BASKET_FLOOR: "봉투 바닥 중심 (그리퍼 끝을 봉투 바닥 중심에, 아래를 향하게)",
              RETURN: "꺼낸 상품 내려놓는 첫째 자리 (그리퍼 끝을 탁자 면에, 아래를 향하게)"}
    for name in sys.argv[1:] or list(labels):
        if name not in labels:
            print(f"알 수 없는 이름: {name} (사용 가능: {', '.join(labels)})")
            continue
        answer = input(f"{labels[name]}로 로봇을 옮기고 Enter (s: 건너뛰기, q: 저장 후 종료) > ").strip().lower()
        if answer == "q":
            break
        if answer == "s":
            continue
        joints = [round(float(v), 3) for v in get_current_posj()]
        if name == RETURN:
            data["return_spot"] = [round(float(v), 3) for v in get_current_posx()[0]]
            print(f"  저장: 내려놓는 곳 = {data['return_spot']}")
        elif name == BASKET_FLOOR:
            data["basket_center"] = [round(float(v), 3) for v in get_current_posx()[0]]
            print(f"  저장: 봉투 바닥 중심 = {data['basket_center']}")
        elif name == BASKET_VIEW:
            data["basket_view"] = joints
            print(f"  저장: 봉투 촬영 자세 = {joints}")
        else:
            data["view_poses"][name] = joints
            print(f"  저장: {name} = {joints}")

    with open(config.POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n{config.POSITIONS_FILE} 에 저장했습니다.")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
