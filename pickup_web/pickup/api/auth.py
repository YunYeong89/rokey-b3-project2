"""로그인 / 로그아웃 + 다른 라우터에서 쓰는 사용자 확인 (Depends)

브라우저는 로그인 응답의 token 을 "Authorization: Bearer <token>" 헤더로 보냄.
  current_user   로그인했으면 {"username", "role"}, 비회원이면 None (손님 화면은 둘 다 허용)
  require_admin  관리자만 (비로그인 401, 손님 403) -> 직원 화면 API
"""
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import FAIL_MAX, FAIL_WINDOW_SEC
from .schemas import LoginReq


def _token(request: Request):
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h[:7].lower() == "bearer " else ""


def current_user(request: Request):
    return request.app.state.svc.accounts.user(_token(request))


def require_admin(user=Depends(current_user)):
    if user is None:
        raise HTTPException(401, "로그인이 필요해요")
    if user["role"] != "admin":
        raise HTTPException(403, "관리자만 볼 수 있어요")
    return user


def build(svc):
    r = APIRouter()
    accounts = svc.accounts
    fails = defaultdict(deque)  # IP -> 로그인 실패 시각들 (비밀번호 무차별 대입 막기)

    @r.post("/api/login")
    def login(req: LoginReq, request: Request):
        ip = request.client.host if request.client else "?"
        now = time.time()
        q = fails[ip]
        while q and now - q[0] > FAIL_WINDOW_SEC:
            q.popleft()
        if len(q) >= FAIL_MAX:
            wait = int(FAIL_WINDOW_SEC - (now - q[0])) + 1
            return JSONResponse({"detail": f"로그인 실패가 너무 많아요. {wait}초 뒤에 다시 시도해 주세요"},
                                status_code=429)
        res = accounts.login(req.username.strip(), req.password)
        if res is None:
            q.append(now)
            raise HTTPException(401, "ID 또는 비밀번호가 맞지 않아요")
        token, user = res
        return {"token": token, **user}

    @r.post("/api/logout")
    def logout(request: Request):
        accounts.logout(_token(request))
        return {"ok": True}

    @r.get("/api/me")
    def me(user=Depends(current_user)):
        """지금 로그인한 사용자 (비회원이면 401)"""
        if user is None:
            raise HTTPException(401, "로그인이 필요해요")
        return user

    return r
