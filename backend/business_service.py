"""本地业务事实、政策审核和申请状态管理；仅登录坐席可维护来源。"""

from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator
from psycopg.types.json import Jsonb


class SignedItem(BaseModel):
    sku: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    signed_at: datetime | None = None
    received_quantity: int = Field(ge=0)
    eligible_quantity: int = Field(ge=0)
    checks: dict[str, bool | None] = Field(default_factory=dict, max_length=20)

    @model_validator(mode='after')
    def valid(self):
        if self.signed_at and (self.signed_at.tzinfo is None or self.signed_at > datetime.now(timezone.utc)):
            raise ValueError('签收时间必须带时区且不能在未来')
        if self.eligible_quantity > self.received_quantity:
            raise ValueError('可申请数量不能超过签收数量')
        return self


class OrderFacts(BaseModel):
    expected_version: int = Field(ge=0)
    market: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=64)
    purchased_at: datetime
    source: str = Field(min_length=1, max_length=500)
    is_demo: bool
    items: list[SignedItem] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def valid(self):
        if self.purchased_at.tzinfo is None or self.purchased_at > datetime.now(timezone.utc):
            raise ValueError('购买时间必须带时区且不能在未来')
        if len({i.sku for i in self.items}) != len(self.items):
            raise ValueError('商品签收事实不得重复；分批签收应提供已核实的累计数量及最早签收时间，避免延长先签收商品的期限')
        return self


class Policy(BaseModel):
    version: int = Field(ge=1)
    action: Literal['return', 'exchange', 'repair']
    reason_kind: Literal['no_reason', 'quality']
    category: str = Field(min_length=1, max_length=64)
    market: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=64)
    effective_from: date
    effective_until: date
    duration: int = Field(ge=1, le=3650)
    unit: Literal['hours', 'days', 'months', 'years']
    timezone: str = 'Asia/Shanghai'
    honor_notice: bool = True
    conditions: str = Field(min_length=1, max_length=2000)
    required_checks: list[str] = Field(default_factory=list, max_length=20)
    source: str = Field(min_length=1, max_length=500)
    is_demo: bool

    @model_validator(mode='after')
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError('时区无效') from None
        if self.effective_until < self.effective_from:
            raise ValueError('政策日期范围无效')
        return self


async def audit(conn, actor, action, resource, data):
    await conn.execute('INSERT INTO customer_chat.business_audit (actor,action,resource,data) VALUES (%s,%s,%s,%s)',
                       (actor, action, resource, Jsonb(data)))


class ApplicationStatus(BaseModel):
    expected_status: str
    status: Literal['approved', 'rejected', 'completed', 'cancelled']
    note: str = Field(min_length=1, max_length=1000)
