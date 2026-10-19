import asyncio
import logging
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from openai import APIError
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from .minimax_client import MiniMaxClient
from .auth import current_user, router as auth_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("请在 .env 中配置 DATABASE_URL")
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    async with AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "connect_timeout": 5,
            "options": "-c search_path=customer_chat",
        },
    ) as pool:
        await pool.wait(timeout=10)
        checkpointer = AsyncPostgresSaver(
            pool, serde=JsonPlusSerializer(allowed_msgpack_modules=None)
        )
        async with pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended('customer_chat:setup', 0))")
            try:
                await conn.execute("CREATE SCHEMA IF NOT EXISTS customer_chat")
                await checkpointer.setup()
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended('customer_chat:setup', 0))")
        app.state.db_pool = pool
        # 请求锁最多占四个连接，留一个给 Saver，避免耗尽连接池。
        app.state.chat_slots = asyncio.Semaphore(4)
        app.state.minimax = MiniMaxClient(checkpointer)
        yield


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(pattern=r"\S")


@app.post("/api/chat")
async def chat(request: ChatRequest, http_request: Request, user_id: str = Depends(current_user)) -> dict[str, str]:
    thread_id = f"chat:{user_id}"
    try:
        async with http_request.app.state.chat_slots:
            async with http_request.app.state.db_pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (thread_id,)
                    )
                    return {"reply": await http_request.app.state.minimax.chat(request.message, thread_id)}
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except APIError as exc:
        logger.warning("MiniMax 调用失败：%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="MiniMax 调用失败，请检查密钥、模型权限和服务状态") from exc
    except psycopg.Error as exc:
        logger.warning("会话存储失败：%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="会话存储暂时不可用，请稍后重试") from exc
