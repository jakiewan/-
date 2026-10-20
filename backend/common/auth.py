import json
import os
import secrets
import time

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .constants import COOKIE_NAME, PROJECT_ROOT, SESSION_TTL

load_dotenv(PROJECT_ROOT / ".env")

router = APIRouter(prefix="/api/auth")
# shortcut: 会话保存在单进程内存中，多进程部署或需要重启后保留登录时改用共享存储。
sessions: dict[str, tuple[str, float]] = {}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def current_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME, "")
    session = sessions.get(token)
    if session is None or session[1] <= time.time():
        sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="请先登录")
    return session[0]


@router.post("/login")
def login(credentials: LoginRequest, request: Request, response: Response):
    try:
        users = json.loads(os.getenv("AUTH_USERS", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=503, detail="AUTH_USERS 配置格式错误") from None
    if not isinstance(users, dict) or not users or any(
        not isinstance(name, str) or not isinstance(password, str) or not password
        for name, password in users.items()
    ):
        raise HTTPException(status_code=503, detail="请在 .env 中配置 AUTH_USERS")

    username = credentials.username.strip()
    password = users.get(username, "")
    if not secrets.compare_digest(credentials.password.encode(), password.encode()):
        raise HTTPException(status_code=401, detail="账号或密码错误")

    now = time.time()
    for token in [token for token, session in sessions.items() if session[1] <= now]:
        sessions.pop(token, None)
    sessions.pop(request.cookies.get(COOKIE_NAME, ""), None)
    token = secrets.token_urlsafe(32)
    sessions[token] = (username, now + SESSION_TTL)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL,
        path="/api",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return {"username": username}


@router.get("/me")
def me(username: str = Depends(current_user)):
    return {"username": username}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    sessions.pop(request.cookies.get(COOKIE_NAME, ""), None)
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, samesite="strict")
