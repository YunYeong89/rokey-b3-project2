"""관리자 Analytics Dashboard API (모두 관리자 로그인 필요, 직원 화면 API 와 같은 require_admin)

공통 쿼리 파라미터
  range    today / 7d / 30d / all / custom   (custom 이면 start, end = YYYY-MM-DD 한국 날짜)
  product  상품 id (쉼표로 여러 개)   category  카테고리 이름
  source   all / robot(실제 로봇) / mock(데모 로봇)
  gran     day / week / month (매출 추이 단위)
원본 주문·고객 기록은 내려보내지 않고 서버에서 집계한 결과만 JSON 으로 반환
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from ..analytics.common import Filters
from .auth import require_admin


def build(svc):
    r = APIRouter(prefix="/api/analytics", dependencies=[Depends(require_admin)])

    def filters(range: str = "30d", start: str = "", end: str = "", product: str = "", category: str = "",
                source: str = "all", gran: str = "day"):
        try:
            return Filters.parse(range, start, end, product, category, source, gran)
        except ValueError:
            raise HTTPException(400, "날짜는 YYYY-MM-DD 형식이에요")

    def an():
        if svc.analytics is None:
            raise HTTPException(503, "분석 DB 를 열지 못했어요 (서버 로그 확인)")
        return svc.analytics

    def live_inventory():
        """지금 재고 + 로봇이 처리 중인(아직 재고에서 빼지 않은) 주문 수량"""
        reserved = {}
        with svc.store.lock:
            rows = [dict(x) for x in svc.store.rows()]
            for o in svc.orders.orders.values():
                if o["status"] == "in_progress":
                    for pid, n in o["items"].items():
                        reserved[pid] = reserved.get(pid, 0) + n
        return rows, reserved

    def run(tab, fn):
        try:
            return fn()
        except HTTPException:
            raise
        except Exception as e:   # 한 탭 계산이 실패해도 서버와 다른 탭은 그대로
            print(f"[analytics] {tab} 계산 실패: {e!r}")
            raise HTTPException(500, f"{tab} 분석 중 오류가 났어요: {e}")

    @r.get("/version")
    def version():
        """대시보드 자동 갱신용: 값이 바뀌었을 때만 화면이 다시 불러옴 (가벼운 호출)"""
        return {"version": an().version()}

    @r.get("/overview")
    def overview(f: Filters = Depends(filters)):
        rows, reserved = live_inventory()
        return run("overview", lambda: an().overview(f, rows, reserved))

    @r.get("/sales")
    def sales(f: Filters = Depends(filters)):
        return run("sales", lambda: an().sales(f))

    @r.get("/products")
    def products(f: Filters = Depends(filters)):
        return run("products", lambda: an().products(f))

    @r.get("/customers")
    def customers(f: Filters = Depends(filters)):
        return run("customers", lambda: an().customers(f))

    @r.get("/inventory")
    def inventory(f: Filters = Depends(filters), item: str = Query("", description="추이 그래프를 볼 상품 id")):
        rows, reserved = live_inventory()
        return run("inventory", lambda: an().inventory(f, rows, reserved, item or None))

    @r.get("/recommendations")
    def recommendations(f: Filters = Depends(filters)):
        return run("recommendations", lambda: an().recommendations(f))

    @r.get("/packing")
    def packing(f: Filters = Depends(filters)):
        return run("packing", lambda: an().packing(f))

    @r.get("/robot")
    def robot(f: Filters = Depends(filters)):
        return run("robot", lambda: an().robot(f))

    return r
