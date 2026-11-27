"""FastAPI 앱 조립. 화면별 라우터를 PickupService 하나로 묶음"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..config import STATIC
from . import pages, pickup, recommend, shop, staff


def create_app(svc):
    app = FastAPI(title="편의점 픽업 주문")
    for module in (shop, recommend, staff, pickup, pages):
        app.include_router(module.build(svc))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    # 맨 마지막: /css, /js, /vendor, /manifest.json 처럼 html 이 상대 경로로 부르는 파일
    app.mount("/", StaticFiles(directory=STATIC), name="root")
    return app
