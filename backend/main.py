import asyncio
import logging
import os
import json
from contextlib import aclosing, asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.store.postgres.aio import AsyncPostgresStore
from openai import APIError
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from .utils.minimax_client import MiniMaxClient
from .common.auth import current_user, router as auth_router

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
    ) as pool, AsyncPostgresStore(pool) as store:
        await pool.wait(timeout=10)
        checkpointer = AsyncPostgresSaver(
            pool, serde=JsonPlusSerializer(allowed_msgpack_modules=None)
        )
        async with pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended('customer_chat:setup', 0))")
            try:
                await conn.execute("CREATE SCHEMA IF NOT EXISTS customer_chat")
                await checkpointer.setup()
                await store.setup()
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended('customer_chat:setup', 0))")
        app.state.db_pool = pool
        # 请求锁最多占四个连接，留一个给 Saver/Store，避免耗尽连接池。
        app.state.chat_slots = asyncio.Semaphore(4)
        app.state.minimax = MiniMaxClient(checkpointer, store)
        yield


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(pattern=r"\S")


@asynccontextmanager
async def conversation_lock(app: FastAPI, thread_id: str):
    async with app.state.chat_slots:
        async with app.state.db_pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (thread_id,)
                )
                yield


@app.post("/api/chat")
async def chat(request: ChatRequest, http_request: Request, user_id: str = Depends(current_user)) -> dict[str, str]:
    thread_id = f"chat:{user_id}"
    try:
        async with conversation_lock(http_request.app, thread_id):
            return {"reply": await http_request.app.state.minimax.chat(request.message, thread_id, user_id)}
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except APIError as exc:
        logger.warning("MiniMax 调用失败：%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="MiniMax 调用失败，请检查密钥、模型权限和服务状态") from exc
    except psycopg.Error as exc:
        logger.warning("会话存储失败：%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="会话存储暂时不可用，请稍后重试") from exc


@app.post("/api/chat/stream")
async def stream_chat(request: ChatRequest, http_request: Request, user_id: str = Depends(current_user)):
    def event(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():
        yield event("start", {})
        yield event("progress", {"step": "queue", "label": "等待处理（非节点）", "status": "running"})
        try:
            thread_id = f"chat:{user_id}"
            async with conversation_lock(http_request.app, thread_id):
                yield event("progress", {"step": "queue", "label": "等待处理（非节点）", "status": "done"})
                async with aclosing(http_request.app.state.minimax.stream(request.message, thread_id, user_id)) as events:
                    async for name, data in events:
                        yield event(name, data)
            yield event("done", {})
        except Exception as exc:
            logger.exception("流式对话失败：%s", type(exc).__name__)
            if isinstance(exc, APIError):
                detail = "MiniMax 调用失败，请检查密钥、模型权限和服务状态"
            elif isinstance(exc, psycopg.Error):
                detail = "会话存储暂时不可用，请稍后重试"
            elif isinstance(exc, ValueError):
                detail = "模型未返回有效结果，请重试"
            else:
                detail = "对话处理失败，请稍后重试"
            yield event("error", {"message": detail})

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
