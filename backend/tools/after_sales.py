"""本项目的售后申请业务方法：落库成功才返回申请单，不代表外部商城受理。"""

from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from ..common.auth import pilot_enabled
from .conversations import check_execution
from .eligibility import check_eligibility
import os

ACTION_LABELS = {"return": "退货", "exchange": "换货", "repair": "维修"}
STATUS_LABELS = {'pending_review': '待审核', 'approved': '本地审核通过', 'rejected': '本地审核拒绝',
                 'completed': '本地处理完成', 'cancelled': '已取消'}
ELIGIBLE_STATUSES = ("paid", "processing", "shipped", "completed")


async def setup_after_sales(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_chat.after_sales_applications (
            application_no TEXT PRIMARY KEY,
            request_id UUID NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            order_no TEXT NOT NULL REFERENCES customer_chat.orders(order_no),
            item_sku TEXT NOT NULL,
            item JSONB NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('return', 'exchange', 'repair')),
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending_review',
            is_demo BOOLEAN NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS after_sales_pending_item_idx
        ON customer_chat.after_sales_applications (user_id, order_no, item_sku)
        WHERE status = 'pending_review'
    """)


async def submit_after_sales(pool, user_id: str, *, order_no: str, item_sku: str,
                             action: str, reason: str, request_id: str, expected_items: list | None = None,
                             context=None, reason_kind="", expected_eligibility=None) -> dict:
    """锁定本人订单后创建申请；相同动作返回原单，不同动作明确拒绝，避免误报提交成功。"""
    if not user_id or action not in ACTION_LABELS:
        raise ValueError("售后申请身份或类型无效")
    # 空 SKU 表示整单退货，商品快照保存在原有 JSONB 字段中。
    if item_sku == "" and action != "return":
        raise ValueError("整单申请仅支持退货，换货或维修请选择一件商品")
    async with pool.connection() as conn:
        async with conn.transaction():
            await check_execution(conn, context)
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT status, items, is_demo FROM customer_chat.orders
                    WHERE order_no = %s AND user_id = %s FOR UPDATE
                """, (order_no, user_id))
                order = await cur.fetchone()
                if order is None:
                    raise ValueError("未找到您名下的该订单，请重新核对订单号")
                await cur.execute("""
                    SELECT * FROM customer_chat.after_sales_applications
                    WHERE user_id = %s AND (request_id = %s OR
                        (order_no = %s AND (item_sku = %s OR item_sku = '' OR %s = '') AND status = 'pending_review'))
                    ORDER BY created_at DESC LIMIT 1
                """, (user_id, request_id, order_no, item_sku, item_sku))
                application = await cur.fetchone()
                existing = application is not None
                if application is not None and (application["order_no"], application["item_sku"], application["action"]) != (order_no, item_sku, action):
                    status = "待审核" if application["status"] == "pending_review" else application["status"]
                    raise ValueError(
                        f"订单 {application['order_no']} 中的 {application['item']['name']} 已有{ACTION_LABELS[application['action']]}申请"
                        f"（申请单号：{application['application_no']}，状态：{status}），"
                        f"本次{ACTION_LABELS[action]}申请未提交，请先联系售后客服处理原申请"
                    )
                if application is None:
                    if os.getenv('AFTER_SALES_WRITES_ENABLED', 'true').lower() != 'true' or not pilot_enabled(user_id):
                        raise ValueError('新售后提交暂时停用，请联系人工客服')
                    eligibility = await check_eligibility(conn, user_id, order_no=order_no, item_sku=item_sku,
                        action=action, reason_kind=reason_kind, request_id=request_id)
                    if eligibility['status'] != 'eligible':
                        raise ValueError(eligibility['message'])
                    if not expected_eligibility or eligibility['fingerprint'] != expected_eligibility:
                        raise ValueError('资格依据或政策已变化，请重新核对后确认')
                    if order["status"] not in ELIGIBLE_STATUSES:
                        raise ValueError("该订单未付款或已取消，无法提交退货、换货或维修申请")
                    items = order["items"] if item_sku == "" else [item for item in order["items"] if item.get("sku") == item_sku]
                    if not items or (item_sku != "" and len(items) != 1):
                        raise ValueError("无法唯一确定申请商品，请重新选择订单中的商品")
                    if expected_items is not None and items != expected_items:
                        raise ValueError("订单商品已变化，请重新核对商品及数量后确认")
                    item = {"sku": "", "name": "整单全部商品", "items": items} if item_sku == "" else items[0]
                    await cur.execute("""
                        INSERT INTO customer_chat.after_sales_applications
                        (application_no, request_id, user_id, order_no, item_sku, item, action, reason, is_demo, eligibility)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
                    """, ("AS-" + uuid4().hex.upper(), request_id, user_id, order_no,
                          item_sku, Jsonb(item), action, reason, order["is_demo"], Jsonb(eligibility)))
                    application = await cur.fetchone()
                    await conn.execute('INSERT INTO customer_chat.business_audit (actor,action,resource,data) VALUES (%s,%s,%s,%s)',
                        (user_id, 'submit_after_sales', application['application_no'], Jsonb({
                            'request_id': request_id, 'eligibility': eligibility, 'items': items,
                            'conversation_id': getattr(context, 'conversation_id', None),
                            'message_id': getattr(context, 'message_id', None)})))
    return {
        "application_no": application["application_no"], "order_no": application["order_no"],
        "action": application["action"], "action_label": ACTION_LABELS[application["action"]],
        "item": application["item"], "items": application["item"].get("items", [application["item"]]),
        "all_items": application["item_sku"] == "", "reason": application["reason"],
        "status": application["status"],
        "status_label": STATUS_LABELS.get(application["status"], application["status"]),
        "created_at": application["created_at"].isoformat(),
        "is_demo": application["is_demo"], "existing": existing,
    }
