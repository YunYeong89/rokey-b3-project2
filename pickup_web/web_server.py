#!/usr/bin/env python3
"""
편의점 픽업 주문 웹 서버 (FastAPI + ROS2 브릿지) - 실행 진입점

실행:
    pip install fastapi uvicorn "qrcode[pil]"
    python3 web_server.py            # ROS2가 있으면 로봇 모드, 없으면 자동으로 데모 모드
    python3 web_server.py --mock     # 강제로 데모 모드 (로봇 없이 시뮬레이션)
    python3 web_server.py --mock --pickup-expire-min 1   # 픽업 만료 시간을 1분으로 (테스트용)

접속:
    손님 화면    http://<PC IP>:8000/
    직원 화면    http://<PC IP>:8000/#staff
    픽업 키오스크 http://localhost:8000/kiosk   (카메라 때문에 가게 PC 에서 localhost 로 열기)
    서버를 켜면 위 주소(PC IP 포함)를 터미널에 출력함

코드는 pickup/ 패키지에 있음 (pickup/__init__.py 에 파일별 역할).

픽업 흐름 (주문 기록은 data/runtime/orders.json):
    in_progress(로봇 작업 중) -> 로봇 done -> ready_for_pickup -> picked_up
                                       ready_for_pickup -> expired (준비 후 PICKUP_EXPIRE_MIN 분)
    로봇 cancelled / error -> cancelled / error
    QR 내용: "ROKEY-PICKUP:{pickup_token}"  (order_id 는 넣지 않음)

ROS2 토픽 (모두 std_msgs/String, JSON 문자열):
    웹 -> 로봇
      /order          {"order_id": 3, "items": {"tuna_can": 2, "milk_pack": 1}}
      /order_choice   {"order_id": 3, "choice": "partial" | "substitute" | "cancel", "substitute": "spam_can"}
      /scan_request   {"reason": "manual"}
      /pickup         {"order_id": 3, "pickup_code": "A-017"}   # 손님이 키오스크에서 수령 완료
    로봇 -> 웹
      /order_status   {"order_id": 3, "state": "checking"|"picking"|"need_choice"|"done"|"cancelled"|"error",
                       "message": "...", "current": "tuna_can", "done": 1, "total": 3,
                       "shortage": {"id": "tuna_can", "want": 2, "have": 1}, "picked": {"tuna_can": 1},
                       "staff_items": {"cola": 1}}   # done 일 때: 로봇이 안 담는 가상 상품(직원 준비)
      /vision_count   {"tuna_can": 1, "spam_can": 3, ...}   # 카메라로 센 개수
"""
import argparse
import socket

from pickup.config import PICKUP_EXPIRE_MIN


def lan_ip():
    """같은 와이파이의 휴대폰이 접속할 이 PC 의 IP (UDP connect 는 패킷을 보내지 않음). 모르면 127.0.0.1"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def print_urls(port):
    ip = lan_ip()
    print(f"[web] 손님 화면     http://{ip}:{port}/          (같은 와이파이의 휴대폰에서)")
    print(f"[web] 픽업 키오스크 http://localhost:{port}/kiosk   (이 PC 의 브라우저에서 열어야 카메라가 켜짐)")
    print(f"[web] 직원 화면     http://{ip}:{port}/#staff    (관리자 로그인)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="로봇 없이 데모 모드로 실행")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--pickup-expire-min", type=float, default=PICKUP_EXPIRE_MIN,
                    help=f"픽업 만료 시간(분), 기본 {PICKUP_EXPIRE_MIN}")
    args = ap.parse_args()

    import uvicorn
    from pickup.api import create_app
    from pickup.robot import connect_robot
    from pickup.service import PickupService

    svc = PickupService(expire_min=args.pickup_expire_min)
    svc.robot = connect_robot(svc, mock=args.mock)
    print_urls(args.port)
    uvicorn.run(create_app(svc), host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
