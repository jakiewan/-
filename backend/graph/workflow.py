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


def build_workflow(classifier=None):
    # 定义节点间共享的状态结构，包含消息、意图、工具轮次和转人工标记。
    builder = StateGraph(CustomerServiceState)

    # 意图识别：使用本地微调 BERT；partial 预先绑定分类器，执行时由图传入 state。
    builder.add_node("recognize_intent", partial(recognize_intent, classifier=classifier))
    # 知识检索：查询商品知识、售后政策等，将结果写入 context（待实现）。
    builder.add_node("retrieve_knowledge", retrieve_knowledge)
    # 生成回答：结合上下文生成消息，并判断是否需要调用工具（待实现）。
    builder.add_node("generate_answer", generate_answer)
    # 工具执行：执行模型请求的工具，追加结果并增加工具调用轮次（待实现）。
    builder.add_node("execute_tools", execute_tools)
    # 转人工检查：判断本轮是否需要人工处理，更新转人工标记（待实现）。
    builder.add_node("check_handoff", check_handoff)
    # 转人工：对接人工客服服务并记录真实转接结果（待实现）。
    builder.add_node("handoff", handoff)

    # 入口边：START 是图的虚拟入口，每轮先执行意图识别。
    builder.add_edge(START, "recognize_intent")
    # 条件边：route_intent 返回哪个节点名称，本轮就进入哪个分支。
    builder.add_conditional_edges("recognize_intent", route_intent, {
        # 当前骨架将咨询、投诉、售后和订单查询都送入检索，业务分流尚待细化。
        "retrieve_knowledge": "retrieve_knowledge",
        # 闲聊跳过检索，直接生成回答。
        "generate_answer": "generate_answer",
        # 用户明确要求转人工时，直接进入转人工检查。
        "check_handoff": "check_handoff",
    })
    # 普通边：检索完成后，使用检索结果生成回答。
    builder.add_edge("retrieve_knowledge", "generate_answer")
    # 条件边：根据模型是否需要工具，以及本轮已执行的工具轮次分流。
    builder.add_conditional_edges("generate_answer", route_answer, {
        # 需要工具且不足 5 轮时，进入工具执行。
        "execute_tools": "execute_tools",
        # 不需要工具或已达到轮次上限时，进入转人工检查。
        "check_handoff": "check_handoff",
    })
    # 循环回边：工具结果交回模型，生成最终回答或决定下一轮工具调用。
    builder.add_edge("execute_tools", "generate_answer")
    # 条件边：需要人工，或仍需工具但已用满 5 轮时转人工，否则结束本轮。
    builder.add_conditional_edges("check_handoff", route_handoff, {
        "handoff": "handoff",
        # END 是图的虚拟终点，表示本轮图执行结束。
        END: END,
    })
    # 转人工节点完成后，结束本轮图执行。
    builder.add_edge("handoff", END)
    # 将节点、普通边和条件边编译为可调用的图；此处未配置检查点存储。
    return builder.compile()


if __name__ == "__main__":
    print(build_workflow().get_graph().draw_mermaid())
