"""화면(html)과 공개 데이터 파일"""
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from ..config import DATA, RUNTIME, STATIC

ASSET_RE = re.compile(r'(href|src)="((?:css|js|vendor)/[^"?#]+)"')


def page(name):
    """html 을 보내면서 css·js 주소에 파일 수정 시각을 붙임 (css/index.css?v=...).
    파일이 바뀌면 주소가 바뀌어서, 브라우저가 예전 css·js 를 캐시에서 꺼내 쓰지 못함.
    static/*.html 원본은 그대로라 서버 없이 파일로 열어도 동작"""
    def versioned(m):
        f = STATIC / m.group(2)
        return f'{m.group(1)}="{m.group(2)}?v={int(f.stat().st_mtime)}"' if f.is_file() else m.group(0)
    html = ASSET_RE.sub(versioned, (STATIC / name).read_text(encoding="utf-8"))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def build(svc):
    r = APIRouter()

    @r.get("/")
    def index():
        return page("index.html")

    @r.get("/kiosk")
    def kiosk():
        return page("kiosk.html")

    @r.get("/data/{name:path}")
    def data_file(name: str):
        """products.json, recommend/*.json (브라우저 데모용). runtime/ (주문 token, 구매 이력) 은 막음"""
        path = (DATA / name).resolve()
        public = path.is_relative_to(DATA) and not path.is_relative_to(RUNTIME)
        if not public or any(part.startswith(".") for part in path.relative_to(DATA).parts) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    return r
