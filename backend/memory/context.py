from dataclasses import dataclass
from datetime import datetime
from psycopg_pool import AsyncConnectionPool

MEMORY_NAMESPACE = ("user_memories",)


@dataclass
class UserContext:
    user_id: str
    db_pool: AsyncConnectionPool | None = None
    conversation_id: str | None = None
    conversation_version: int | None = None
    message_id: int | None = None
    received_at: datetime | None = None
    confirmation: dict | None = None
