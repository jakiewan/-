from typing import Literal

from langgraph.graph import MessagesState

Intent = Literal["咨询", "投诉", "售后", "订单查询", "转人工", "闲聊"]


class CustomerServiceState(MessagesState):
    intent: Intent
    intent_reason: str
    context: str
    requires_tools: bool
    tool_rounds: int
    requires_handoff: bool
