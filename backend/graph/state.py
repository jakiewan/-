from typing import Literal

from langgraph.graph import MessagesState


class CustomerServiceState(MessagesState):
    intent: Literal["咨询", "投诉", "售后", "订单查询", "转人工", "闲聊"]
    context: str
    requires_tools: bool
    tool_rounds: int
    requires_handoff: bool
