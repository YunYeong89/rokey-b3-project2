"""화면(html)과 공개 데이터 파일"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config import DATA, RUNTIME, STATIC


def build(svc):
    r = APIRouter()

    @r.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @r.get("/kiosk")
    def kiosk():
        return FileResponse(STATIC / "kiosk.html")

    @r.get("/data/{name:path}")
    def data_file(name: str):
        """products.json, recommend/*.json (브라우저 데모용). runtime/ (주문 token, 구매 이력) 은 막음"""
        path = (DATA / name).resolve()
        public = path.is_relative_to(DATA) and not path.is_relative_to(RUNTIME)
        if not public or any(part.startswith(".") for part in path.relative_to(DATA).parts) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    return r
