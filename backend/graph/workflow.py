"""客服工作流骨架；意图识别已实现，其余业务节点待实现。"""

from functools import partial
from typing import Literal

from langgraph.graph import END, START, StateGraph

from .nodes import (
    check_handoff,
    execute_tools,
    generate_answer,
    handoff,
    recognize_intent,
    retrieve_knowledge,
)
from .state import CustomerServiceState

MAX_TOOL_ROUNDS = 5


def route_intent(state: CustomerServiceState) -> Literal["retrieve_knowledge", "generate_answer", "check_handoff"]:
    if state["intent"] == "闲聊":
        return "generate_answer"
    if state["intent"] == "转人工":
        return "check_handoff"
    return "retrieve_knowledge"


def route_answer(state: CustomerServiceState) -> Literal["execute_tools", "check_handoff"]:
    if state["requires_tools"] and state["tool_rounds"] < MAX_TOOL_ROUNDS:
        return "execute_tools"
    return "check_handoff"


def route_handoff(state: CustomerServiceState) -> Literal["handoff", "__end__"]:
    exhausted = state.get("requires_tools", False) and state["tool_rounds"] >= MAX_TOOL_ROUNDS
    if state["requires_handoff"] or exhausted:
        return "handoff"
    return END


def build_workflow(model=None):
    builder = StateGraph(CustomerServiceState)
    builder.add_node("recognize_intent", partial(recognize_intent, model=model))
    builder.add_node("retrieve_knowledge", retrieve_knowledge)
    builder.add_node("generate_answer", generate_answer)
    builder.add_node("execute_tools", execute_tools)
    builder.add_node("check_handoff", check_handoff)
    builder.add_node("handoff", handoff)

    builder.add_edge(START, "recognize_intent")
    builder.add_conditional_edges("recognize_intent", route_intent, {
        "retrieve_knowledge": "retrieve_knowledge",
        "generate_answer": "generate_answer",
        "check_handoff": "check_handoff",
    })
    builder.add_edge("retrieve_knowledge", "generate_answer")
    builder.add_conditional_edges("generate_answer", route_answer, {
        "execute_tools": "execute_tools",
        "check_handoff": "check_handoff",
    })
    builder.add_edge("execute_tools", "generate_answer")
    builder.add_conditional_edges("check_handoff", route_handoff, {
        "handoff": "handoff",
        END: END,
    })
    builder.add_edge("handoff", END)
    return builder.compile()


if __name__ == "__main__":
    print(build_workflow().get_graph().draw_mermaid())
