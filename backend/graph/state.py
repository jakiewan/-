from typing import Literal, TypedDict

from langchain.agents import AgentState

Intent = Literal["咨询", "投诉", "售后", "订单查询", "转人工", "闲聊"]
BusinessRoute = Literal[
    "handle_chitchat", "retrieve_knowledge", "query_order",
    "handle_after_sales", "handle_complaint", "complex_agent",
]


class IntentTask(TypedDict):
    task_id: str
    intent: Intent
    request: str
    slots: dict[str, object]
    status: Literal["pending", "needs_info", "succeeded", "failed", "unsupported", "simulated"]
    result: dict[str, object] | None
    evidence: list[dict[str, object]]


class CustomerServiceState(AgentState):
    intent: Intent
    intent_reason: str
    intent_confidence: float
    intent_context_used: bool
    context: str
    requires_tools: bool
    tool_rounds: int
    requires_handoff: bool
    needs_clarification: bool
    clarification_question: str
    is_complex: bool
    tasks: list[IntentTask]
    business_result: dict[str, object]
    evidence: list[dict[str, object]]
    handoff_reason: str
    handoff_result: dict[str, object] | None
    actions: list[dict[str, str]]
    answer_candidate: str
    answer_valid: bool
    validation_reason: str
