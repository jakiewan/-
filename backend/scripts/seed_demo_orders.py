"""为配置的登录账号写入演示订单；不覆盖、删除任何已有订单。"""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from ..common.constants import PROJECT_ROOT
from ..tools.orders import setup_orders


async def main():
    load_dotenv(PROJECT_ROOT / ".env")
    users = json.loads(os.getenv("AUTH_USERS", "{}"))
    if not users:
        raise ValueError("请先配置 AUTH_USERS，再为这些登录账号准备订单")
    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    async with await AsyncConnection.connect(database_url) as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS customer_chat")
        await setup_orders(conn)
        now = datetime.now(timezone.utc)
        for index, user_id in enumerate(users):
            prefix = hashlib.sha256(user_id.encode()).hexdigest()[:8].upper()
            numbers = ["123" if index == 0 else "456" if index == 1 else f"DEMO-{prefix}-001",
                       f"DEMO-{prefix}-002", f"DEMO-{prefix}-003", f"DEMO-{prefix}-004"]
            for number, status, age in zip(numbers, ("paid", "shipped", "completed", "cancelled"),
                                           (timedelta(days=4), timedelta(days=1), timedelta(hours=3), timedelta(hours=2))):
                shipped = status == "shipped"
                items = [{"sku": "GC-A20-128", "name": "GC A20 手机", "spec": "128GB / 蓝色", "quantity": 1, "unit_price": "1999.00"},
                         {"sku": "EARPHONE-DEMO", "name": "蓝牙耳机", "spec": "白色", "quantity": 1, "unit_price": "199.00"}]
                events = [{"occurred_at": (now - timedelta(hours=hours)).isoformat(), "description": text}
                          for hours, text in ((2, "演示物流：包裹运输中"), (12, "演示物流：已从分拨中心发出"), (20, "演示物流：快递已揽收"))] if shipped else []
                await conn.execute("""
                    INSERT INTO customer_chat.orders
                    (order_no,user_id,status,created_at,items,total_amount,carrier,tracking_no,logistics_status,logistics_events,is_demo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
                    ON CONFLICT (order_no) DO NOTHING
                """, (number, user_id, status, now - age, Jsonb(items), "2198.00",
                      "演示快递" if shipped else None, f"DEMO-TRACK-{prefix}" if shipped else None,
                      "包裹运输中" if shipped else "尚未发货" if status == "paid" else "订单已完成" if status == "completed" else "订单已取消",
                      Jsonb(events)))
                print(f"演示订单 {user_id}: {number} / {status}")


if __name__ == "__main__":
    asyncio.run(main())
