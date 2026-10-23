"""知识正文检查和版本审核接口。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ..business_service import audit
from ..common.auth import support_user
from ..knowledge_service import Resource, Review, body_hash, is_approved, read_resource

router = APIRouter(prefix='/api/support/knowledge')


@router.post('/inspect')
async def inspect(body: Resource,request: Request,actor: str = Depends(support_user)):
    key,payload = await read_resource(request,body)
    approved = await asyncio.to_thread(is_approved,payload,key,request.app.state.database_url)
    return {'resource':body.model_dump(),'hash':body_hash(payload),'approved':approved,'content':payload}


@router.put('/review')
async def review(body: Review,request: Request,actor: str = Depends(support_user)):
    key,payload = await read_resource(request,body)
    digest = body_hash(payload)
    if digest != body.expected_hash:
        raise HTTPException(409,'正文已变化，请重新核对后审核')
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            await conn.execute('''INSERT INTO customer_chat.knowledge_reviews
                (resource_key,body_hash,approved,expires_at,reviewed_by) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (resource_key) DO UPDATE SET body_hash=EXCLUDED.body_hash,approved=EXCLUDED.approved,
                expires_at=EXCLUDED.expires_at,reviewed_by=EXCLUDED.reviewed_by,reviewed_at=now()''',
                (key,digest,body.approved,body.expires_at,actor))
            await audit(conn,actor,'knowledge_approve' if body.approved else 'knowledge_withdraw',key,{'hash':digest,'expires_at':body.expires_at.isoformat() if body.expires_at else None})
    return {'approved':body.approved,'hash':digest}
