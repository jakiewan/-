import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from urllib.parse import urlsplit
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .utils.minimax_client import MiniMaxClient
from .utils.deepseek_client import DeepSeekClient
from .rag import FaqRetriever
from .common.auth import current_user, router as auth_router
from .tools.orders import setup_orders
from .tools.after_sales import setup_after_sales
from .tools.eligibility import setup_eligibility
from .api.business import router as business_router
from .tools.conversations import setup_conversations
from .conversation_service import MessageInput, accept_message, reply_events, process_queue, sse, router as conversation_router
from .utils.feishu_client import notification_worker

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
        check=AsyncConnectionPool.check_connection,
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
                await setup_orders(conn)
                await setup_after_sales(conn)
                await setup_eligibility(conn)
                await setup_conversations(conn)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended('customer_chat:setup', 0))")
        app.state.db_pool = pool
        retriever = None
        try:
            try:
                retriever = await asyncio.to_thread(FaqRetriever)
            except Exception as exc:
                logger.warning("知识检索初始化失败：%s", type(exc).__name__)
            mode = os.getenv("LLM_MODE", "minimax")
            client_class = {"minimax": MiniMaxClient, "deepseek": DeepSeekClient}.get(mode)
            if client_class is None:
                raise ValueError("LLM_MODE 仅支持 minimax 或 deepseek")
            app.state.minimax = client_class(checkpointer, store, retriever=retriever, db_pool=pool)
            workers = [asyncio.create_task(process_queue(app)), asyncio.create_task(notification_worker(pool))]
            try:
                yield
            finally:
                for worker in workers:
                    worker.cancel()
                for worker in workers:
                    with suppress(asyncio.CancelledError):
                        await worker
        finally:
            if retriever is not None:
                await asyncio.to_thread(retriever.close)


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
logger = logging.getLogger(__name__)

app.include_router(conversation_router)
app.include_router(business_router)


@app.middleware("http")
async def verify_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and origin:
        public = urlsplit(os.getenv("SUPPORT_PUBLIC_BASE_URL", ""))
        allowed = f"{public.scheme}://{public.netloc}" if public.netloc else None
        if origin != allowed and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "请求来源不允许"}, status_code=403)
    return await call_next(request)


@app.post("/api/chat")
async def chat(body: MessageInput, request: Request, user_id: str = Depends(current_user)):
    cid, message = await accept_message(request, body, user_id)
    reply = "消息已保存，正在等待人工处理。"
    async for kind, data in reply_events(request.app, cid, message, request):
        if kind == "final":
            reply = data["text"]
        elif kind == "error":
            raise HTTPException(503, data["message"])
    return {"reply": reply}


@app.post("/api/chat/stream")
async def stream_chat(body: MessageInput, request: Request, user_id: str = Depends(current_user)):
    cid, message = await accept_message(request, body, user_id)

    async def generate():
        async for kind, data in reply_events(request.app, cid, message, request):
            yield sse(kind, data)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
