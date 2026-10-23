"""持久化受理、后台执行和用户/坐席的同一条会话。"""

import asyncio
import json
import logging
import os
import re
from contextlib import aclosing, suppress
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from .common.auth import current_user, is_support, support_user
from .memory.context import UserContext
from .tools.conversations import (add_message, conversation_view, emit, get_or_create,
                                  locked_conversation, message_view, request_handoff)

logger = logging.getLogger(__name__)


class MessageInput(BaseModel):
    message: str = Field(pattern=r"\S", max_length=10000)
    client_message_id: UUID | None = None
    conversation_id: UUID | None = None
    confirmation: dict | None = None


class VersionInput(BaseModel):
    version: int = Field(ge=0)


class EndInput(VersionInput):
    note: str = Field(min_length=1, max_length=1000)
    resume_ai: bool = False


async def access(conn, conversation_id, user_id):
    row = await locked_conversation(conn, conversation_id)
    if row["user_id"] != user_id and not is_support(user_id):
        raise HTTPException(403, "无权访问此会话")
    return row























async def accept_message(request, body, user_id):
    pool = request.app.state.db_pool
    cid = str(body.conversation_id) if body.conversation_id else await get_or_create(pool, request.app.state.minimax, user_id)
    async with pool.connection() as conn:
        async with conn.transaction():
            conversation = await access(conn, cid, user_id)
            if conversation["user_id"] != user_id:
                raise HTTPException(403, "客服回复请使用坐席入口")
            if body.client_message_id:
                old = await (await conn.execute("""SELECT * FROM customer_chat.conversation_messages
                    WHERE conversation_id = %s AND actor = %s AND client_message_id = %s""",
                    (cid, user_id, str(body.client_message_id)))).fetchone()
                if old:
                    if old["content"] != body.message or old["role"] != "user" or (body.confirmation is not None and old["metadata"].get("confirmation") != body.confirmation):
                        raise HTTPException(409, "同一消息标识不能用于不同内容")
                    return cid, old
            if conversation["mode"] == "closed":
                conversation = await (await conn.execute("""UPDATE customer_chat.conversations
                    SET mode = 'ai', version = version + 1 WHERE id = %s RETURNING *""", (cid,))).fetchone()
                await emit(conn, cid, "state", conversation_view(conversation))
            from .graph.after_sales import is_confirmation
            confirmation = None
            if body.confirmation is not None or is_confirmation(body.message):
                previous = await (await conn.execute("""SELECT metadata,version FROM customer_chat.conversation_messages
                    WHERE conversation_id=%s AND role='assistant' ORDER BY id DESC LIMIT 1""", (cid,))).fetchone()
                if previous and previous['version'] == conversation['version']:
                    confirmation = previous['metadata'].get('confirmation')
                if body.confirmation is not None and (not is_confirmation(body.message) or not confirmation or body.confirmation != confirmation):
                    raise HTTPException(409, '确认内容已失效，请刷新并重新核对')
            message, created = await add_message(conn, conversation, "user", user_id, body.message,
                metadata={'confirmation': confirmation},
                client_id=str(body.client_message_id or uuid4()), status="queued" if conversation["mode"] == "ai" else "received")
            if created and re.fullmatch(r"(?:请)?(?:帮我)?转人工(?:客服)?", body.message.strip().strip("，,。.!！?？~～")):
                conversation = await request_handoff(conn, conversation, "用户明确要求人工客服")
            return cid, message


def sse(kind, data, seq=None):
    prefix = f"id: {seq}\n" if seq is not None else ""
    return f"{prefix}event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"





async def reply_events(app, cid, message, request=None):
    yield "start", {"conversation_id": cid, "message_id": str(message["id"]), "client_message_id": message["client_message_id"]}
    cursor = 0
    while True:
        if request is not None:
            try:
                await current_user(request)
            except HTTPException:
                yield "error", {"message": "登录已失效，请重新登录"}
                return
        async with app.state.db_pool.connection() as conn:
            current = await (await conn.execute("SELECT status FROM customer_chat.conversation_messages WHERE id = %s", (message["id"],))).fetchone()
            rows = await (await conn.execute("""SELECT * FROM customer_chat.conversation_events
                WHERE conversation_id = %s AND request_id = %s AND seq > %s ORDER BY seq LIMIT 100""", (cid, message["id"], cursor))).fetchall()
        if current["status"] in ("received", "interrupted"):
            yield "manual", {"message": "消息已保存，人工接待状态请查看会话。"}
            yield "done", {}
            return
        for row in rows:
            cursor = row["seq"]
            if row["kind"] == "message":
                yield "final", row["data"]
            elif row["kind"] == "run_error":
                yield "error", row["data"]
            else:
                yield row["kind"], row["data"]
        if len(rows) == 100:
            continue
        if current["status"] not in ("queued", "running"):
            yield "done", {}
            return
        await asyncio.sleep(0.2)


