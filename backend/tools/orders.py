"""订单查询仅接受后端登录身份；所有 SQL 都保留 user_id 归属条件。"""

from psycopg.rows import dict_row

ORDER_STATUSES = {
    "pending_payment": "待付款", "paid": "待发货", "processing": "配货中",
    "shipped": "运输中", "completed": "已完成", "cancelled": "已取消",
}
ONGOING_STATUSES = ("pending_payment", "paid", "processing", "shipped")


async def setup_orders(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_chat.orders (
            order_no TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending_payment','paid','processing','shipped','completed','cancelled')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            items JSONB NOT NULL CHECK (jsonb_typeof(items) = 'array'),
            total_amount NUMERIC(12,2) NOT NULL CHECK (total_amount >= 0),
            currency TEXT NOT NULL DEFAULT 'CNY',
            carrier TEXT,
            tracking_no TEXT,
            logistics_status TEXT NOT NULL DEFAULT '',
            logistics_events JSONB NOT NULL DEFAULT '[]',
            is_demo BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS orders_owner_created_idx
        ON customer_chat.orders (user_id, created_at DESC, order_no DESC)
    """)


async def find_user_order(pool, user_id: str, order_no: str | None = None, *, product_name: str | None = None, for_after_sales: bool = False) -> dict | None:
    """无单号默认查进行中订单；售后另含已完成订单，排除未付款及已取消订单。"""
    if not user_id:
        raise ValueError("订单查询需要登录身份")
    sql = """SELECT order_no, status, created_at, items, total_amount, currency,
                    carrier, tracking_no, logistics_status, logistics_events, is_demo
             FROM customer_chat.orders WHERE user_id = %s"""
    params = [user_id]
    if order_no is not None:
        sql += " AND order_no = %s"
        params.append(order_no)
    else:
        sql += " AND status = ANY(%s)"
        params.append(["paid", "processing", "shipped", "completed"] if for_after_sales else list(ONGOING_STATUSES))
    if product_name is not None:
        sql += """ AND EXISTS (
            SELECT 1 FROM jsonb_array_elements(items) AS item
            WHERE strpos(lower(replace(item->>'name', ' ', '')), lower(replace(%s, ' ', ''))) > 0
        )"""
        params.append(product_name)
    sql += " ORDER BY created_at DESC, order_no DESC LIMIT 1"
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            order = await cur.fetchone()
    if order is None:
        return None
    async with pool.connection() as conn:
        fact = await (await conn.execute("""SELECT data FROM customer_chat.order_facts WHERE order_no=%s
            AND verified_at>now()-interval '24 hours'""",(order['order_no'],))).fetchone()
    order['signed_verified'] = bool(fact and fact['data']['is_demo']==order['is_demo'] and
        all(any(f['sku']==i['sku'] and f.get('signed_at') and f['received_quantity']>=i['quantity'] for f in fact['data']['items']) for i in order['items']))
    order["status_label"] = ORDER_STATUSES[order["status"]]
    order["created_at"] = order["created_at"].isoformat()
    order["total_amount"] = format(order["total_amount"], ".2f")
    return order


async def find_after_sales_orders(pool, user_id, product_name=None):
    async with pool.connection() as conn:
        return await (await conn.execute("""SELECT order_no FROM customer_chat.orders
            WHERE user_id=%s AND status=ANY(%s) AND (%s::text IS NULL OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(items) item
                WHERE strpos(lower(replace(item->>'name',' ','')), lower(replace(%s,' ','')))>0))
            ORDER BY created_at DESC LIMIT 20""", (user_id, ['paid','processing','shipped','completed'], product_name, product_name))).fetchall()
