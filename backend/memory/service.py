"""PostgreSQL 是记忆唯一真相；模型候选不携带身份或执行权限。"""

import os
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from psycopg.types.json import Jsonb

TENANT = os.getenv('MEMORY_TENANT', 'local')
KEYS = {'preferred_name', 'phone_preferences', 'reply_style', 'current_device', 'agreement', 'todo', 'episode'}
# 禁止项先于提取模型过滤；无法确定的语义仍由候选校验拒绝，规则不宣称覆盖所有隐私。
SENSITIVE = re.compile(r'密码|验证码|私钥|密钥|令牌|身份证|银行卡|住址|地址|手机号|病历|诊断|api.?key|secret|password|token|sk-[\w-]+|\b\d{11,}\b|[\w.+-]+@[\w.-]+', re.I)
UNSAFE = re.compile(r'忽略.{0,8}(规则|指令)|系统提示|开发者模式|越权|自动.{0,4}(退款|提交)|无需确认')


def safe_memory(text):
    return not SENSITIVE.search(text) and not UNSAFE.search(text)


class MemoryValue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: Literal['preferred_name', 'phone_preferences', 'reply_style', 'current_device', 'agreement', 'todo', 'episode']
    value: str = Field(min_length=1, max_length=300)
    quote: str = Field(min_length=1, max_length=500)
    scope_id: str = Field(default='user', max_length=128)
    due_at: datetime | None = None
    task_status: Literal['open', 'done', 'cancelled'] = 'open'

    @model_validator(mode='after')
    def valid(self):
        if not safe_memory(self.value + '\n' + self.quote):
            raise ValueError('该内容不适合保存到长期记忆')
        if self.value not in self.quote:
            raise ValueError('记忆内容必须保留原文片段，不能改写否定或条件')
        scoped = self.key in ('agreement', 'todo', 'episode')
        if scoped == (self.scope_id == 'user'):
            raise ValueError('任务记忆需要具体任务，普通偏好只能属于用户范围')
        if self.due_at and (self.key != 'todo' or self.due_at.tzinfo is None):
            raise ValueError('只有待办支持带时区的截止日期')
        return self


async def validate_scope(conn,user_id,body):
    if body.scope_id != 'user':
        row = await (await conn.execute('''SELECT 1 FROM customer_chat.conversation_messages m
            JOIN customer_chat.conversations c ON c.id=m.conversation_id
            WHERE c.user_id=%s AND m.role='assistant' AND m.metadata->'tasks' @> %s LIMIT 1''',
            (user_id,Jsonb([{'task_id':body.scope_id}])))).fetchone()
        if not row:
            raise HTTPException(422,'任务引用不属于当前用户的已保存任务')
    if body.due_at and body.due_at.date().isoformat() not in body.quote:
        raise HTTPException(422,'截止日期需在原文中明确写为 YYYY-MM-DD，不能猜测日期')


