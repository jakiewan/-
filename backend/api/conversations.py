"""用户和坐席的会话管理与事件流接口。"""

import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..common.auth import current_user, is_support, support_user
from ..conversation_service import EndInput, MessageInput, VersionInput, access, sse
from ..tools.conversations import (add_message, conversation_view, emit, get_or_create,
                                   locked_conversation, message_view, request_handoff)

router = APIRouter(prefix="/api")


@router.get("/conversation")
async def mine(request: Request, user_id: str = Depends(current_user)):
    cid = await get_or_create(request.app.state.db_pool, request.app.state.minimax, user_id)
    return await snapshot(cid, request, user_id)


@router.get("/conversations/{conversation_id}")
async def snapshot(conversation_id: str, request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            conversation = await access(conn, conversation_id, user_id)
            rows = await (await conn.execute("""SELECT * FROM customer_chat.conversation_messages
                WHERE conversation_id = %s ORDER BY id""", (conversation_id,))).fetchall()
            intents = await (await conn.execute("""SELECT DISTINCT ON (request_id) request_id,data
                FROM customer_chat.conversation_events WHERE conversation_id=%s AND kind='intent' AND request_id IS NOT NULL
                ORDER BY request_id,seq DESC""", (conversation_id,))).fetchall()
            by_request = {row['request_id']: row['data'] for row in intents}
            for row in rows:
                if row['role'] == 'assistant' and not row['metadata'].get('intent_details') and row['reply_to'] in by_request:
                    row['metadata'] = {**row['metadata'], 'intent_details': by_request[row['reply_to']]}
            support = await (await conn.execute("SELECT notification_status, resolution FROM customer_chat.support_requests WHERE id = %s",
                                               (conversation["handoff_id"],))).fetchone()
            return {"conversation": conversation_view(conversation), "messages": [{**message_view(row), **({"confirmationRequired": False, "confirmation": None} if row["version"] != conversation["version"] or conversation["mode"] != "ai" else {})} for row in rows],
                    "notification_status": support["notification_status"] if support else None,
                    "support_note": support["resolution"] if support and is_support(user_id) else None}


@router.get("/support/conversations")
async def support_list(request: Request, user_id: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        rows = await (await conn.execute("""SELECT c.*, r.reason, r.notification_status, r.resolution
            FROM customer_chat.conversations c JOIN customer_chat.support_requests r ON r.id = c.handoff_id
            ORDER BY CASE c.mode WHEN 'waiting_human' THEN 0 WHEN 'human' THEN 1 ELSE 2 END, c.updated_at DESC
            LIMIT 100""")).fetchall()
    return {"conversations": [{**conversation_view(row), "reason": row["reason"],
                               "notification_status": row["notification_status"]} for row in rows]}


@router.post("/conversations/{conversation_id}/handoff")
async def handoff(conversation_id: str, body: VersionInput, request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            conversation = await access(conn, conversation_id, user_id)
            if conversation["user_id"] != user_id:
                raise HTTPException(403, "请使用坐席接管入口")
            if conversation["mode"] not in ("human", "waiting_human"):
                if conversation["version"] != body.version:
                    raise HTTPException(409, "会话已变化，请刷新后重试")
                await add_message(conn, conversation, "user", user_id, "请帮我转人工客服")
            conversation = await request_handoff(conn, conversation, "用户点击转人工")
    return conversation_view(conversation)


@router.post("/support/conversations/{conversation_id}/claim")
async def claim(conversation_id: str, body: VersionInput, request: Request, user_id: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            conversation = await locked_conversation(conn, conversation_id)
            if conversation["mode"] == "human" and conversation["agent_user"] == user_id:
                return conversation_view(conversation)
            if conversation["version"] != body.version or conversation["mode"] != "waiting_human":
                raise HTTPException(409, "会话状态已变化，请刷新后接管")
            conversation = await (await conn.execute("""UPDATE customer_chat.conversations
                SET mode = 'human', agent_user = %s, version = version + 1 WHERE id = %s RETURNING *""",
                (user_id, conversation_id))).fetchone()
            await add_message(conn, conversation, "system", "system", "人工客服已接管，将在此会话中为您服务。")
            await emit(conn, conversation_id, "state", conversation_view(conversation))
    return conversation_view(conversation)


@router.post("/support/conversations/{conversation_id}/messages")
async def agent_message(conversation_id: str, body: MessageInput, request: Request, user_id: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            conversation = await locked_conversation(conn, conversation_id)
            if conversation["mode"] != "human" or conversation["agent_user"] != user_id:
                raise HTTPException(409, "请先接管会话，结束接待后不能继续发送")
            message, _ = await add_message(conn, conversation, "agent", user_id, body.message,
                                          client_id=str(body.client_message_id or uuid4()))
    return message_view(message)


@router.post("/support/conversations/{conversation_id}/end")
async def end(conversation_id: str, body: EndInput, request: Request, user_id: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            conversation = await locked_conversation(conn, conversation_id)
            if conversation["mode"] != "human" or conversation["agent_user"] != user_id or conversation["version"] != body.version:
                raise HTTPException(409, "接待状态已变化，请刷新后重试")
            await conn.execute("UPDATE customer_chat.support_requests SET status = 'closed', resolution = %s WHERE id = %s",
                               (body.note, conversation["handoff_id"]))
            conversation = await (await conn.execute("""UPDATE customer_chat.conversations
                SET mode = %s, agent_user = NULL, version = version + 1 WHERE id = %s RETURNING *""",
                ("ai" if body.resume_ai else "closed", conversation_id))).fetchone()
            text = "人工接待已结束。" + ("已恢复 AI 服务，待提交申请需要重新确认。" if body.resume_ai else "如需继续咨询，可以发送新消息。")
            await add_message(conn, conversation, "system", user_id, text)
            await emit(conn, conversation_id, "state", conversation_view(conversation))
    return conversation_view(conversation)


@router.get("/conversations/{conversation_id}/events")
async def events(conversation_id: str, request: Request, after: int = Query(default=0, ge=0), user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            await access(conn, conversation_id, user_id)
    try:
        cursor = int(request.headers.get("last-event-id", after))
        if cursor < 0:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "事件游标无效") from None

    async def generate():
        nonlocal cursor
        ticks = 0
        while not await request.is_disconnected():
            try:
                if await current_user(request) != user_id:
                    break
                async with request.app.state.db_pool.connection() as conn:
                    async with conn.transaction():
                        conversation = await access(conn, conversation_id, user_id)
                        oldest = await (await conn.execute("SELECT min(seq) AS seq FROM customer_chat.conversation_events WHERE conversation_id=%s", (conversation_id,))).fetchone()
                        if cursor > conversation["event_seq"] or (cursor < conversation["event_seq"] and (oldest["seq"] is None or cursor + 1 < oldest["seq"])):
                            yield sse("reset", {})
                            return
                        rows = await (await conn.execute("""SELECT * FROM customer_chat.conversation_events
                            WHERE conversation_id = %s AND seq > %s ORDER BY seq LIMIT 100""", (conversation_id, cursor))).fetchall()
                for row in rows:
                    cursor = row["seq"]
                    yield sse(row["kind"], row["data"], cursor)
                if len(rows) == 100:
                    continue
                ticks += 1
                if ticks % 15 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)
            except HTTPException:
                yield sse("auth_expired", {})
                return
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
