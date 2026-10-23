"""本地业务事实、政策和售后申请接口。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb

from ..business_service import ApplicationStatus, OrderFacts, Policy, audit
from ..common.auth import current_user, support_user

router = APIRouter(prefix='/api')


@router.put('/support/orders/{order_no}/facts')
async def save_facts(order_no: str, body: OrderFacts, request: Request, actor: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            order = await (await conn.execute('SELECT * FROM customer_chat.orders WHERE order_no=%s FOR UPDATE', (order_no,))).fetchone()
            if not order:
                raise HTTPException(404, '订单不存在')
            if body.is_demo != order['is_demo'] or any(i.sku not in {x['sku'] for x in order['items']} for i in body.items):
                raise HTTPException(422, '订单性质或商品与事实不一致')
            old = await (await conn.execute('SELECT version FROM customer_chat.order_facts WHERE order_no=%s', (order_no,))).fetchone()
            if body.expected_version != (old['version'] if old else 0):
                raise HTTPException(409, '事实版本已变化')
            version = body.expected_version + 1
            await conn.execute('''INSERT INTO customer_chat.order_facts (order_no,version,data,source,verified_by)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (order_no) DO UPDATE SET version=EXCLUDED.version,
                data=EXCLUDED.data,source=EXCLUDED.source,verified_by=EXCLUDED.verified_by,verified_at=now()''',
                (order_no, version, Jsonb(body.model_dump(mode='json')), body.source, actor))
            await audit(conn, actor, 'verify_facts', order_no, {'version': version, 'source': body.source})
    return {'version': version}


@router.put('/support/policies/{policy_id}')
async def approve_policy(policy_id: str, body: Policy, request: Request, actor: str = Depends(support_user)):
    if not 1 <= len(policy_id) <= 128:
        raise HTTPException(422, '政策编号长度无效')
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('policy:' + policy_id,))
            latest = await (await conn.execute('SELECT max(version) AS version FROM customer_chat.after_sales_policies WHERE policy_id=%s', (policy_id,))).fetchone()
            if body.version != (latest['version'] or 0) + 1:
                raise HTTPException(409, '政策版本必须依次递增，历史版本不可覆盖')
            await conn.execute('UPDATE customer_chat.after_sales_policies SET active=FALSE WHERE policy_id=%s', (policy_id,))
            await conn.execute('INSERT INTO customer_chat.after_sales_policies (policy_id,version,data,approved_by) VALUES (%s,%s,%s,%s)',
                               (policy_id, body.version, Jsonb(body.model_dump(mode='json')), actor))
            await audit(conn, actor, 'approve_policy', policy_id, {'version': body.version, 'source': body.source})
    return {'policy_id': policy_id, 'version': body.version, 'active': True}


@router.delete('/support/policies/{policy_id}')
async def withdraw_policy(policy_id: str, request: Request, actor: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            await conn.execute('UPDATE customer_chat.after_sales_policies SET active=FALSE WHERE policy_id=%s', (policy_id,))
            await audit(conn, actor, 'withdraw_policy', policy_id, {})
    return {'active': False}


@router.get('/after-sales')
async def applications(request: Request, user_id: str = Depends(current_user)):
    async with request.app.state.db_pool.connection() as conn:
        rows = await (await conn.execute('''SELECT application_no,order_no,action,status,is_demo,created_at,updated_at
            FROM customer_chat.after_sales_applications WHERE user_id=%s ORDER BY created_at DESC LIMIT 100''', (user_id,))).fetchall()
    return {'applications': rows, 'scope': '本项目本地记录，非外部商城回执'}


@router.patch('/support/after-sales/{application_no}')
async def update_application(application_no: str, body: ApplicationStatus, request: Request, actor: str = Depends(support_user)):
    transitions = {'pending_review': {'approved', 'rejected', 'cancelled'}, 'approved': {'completed', 'cancelled'}}
    if body.status not in transitions.get(body.expected_status, set()):
        raise HTTPException(409, '不支持的状态迁移')
    async with request.app.state.db_pool.connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute('''UPDATE customer_chat.after_sales_applications SET status=%s,updated_at=now()
                WHERE application_no=%s AND status=%s RETURNING application_no''',
                (body.status, application_no, body.expected_status))).fetchone()
            if not row:
                raise HTTPException(409, '申请不存在或状态已变化')
            await audit(conn, actor, 'application_status', application_no, body.model_dump())
    return {'application_no': application_no, 'status': body.status, 'scope': 'local'}


@router.get('/support/orders/{order_no}/facts')
async def read_facts(order_no: str, request: Request, actor: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        row = await (await conn.execute('SELECT order_no,version,data,source,verified_by,verified_at FROM customer_chat.order_facts WHERE order_no=%s', (order_no,))).fetchone()
    if not row:
        raise HTTPException(404,'该订单尚无已核验事实')
    return row


@router.get('/support/policies')
async def read_policies(request: Request, actor: str = Depends(support_user)):
    async with request.app.state.db_pool.connection() as conn:
        rows = await (await conn.execute('SELECT * FROM customer_chat.after_sales_policies ORDER BY policy_id,version DESC')).fetchall()
    return {'policies':rows}
