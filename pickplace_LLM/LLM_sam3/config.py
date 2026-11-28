"""LLM 설정 — 바꿀 값은 전부 여기 있습니다.

  1. 품목 / 행동 / 개수
  2. 호출어 ("자비스")
  3. 녹음 · STT · 문장 해석
  4. 안내 음성 (TTS)
  5. 로봇 동작 — pickplace_sam3 (카메라 + SAM3/DINOv3 로 매대를 보고 집어 봉투에)  ← 위치 티칭·인식 설정은 그쪽 폴더
  6. 주문 확인 (QR → 1/2번 장바구니)
  (맨 아래) 내부에서 쓰는 함수 — 고칠 필요 없음

자주 바꾸는 값
  - 품목 추가/변경          : ITEMS, ITEM_ALIASES, ITEM_TO_PID (+ pickplace_sam3 상품 목록·갤러리)
  - 호출어가 잘 안 잡힘     : WAKEWORD_EXTRA_WORDS 에 터미널 "[wakeword] 들림" 글자 추가
  - 말했는데 무음 처리됨    : SPEECH_RMS_MIN 낮추기 (예: 500)
  - 안내 문구 / 목소리      : VOICE_MESSAGES / TTS_VOICE
  - 로봇 속도·위치·인식기   : pickplace_sam3/config.py (이 폴더에는 로봇 코드가 없음)
  - QR → 장바구니 번호      : ORDERS
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")                  # OpenAI 키 (OPENAI_API_KEY 또는 OPENAI_APIKEY)
SOUNDS_DIR = os.path.join(BASE_DIR, "sounds")              # 안내 음성 (cache/ 자동 생성)


# ==================================================================== 1. 품목 / 행동 / 개수
ITEMS = ["콜라", "사이다", "쫄병", "빼빼로", "스팸", "참치캔", "라면"]

# STT 가 다르게 받아 적을 수 있는 표현 → 표준 품목명 (Whisper 테스트에서 실제로 나온 오인식 포함)
ITEM_ALIASES = {
    "콜라": ["콜라", "코카콜라", "코카 콜라", "펩시", "코크"],
    "사이다": ["사이다", "칠성사이다", "칠성 사이다", "스프라이트", "싸이다"],
    "쫄병": ["쫄병스낵", "쫄병 스낵", "쫄병", "졸병", "쫄뱅", "쫄볍", "소병", "쏠병", "줄병"],
    "빼빼로": ["빼빼로", "빼뺴로", "뻬뻬로", "빼빼루", "빼뻬로", "페페로", "pepero"],
    "스팸": ["스팸", "스펨", "스캠", "쓰팸", "spam"],
    "참치캔": ["참치캔", "참치 캔", "동원참치", "동원 참치", "참치"],
    "라면": ["컵라면", "신라면", "라면", "라멘"],
}

ACTION_ADD = "추가"      # 진열대 → 장바구니
ACTION_REMOVE = "빼기"   # 장바구니 → 진열대
ACTION_KEYWORDS = {
    ACTION_REMOVE: ["빼줘", "빼 줘", "빼주", "빼", "제거", "삭제", "취소", "꺼내", "덜어",
                    "필요 없", "필요없"],
    ACTION_ADD: ["추가", "넣어", "담아", "가져와", "가져다", "주세요", "줘", "주라",
                 "살게", "살래", "구매"],
}

ALL = -1                                       # "다/전부/모두" → 전부 (빼기 전용)
MAX_COUNT_PER_COMMAND = 5                      # 한 번에 옮길 수 있는 최대 개수 (안전용)


# ==================================================================== 2. 호출어 ("자비스")
# 짧은 말소리를 Whisper 로 받아 적어 "자비스/Jarvis" 를 찾고(voice/wake_match.py 발음 규칙),
# 영어식 "Jarvis" 는 openwakeword 모델로도 바로 잡는다. "자비스, 라면 하나 추가해줘" 처럼 이어 말해도 된다.
WAKEWORD_PHRASE = "자비스"         # 안내 문구에 표시할 호출어
WAKEWORD_MODE = "hybrid"           # hybrid: Whisper + 모델 / stt: Whisper 만 / model: 모델만 (인터넷·API 안 씀)
WAKEWORD_EXTRA_WORDS = []          # 놓치는 표현 직접 추가. 예: ["자부스", "쟈비쓰"]
WAKEWORD_MAX_SEGMENT_SEC = 6.0     # 이보다 긴 말소리는 잘라서 확인 ("자비스, 라면 하나 추가해줘" 가 들어갈 길이)
WAKEWORD_MODEL = "hey_jarvis"      # openwakeword 모델: 기본 모델 이름 또는 .tflite 경로
WAKEWORD_THRESHOLD = 0.5           # 모델 감지 기준 (오감지 많으면 0.7)


# ==================================================================== 3. 녹음 · STT · 문장 해석
SAMPLE_RATE = 16000
RECORD_SECONDS = 5                 # "네, 말씀하세요" 뒤 녹음 시간
# 무음 판정: 30ms 구간 RMS 가 max(SPEECH_RMS_MIN, 잡음의 3배) 를 넘는 시간이 MIN_SPEECH_SEC 미만이면 무음.
# 무음을 Whisper 에 보내면 없는 말을 지어내므로(환각) 아예 보내지 않는다.
SPEECH_RMS_MIN = 1000              # 마이크 소리가 작으면 낮추기 (예: 500)
MIN_SPEECH_SEC = 0.5
OPENAI_MODEL = "gpt-4o"            # 문장 → 품목/개수/행동 추출 LLM


# ==================================================================== 4. 안내 음성 (TTS)
# edge-tts 음성 (처음 만들 때만 인터넷 필요, 이후 sounds/cache/ 재생). 바꾸면 음성 파일은 자동으로 다시 만들어진다.
TTS_VOICE = "ko-KR-SunHiNeural"    # 여성 안내 음성 "선희"
TTS_RATE = "+0%"                   # 속도: "-10%" 차분하게 / "+10%" 빠르게
TTS_PITCH = "+0Hz"                 # 높이
ESPEAK_VOICE = "ko+f3"             # edge-tts 를 못 쓸 때 대신 쓰는 로컬 기계음 (여성 변형)

# 안내 멘트 ({basket} 은 장바구니 번호). 문구를 바꾸면 음성 파일은 자동으로 다시 만들어진다.
VOICE_MESSAGES = {
    "listening":  "네, 말씀하세요.",      # 호출어 감지 직후 (이 소리가 끝나면 녹음 시작)
    "show_qr":    "어서 오세요. 화면 속 QR 코드를 보여주세요.",
    "qr_invalid": "확인되지 않는 QR 코드입니다. 화면 속 QR 코드를 다시 보여주세요.",
    "pickup":     "주문이 확인되었습니다. {basket}번 장바구니에 있는 물품을 가져가 주세요.",
    "warn_no_qr": "잠시만요! QR 코드를 먼저 확인해야 물품을 가져가실 수 있습니다. 화면 속 QR 코드를 보여주세요.",
    "warn_wrong": "잠시만요! 잘못 가져가셨습니다. {basket}번 장바구니의 물품을 가져가 주세요.",
    "goodbye":    "감사합니다, 안녕히 가십시오.",
}
WARNING_MESSAGES = ("warn_no_qr", "warn_wrong")   # 경고음(삐삐) 후 재생


# ==================================================================== 5. 로봇 동작 — pickplace_sam3
# 로봇은 pickplace_sam3 가 움직인다: 매대를 찍어 SAM3(물건 나누기) + DINOv3(상품 맞히기) 로 찾고, 가까운 것부터 집어 봉투에.
# 이 폴더(LLM_sam3)는 음성 → 명령 → pickplace_sam3 에 '넣어 줘 / 꺼내 줘' 만 보낸다 (logic/pickplace_bridge.py).
PICKPLACE_DIR = os.path.join(BASE_DIR, "..", "pickplace_sam3")   # 같은 pickplace_LLM 폴더 안의 pickplace_sam3

# 음성 품목 → pickplace_sam3 상품 id (pickplace_sam3/config.py 의 DINO_PROMPTS 키, 갤러리 폴더 이름)
ITEM_TO_PID = {
    "콜라": "cola",          # 콜라 범주 = 코카콜라 + 펩시
    "사이다": "cider",
    "쫄병": "snack_bag",     # pickplace 의 '과자' (봉지)
    "빼빼로": "pepero",
    "스팸": "spam_can",
    "참치캔": "tuna_can",
    "라면": "cup_ramen",
}
PID_TO_ITEM = {pid: item for item, pid in ITEM_TO_PID.items()}


# ==================================================================== 6. 주문 확인 (QR → 1/2번 장바구니)
# QR 에 담긴 문자열 → 장바구니 번호 (주문 시스템 / QR 생성 방식에 맞게 바꾸세요)
ORDERS = {
    "ORDER-0001": 1,
    "ORDER-0002": 2,
}
PICKUP_BASKETS = (1, 2)


# ==================================================================== 내부에서 쓰는 함수 (고칠 필요 없음)
def load_openai_key():
    """LLM_sam3/.env 에서 OpenAI 키를 읽는다. 실행 위치(cwd)와 상관없이 항상 이 파일과 같은 폴더의 .env."""
    from dotenv import dotenv_values

    if not os.path.exists(ENV_PATH):
        print(f"[config] {ENV_PATH} 파일이 없습니다.")
        return None
    values = dotenv_values(ENV_PATH)
    return values.get("OPENAI_API_KEY") or values.get("OPENAI_APIKEY")
