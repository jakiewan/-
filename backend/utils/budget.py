"""整轮共享预算：调用前保守预留，恢复时沿用已落库的消耗。"""

import asyncio
import os
from contextvars import ContextVar
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

ACTIVE_BUDGET = ContextVar('customer_turn_budget', default=None)


class BudgetExceeded(RuntimeError):
    pass


def error_kind(exc):
    if isinstance(exc, BudgetExceeded):
        return 'budget_exhausted'
    if isinstance(exc, TimeoutError) or 'Timeout' in type(exc).__name__:
        return 'timeout'
    status = getattr(exc, 'status_code', None)
    if status == 429:
        return 'rate_limited'
    if status and status >= 500:
        return 'upstream_unavailable'
    if status in (401,403):
        return 'permission_denied'
    if isinstance(exc, ValueError):
        return 'invalid_result'
    return 'internal_error'


class TurnBudget:
    def __init__(self,pool,message):
        self.pool,self.message_id=pool,message['id']
        self.received_at=message['created_at']
        self.data=dict(message['metadata'].get('budget',{}))
        self.lock=asyncio.Lock()
        self.input_price=float(os.getenv('LLM_INPUT_USD_PER_MILLION','0'))
        self.output_price=float(os.getenv('LLM_OUTPUT_USD_PER_MILLION','0'))
        self.cost_limit=float(os.getenv('TURN_MAX_USD','0'))
        if self.cost_limit and (self.input_price<=0 or self.output_price<=0):
            raise ValueError('配置费用上限时必须同时提供当前模型的输入和输出单价')

    def remaining(self):
        return float(os.getenv('TURN_TIMEOUT_SECONDS','180'))-(datetime.now(timezone.utc)-self.received_at).total_seconds()

    async def charge(self, *, input_tokens=0, output_tokens=0, calls=0, tools=0, measured=False):
        async with self.lock:
            data=dict(self.data)
            for key,value in (('input_tokens',input_tokens),('output_tokens',output_tokens),('model_calls',calls),('tool_calls',tools)):
                data[key]=data.get(key,0)+value
            if self.remaining()<=0 or data['input_tokens']+data['output_tokens']>int(os.getenv('TURN_MAX_TOKENS','120000')) or data['model_calls']>int(os.getenv('TURN_MAX_MODEL_CALLS','16')) or data['tool_calls']>int(os.getenv('TURN_MAX_TOOL_CALLS','24')):
                raise BudgetExceeded('本轮处理预算已用完，未继续执行新的操作')
            data['estimated_usd']=(data['input_tokens']*self.input_price+data['output_tokens']*self.output_price)/1e6 if self.input_price and self.output_price else None
            if self.cost_limit and data['estimated_usd']>self.cost_limit:
                raise BudgetExceeded('本轮费用预算已用完')
            if measured:
                data['usage_reports']=data.get('usage_reports',0)+1
            self.data=data
            async with self.pool.connection() as conn:
                await conn.execute("UPDATE customer_chat.conversation_messages SET metadata=jsonb_set(metadata,'{budget}',%s) WHERE id=%s",(Jsonb(data),self.message_id))


async def count_tool():
    budget=ACTIVE_BUDGET.get()
    if budget:
        await budget.charge(tools=1)


class BudgetedModel:
    def __init__(self,model):
        self.model=model

    def model_copy(self,*,update):
        return BudgetedModel(self.model.model_copy(update=update))

    async def ainvoke(self,messages,**kwargs):
        budget=ACTIVE_BUDGET.get()
        max_output=int(os.getenv('MODEL_MAX_OUTPUT_TOKENS','4096'))
        # UTF-8 字节数加消息开销是预留上界；供应商 usage 返回后再核减。
        estimated=sum(len(m.text.encode('utf-8'))+64 for m in messages)
        if estimated>int(os.getenv('MODEL_MAX_INPUT_TOKENS','40000')):
            raise BudgetExceeded('模型输入超过单次预算')
        for attempt in range(2):
            if budget:
                await budget.charge(input_tokens=estimated,output_tokens=max_output,calls=1)
            try:
                async with asyncio.timeout(min(60,budget.remaining()) if budget else 60):
                    response=await self.model.ainvoke(messages,max_tokens=max_output,**kwargs)
                usage=response.usage_metadata or {}
                if budget and usage:
                    await budget.charge(input_tokens=usage.get('input_tokens',estimated)-estimated,
                                        output_tokens=usage.get('output_tokens',max_output)-max_output,measured=True)
                return response
            except Exception as exc:
                # 超时无法确认实际用量，保留预留；仅模型读取瞬态错误有限重试一次。
                if attempt or error_kind(exc) not in ('timeout','rate_limited','upstream_unavailable') or (budget and budget.remaining()<3):
                    raise
                await asyncio.sleep(0.5)

    async def astream(self,messages,**kwargs):
        # 回复统一先校验再展示，此处不向浏览器转发未经校验的片段。
        yield await self.ainvoke(messages,**kwargs)
