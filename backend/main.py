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
from .common.auth import current_user, support_user
from .api.auth import router as auth_router
from .tools.orders import setup_orders
from .tools.after_sales import setup_after_sales
from .tools.eligibility import setup_eligibility
from .api.business import router as business_router
from .tools.conversations import setup_conversations
from .conversation_service import MessageInput, accept_message, reply_events, process_queue, sse
from .api.conversations import router as conversation_router
from .utils.feishu_client import notification_worker
from .memory.service import setup_memory
from .api.memory import router as memory_router
from .memory.worker import memory_worker
from .knowledge_service import setup_knowledge
from .api.knowledge import router as knowledge_router

async def retain_events(pool):
    while True:
        try:
            days = max(1, min(90, int(os.getenv('EVENT_RETENTION_DAYS', '7'))))
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM customer_chat.conversation_events WHERE created_at<now()-(%s*interval '1 day')", (days,))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('事件保留清理失败: %s', type(exc).__name__)
        await asyncio.sleep(60)


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
                await setup_memory(conn)
                await setup_knowledge(conn)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended('customer_chat:setup', 0))")
        app.state.db_pool = pool
        app.state.database_url = database_url
        retriever = None
        try:
            try:
                retriever = await asyncio.to_thread(FaqRetriever)
            except Exception as exc:
                logger.warning("知识检索初始化失败：%s", type(exc).__name__)
            app.state.retriever = retriever
            mode = os.getenv("LLM_MODE", "minimax")
            client_class = {"minimax": MiniMaxClient, "deepseek": DeepSeekClient}.get(mode)
            if client_class is None:
                raise ValueError("LLM_MODE 仅支持 minimax 或 deepseek")
            app.state.minimax = client_class(checkpointer, store, retriever=retriever, db_pool=pool)
            workers = [*[asyncio.create_task(process_queue(app)) for _ in range(max(1,min(4,int(os.getenv('CHAT_WORKERS','2')))))], asyncio.create_task(notification_worker(pool)),
                       asyncio.create_task(memory_worker(pool, app.state.minimax.model)), asyncio.create_task(retain_events(pool))]
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
app.include_router(memory_router)
app.include_router(knowledge_router)


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


@app.get('/api/health')
async def health(request: Request):
    try:
        async with request.app.state.db_pool.connection() as conn:
            await conn.execute('SELECT 1')
        return {'status': 'ok'}
    except Exception:
        return JSONResponse({'status': 'unavailable'},status_code=503)


@app.get('/api/support/metrics')
async def metrics(request: Request, user_id: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        messages = await (await conn.execute("""SELECT status,count(*) AS count FROM customer_chat.conversation_messages
            WHERE role='user' AND created_at>now()-interval '24 hours' GROUP BY status""")).fetchall()
        waiting = await (await conn.execute("""SELECT count(*) AS count,coalesce(max(extract(epoch FROM(now()-created_at))),0) AS longest_wait_seconds
            FROM customer_chat.support_requests WHERE status='open'""")).fetchone()
        notifications = await (await conn.execute('SELECT notification_status,count(*) AS count FROM customer_chat.support_requests GROUP BY notification_status')).fetchall()
        memory = await (await conn.execute('SELECT status,count(*) AS count FROM customer_chat.memory_jobs GROUP BY status')).fetchall()
        latency = await (await conn.execute("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY (metadata->>'elapsed_ms')::float) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY (metadata->>'elapsed_ms')::float) AS p95_ms,
            sum((metadata->'usage'->>'model_calls')::int) AS model_calls,
            sum((metadata->'usage'->>'tool_calls')::int) AS tool_calls
            FROM customer_chat.conversation_messages WHERE role='assistant' AND created_at>now()-interval '24 hours'""")).fetchone()
    return {'messages_24h':messages,'human_waiting':waiting,'notifications':notifications,'memory_jobs':memory,'latency_usage_24h':latency}
