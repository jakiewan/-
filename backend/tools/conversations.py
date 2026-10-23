"""会话、消息和可回放事件；所有可见状态先落库再推送。"""

from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb


async def setup_conversations(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_chat.login_sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customer_chat.conversations (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE, graph_thread_id TEXT NOT NULL UNIQUE,
            mode TEXT NOT NULL DEFAULT 'ai' CHECK (mode IN ('ai','waiting_human','human','closed')),
            version BIGINT NOT NULL DEFAULT 0, event_seq BIGINT NOT NULL DEFAULT 0,
            agent_user TEXT, handoff_id TEXT, history_imported BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS customer_chat.conversation_messages (
            id BIGSERIAL PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES customer_chat.conversations(id),
            role TEXT NOT NULL CHECK (role IN ('user','assistant','agent','system')),
            actor TEXT NOT NULL, content TEXT NOT NULL, metadata JSONB NOT NULL DEFAULT '{}',
            client_message_id TEXT, graph_message_id TEXT, reply_to BIGINT UNIQUE,
            status TEXT NOT NULL DEFAULT 'received', version BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (conversation_id, actor, client_message_id),
            UNIQUE (conversation_id, graph_message_id)
        );
        CREATE TABLE IF NOT EXISTS customer_chat.conversation_events (
            conversation_id TEXT NOT NULL REFERENCES customer_chat.conversations(id),
            seq BIGINT NOT NULL, kind TEXT NOT NULL, data JSONB NOT NULL,
            request_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (conversation_id, seq)
        );
        CREATE TABLE IF NOT EXISTS customer_chat.support_requests (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES customer_chat.conversations(id),
            reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            notification_status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(), external_message_id TEXT,
            last_error TEXT, resolution TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS conversation_message_history_idx
            ON customer_chat.conversation_messages(conversation_id, id);
        CREATE INDEX IF NOT EXISTS conversation_message_pending_idx
            ON customer_chat.conversation_messages(id) WHERE status IN ('queued','running');
    """)


def message_view(row):
    return {"id": str(row["id"]), "role": row["role"], "text": row["content"],
            "actor": row["actor"], "client_message_id": row["client_message_id"],
            "reply_to": str(row["reply_to"]) if row["reply_to"] else None,
            "status": row["status"], "version": row["version"], "created_at": row["created_at"].isoformat(), **row["metadata"]}


def conversation_view(row):
    return {key: row[key] for key in ("id", "user_id", "mode", "version", "event_seq", "agent_user", "handoff_id")}


async def locked_conversation(conn, conversation_id):
    row = await (await conn.execute(
        "SELECT * FROM customer_chat.conversations WHERE id = %s FOR UPDATE", (conversation_id,)
    )).fetchone()
    if row is None:
        raise HTTPException(404, "会话不存在")
    return row


async def emit(conn, conversation_id, kind, data, request_id=None):
    row = await (await conn.execute("""
        UPDATE customer_chat.conversations SET event_seq = event_seq + 1, updated_at = now()
        WHERE id = %s RETURNING event_seq
    """, (conversation_id,))).fetchone()
    await conn.execute("""
        INSERT INTO customer_chat.conversation_events(conversation_id, seq, kind, data, request_id)
        VALUES (%s,%s,%s,%s,%s)
    """, (conversation_id, row["event_seq"], kind, Jsonb(data), request_id))


async def add_message(conn, conversation, role, actor, content, *, client_id=None,
                      reply_to=None, metadata=None, status="received", graph_id=None):
    if client_id:
        old = await (await conn.execute("""
            SELECT * FROM customer_chat.conversation_messages
            WHERE conversation_id = %s AND actor = %s AND client_message_id = %s
        """, (conversation["id"], actor, client_id))).fetchone()
        if old:
            if old["content"] != content or old["role"] != role:
                raise HTTPException(409, "同一消息标识不能用于不同内容")
            return old, False
    row = await (await conn.execute("""
        INSERT INTO customer_chat.conversation_messages
            (conversation_id,role,actor,content,metadata,client_message_id,reply_to,status,version,graph_message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (conversation["id"], role, actor, content, Jsonb(metadata or {}), client_id,
          reply_to, status, conversation["version"], graph_id))).fetchone()
    await emit(conn, conversation["id"], "message", message_view(row), reply_to)
    return row, True


async def request_handoff(conn, conversation, reason):
    if conversation["mode"] in ("waiting_human", "human"):
        return conversation
    handoff_id = str(uuid4())
    conversation = await (await conn.execute("""
        UPDATE customer_chat.conversations SET mode = 'waiting_human', version = version + 1,
            handoff_id = %s, agent_user = NULL WHERE id = %s RETURNING *
    """, (handoff_id, conversation["id"]))).fetchone()
    await conn.execute("""INSERT INTO customer_chat.support_requests(id,conversation_id,reason)
                          VALUES (%s,%s,%s)""", (handoff_id, conversation["id"], reason[:300]))
    await conn.execute("""UPDATE customer_chat.conversation_messages SET status = 'interrupted'
        WHERE conversation_id = %s AND status IN ('queued','running')""", (conversation["id"],))
    await add_message(conn, conversation, "system", "system", "已登记转人工请求，正在等待客服接管。等待期间您可以继续留言。")
    await emit(conn, conversation["id"], "state", conversation_view(conversation))
    return conversation


async def check_execution(conn, context):
    """与人工切换共用短行锁，使本地业务提交和执行权变更互斥。"""
    if context is None or context.conversation_id is None:
        return
    conversation = await locked_conversation(conn, context.conversation_id)
    if conversation["mode"] != "ai" or conversation["version"] != context.conversation_version:
        raise ValueError("会话已转人工，本次自动业务已停止")


async def get_or_create(pool, client, user_id):
    async with pool.connection() as conn:
        await conn.execute("""INSERT INTO customer_chat.conversations(id,user_id,graph_thread_id)
            VALUES (%s,%s,%s) ON CONFLICT(user_id) DO NOTHING""",
            (str(uuid4()), user_id, f"manual:chat:{user_id}"))
        conversation = await (await conn.execute(
            "SELECT * FROM customer_chat.conversations WHERE user_id = %s", (user_id,)
        )).fetchone()
    if conversation["history_imported"]:
        return conversation["id"]
    snapshot = await client.agent.aget_state({"configurable": {"thread_id": conversation["graph_thread_id"]}})
    messages = snapshot.values.get("messages", [])
    if not messages:
        old = await client.checkpointer.aget_tuple({"configurable": {"thread_id": f"chat:{user_id}"}})
        messages = old.checkpoint["channel_values"].get("messages", []) if old else []
    completed = [i for i, m in enumerate(messages) if m.type == "ai" and m.text and not m.tool_calls]
    async with pool.connection() as conn:
        async with conn.transaction():
            conversation = await locked_conversation(conn, conversation["id"])
            if not conversation["history_imported"]:
                for i, m in enumerate(messages[:completed[-1] + 1] if completed else []):
                    if m.type not in ("human", "ai") or not m.text or getattr(m, "tool_calls", None):
                        continue
                    await add_message(conn, conversation, "user" if m.type == "human" else "assistant",
                                      user_id if m.type == "human" else "ai", m.text,
                                      graph_id=m.id or f"legacy-{i}")
                await conn.execute("UPDATE customer_chat.conversations SET history_imported = TRUE WHERE id = %s", (conversation["id"],))
    return conversation["id"]
