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
    order["status_label"] = ORDER_STATUSES[order["status"]]
    order["created_at"] = order["created_at"].isoformat()
    order["total_amount"] = format(order["total_amount"], ".2f")
    return order
