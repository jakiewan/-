from dataclasses import dataclass
from psycopg_pool import AsyncConnectionPool

MEMORY_NAMESPACE = ("user_memories",)


@dataclass
class UserContext:
    user_id: str
    db_pool: AsyncConnectionPool | None = None
