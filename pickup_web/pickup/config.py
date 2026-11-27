"""경로와 설정값 (한곳에서 관리)"""
from pathlib import Path

# ---- 경로
BASE = Path(__file__).resolve().parent.parent   # pickup_web/
STATIC = BASE / "static"
DATA = BASE / "data"

PRODUCTS_FILE = DATA / "products.json"         # 상품 목록 (서버, 로봇 노드, 브라우저 데모가 함께 읽음)

REC_DATA = DATA / "recommend"                  # 추천 입력 (Colab 분석 결과)
RECS_FILE = REC_DATA / "our_recs.json"         # 대체 상품, 예비 인기도
HYBRID_FILE = REC_DATA / "hybrid_scores_optimized.json"
SAME_FILE = REC_DATA / "same_aisle_scores.json"
CROSS_FILE = REC_DATA / "cross_aisle_scores.json"

RUNTIME = DATA / "runtime"                     # 서버가 만드는 파일 (git 제외, /data 로 공개하지 않음)
INVENTORY_FILE = RUNTIME / "inventory.json"    # 재고 (진열·창고)
ORDERS_FILE = RUNTIME / "orders.json"          # 주문·픽업 기록 (token 포함)
HISTORY_FILE = RUNTIME / "customer_history.db" # 고객 구매 이력·추천 반응

# ---- 재고
LOW_STOCK = 1                   # 진열 재고가 이 개수 이하이면 보충 필요

# ---- 픽업
PICKUP_EXPIRE_MIN = 30          # 준비 완료 후 이 시간(분)이 지나면 만료 (--pickup-expire-min 으로 바꿀 수 있음)
PICKUP_LETTER = "A"             # 픽업 번호 앞 글자 (A-001 ~ A-999 를 돌아가며 사용)
QR_PREFIX = "ROKEY-PICKUP:"     # QR 내용 = QR_PREFIX + pickup_token
EXPIRED_SHOW_MIN = 180          # 직원 화면 "픽업 대기"에 만료된 주문을 보여 주는 시간(분)
FAIL_MAX = 10                   # verify / manual: FAIL_WINDOW_SEC 초 안에 이만큼 틀리면
FAIL_WINDOW_SEC = 60            #   그 IP 는 잠시 막음 (429)