async def setup_memory(conn):
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS customer_chat.memory_settings (
            tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, auto_capture BOOLEAN NOT NULL DEFAULT FALSE,
            memory_epoch BIGINT NOT NULL DEFAULT 0, blocked_keys JSONB NOT NULL DEFAULT '[]',
            history_cutoff BIGINT NOT NULL DEFAULT 0, migrated BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (tenant_id,user_id)
        );
        ALTER TABLE customer_chat.memory_settings ADD COLUMN IF NOT EXISTS capture_after BIGINT NOT NULL DEFAULT 0;
        CREATE TABLE IF NOT EXISTS customer_chat.memory_items (
            id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            memory_key TEXT NOT NULL, scope_id TEXT NOT NULL, version INTEGER NOT NULL,
            value TEXT NOT NULL, quote TEXT NOT NULL, source_type TEXT NOT NULL, source_sequence BIGINT NOT NULL,
            epoch BIGINT NOT NULL, status TEXT NOT NULL, task_status TEXT NOT NULL DEFAULT 'open',
            due_at TIMESTAMPTZ, expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), reviewed_by TEXT,
            UNIQUE (tenant_id,user_id,memory_key,scope_id,version)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS memory_active_idx ON customer_chat.memory_items
            (tenant_id,user_id,memory_key,scope_id) WHERE status='active';
        CREATE TABLE IF NOT EXISTS customer_chat.memory_events (
            id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            action TEXT NOT NULL, memory_id TEXT, epoch BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        ALTER TABLE customer_chat.memory_events ADD COLUMN IF NOT EXISTS memory_key TEXT;
        ALTER TABLE customer_chat.memory_events ADD COLUMN IF NOT EXISTS scope_id TEXT;
        CREATE TABLE IF NOT EXISTS customer_chat.memory_jobs (
            id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
            message_id BIGINT NOT NULL UNIQUE REFERENCES customer_chat.conversation_messages(id),
            epoch BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            lease_until TIMESTAMPTZ, candidates JSONB, error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    ''')


async def settings(conn, user_id):
    await conn.execute('INSERT INTO customer_chat.memory_settings (tenant_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING', (TENANT, user_id))
    return await (await conn.execute('SELECT * FROM customer_chat.memory_settings WHERE tenant_id=%s AND user_id=%s FOR UPDATE', (TENANT, user_id))).fetchone()


async def event(conn, user_id, action, item_id, epoch, *, key=None, scope_id=None):
    await conn.execute('INSERT INTO customer_chat.memory_events (tenant_id,user_id,action,memory_id,epoch,memory_key,scope_id) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                       (TENANT, user_id, action, item_id, epoch, key, scope_id))


async def put_memory(conn, user_id, candidate, *, source='explicit', sequence=0, epoch=None, review=False):
    pref = await settings(conn, user_id)
    if epoch is not None and epoch != pref['memory_epoch']:
        return None
    key = candidate.key + ':' + candidate.scope_id
    if source == 'auto' and (not pref['auto_capture'] or key in pref['blocked_keys'] or '*' in pref['blocked_keys']):
        return None
    old = await (await conn.execute('''SELECT * FROM customer_chat.memory_items WHERE tenant_id=%s AND user_id=%s
        AND memory_key=%s AND scope_id=%s AND status='active' FOR UPDATE''', (TENANT, user_id, candidate.key, candidate.scope_id))).fetchone()
    if old and sequence and old['source_sequence'] > sequence:
        return None
    if old and source == 'auto' and old['source_sequence'] >= sequence:
        return old['id']
    if old and old['value'] == candidate.value and old['task_status'] == candidate.task_status:
        return old['id']
    count = await (await conn.execute("SELECT count(*) AS n FROM customer_chat.memory_items WHERE tenant_id=%s AND user_id=%s AND status IN ('active','pending_review') AND expires_at>now()", (TENANT, user_id))).fetchone()
    if count['n'] >= 100 and source == 'auto':
        return None
    if count['n'] >= 100 and not old:
        raise HTTPException(409, '已达到 100 条记忆上限，请先管理现有条目')
    # 自动冲突不能覆盖明确保存项；共识和待办候选需用户复核。
    pending = source == 'auto' and (review or candidate.key in ('agreement', 'todo', 'episode') or old is not None)
    if old and not pending:
        await conn.execute("UPDATE customer_chat.memory_items SET status='superseded',expires_at=LEAST(expires_at,now()+interval '90 days') WHERE id=%s", (old['id'],))
    latest = await (await conn.execute('''SELECT coalesce(max(version),0) AS version FROM customer_chat.memory_items
        WHERE tenant_id=%s AND user_id=%s AND memory_key=%s AND scope_id=%s''', (TENANT, user_id, candidate.key, candidate.scope_id))).fetchone()
    item_id = str(uuid4())
    days = 7 if pending else 180 if source == 'explicit' and candidate.key in ('preferred_name', 'reply_style', 'phone_preferences') else 90
    await conn.execute('''INSERT INTO customer_chat.memory_items
        (id,tenant_id,user_id,memory_key,scope_id,version,value,quote,source_type,source_sequence,epoch,status,task_status,due_at,expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (item_id,TENANT,user_id,candidate.key,candidate.scope_id,latest['version']+1,candidate.value,candidate.quote,
         source,sequence,pref['memory_epoch'],'pending_review' if pending else 'active',candidate.task_status,candidate.due_at,
         datetime.max.replace(tzinfo=timezone.utc) if not pending and candidate.key == 'todo' and candidate.task_status == 'open' else datetime.now(timezone.utc)+timedelta(days=days)))
    if source == 'explicit':
        await conn.execute('UPDATE customer_chat.memory_settings SET blocked_keys=%s WHERE tenant_id=%s AND user_id=%s',
                           (Jsonb([k for k in pref['blocked_keys'] if k != key]), TENANT, user_id))
    await event(conn, user_id, 'candidate' if pending else 'save', item_id, pref['memory_epoch'])
    return item_id


async def list_memory(conn, user_id, *, pending=False):
    return await (await conn.execute('''SELECT id,memory_key,scope_id,version,value,source_type,status,
        CASE WHEN task_status='open' AND due_at<now() THEN 'overdue' ELSE task_status END AS task_status,
        due_at,expires_at,source_sequence FROM customer_chat.memory_items
        WHERE tenant_id=%s AND user_id=%s AND status=ANY(%s) AND expires_at>now() ORDER BY created_at DESC LIMIT 100''',
        (TENANT,user_id,['active','pending_review'] if pending else ['active']))).fetchall()


async def forget(conn, user_id, key=None, scope_id='user', *, current_message_id=None):
    # 与回复落库保持会话→记忆的锁顺序，避免删除与完成轮次互相等待。
    await conn.execute('SELECT id FROM customer_chat.conversations WHERE user_id=%s FOR UPDATE', (user_id,))
    pref = await settings(conn, user_id)
    # 同 key 的旧版本与候选正文一并清除，保留无正文的删除审计。
    await conn.execute('''UPDATE customer_chat.memory_items SET status='deleted',value='',quote=''
        WHERE tenant_id=%s AND user_id=%s AND (%s::text IS NULL OR (memory_key=%s AND scope_id=%s))''',
        (TENANT,user_id,key,key,scope_id))
    cutoff = await (await conn.execute('''SELECT coalesce(max(m.id),0) AS id FROM customer_chat.conversation_messages m
        JOIN customer_chat.conversations c ON c.id=m.conversation_id WHERE c.user_id=%s''', (user_id,))).fetchone()
    deleted_keys = await (await conn.execute('SELECT DISTINCT memory_key,scope_id FROM customer_chat.memory_items WHERE tenant_id=%s AND user_id=%s', (TENANT,user_id))).fetchall()
    blocked = list(set(pref['blocked_keys'] + ([key + ':' + scope_id] if key else [r['memory_key'] + ':' + r['scope_id'] for r in deleted_keys])))
    await conn.execute('''UPDATE customer_chat.memory_settings SET memory_epoch=memory_epoch+1,blocked_keys=%s,
        history_cutoff=%s,auto_capture=CASE WHEN %s THEN FALSE ELSE auto_capture END WHERE tenant_id=%s AND user_id=%s''',
        (Jsonb(blocked), current_message_id if current_message_id is not None else cutoff['id'], key is None, TENANT,user_id))
    await conn.execute("UPDATE customer_chat.memory_jobs SET status='cancelled',candidates=NULL WHERE tenant_id=%s AND user_id=%s AND status IN ('pending','running')", (TENANT,user_id))
    if current_message_id is None:
        from ..tools.conversations import emit, conversation_view
        rows = await (await conn.execute('UPDATE customer_chat.conversations SET version=version+1 WHERE user_id=%s RETURNING *', (user_id,))).fetchall()
        for row in rows:
            await conn.execute("UPDATE customer_chat.conversation_messages SET status='interrupted' WHERE conversation_id=%s AND status IN ('queued','running')", (row['id'],))
            await emit(conn, row['id'], 'state', conversation_view(row))
    await event(conn, user_id, 'forget:' + (key or 'all'), None, pref['memory_epoch']+1, key=key, scope_id=scope_id)


async def migrate_profile(pool, store, user_id):
    from .context import MEMORY_NAMESPACE
    item = await store.aget(MEMORY_NAMESPACE, user_id)
    async with pool.connection() as conn:
        async with conn.transaction():
            pref = await settings(conn, user_id)
            if not pref['migrated']:
                for key, value in (item.value if item else {}).items():
                    if key in ('preferred_name','phone_preferences','reply_style') and isinstance(value,str) and safe_memory(value):
                        await put_memory(conn, user_id, MemoryValue(key=key,value=value,quote=value))
                await conn.execute('UPDATE customer_chat.memory_settings SET migrated=TRUE WHERE tenant_id=%s AND user_id=%s', (TENANT,user_id))
    # 只在主库提交后删除旧副本，失败重试不会重新覆盖新版。
    if item:
        await store.adelete(MEMORY_NAMESPACE,user_id)
