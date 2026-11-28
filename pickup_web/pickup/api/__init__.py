"""FastAPI 앱 조립. 화면별 라우터를 PickupService 하나로 묶음"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from ..config import STATIC
from . import analytics, auth, pages, pickup, recommend, shop, staff

NO_CACHE_EXT = (".html", ".css", ".js", ".json")


def _no_cache(path):
    """화면 파일: 서버를 고친 뒤 브라우저가 예전 css·js 를 계속 쓰지 않게 매번 새 버전인지 확인시킴 (안 바뀌었으면 304)"""
    return path in ("/", "/kiosk") or path.endswith(NO_CACHE_EXT)


def create_app(svc):
    app = FastAPI(title="편의점 픽업 주문")
    app.state.svc = svc   # api/auth.py 의 current_user 가 계정(svc.accounts)을 찾을 때 사용

    @app.middleware("http")
    async def cache_headers(request: Request, call_next):
        resp = await call_next(request)
        if _no_cache(request.url.path) and "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    for module in (auth, shop, recommend, staff, pickup, analytics, pages):
        app.include_router(module.build(svc))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    # 맨 마지막: /css, /js, /vendor, /manifest.json 처럼 html 이 상대 경로로 부르는 파일
    app.mount("/", StaticFiles(directory=STATIC), name="root")
    return app
