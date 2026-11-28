"""관리자 Analytics Dashboard (서버 쪽)

db               분석용 테이블 + 기록 함수 (AnalyticsDB). customer_history.db 에 테이블만 추가
common           한국 시간, 기간·상품 필터 (Filters), '데이터 수집 중' 표시
sales            매출·판매 (Overview, Sales 탭)
customers        고객 구매 행동, R / S / C, support · confidence · lift
inventory        재고 최적화 (목표 재고, 보충 필요 수량, 예측 모델 연동)
recommendations  추천 성과 (rec_events)
packing          포장 계획 (packing_logs + packing_planning 오프라인 실험 결과)
robot            로봇 작업 (RobotTracker 가 /order_status 로 robot_logs 기록)
service          기록 + 탭별 조회·캐시 (Analytics). PickupService.analytics

자세한 내용은 README.md
"""
from .db import AnalyticsDB
from .service import Analytics

__all__ = ["Analytics", "AnalyticsDB"]
