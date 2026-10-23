"""由已审核政策和有来源的商品签收事实判定资格；缺证据不等于无资格。"""

import calendar
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


async def setup_eligibility(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_chat.order_facts (
            order_no TEXT PRIMARY KEY REFERENCES customer_chat.orders(order_no),
            version INTEGER NOT NULL, data JSONB NOT NULL, source TEXT NOT NULL,
            verified_by TEXT NOT NULL, verified_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS customer_chat.after_sales_policies (
            policy_id TEXT NOT NULL, version INTEGER NOT NULL, data JSONB NOT NULL,
            approved_by TEXT NOT NULL, approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            active BOOLEAN NOT NULL DEFAULT TRUE, PRIMARY KEY (policy_id, version)
        );
        CREATE TABLE IF NOT EXISTS customer_chat.after_sales_notices (
            request_id UUID PRIMARY KEY, user_id TEXT NOT NULL, order_no TEXT NOT NULL,
            item_sku TEXT NOT NULL, action TEXT NOT NULL, reason_kind TEXT NOT NULL,
            received_at TIMESTAMPTZ NOT NULL, message_id BIGINT
        );
        ALTER TABLE customer_chat.after_sales_applications ADD COLUMN IF NOT EXISTS eligibility JSONB;
        ALTER TABLE customer_chat.after_sales_applications ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
        CREATE TABLE IF NOT EXISTS customer_chat.business_audit (
            id BIGSERIAL PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
            resource TEXT NOT NULL, data JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def deadline_at(signed_at, policy):
    """自然日按当地日期截止；月年按日历计算并收敛到月末。"""
    local = datetime.fromisoformat(signed_at).astimezone(ZoneInfo(policy['timezone']))
    count, unit = policy['duration'], policy['unit']
    if unit == 'hours':
        return local.astimezone(timezone.utc) + timedelta(hours=count)
    if unit == 'days':
        day = local.date() + timedelta(days=count)
    else:
        months = count * (12 if unit == 'years' else 1)
        year, month = divmod(local.year * 12 + local.month - 1 + months, 12)
        day = local.date().replace(year=year, month=month + 1,
                                  day=min(local.day, calendar.monthrange(year, month + 1)[1]))
    # 从签收次日算第一个自然日，第 N 日的下一天零点为排他的截止点。
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), local.tzinfo)


def evaluate(order, fact, policies, *, action, reason_kind, items, noticed_at, now):
    result = {'status': 'unknown', 'message': '签收或适用政策资料不足，资格待核实。', 'items': []}
    if order['status'] not in ('paid', 'processing', 'shipped', 'completed'):
        return {**result, 'status': 'ineligible', 'message': '订单未付款或已取消，不能申请。'}
    if not fact:
        return result
    data = fact['data']
    if data['is_demo'] != order['is_demo'] or now - fact['verified_at'] > timedelta(hours=24):
        return {**result, 'message': '订单事实来源不匹配或超过 24 小时未核验，资格待核实。'}
    for item in items:
        signed = next((row for row in data['items'] if row['sku'] == item['sku']), None)
        row = {'sku': item['sku'], 'status': 'unknown', 'reason': '商品签收、数量或政策待核实'}
        result['items'].append(row)
        if not signed or not signed.get('signed_at') or signed['received_quantity'] < item['quantity']:
            continue
        if datetime.fromisoformat(signed['signed_at']) > now:
            continue
        matched = [p for p in policies if p['data']['action'] == action
                   and p['data']['reason_kind'] == reason_kind and p['data']['category'] == signed['category']
                   and p['data']['market'] == data['market'] and p['data']['channel'] == data['channel']
                   and p['data']['is_demo'] == data['is_demo']
                   and p['data']['effective_from'] <= data['purchased_at'][:10] <= p['data']['effective_until']]
        if len(matched) != 1:
            continue
        policy = matched[0]
        deadline = deadline_at(signed['signed_at'], policy['data'])
        row.update(policy_id=policy['policy_id'], policy_version=policy['version'],
                   source=policy['data']['source'], conditions=policy['data']['conditions'], deadline=deadline.isoformat(), fact_version=fact['version'])
        checks = [signed.get('checks', {}).get(key) for key in policy['data'].get('required_checks', [])]
        if any(value is False for value in checks):
            row.update(status='ineligible', reason='未满足政策要求的商品条件')
        elif any(value is None for value in checks):
            row.update(status='unknown', reason='政策要求的商品条件尚未核实')
        elif signed['eligible_quantity'] < item['quantity']:
            row.update(status='ineligible', reason='可申请数量不足')
        elif (noticed_at if policy['data']['honor_notice'] else now) >= deadline:
            row.update(status='ineligible', reason='超过适用政策期限')
        else:
            row.update(status='eligible', reason='符合本地已审核政策')
    states = {row['status'] for row in result['items']}
    result['status'] = 'ineligible' if 'ineligible' in states else 'unknown' if 'unknown' in states or not states else 'eligible'
    result['message'] = {'eligible': '所选商品均符合已审核政策，提交前还会复核。',
                         'ineligible': '所选范围存在不符合条件的商品，未自动缩减申请范围。',
                         'unknown': '所选范围存在待核实商品，尚不能确认申请资格。'}[result['status']]
    result['fingerprint'] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result


async def check_eligibility(conn, user_id, *, order_no, item_sku, action, reason_kind, request_id,
                            noticed_at=None, message_id=None):
    order = await (await conn.execute('SELECT * FROM customer_chat.orders WHERE user_id=%s AND order_no=%s FOR UPDATE',
                                     (user_id, order_no))).fetchone()
    if not order:
        raise ValueError('未找到您名下的订单')
    now = datetime.now(timezone.utc)
    await conn.execute('''INSERT INTO customer_chat.after_sales_notices
        (request_id,user_id,order_no,item_sku,action,reason_kind,received_at,message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
        (request_id, user_id, order_no, item_sku, action, reason_kind, noticed_at or now, message_id))
    notice = await (await conn.execute('SELECT * FROM customer_chat.after_sales_notices WHERE request_id=%s', (request_id,))).fetchone()
    if tuple(notice[k] for k in ('user_id', 'order_no', 'item_sku', 'action', 'reason_kind')) != (user_id, order_no, item_sku, action, reason_kind):
        raise ValueError('申请标识与通知范围不一致，请重新确认')
    fact = await (await conn.execute('SELECT * FROM customer_chat.order_facts WHERE order_no=%s FOR SHARE', (order_no,))).fetchone()
    policies = await (await conn.execute('SELECT * FROM customer_chat.after_sales_policies WHERE active FOR SHARE')).fetchall()
    items = [i for i in order['items'] if item_sku == '' or i['sku'] == item_sku]
    return evaluate(order, fact, policies, action=action, reason_kind=reason_kind, items=items,
                    noticed_at=notice['received_at'], now=now)
