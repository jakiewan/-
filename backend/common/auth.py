import os
from hashlib import sha256

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request

from .constants import COOKIE_NAME, PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


async def current_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    async with request.app.state.db_pool.connection() as conn:
        session = await (await conn.execute("""SELECT user_id FROM customer_chat.login_sessions
            WHERE token_hash = %s AND expires_at > now()""", (sha256(token.encode()).hexdigest(),))).fetchone()
    if session is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return session["user_id"]


def pilot_enabled(username):
    allowed = os.getenv('FEATURE_USER_ALLOWLIST', '*')
    return allowed.strip() == '*' or username in {name.strip() for name in allowed.split(',')}


def is_support(username):
    return bool(os.getenv("SUPPORT_USER")) and username == os.getenv("SUPPORT_USER")


def support_user(username: str = Depends(current_user)):
    if not is_support(username):
        raise HTTPException(403, "此操作需要客服权限")
    return username