async def run_message(app, conversation, message):
    pool, client = app.state.db_pool, app.state.minimax
    config = {"configurable": {"thread_id": conversation["graph_thread_id"]}}
    snapshot = await client.agent.aget_state(config)
    gid = f"chat-message-{message['id']}"
    existing = snapshot.values.get("messages", [])
    known_ids = {m.id for m in existing}
    last_user = next((m for m in reversed(existing) if m.type == "human"), None)
    same_run = last_user is not None and last_user.id == gid
    if same_run and not snapshot.next and existing[-1].type == "ai":
        payload = client.final_payload(snapshot.values)
    else:
        async with pool.connection() as conn:
            history = await (await conn.execute("""SELECT * FROM customer_chat.conversation_messages
                WHERE conversation_id = %s AND id <= %s AND role IN ('user','assistant','agent') ORDER BY id""",
                (conversation["id"], message["id"]))).fetchall()
        imported = []
        for row in history:
            mid = row["graph_message_id"] or f"chat-message-{row['id']}"
            if mid in known_ids:
                continue
            cls = HumanMessage if row["role"] == "user" else AIMessage
            imported.append(cls(content=row["content"], id=mid,
                                additional_kwargs={"sender_type": row["role"]}))
        inputs = {"messages": imported, "conversation_version": conversation["version"]}
        if snapshot.values.get("conversation_version", 0) != conversation["version"]:
            inputs["tasks"] = []  # 人工接待或新会话代次使旧售后确认失效。
        elif same_run and snapshot.next:
            inputs = None
        context = UserContext(conversation['user_id'], pool, conversation['id'], conversation['version'],
                              message_id=message['id'], received_at=message['created_at'],
                              confirmation=message['metadata'].get('confirmation'))
        payload = None
        async with aclosing(client.stream(message["content"], f"chat:{conversation['user_id']}", conversation["user_id"],
                            run={"inputs": inputs, "config": config}, context=context)) as stream:
            async for kind, data in stream:
                if kind == "final":
                    payload = data
                    continue
                async with pool.connection() as conn:
                    async with conn.transaction():
                        current = await locked_conversation(conn, conversation["id"])
                        if current["mode"] != "ai" or current["version"] != conversation["version"]:
                            return
                        await emit(conn, conversation["id"], kind, data, message["id"])
        if not payload or not payload["text"]:
            raise ValueError("未取得最终回复")
    async with pool.connection() as conn:
        async with conn.transaction():
            current = await locked_conversation(conn, conversation["id"])
            if current["mode"] != "ai" or current["version"] != conversation["version"]:
                return
            await add_message(conn, current, "assistant", "ai", payload["text"], reply_to=message["id"],
                              graph_id=payload.get("graph_message_id"),
                              metadata={k: v for k, v in payload.items() if k not in ("text", "graph_message_id")})
            await conn.execute("UPDATE customer_chat.conversation_messages SET status = 'succeeded' WHERE id = %s", (message["id"],))


async def process_queue(app):
    """每进程一个执行器；会话级数据库锁阻止其他进程同时推进同一检查点。"""
    pool = app.state.db_pool
    while True:
        try:
            async with pool.connection() as conn:
                candidates = await (await conn.execute("""SELECT DISTINCT c.id FROM customer_chat.conversations c
                    JOIN customer_chat.conversation_messages m ON m.conversation_id = c.id
                    WHERE c.mode = 'ai' AND m.status IN ('queued','running') ORDER BY c.id LIMIT 20""")).fetchall()
            for candidate in candidates:
                # 会话执行锁不锁住会话行；人工控制操作和业务复核使用独立的短事务。
                async with pool.connection() as lock_conn:
                    acquired = await (await lock_conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok",
                                                              (f"conversation:{candidate['id']}",))).fetchone()
                    if not acquired["ok"]:
                        continue
                    try:
                        async with lock_conn.transaction():
                            conversation = await locked_conversation(lock_conn, candidate["id"])
                            message = await (await lock_conn.execute("""SELECT * FROM customer_chat.conversation_messages
                                WHERE conversation_id = %s AND status IN ('queued','running') ORDER BY id LIMIT 1""", (candidate["id"],))).fetchone()
                            if message is None or conversation["mode"] != "ai":
                                continue
                            await lock_conn.execute("UPDATE customer_chat.conversation_messages SET status = 'running' WHERE id = %s", (message["id"],))
                        task = asyncio.create_task(run_message(app, conversation, message))
                        try:
                            async with asyncio.timeout(180):
                                while not task.done():
                                    await asyncio.wait({task}, timeout=0.3)
                                    current = await (await lock_conn.execute("SELECT mode,version FROM customer_chat.conversations WHERE id = %s", (conversation["id"],))).fetchone()
                                    if current["mode"] != "ai" or current["version"] != conversation["version"]:
                                        task.cancel()
                                        break
                                with suppress(asyncio.CancelledError):
                                    await task
                        finally:
                            task.cancel()
                            with suppress(asyncio.CancelledError):
                                await task
                    except Exception as exc:
                        logger.warning("会话执行失败 %s: %s", candidate["id"], type(exc).__name__)
                        async with lock_conn.transaction():
                            await locked_conversation(lock_conn, candidate["id"])
                            await lock_conn.execute("UPDATE customer_chat.conversation_messages SET status = 'failed' WHERE id = %s AND status = 'running'", (message["id"],))
                            await emit(lock_conn, candidate["id"], "run_error", {"message": "本次处理未完成，请重新咨询；已提交的业务请先查询结果。", "request_id": str(message["id"])}, message["id"])
                    finally:
                        await lock_conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (f"conversation:{candidate['id']}",))
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("会话队列暂不可用: %s", type(exc).__name__)
            await asyncio.sleep(2)
