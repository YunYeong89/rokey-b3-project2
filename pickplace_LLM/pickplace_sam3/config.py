"""pickplace 설정 — 바꿀 값은 전부 여기 있습니다.

  1. 상품 (pickup_web 의 products.json) + DINO 가 찾을 영어 이름
  2. 카메라 · Grounding DINO
  2-1. 인식기 고르기 — SAM3 + DINOv3 (기본) / Grounding DINO / 팀원 코드
  3. 로봇 · 그리퍼
  4. 위치 (매대 촬영 자세 / 봉투 촬영 자세 / 봉투 바닥 중심 / 봉투 깊이)  ← 예시값. teach.py 로 티칭하면 positions.json 이 우선 적용
  5. 집기 (깊이 보정, 접근 거리)
  6. 봉투 빈자리 · 쌓기 · 정리 (자리가 없으면 다른 물품을 옮겨서 자리 만들기)
  7. 넣는 순서 — 무거운 물품 → 큰 물품 → 가볍고 작은 물품 (상품별 무게·크기 표)
  8. 집는 우선순위 — ① 가까운 물품 → ② 무거운 물품 (매대를 찍어 보고 정함)
  9. 정면(수평) 집기 — 캔 음료·컵라면은 앞에서 정중앙을 수평으로 집고, 수평 그대로 봉투 바닥 가까이 놓음
  10. 봉투에서 꺼내기 (LLM 음성 "빼기") — 꺼낸 상품을 탁자 위 '내려놓는 곳' 에
  (맨 아래) 내부에서 쓰는 함수 — 고칠 필요 없음
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")             # teach.py 가 만드는 티칭 좌표
GRIPPER2CAM_FILE = os.path.join(DATA_DIR, "T_gripper2camera.npy")     # 실습 핸드아이 캘리브레이션 결과 (mm)
LAST_DETECTION_IMAGE = os.path.join(DATA_DIR, "last_detection.jpg")   # 마지막으로 찾은 결과 그림
LAST_BASKET_IMAGE = os.path.join(DATA_DIR, "last_basket.png")         # 마지막 봉투 지도 (빈자리·쌓을 자리·옮길 물건)


# ==================================================================== 1. 상품
# 상품 목록: 이 폴더의 data/products.json (웹 pickup_web 의 products.json 복사본 — 웹 목록이 바뀌면 이 파일을 바꿔 넣기)
#   이 폴더 밖 파일은 읽지 않는다 (pickplace_LLM 폴더 하나만 있으면 돌게)
LOCAL_PRODUCTS_FILE = os.path.join(DATA_DIR, "products.json")
# 웹 목록에서 robot: false 지만 pickplace 로봇이 집을 상품 → products.json 값 위에 덮어쓸 값 (웹 파일은 바꾸지 않음)
EXTRA_ROBOT_PRODUCTS = {
    "cola":      {"shelf": "upper"},                    # 콜라 범주 = 코카콜라 + 펩시 (DINO_PROMPTS 에 두 이름)
    "cup_ramen": {"shelf": "upper", "fragile": True},   # 종이컵이라 세게 쥐면 찌그러짐 → 약하게·천천히, 위에 안 쌓음
}
# 웹 목록에 아예 없는 상품 (pickplace 에서만 씀)
NEW_PRODUCTS = [
    {"id": "cider", "name": "사이다", "emoji": "🥤", "category": "음료", "robot": True, "shelf": "upper",
     "fragile": False, "deformable": False},
    {"id": "pepero", "name": "빼빼로", "emoji": "🍫", "category": "과자", "robot": True, "shelf": "upper",
     "fragile": False, "deformable": False},      # LLM 음성 품목 '빼빼로' (크기는 예시값 — 실물로 재기)
]
# 키보드·명령에서 쓰는 다른 이름 → 상품 id
PRODUCT_ALIASES = {
    "펩시": "cola", "pepsi": "cola", "코카콜라": "cola", "코크": "cola", "coke": "cola",
    "사이다": "cider", "cider": "cider",
    "라면": "cup_ramen", "신라면": "cup_ramen", "ramen": "cup_ramen",
    "빼빼로": "pepero", "pepero": "pepero", "쫄병": "snack_bag", "참치": "tuna_can", "스팸": "spam_can",
}

# Grounding DINO 는 영어로 찾는다. 상품 id → 찾을 때 쓸 영어 설명 (잘 안 찾아지면 여기를 바꾸기)
DINO_PROMPTS = {
    "tuna_can":    "tuna can",
    "spam_can":    "spam can",
    "milk_pack":   "small milk carton",
    "soap_box":    "soap box",
    "choco_box":   "chocolate box",
    "snack_bag":   "snack bag",
    "mini_tissue": "tissue pack",
    "egg_pack":    "egg carton",
    "new_item":    "object",
    "cola":        ["red coca cola can", "black pepsi can"],   # 목록 = 여러 이름 모두 이 상품 (펩시도 일단 콜라 범주)
    "cider":       "white cider can",    # 시뮬 실측: "cider can" 이면 펩시를 사이다로 부름 → 색을 넣으니 펩시·사이다가 갈림
    "cup_ramen":   "red noodle cup",     # 실측(첫 매대 사진): "ramen cup" 0.38 → "red noodle cup" 0.55, 콜라 2개도 그대로 찾음
    "pepero":      "pepero box",         # (시뮬 확인 안 함)
}
# 상품이 아닌 것. 모든 상품 이름과 함께 한꺼번에 찾게 하면(경쟁) 사람·손을 상품으로 착각하지 않는다.
# (실측: "tuna can" 만 찾게 하면 사람을 tuna 0.37 로 잘못 찾음 → 경쟁시키면 person 0.84 로 분리되어 오인식 0건)
DINO_DISTRACTORS = ["person", "hand"]


def prompts_of(pid):
    """상품 id → DINO 이름 목록 (DINO_PROMPTS 값이 문자열이면 하나)"""
    v = DINO_PROMPTS.get(pid, pid.replace("_", " "))
    return list(v) if isinstance(v, (list, tuple)) else [v]


def all_prompts():
    """모든 상품의 DINO 이름 (중복 없이)"""
    return list(dict.fromkeys(p for pid in DINO_PROMPTS for p in prompts_of(pid)))


# ==================================================================== 2. 카메라 · Grounding DINO
COLOR_TOPIC = "/camera/color/image_raw"
DEPTH_TOPIC = "/camera/aligned_depth_to_color/image_raw"   # 컬러에 맞춘 깊이 (mm)
CAMERA_INFO_TOPIC = "/camera/color/camera_info"
CAMERA_TIMEOUT = 5.0           # 사진을 기다리는 최대 시간(초)
SETTLE_SEC = 0.5               # 촬영 자세에 도착한 뒤 흔들림이 멎을 때까지 기다리는 시간

DINO_MODEL = "IDEA-Research/grounding-dino-tiny"   # 처음 한 번 내려받음 (~700MB), 이후 캐시
BOX_THRESHOLD = 0.35           # 이 점수 이상인 상자만 인정 (오인식이 많으면 올리기)
TEXT_THRESHOLD = 0.25
LABEL_MARGIN = 0.05            # (우선순위 집기) 상자의 1등 이름 점수가 2등보다 이만큼 높아야 그 상품으로 인정
SIZE_CHECK = (0.75, 1.3)       # (우선순위 집기) 상자 폭 x 깊이로 잰 실제 폭이 표의 폭(짧은 변)의 이 배율 안이어야 인정
                               #   상자마다 '폭이 맞는 주문 상품' 중 이름 점수가 가장 높은 것으로 정함
                               #   (DINO 가 빨간 컵라면을 콜라로 부름 — 시뮬 0.37~0.45 → 폭 98mm 라 컵라면으로 다시 정함)
SIZE_MIN_SCORE = 0.15          # 그때 그 상품 이름 점수가 이 이상이어야 함 (시뮬: 콜라로 불린 앞 컵라면의 컵라면 점수 0.18)
                               #   DINO 1등 이름이 주문 상품일 때만 바로잡음 — 펩시·사이다로 불린 상자는 그대로 제외 (펩시도 콜라 점수 0.16)
SIZE_RELABEL_MIN_SCORE = 0.05     # DINO 가 다른 상품 이름으로 불렀는데 그 상품 폭과 안 맞으면 → 폭 맞는 주문 상품 이름 점수가 이만큼만 돼도 인정
                               #   (3cm 간격 시뮬: 혼자 선 컵라면을 '콜라' 0.53~0.65 로 부르고 컵라면 점수 0.07~0.13. 스팸·과자는 0.01~0.02)
OCCLUDED_RATIO = 0.3           # 더 가까운 다른 상자가 이 비율 넘게 덮으면 '뒤에 가려진 물건' → 이번엔 안 집음 (깊이가 앞 물건 것이라)
NMS_IOU = 0.6                  # (우선순위 집기) 이보다 많이 겹치는 상자는 같은 물건으로 보고 점수 높은 것만 남김
DINO_THREADS = 4               # CPU 스레드 수


# ==================================================================== 2-1. 인식기 고르기 — Grounding DINO / SAM3 + DINOv3 / 팀원 코드
# 로봇 코드는 인식기가 무엇이든 같은 모양의 답(vision/detector.py 의 Box)만 받는다 → 여기 한 줄만 바꾸면 됨
#   "sam3_dinov3" : SAM3 로 물건 나누기(마스크) + DINOv3 로 어느 상품인지 맞히기 (vision/sam3_dinov3.py, 갤러리 사진 필요)
#   "gdino"       : Grounding DINO (원래 pickplace 방식, 위 2번 설정 — 갤러리 없이 바로 됨, 시뮬 검증 끝)
#   "external"    : 팀원이 만든 인식 함수를 그대로 연결 (vision/external.py, 아래 EXTERNAL_DETECTOR)
# 실행할 때 바꾸기: --detector gdino  또는  PICKPLACE_DETECTOR=gdino python3 main.py
DETECTOR = os.environ.get("PICKPLACE_DETECTOR", "sam3_dinov3")
DEVICE = "auto"                # "auto" = GPU 있으면 cuda, 없으면 cpu  ("cuda", "cpu" 로 고정 가능)

# --- SAM3 (물건 나누기) — Hugging Face transformers 의 Sam3Model (사용 승인 받은 계정으로 huggingface-cli login 필요)
SAM3_MODEL = "facebook/sam3"
SAM3_PROMPTS = ["can", "cup", "box", "bottle", "pack"]   # 일반 단어로 '물건'만 나눔 (상품 구분은 DINOv3 가 함)
SAM3_THRESHOLD = 0.4           # 이 점수 이상인 물건만 (매대·봉투까지 잡히면 올리기)
SAM3_MERGE_IOU = 0.6           # 여러 단어가 같은 물건을 잡았을 때: 마스크가 이 비율 넘게 겹치면 하나로
SAM3_MAX_AREA = 0.25           # 사진의 이 비율보다 큰 마스크는 물건이 아님 (매대 판·벽)

# --- DINOv3 (어느 상품인지) — 갤러리(상품별 참조 사진)와 특징 비교
DINOV3_MODEL = "facebook/dinov3-vits16-pretrain-lvd1689m"   # 팀원이 쓰는 크기로 (vitb16, vitl16 …)
GALLERY_DIR = os.path.join(DATA_DIR, "gallery")             # data/gallery/<상품 id>/*.jpg  (폴더 이름 = config 의 상품 id)
GALLERY_FILE = os.path.join(GALLERY_DIR, "embeddings.npz")  # 인식기가 시작할 때 사진으로 만드는 특징 파일 (git 에 안 올림)
CROP_PAD = 8                   # 물건 조각을 자를 때 상자 둘레 여유(px)
DINOV3_MIN_SIM = 0.35          # 가장 닮은 상품과의 코사인 유사도가 이보다 낮으면 모르는 물건 "?" (realsense_check 로 맞추기)
DINOV3_TEMPERATURE = 0.05      # 유사도 → 0~1 점수 (softmax) 의 날카로움. 작을수록 1등 점수가 1 에 가까움

# --- 팀원 코드 연결 (DETECTOR = "external") — "모듈:함수"  (모듈은 pickplace 폴더 기준 import 경로, 예: "team_vision.infer:detect")
#   함수(bgr 사진) → [{"pid" 또는 "label", "score", "box": [x1, y1, x2, y2], "mask"(선택), "scores"(선택)}, ...]
#   자세한 형식: vision/external.py
EXTERNAL_DETECTOR = os.environ.get("PICKPLACE_EXTERNAL", "")


# ==================================================================== 3. 로봇 · 그리퍼
ROBOT_ID = "dsr01"
ROBOT_MODEL = "m0609"
VELOCITY, ACC = 60, 60
FRAGILE_VELOCITY, FRAGILE_ACC = 30, 30   # fragile 상품(계란 등)은 천천히

GRIPPER_NAME = "rg2"
TOOLCHARGER_IP = "192.168.1.1"     # --gripper modbus (실제 RG2 직결)
TOOLCHARGER_PORT = "502"
GRIPPER_FORCE = 400
FRAGILE_GRIPPER_FORCE = 150        # fragile 상품은 약하게
GRIPPER_TIMEOUT = 5.0              # 그리퍼 동작 대기 최대 시간(초)
GRIP_WIDTH_DMM = 400               # --gripper ros: 물건을 문 상태의 너비(1/10mm, 400=40mm)

JREADY = [0, 0, 90, 0, 90, 0]      # 대기 자세 (joint, deg)


# ==================================================================== 4. 위치 (예시값 — teach.py 로 교체)
# 매대 촬영 자세 (joint, deg). products.json 의 "shelf" 값(upper / lower)으로 고른다.
# 예시값 = 실제 배치 사진 3장으로 잡은 배치(매대 앞 끝 x≈650mm, 윗칸 판 높이 330mm, 아래 칸 30mm)에서
#   카메라가 매대를 정면에서 내려다보는 자세 (가상 로봇 역기구학으로 계산 — 실제 로봇은 teach.py 로 티칭)
VIEW_POSES = {
    "upper": [-40.9, -27.6, 75.2, 32.7, 85.8, 61.0],      # 카메라 (180, -15, 760), 35° 아래 → 윗칸 컵라면~사이다가 한 화면에
    "lower": [-18.0, -22.6, 147.8, 113.2, 17.4, -32.6],   # 카메라 (250, -5, 330) → 아래 칸 물건 쪽
}
# 담는 곳 = 비닐봉투 (거치대에 입구를 벌려 세워 둔 봉투). 코드 안 이름은 BASKET_* 그대로 쓴다.
# 봉투 촬영 자세 (joint, deg) — 카메라가 봉투 '바로 위'에서 내려다보는 자세가 좋다
#   (비스듬히 보면 높은 물건 뒤 바닥이 가려져 깊이가 비고, 봉투 일부가 화면 밖으로 나감)
#   예시값: 아래 BASKET_CENTER 기준 카메라가 중심에서 8mm, 바닥 위 555mm (가상 로봇에서 계산)
BASKET_VIEW_POSE = [80.6, 19.6, 66.5, 0.0, 93.9, 80.6]   # 카메라가 봉투 위 약 46cm 에서 내려다봄 (봉투 자리 기준 계산값)
#   팔꿈치(J3)를 굽힌 해로 고름 — J3=0 (팔을 다 편 자세)이면 특이점이라 여기서 봉투 안 물건 위로 직선 이동이 멈춤 (시뮬 확인)
# 봉투 바닥 중심 [x, y, z, rx, ry, rz] — 그리퍼 끝을 바닥 중심에 댄 자세 (그리퍼는 아래를 향함)
#   z = 바닥 높이, 방향(rx, ry, rz) = 봉투 안에서 집고 놓을 때의 그리퍼 방향
BASKET_CENTER = [53.0, 605.0, 0.0, 0.0, 180.0, 0.0]     # RViz 화면에 다시 표시해 준 빨간 네모 자리 (탁자 위, 로봇에서 약 61cm, 예시값)
BASKET_SIZE = (250.0, 150.0)       # 봉투 바닥(입구) 안쪽 크기 (가로, 세로) mm — 가로가 BASKET_YAW 방향 (펼쳐 둔 비닐 25x15cm)
BASKET_YAW = 0.0                   # 봉투 가로 방향이 base x축과 이루는 각도(deg) — 빨간 네모가 x 방향으로 긴 모양
CONTAINER_DEPTH = 150.0            # 평소 채우는 높이(mm) — 쌓은 물건 윗면을 바닥에서 이 높이까지 (넣기 편하게 정한 기준, 비닐은 여유 있게 펼쳐 둠)
CONTAINER_MAX = 200.0              # 넘칠 것 같을 때 최대(mm) — 150mm 안에 자리가 없을 때만 여기까지 채움


# ==================================================================== 5. 집기
# 카메라가 잰 점은 물건 '겉면'이라, 그리퍼가 감쌀 수 있게 그리퍼 방향(도구 z축)으로 더 들어간다.
# (실습 pick_and_place_voice 의 DEPTH_OFFSET=-35, MIN_DEPTH=2 와 같은 값)
PICK_DEPTH = 35.0                  # 겉면에서 더 들어가는 거리(mm)
MIN_PICK_Z = 2.0                   # 집는 높이 하한(mm) — 바닥·선반에 부딪히지 않게
APPROACH_DIST = 100.0              # 집기 전 그리퍼 방향 반대쪽으로 떨어져서 접근하는 거리(mm)
BASKET_APPROACH = 100.0            # 봉투 위 접근 높이(mm)
DEPTH_WINDOW = 7                   # 깊이를 읽을 때 중심 주변 7x7 픽셀의 중간값 사용


# ==================================================================== 6. 봉투 빈자리 · 쌓기 · 정리
# 봉투 안을 5mm 격자로 나눠 칸마다 높이를 재고, 바닥보다 OCCUPIED_HEIGHT 이상 높은 칸을 '물건 있음'으로 본다.
GRID_CELL = 5.0                    # 격자 한 칸(mm) — 1cm 안으로 붙은 두 물건도 따로 구분되게
OCCUPIED_HEIGHT = 15.0             # 바닥보다 이만큼 높으면 물건 있음(mm)
WALL_MARGIN = 5.0                  # 봉투 벽에서 띄울 거리(mm) — 비닐 벽은 밀리므로 5mm (10mm 면 입구 20x15cm 에 눕힌 캔 2개가 안 들어감)
FINGER_CLEARANCE = 15.0            # 상품 둘레 손가락이 내려올 자리(mm) — 이 둘레는 손가락 끝보다 낮아야 함
FINGER_SAFETY = 5.0                # 손가락 끝과 둘레 물건 사이 여유(mm)
GRIPPER_CLEARANCE = 15.0           # 봉투 안 물건을 옮길 때 물건 둘레에 더 비워 둘 거리(mm, 손가락 자리)
SPOT_SAFETY = 5.0                  # 빈자리를 찾을 때 물건 둘레에 더 두는 안전 여유(mm) — 격자 반올림·깊이 흔들림 대비
MAX_UNKNOWN_RATIO = 0.1            # 빈자리 안에 깊이를 못 읽은 칸이 이 비율까지는 허용
MIN_OBJECT_AREA = 400.0            # 이보다 작은 덩어리(mm², 2x2cm)는 잡음으로 보고 무시
MIN_MOVE_DIST = 30.0               # 물건을 옮길 때 최소 이동 거리(mm) — 제자리 이동 방지
# 옮겨도 되는 물건인지 (아니면 장애물로만 취급) — 여러 물건이 붙어 한 덩어리로 보이면 그 '사이'를 집게 되므로
GRIPPER_MAX_GRASP = 100.0          # 짧은 변이 이보다 크면 못 잡음 (RG2 최대 110mm)
MIN_FILL_RATIO = 0.7               # 덩어리가 자기 직사각형을 채운 비율 — 낮으면 여러 물건이 붙은 것
MAX_HEIGHT_SPREAD = 25.0           # 윗면 높이 차이(mm) — 크면 높이가 다른 물건이 붙은 것

BASKET_PICK_DEPTH = 20.0           # 봉투 안 물건을 집을 때 윗면에서 내려가는 거리(mm)
MIN_GRASP_ABOVE_FLOOR = 10.0       # 집는 높이 하한 (바닥 위 mm)
PLACE_CLEARANCE = 5.0              # 옮긴 물건을 놓을 때 집었던 높이보다 이만큼 위에서 놓기(mm)
DROP_CLEARANCE = 10.0              # 상품 바닥을 받침면에서 이만큼 띄워 놓고 그리퍼를 연다(mm)
GRIPPER_CLOSE_AXIS = "y"           # 그리퍼 손가락이 닫히는 방향 (도구 좌표 'x' 또는 'y') — 실제 RG2 장착 방향에 맞추기
RESCAN_AFTER_MOVE = True           # 물건을 옮긴 뒤 봉투를 다시 찍어서 빈자리 확인

# 쌓기 (다른 물건 위에 놓기) — 바닥을 먼저 채우고(낮은 자리 우선), 바닥이 차면 쌓는다
STACKING = True
SUPPORT_TOL = 10.0                 # 받침면으로 인정하는 높이 차이(mm) — 가장 높은 곳에서 이만큼 안
MIN_SUPPORT_RATIO = 0.7            # 상품 바닥 중 받쳐지는 면적 비율 (낮으면 기우뚱 → 안 쌓음)
NO_STACK_ON_FRAGILE = True         # 깨지기 쉬운(fragile) 물건 위에는 안 쌓음 (우리가 넣은 물건 기록으로 판단)
NO_HEAVIER_ON_LIGHTER = True       # 가벼운 물건 위에 더 무거운 물건을 안 올림 (우리가 넣은 물건 기록으로 판단)


# ==================================================================== 7. 넣는 순서 (무게·크기)
# 주문을 ① 무거운 물품 → ② 큰 물품 → ③ 가볍고 작은 물품 순서로 넣는다 (무거운 게 아래로 → 쌓기에도 유리)
HEAVY_WEIGHT_G = 200               # 이 무게(g) 이상 = 무거운 물품
BIG_VOLUME_CM3 = 400               # 부피(cm³)가 이 이상이거나
BIG_LENGTH_MM = 140                # 가장 긴 변이 이 이상 = 큰 물품
# 상품별 무게(g)와 크기 (가로, 세로, 높이 mm) — ⚠️ 예시값입니다. 실제 상품을 재서 바꾸세요.
#   크기는 봉투 안 자리 계산에, 높이는 쌓기·놓는 높이 계산에 쓴다 (높이는 매대에서 RealSense 로 잴 수 있으면 잰 값 사용)
PRODUCT_SPECS = {
    "tuna_can":    dict(weight=180, size=(85, 85, 45)),
    "spam_can":    dict(weight=340, size=(100, 60, 75)),
    "milk_pack":   dict(weight=215, size=(55, 55, 100)),
    "soap_box":    dict(weight=100, size=(90, 60, 35)),
    "choco_box":   dict(weight=80,  size=(140, 75, 25)),
    "snack_bag":   dict(weight=60,  size=(180, 130, 60)),
    "mini_tissue": dict(weight=20,  size=(110, 55, 30)),
    "egg_pack":    dict(weight=100, size=(160, 100, 65)),
    "new_item":    dict(weight=100, size=(80, 80, 80)),
    "cola":        dict(weight=370, size=(66, 66, 122)),    # 350ml 캔 (펩시도 같은 크기)
    "cider":       dict(weight=265, size=(60, 60, 122)),    # 245ml 캔 (사진에서 콜라보 약간 가늚 — 예시값)
    "cup_ramen":   dict(weight=80,  size=(98, 98, 105)),    # 컵라면 (소컵)
    "pepero":      dict(weight=50,  size=(75, 25, 155)),    # 빼빼로 상자 (예시값)
}
DEFAULT_SPEC = dict(weight=100, size=(80, 80, 80))   # 표에 없는 상품


# ==================================================================== 8. 집는 우선순위 (가까운 → 무거운)
# "near_heavy": 매대를 찍어서 주문 상품을 전부 찾은 뒤 ① 로봇에서 가까운 것 → ② 무거운 것 순서로 하나씩 집는다
# "weight":     예전 방식 — 찍기 전에 7번 표(무게·크기)만 보고 순서를 정함
PICK_PRIORITY = "near_heavy"
NEAR_ORIGIN = (0.0, 0.0)           # 거리를 재는 기준점 (base x, y mm) — (0, 0) = 로봇 받침 중심
NEAR_TOL = 50.0                    # 가장 가까운 것과 거리 차이가 이 안(mm)이면 '똑같이 가깝다'고 보고 무거운 것을 먼저
# 매대는 한 번 찍은 순서표대로 집는다 (상품마다 DINO 를 다시 돌리지 않음 — RViz 와 같이 돌리면 한 번에 20~45초)
RELOOK_RADIUS = 120.0              # 다음 상품이 방금 집은 자리에서 이 안(mm)이면 손가락에 밀렸을 수 있으므로 다시 찍어 확인
RESURVEY_EACH_PICK = False         # True: 예전처럼 매번 다시 찍기 (느리지만 사람이 매대를 자주 건드릴 때 안전)


# ==================================================================== 9. 정면(수평) 집기 — 캔 음료·컵라면
# 매대 촬영 자세에서 카메라가 보는 방향(수평 성분)으로 다가가, 상품 앞면 가운데에서 반지름만큼 들어간 '정중앙'을
# 그리퍼를 수평으로 두고 손가락이 좌우(수평)로 닫히게 집는다. 봉투에도 수평 그대로 내려서 놓는다.
# ⚠️ 매대 촬영 자세(VIEW_POSES)는 카메라가 매대를 '정면에서 수평으로' 보도록 티칭해야 한다 (위에서 내려다보면 위에서 집음)
FRONT_GRASP = {"cola", "cider", "cup_ramen"}     # 캔 음료를 더 추가하면 여기에 상품 id 를 넣기
FRONT_RELEASE_CLEARANCE = 5.0      # 상품 바닥을 봉투 바닥(받침면) 위 이만큼에서 놓는다(mm)
# 봉투 입구에 걸리지 않게 — 수평 그리퍼가 입구 아래로 내려가려면 손가락 끝~플랜지~손목이 '전부' 입구 안에 들어가야 한다.
# 안 들어가면 그리퍼 바닥이 입구 테두리 위에 오는 높이에서 놓는다 (그만큼 떨어뜨림 — 로그에 표시)
GRIPPER_LENGTH = 200.0             # TCP(집는 점)에서 플랜지까지(mm) — 실제 RG2(+퀵체인저)를 재서 바꾸기
GRIPPER_HALF_WIDTH = 50.0          # 수평 그리퍼를 위에서 봤을 때 폭의 반(mm) (손가락 바깥쪽)
GRIPPER_BELOW = 45.0               # 수평일 때 그리퍼 축에서 몸통 아래쪽까지(mm)
WRIST_RADIUS = 60.0                # 플랜지 뒤에서 위로 올라가는 로봇 손목의 반지름(mm)
RIM_MARGIN = 10.0                  # 봉투 입구 테두리와 띄울 거리(mm)
IK_MIN_ELBOW = 20.0                # 관절 해를 고를 때 팔꿈치(J3)가 이보다 펴진(0°에 가까운) 해는 마지막에 (특이점)
IK_MIN_WRIST = 10.0                # 손목(J5)이 이보다 0°에 가까운 해도 마지막에
CARRY_CLEARANCE = 40.0             # 매대 → 봉투로 옮길 때 '봉투 입구·안 물건 + 쥔 물건' 위로 더 띄우는 높이(mm), 여기서 수직으로 내려감
# 선반에서 빼기 — 정면으로 쥔 뒤 살짝 들고(선반 위를 끌지 않게), 상품 뒷면이 선반 앞 끝에서 SHELF_CLEARANCE 떨어질 때까지 수평으로 뺀 뒤 봉투로
SHELF_CLEARANCE = 100.0            # 선반 앞 끝에서 띄울 거리(mm) — 다가갈 때도 여기서 시작
PICK_LIFT = 15.0                   # 쥔 뒤 빼기 전에 들어 올리는 높이(mm)
SHELF_EDGE_GAP = 30.0              # 선반 끝을 깊이로 못 잴 때 쓰는 '상품 앞면 ~ 선반 앞 끝' 거리(mm)
SHELF_EDGE_GAP_MAX = 150.0         # 잰 값이 이보다 크면 잘못 잰 것으로 보고 이 값까지만
# 옆 물건 치우기 (logic/neighbors.py) — 벌린 손가락이 옆 물건에 닿으면: 옆 물건을 선반 위 옆자리로 옮김 → 상품을 집어 봉투에 →
#   옮겼던 물건을 제자리로. 치울 물건은 좁게 벌려(그 폭 + 양쪽 NARROW_MARGIN) 집어서 그 물건의 손가락도 안 닿게 함
CLEAR_NEIGHBORS = True
UNCOVER_HIDDEN = True              # 주문 상품이 앞 물건 뒤에 가려져 있으면: 앞 물건을 치움 → 다시 찍어 집음 → 앞 물건 제자리로
GRIPPER_OPEN = 110.0               # 다 벌렸을 때 손가락 안쪽 사이 폭(mm) — RG2 최대
FINGER_PAD = 10.0                  # 손가락 두께의 반(mm)
TARGET_OPEN_MARGIN = 8.0           # 상품을 집을 때도 다 벌리지 않고 '폭 + 양쪽 이만큼'만 벌림 (콜라 66 → 82mm)
                                   #   다 벌리면(110mm) 손가락이 캔 옆으로 42mm 나와 3cm 간격 이웃에 닿음 — 위치 오차는 ±8mm 까지 견딤
NARROW_MARGIN = 7.0                # 치울 물건을 집을 때 물건 양쪽으로 남기는 여유(mm) — 10mm 면 펩시가 콜라와 34mm 틈이라 못 집음 (시뮬)
SHIFT_MAX = 250.0                  # 치울 물건을 옆으로 옮겨 볼 최대 거리(mm)
SHELF_PLACE_CLEARANCE = 3.0        # 선반에 다시 놓을 때 선반 위로 띄워 놓는 높이(mm)
NEIGHBOR_MARGIN = 20.0             # 잠깐 둘 자리에서 다른 물건과 띄울 거리(mm) — 사진으로 잰 위치 오차(시뮬 15mm) 대비
LAY_DOWN = {"cola", "cider", "cup_ramen"}   # 세운 채 수평 그리퍼가 입구에 못 들어가면 '눕혀서' 넣는 상품 (캔 음료, 컵라면)
BRIDGE_ON_LYING = True             # 눕힌 캔 위에는 안 쌓지만, 나란히 누운 두 개 '사이 홈'에 걸쳐서는 얹을 수 있음
                                   #   그리퍼를 아래로 돌리면 정중앙을 쥔 캔이 눕고, 수직 그리퍼는 입구에 들어가 바닥 위 5mm 에 놓을 수 있음
WRIST_REACH = (300.0, 750.0)       # 수평으로 놓을 때 플랜지(손목)가 로봇 받침 축에서 떨어질 거리 범위(mm)
                                   #   봉투가 로봇 가까이 있으면 그리퍼가 바깥을 향할 때 손목이 몸통에 붙어 못 감 (시뮬에서 확인)


# ==================================================================== 10. 봉투에서 꺼내기 (LLM 음성 "빼기")
# 꺼낸 상품은 탁자 위 '내려놓는 곳' 에 차례로 놓는다 (매대에 다시 진열하는 것은 직원 — 매대 빈자리 찾기는 아직 안 함)
# RETURN_SPOT: 첫째 자리 — 그리퍼를 아래로 향하게 하고 끝을 탁자 면에 댄 자세 [x, y, z, rx, ry, rz] (teach.py 내려놓는곳)
RETURN_SPOT = [300.0, 470.0, 0.0, 0.0, 180.0, 0.0]    # 예시값 — 봉투(빨간 네모) 오른쪽 앞 탁자 위
#   ⚠️ 쥔 채 옮기는 높이(손가락 끝 ~240mm)에서 팔이 다 펴지는 곳은 특이점이라 멈춤 (팔꿈치 J3 ≈ 0)
#      시뮬 확인: (380, 560)·(300, 600) 은 멈춤 / (300, 470)·(300, 340)·(300, 210)·(250, 600)·(-200, 550) 은 됨
RETURN_STEP = (0.0, -130.0)        # 다음 자리까지 (x, y) mm — 한 줄로 놓음 (눕힌 캔 122mm 보다 넓게)
RETURN_SLOTS = 3                   # 자리 수 (다 차면 더 꺼내지 않고 알려 줌)


# ==================================================================== 내부에서 쓰는 함수 (고칠 필요 없음)
def products_file():
    return LOCAL_PRODUCTS_FILE


def _load_taught_positions():
    """positions.json 이 있으면 예시 좌표를 덮어쓴다. 티칭된 이름 집합을 돌려준다."""
    global BASKET_VIEW_POSE, BASKET_CENTER, RETURN_SPOT
    taught = set()
    if not os.path.exists(POSITIONS_FILE):
        return taught
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for shelf, joints in data.get("view_poses", {}).items():
        if shelf in VIEW_POSES and joints:
            VIEW_POSES[shelf] = [float(v) for v in joints]
            taught.add(f"촬영 자세({shelf})")
    if data.get("basket_view"):
        BASKET_VIEW_POSE = [float(v) for v in data["basket_view"]]
        taught.add("봉투 촬영 자세")
    if data.get("basket_center"):
        BASKET_CENTER = [float(v) for v in data["basket_center"]]
        taught.add("봉투 바닥 중심")
    if data.get("return_spot"):
        RETURN_SPOT = [float(v) for v in data["return_spot"]]
        taught.add("내려놓는 곳")
    return taught


_TAUGHT = _load_taught_positions()
UNTAUGHT_POSITIONS = [n for n in [f"촬영 자세({s})" for s in VIEW_POSES] + ["봉투 촬영 자세", "봉투 바닥 중심", "내려놓는 곳"]
                      if n not in _TAUGHT]
