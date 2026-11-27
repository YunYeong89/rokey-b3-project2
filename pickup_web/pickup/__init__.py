"""로키마트 픽업 주문 (서버 패키지)

config      경로·설정값
catalog     상품 목록 (data/products.json)
inventory   재고·직원 알림 (Store)
orders      주문·픽업 기록 (Orders)
history     고객 구매 이력·추천 반응 (SQLite)
recommender 추천 (Switching Hybrid)
robot       웹 <-> 로봇 연결 (ROS2 / Mock)
service     위를 묶은 서버 상태 (PickupService)
api/        FastAPI 라우터 (shop / recommend / staff / pickup / pages)

robot_order_node.py 는 이 패키지를 import 하지 않음 (data/products.json 만 직접 읽음).
"""
