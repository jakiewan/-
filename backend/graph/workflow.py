"""独立的客服主图与复杂问题子图骨架；尚未接入聊天 API，业务节点明确报待实现。"""

from functools import partial
from typing import Literal

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import BusinessRoute, CustomerServiceState

# 初始阈值尚待业务样本验证，不表示 BERT 分数已经校准。
INTENT_CONFIDENCE_THRESHOLD = 0.85
MAX_TOOL_ROUNDS = 5


def route_intent(state: CustomerServiceState) -> Literal["fallback_intent", "handoff", "select_business_route"]:
    if state["intent_confidence"] < INTENT_CONFIDENCE_THRESHOLD:
        return "fallback_intent"
    if state["intent"] == "转人工":
        return "handoff"
    return "select_business_route"


def route_fallback(state: CustomerServiceState) -> Literal["generate_answer", "handoff", "select_business_route"]:
    if state["needs_clarification"]:
        return "generate_answer"
    if state["intent"] == "转人工":
        return "handoff"
    return "select_business_route"


def route_business(state: CustomerServiceState) -> BusinessRoute | Literal["generate_answer"]:
    if state["needs_clarification"]:
        return "generate_answer"
    return state["business_route"]


def route_agent(state: CustomerServiceState) -> Literal["execute_tools", "mark_tool_limit", "collect_agent_result"]:
    if state["needs_clarification"] or not state["requires_tools"]:
        return "collect_agent_result"
    if state["tool_rounds"] >= MAX_TOOL_ROUNDS:
        return "mark_tool_limit"
    return "execute_tools"


def route_handoff(state: CustomerServiceState) -> Literal["handoff", "generate_answer"]:
    return "handoff" if state["requires_handoff"] else "generate_answer"


def route_validation(state: CustomerServiceState) -> Literal["__end__", "safe_fallback"]:
    return END if state["answer_valid"] else "safe_fallback"


def build_complex_agent():
    """查询工具循环只返回结构化结果；不配置独立检查点，不生成最终客服回复。"""
    builder = StateGraph(CustomerServiceState)
    builder.add_node("agent_decide", nodes.agent_decide)
    builder.add_node("execute_tools", nodes.execute_tools)
    builder.add_node("mark_tool_limit", nodes.mark_tool_limit)
    builder.add_node("collect_agent_result", nodes.collect_agent_result)

    builder.add_edge(START, "agent_decide")
    builder.add_conditional_edges("agent_decide", route_agent, {
        "execute_tools": "execute_tools",
        "mark_tool_limit": "mark_tool_limit",
        "collect_agent_result": "collect_agent_result",
    })
    builder.add_edge("execute_tools", "agent_decide")
    builder.add_edge("mark_tool_limit", "collect_agent_result")
    builder.add_edge("collect_agent_result", END)
    return builder.compile()


def build_workflow(classifier=None):
    """只编译骨架；不替换现有 create_agent，不连接模型、业务接口或会话存储。"""
    builder = StateGraph(CustomerServiceState)
    builder.add_node("prepare_context", nodes.prepare_context)
    builder.add_node("recognize_intent", partial(nodes.recognize_intent, classifier=classifier))
    builder.add_node("fallback_intent", nodes.fallback_intent)
    builder.add_node("select_business_route", nodes.select_business_route)
    builder.add_node("handle_chitchat", nodes.handle_chitchat)
    builder.add_node("retrieve_knowledge", nodes.retrieve_knowledge)
    builder.add_node("query_order", nodes.query_order)
    builder.add_node("handle_after_sales", nodes.handle_after_sales)
    builder.add_node("handle_complaint", nodes.handle_complaint)
    builder.add_node("complex_agent", build_complex_agent())
    builder.add_node("aggregate_results", nodes.aggregate_results)
    builder.add_node("check_handoff", nodes.check_handoff)
    builder.add_node("handoff", nodes.handoff)
    builder.add_node("generate_answer", nodes.generate_answer)
    builder.add_node("validate_answer", nodes.validate_answer)
    builder.add_node("safe_fallback", nodes.safe_fallback)

    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "recognize_intent")
    builder.add_conditional_edges("recognize_intent", route_intent, {
        "fallback_intent": "fallback_intent",
        "handoff": "handoff",
        "select_business_route": "select_business_route",
    })
    builder.add_conditional_edges("fallback_intent", route_fallback, {
        "generate_answer": "generate_answer",
        "handoff": "handoff",
        "select_business_route": "select_business_route",
    })
    builder.add_conditional_edges("select_business_route", route_business, {
        "handle_chitchat": "handle_chitchat",
        "retrieve_knowledge": "retrieve_knowledge",
        "query_order": "query_order",
        "handle_after_sales": "handle_after_sales",
        "handle_complaint": "handle_complaint",
        "complex_agent": "complex_agent",
        "generate_answer": "generate_answer",
    })
    for branch in (
        "handle_chitchat", "retrieve_knowledge", "query_order",
        "handle_after_sales", "handle_complaint", "complex_agent",
    ):
        builder.add_edge(branch, "aggregate_results")
    builder.add_edge("aggregate_results", "check_handoff")
    builder.add_conditional_edges("check_handoff", route_handoff, {
        "handoff": "handoff",
        "generate_answer": "generate_answer",
    })
    builder.add_edge("handoff", "generate_answer")
    builder.add_edge("generate_answer", "validate_answer")
    builder.add_conditional_edges("validate_answer", route_validation, {
        END: END,
        "safe_fallback": "safe_fallback",
    })
    builder.add_edge("safe_fallback", END)
    return builder.compile()


if __name__ == "__main__":
    print(build_workflow().get_graph().draw_mermaid())
    print(build_complex_agent().get_graph().draw_mermaid())
