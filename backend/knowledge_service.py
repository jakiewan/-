"""审核绑定正文摘要与明确版本，修改正文后旧审核自动失效。"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

import psycopg
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator


async def setup_knowledge(conn):
    await conn.execute('''CREATE TABLE IF NOT EXISTS customer_chat.knowledge_reviews (
        resource_key TEXT PRIMARY KEY, body_hash TEXT NOT NULL, approved BOOLEAN NOT NULL,
        expires_at TIMESTAMPTZ, reviewed_by TEXT NOT NULL, reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )''')


def body_hash(payload):
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def review_status(payload, resource_key, database_url):
    metadata = payload.get('metadata',payload)
    with psycopg.connect(database_url,connect_timeout=3,autocommit=True) as conn:
        row = conn.execute('SELECT body_hash,approved,expires_at FROM customer_chat.knowledge_reviews WHERE resource_key=%s', (resource_key,)).fetchone()
    if row:
        if row[0] != body_hash(payload):
            return 'changed'
        if not row[1]:
            return 'withdrawn'
        if row[2] is not None and row[2] <= datetime.now(timezone.utc):
            return 'expired'
        return 'approved'
    return 'approved' if metadata.get('approved') is True else 'pending'


def is_approved(payload, resource_key, database_url):
    return review_status(payload,resource_key,database_url) == 'approved'


class Resource(BaseModel):
    kind: Literal['document','faq']
    id: str = Field(min_length=1,max_length=128,pattern=r'^[^:\s]+$')
    version: str = Field(min_length=1,max_length=128,pattern=r'^[^:\s]+$')
    goodsid: str = Field(default='65539',pattern=r'^\d{1,20}$')
    channel: Literal['MALL','DIRECT','AUTHORIZED'] = 'MALL'


class Review(Resource):
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    approved: bool
    expires_at: datetime | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.expires_at and (self.expires_at.tzinfo is None or self.expires_at <= datetime.now(timezone.utc)):
            raise ValueError('到期时间必须带时区且在未来')
        return self


def resource_key(retriever, body):
    if body.kind == 'document':
        return f'{retriever.document_prefix}:body:{body.id}:{body.version}'
    return f'{retriever.faq_prefix}:answer:{body.id}:{body.goodsid}:{body.channel}:{body.version}'


async def read_resource(request,body):
    retriever = request.app.state.retriever
    if retriever is None:
        raise HTTPException(503,'知识库暂不可用')
    key = resource_key(retriever,body)
    raw = await asyncio.to_thread(retriever.redis.get,key)
    payload = json.loads(raw or 'null')
    if not isinstance(payload,dict):
        raise HTTPException(404,'该知识版本不存在')
    metadata = payload.get('metadata',payload)
    if metadata.get('version') != body.version or not metadata.get('source'):
        raise HTTPException(422,'知识版本或来源不完整，不能审核')
    return key,payload
