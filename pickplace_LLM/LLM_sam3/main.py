"""편의점 음성 명령 로봇 (LLM_sam3) — 말로 주문하면 pickplace_sam3 가 카메라(SAM3 + DINOv3)로 매대를 보고 집어 봉투에 넣는다.

흐름: "자비스" 호출 → "네, 말씀하세요" → 5초 녹음 → Whisper STT → 품목/개수/행동 추출 (gpt-4o, 실패하면 규칙)
      → 판단 (logic/store_robot.py) → pickplace_sam3 작업 프로세스가 로봇을 움직임 (logic/pickplace_bridge.py)

  source ~/cobot2_ws/install/setup.bash
  python3 main.py --gripper ros            # 실제 로봇 + bringup 그리퍼 (먼저 pickplace_sam3/teach.py 로 위치 티칭)
  python3 main.py                          # 실제 로봇 + 실제 RG2 (Modbus 직결)
  python3 main.py --gripper ros --text     # 마이크 대신 키보드로 명령 입력
  python3 main.py --gripper ros --text --rule   # OpenAI 없이 (규칙 기반 해석)
  python3 main.py --detector gdino ...     # 인식기 바꾸기 (기본: pickplace_sam3 의 config.DETECTOR = sam3_dinov3)
"""
import argparse
import sys

import config
from logic.command_parser import CommandParser, RuleParser
from logic.pickplace_bridge import PickplaceBridge
from logic.store_robot import StoreRobot


def listen(stt):
    """호출어를 기다렸다가 명령 문장을 하나씩 돌려준다."""
    from voice.speaker import Speaker
    from voice.wakeup_word import WakeupWord

    wakeup = WakeupWord(stt=stt)
    cue = Speaker()
    cue.prepare("listening")              # "네, 말씀하세요." 미리 합성
    while True:
        print(f"\n🎙  '{config.WAKEWORD_PHRASE}' 라고 불러주세요... (Ctrl+C: 종료)")
        rest = wakeup.wait()
        if len(rest) >= 2:                # "자비스, 라면 하나 추가해줘" → 다시 녹음하지 않고 바로 명령으로
            yield rest
            continue
        cue.say("listening")              # 끝난 뒤 녹음 시작
        text = stt.speech2text()
        if text:
            yield text
        else:
            print("  🤖 말씀을 듣지 못했어요. 다시 불러주세요.")


def type_in(bridge):
    """키보드로 명령 문장 입력 (--text). q = 끝, n = 새 봉투, r = 내려놓는 곳 치움"""
    print("\n⌨  명령을 입력하세요 (예: 콜라 두 개랑 라면 하나 추가해줘 / 콜라 하나 빼줘) — q: 끝, n: 새 봉투, r: 내려놓는 곳 치움")
    while True:
        try:
            text = input("\n> ").strip()
        except EOFError:
            return
        if text.lower() in ("q", "quit", "exit"):
            return
        if text.lower() == "n":
            bridge.new_bag()
            print("  🛍 새 봉투 — 넣은 기록을 지웠습니다")
            continue
        if text.lower() == "r":
            bridge.clear_returned()
            print("  🧹 내려놓는 곳을 비웠다고 기록했습니다")
            continue
        if text:
            yield text


def main():
    ap = argparse.ArgumentParser(description="편의점 음성 명령 로봇 (LLM_sam3 → pickplace_sam3)")
    ap.add_argument("--gripper", choices=["modbus", "ros"], default="modbus",
                    help="modbus: 실제 RG2 직결(기본값) / ros: /onrobot/sendCommand (m0609_rg2_bringup 의 가상·실제 그리퍼)")
    ap.add_argument("--text", action="store_true", help="마이크 대신 키보드로 명령 입력")
    ap.add_argument("--rule", action="store_true", help="OpenAI 없이 규칙 기반으로만 명령 해석")
    ap.add_argument("--detector", choices=["sam3_dinov3", "gdino", "external"], default=None,
                    help="물체 인식기 (기본: pickplace_sam3 의 config.DETECTOR)")
    ap.add_argument("--yes", action="store_true", help="pickplace_sam3 의 위치를 티칭하지 않았어도 예시 좌표로 진행")
    args = ap.parse_args()

    api_key = None if args.rule else config.load_openai_key()
    if not api_key and not args.text:
        sys.exit(f"음성 인식(Whisper)에 OpenAI 키가 필요합니다. {config.ENV_PATH} 에 OPENAI_API_KEY=... (키보드로 하려면 --text)")
    if not api_key and not args.rule:
        print("⚠️ OpenAI 키가 없어 규칙 기반으로 명령을 해석합니다")
    parser = CommandParser(api_key=api_key) if api_key else RuleParser()

    bridge = PickplaceBridge(gripper=args.gripper, detector=args.detector, yes=args.yes)
    try:
        bridge.start()                     # 로봇·카메라·인식기 준비 (모델 로드 — 처음엔 오래 걸림)
        robot = StoreRobot(bridge)
        if args.text:
            lines = type_in(bridge)
        else:
            from voice.stt import STT
            lines = listen(STT(api_key))
        for text in lines:
            print(f"\n🗣  \"{text}\"")
            robot.handle(parser.parse(text))
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
