"""登录、当前用户和退出登录接口。"""

import json
import os
import secrets
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..common.auth import current_user, is_support
from ..common.constants import COOKIE_NAME, SESSION_TTL

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(credentials: LoginRequest, request: Request, response: Response):
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

    token = secrets.token_urlsafe(32)
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM customer_chat.login_sessions WHERE expires_at <= now() OR token_hash = %s",
                               (sha256(request.cookies.get(COOKIE_NAME, "").encode()).hexdigest(),))
            await conn.execute("INSERT INTO customer_chat.login_sessions VALUES (%s,%s,now() + %s * interval '1 second')",
                               (sha256(token.encode()).hexdigest(), username, SESSION_TTL))
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL,
        path="/api",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return {"username": username, "is_support": is_support(username)}


@router.get("/me")
def me(username: str = Depends(current_user)):
    return {"username": username, "is_support": is_support(username)}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    async with request.app.state.db_pool.connection() as conn:
        await conn.execute("DELETE FROM customer_chat.login_sessions WHERE token_hash = %s",
                           (sha256(request.cookies.get(COOKIE_NAME, "").encode()).hexdigest(),))
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, samesite="strict")
