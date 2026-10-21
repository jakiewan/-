import asyncio
from dataclasses import dataclass, field
from psycopg_pool import AsyncConnectionPool

MEMORY_NAMESPACE = ("user_memories",)


@dataclass
class UserContext:
    user_id: str
    db_pool: AsyncConnectionPool | None = None
    # 避免同一轮并行工具调用覆盖同一份档案。
    memory_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    weather_repair_attempted: bool = False
