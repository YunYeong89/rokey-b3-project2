"""API 요청 본문 (pydantic)"""
from pydantic import BaseModel


class LoginReq(BaseModel):
    username: str = ""
    password: str = ""


class OrderReq(BaseModel):
    items: dict[str, int]                      # 고객은 로그인 세션으로 정함 (비회원이면 구매 이력 저장 안 함)
    rec_attrib: dict[str, dict] | None = None  # 추천을 눌러 담은 상품 {pid: {"slot","algo","rec_id","rank"}}


class ChoiceReq(BaseModel):
    choice: str
    substitute: str | None = None


class RecEventReq(BaseModel):
    event: str = "click"                    # click (노출은 서버가 추천을 줄 때, 구매는 주문 완료 때 기록)
    slot: str                               # home / cart
    product_id: str
    algo: str | None = None
    rec_id: str | None = None
    rank: int | None = None


class RestockReq(BaseModel):
    id: str
    qty: int
    # move: 창고 -> 진열로 옮기기 / receive: 창고에 입고 / adjust: 진열 재고를 직접 고치기(카메라값 반영)
    mode: str = "move"


class VerifyReq(BaseModel):
    token: str = ""


class ManualReq(BaseModel):
    pickup_code: str = ""
    check: str = ""
