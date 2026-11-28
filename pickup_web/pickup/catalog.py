"""상품 목록 (data/products.json)

재고 필드: stock = 진열 재고(손님 화면에 보이는 수량), warehouse = 창고 재고
robot: true = 실물(로봇이 매대에서 집음), false = 가상(직원이 준비)
"""
import json

from .config import PRODUCTS_FILE


def load_products(path=PRODUCTS_FILE):
    return json.loads(path.read_text(encoding="utf-8"))["products"]


PRODUCTS = load_products()
IS_ROBOT = {p["id"]: p.get("robot", False) for p in PRODUCTS}
NAMES = {p["id"]: p["name"] for p in PRODUCTS}


def split_items(items):
    """주문을 로봇이 담을 상품 / 직원이 준비할 상품으로 나누기"""
    robot_items = {k: v for k, v in items.items() if v > 0 and IS_ROBOT.get(k)}
    staff_items = {k: v for k, v in items.items() if v > 0 and not IS_ROBOT.get(k)}
    return robot_items, staff_items
