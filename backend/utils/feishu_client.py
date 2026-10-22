"""飞书只负责通知；投递结果独立于坐席接管状态。"""

import asyncio
import json
import logging
import os
from urllib.parse import urlsplit

import httpx

from ..tools.conversations import emit, locked_conversation

logger = logging.getLogger(__name__)


def notification_card(request_id, conversation_id):
    base = os.getenv("SUPPORT_PUBLIC_BASE_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("SUPPORT_PUBLIC_BASE_URL 未配置为有效页面地址")
    return {
        "header": {"title": {"tag": "plain_text", "content": "客服接待请求"}, "template": "blue"},
        "elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": f"有客户正在等待人工接待。\n请求编号：{request_id[:8]}\n请在坐席页面查看原会话并主动接管。"}},
            {"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "查看会话"},
                                             "type": "primary", "url": f"{base}/?support={conversation_id}"}]},
        ],
    }


async def send_notification(row):
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", json={
            "app_id": os.environ["FEISHU_APP_ID"], "app_secret": os.environ["FEISHU_APP_SECRET"]})
        response.raise_for_status()
        token = response.json()
        if token.get("code") != 0:
            raise ValueError(f"飞书认证失败，错误码 {token.get('code')}")
        response = await client.post("https://open.feishu.cn/open-apis/im/v1/messages", params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token['tenant_access_token']}"}, json={
                "receive_id": os.environ["FEISHU_AGENT_OPEN_ID"], "msg_type": "interactive", "uuid": row["id"],
                "content": json.dumps(notification_card(row["id"], row["conversation_id"]), ensure_ascii=False)})
        response.raise_for_status()
        result = response.json()
        if result.get("code") != 0:
            raise ValueError(f"飞书发送失败，错误码 {result.get('code')}")
        return result["data"]["message_id"]


async def notification_worker(pool):
    while True:
        try:
            async with pool.connection() as conn:
                async with conn.transaction():
                    row = await (await conn.execute("""SELECT r.* FROM customer_chat.support_requests r
                        JOIN customer_chat.conversations c ON c.handoff_id = r.id
                        WHERE r.status = 'open' AND c.mode = 'waiting_human' AND r.attempts < 3
                        AND r.notification_status IN ('pending','retry','sending','not_configured') AND r.next_attempt_at <= now()
                        ORDER BY r.created_at FOR UPDATE OF r SKIP LOCKED LIMIT 1""")).fetchone()
                    if row:
                        configured = all(os.getenv(k) for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_AGENT_OPEN_ID", "SUPPORT_PUBLIC_BASE_URL"))
                        await conn.execute("""UPDATE customer_chat.support_requests SET notification_status = %s,
                            attempts = attempts + %s, next_attempt_at = now() + interval '60 seconds' WHERE id = %s""",
                            ("sending" if configured else "not_configured", 1 if configured else 0, row["id"]))
            if row and configured:
                external_id = None
                try:
                    external_id = await send_notification(row)
                    status, error = "sent", None
                except Exception as exc:
                    status = "retry" if row["attempts"] < 2 else ("unknown" if isinstance(exc, httpx.TransportError) else "failed")
                    error = type(exc).__name__
                    logger.warning("飞书通知 %s: %s", row["id"], error)
                async with pool.connection() as conn:
                    async with conn.transaction():
                        await locked_conversation(conn, row["conversation_id"])
                        await conn.execute("""UPDATE customer_chat.support_requests SET notification_status = %s,
                            external_message_id = %s, last_error = %s WHERE id = %s""", (status, external_id, error, row["id"]))
                        await emit(conn, row["conversation_id"], "notification", {"status": status, "handoff_id": row["id"]})
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("通知队列暂不可用: %s", type(exc).__name__)
            await asyncio.sleep(5)
