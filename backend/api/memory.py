"""用户自己的记忆设置、管理与候选复核。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..common.auth import current_user
from ..memory.service import TENANT, MemoryValue, event, forget, list_memory, put_memory, settings, migrate_profile, validate_scope

router = APIRouter(prefix='/api/memory')


class SettingsInput(BaseModel):
    auto_capture: bool


@router.get('')
async def read(request: Request, user_id: str = Depends(current_user)):
    pool = request.app.state.db_pool
    await migrate_profile(pool, request.app.state.minimax.store, user_id)
    async with pool.connection() as conn:
        async with conn.transaction():
            pref = await settings(conn,user_id)
            items = await list_memory(conn,user_id,pending=True)
    return {'auto_capture': pref['auto_capture'], 'items': items}


@router.put('/settings')
async def configure(body: SettingsInput, request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            pref = await settings(conn,user_id)
            await conn.execute('''UPDATE customer_chat.memory_settings SET auto_capture=%s,memory_epoch=memory_epoch+1,
                capture_after=(SELECT coalesce(max(id),0) FROM customer_chat.conversation_messages)
                WHERE tenant_id=%s AND user_id=%s''', (body.auto_capture,TENANT,user_id))
            await conn.execute("UPDATE customer_chat.memory_jobs SET status='cancelled',candidates=NULL WHERE tenant_id=%s AND user_id=%s AND status IN ('pending','running')", (TENANT,user_id))
            await event(conn,user_id,'auto_on' if body.auto_capture else 'auto_off',None,pref['memory_epoch']+1)
    return {'auto_capture': body.auto_capture}


@router.post('')
async def save(body: MemoryValue, request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            await validate_scope(conn,user_id,body)
            item_id = await put_memory(conn,user_id,body)
    return {'id':item_id}


@router.delete('')
async def clear(request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            await forget(conn,user_id)
    return {'deleted':True,'auto_capture':False}


@router.delete('/{item_id}')
async def delete(item_id: str, request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute('SELECT memory_key,scope_id FROM customer_chat.memory_items WHERE id=%s AND tenant_id=%s AND user_id=%s', (item_id,TENANT,user_id))).fetchone()
            if not row:
                raise HTTPException(404,'记忆不存在')
            await forget(conn,user_id,row['memory_key'],row['scope_id'])
    return {'deleted':True}


class ReviewInput(BaseModel):
    approve: bool
    version: int


@router.post('/{item_id}/review')
async def review(item_id: str, body: ReviewInput, request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            pref = await settings(conn,user_id)
            row = await (await conn.execute('''SELECT * FROM customer_chat.memory_items WHERE id=%s AND tenant_id=%s AND user_id=%s
                AND status='pending_review' AND expires_at>now() FOR UPDATE''',(item_id,TENANT,user_id))).fetchone()
            if not row or row['version'] != body.version or row['epoch'] != pref['memory_epoch']:
                raise HTTPException(409,'候选已变化或已被删除策略淘汰')
            if body.approve:
                candidate = MemoryValue(key=row['memory_key'],value=row['value'],quote=row['quote'],scope_id=row['scope_id'],due_at=row['due_at'],task_status=row['task_status'])
                await validate_scope(conn,user_id,candidate)
                await put_memory(conn,user_id,candidate,source='explicit',sequence=row['source_sequence'],epoch=pref['memory_epoch'])
            await conn.execute("UPDATE customer_chat.memory_items SET status=%s,reviewed_by=%s,value='',quote='' WHERE id=%s", ('approved' if body.approve else 'rejected',user_id,item_id))
            await event(conn,user_id,'approve' if body.approve else 'reject',item_id,pref['memory_epoch'])
    return {'approved':body.approve}


class TaskStatusInput(BaseModel):
    version: int
    status: Literal['done','cancelled']


@router.patch('/{item_id}')
async def finish_task(item_id: str, body: TaskStatusInput, request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            await settings(conn,user_id)
            row = await (await conn.execute('''SELECT * FROM customer_chat.memory_items WHERE id=%s AND tenant_id=%s AND user_id=%s
                AND memory_key='todo' AND status='active' AND task_status='open' FOR UPDATE''', (item_id,TENANT,user_id))).fetchone()
            if not row or row['version'] != body.version:
                raise HTTPException(409,'待办已变化，请刷新后核对')
            candidate = MemoryValue(key='todo',value=row['value'],quote=row['quote'],scope_id=row['scope_id'],due_at=row['due_at'],task_status=body.status)
            result = await put_memory(conn,user_id,candidate,sequence=row['source_sequence'])
    return {'id':result,'status':body.status}
