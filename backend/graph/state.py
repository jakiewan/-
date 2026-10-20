from typing import Literal

from langchain.agents import AgentState

Intent = Literal["咨询", "投诉", "售后", "订单查询", "转人工", "闲聊"]


class CustomerServiceState(AgentState):
    intent: Intent
    intent_reason: str
    intent_confidence: float
    intent_context_used: bool
    context: str
    requires_tools: bool
    tool_rounds: int
    requires_handoff: bool
