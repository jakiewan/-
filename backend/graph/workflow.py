"""手动编排的客服主图与复杂子图；未接通业务返回明确的能力边界。"""

from functools import partial
from typing import Literal

from langgraph.graph import END, START, StateGraph

# 兼容 IDE 直接按文件运行；模块方式运行时无需调整包路径。
if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "backend.graph"

from ..memory.context import UserContext
from ..rag.agent import build_rag_pipeline
from . import nodes
from .state import BusinessRoute, CustomerServiceState

MAX_TOOL_ROUNDS = 5


def route_intent(state: CustomerServiceState) -> BusinessRoute | Literal["handoff", "generate_answer"]:
    return route_business(state)


def route_business(state: CustomerServiceState) -> BusinessRoute | Literal["generate_answer", "handoff"]:
    """根据已识别结果选择条件边，不再调用模型重新判断。"""
    if state["needs_clarification"]:
        return "generate_answer"
    if state["intent"] == "转人工":
        return "handoff"
    if state["is_complex"]:
        return "complex_agent"
    return {
        "咨询": "retrieve_knowledge", "投诉": "handle_complaint", "售后": "handle_after_sales",
        "订单查询": "query_order", "闲聊": "handle_chitchat",
    }[state["intent"]]


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
    builder = StateGraph(CustomerServiceState, context_schema=UserContext)
    builder.add_node("agent_decide", nodes.agent_decide)  # 决定下一步是否需要调用查询工具。
    builder.add_node("execute_tools", nodes.execute_tools)  # 执行查询工具，记录结果并累计工具轮次。
    builder.add_node("mark_tool_limit", nodes.mark_tool_limit)  # 标记工具轮次耗尽，保留已有任务结果。
    builder.add_node("collect_agent_result", nodes.collect_agent_result)  # 整理结构化结果和证据，交回主图。

    builder.add_edge(START, "agent_decide")
    builder.add_conditional_edges("agent_decide", route_agent, {  # 根据追问状态、工具需求和轮次选择下一步。
        "execute_tools": "execute_tools",  # 仍需工具且未达到轮次上限，继续查询。
        "mark_tool_limit": "mark_tool_limit",  # 仍需工具但已达到轮次上限，标记未完成。
        "collect_agent_result": "collect_agent_result",  # 需要追问或无需工具，整理结果并退出子图。
    })
    builder.add_edge("execute_tools", "agent_decide")  # 工具执行后重新决策，形成查询循环。
    builder.add_edge("mark_tool_limit", "collect_agent_result")
    builder.add_edge("collect_agent_result", END)
    return builder.compile()


def build_workflow(*, model=None, checkpointer=None, store=None, retriever=None):
    """父图统一接入模型和会话存储；不提供依赖时仍可独立导出结构。"""
    builder = StateGraph(CustomerServiceState, context_schema=UserContext)
    builder.add_node("prepare_context", nodes.prepare_context)  # 准备本轮上下文，重置临时结果并保留有效任务槽位。
    builder.add_node("recognize_intent", partial(nodes.recognize_intent, model=model))  # LLM 识别单个主意图，结果由 Schema 校验。
    builder.add_node("handle_chitchat", nodes.handle_chitchat)  # 准备普通闲聊上下文；显式档案操作和天气暂缓。
    builder.add_node("retrieve_knowledge", partial(nodes.retrieve_knowledge, pipeline=build_rag_pipeline(model, retriever)))  # 固定先查 FAQ，未命中再查文档并融合、精排。
    builder.add_node("query_order", partial(nodes.query_order, model=model))  # 抽取可选单号，查询当前登录账号名下的订单。
    builder.add_node("handle_after_sales", partial(nodes.handle_after_sales, model=model))  # 节点内抽槽、补参，用户确认后提交售后申请。
    builder.add_node("handle_complaint", nodes.handle_complaint)  # 整理投诉诉求、建议和证据。
    builder.add_node("complex_agent", build_complex_agent())  # 将编译后的复杂问题子图作为主图节点。
    builder.add_node("aggregate_results", nodes.aggregate_results)  # 汇总业务结果、证据、动作和追问内容。
    builder.add_node("check_handoff", nodes.check_handoff)  # 根据结果和风险判断是否需要转人工。
    builder.add_node("handoff", nodes.handoff)  # 获取人工接管结果，区分未接入、失败和成功。
    builder.add_node("generate_answer", partial(nodes.generate_answer, model=model))  # 统一生成候选客服回复，保留页面动作。
    builder.add_node("validate_answer", nodes.validate_answer)  # 校验回复事实、人工状态和动作依据。
    builder.add_node("safe_fallback", nodes.safe_fallback)  # 校验失败时，依据可确认事实输出安全回复并结束。

    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "recognize_intent")
    business_paths = {name: name for name in (
        "handle_chitchat", "retrieve_knowledge", "query_order", "handle_after_sales",
        "handle_complaint", "complex_agent", "generate_answer", "handoff",
    )}
    builder.add_conditional_edges("recognize_intent", route_intent, business_paths)
    for branch in (
        "handle_chitchat", "retrieve_knowledge", "query_order",
        "handle_after_sales", "handle_complaint", "complex_agent",
    ):
        if branch != "query_order":
            builder.add_edge(branch, "aggregate_results")
    builder.add_conditional_edges("query_order", lambda state: "retrieve_knowledge" if state["intent"] == "咨询" else "aggregate_results", {
        "retrieve_knowledge": "retrieve_knowledge", "aggregate_results": "aggregate_results",
    })
    builder.add_edge("aggregate_results", "check_handoff")
    builder.add_conditional_edges("check_handoff", route_handoff, {  # 按 requires_handoff 决定是否先处理人工接管。
        "handoff": "handoff",  # 需要人工接管，先取得接管结果。
        "generate_answer": "generate_answer",  # 无需人工接管，直接生成候选回复。
    })
    builder.add_edge("handoff", "generate_answer")
    builder.add_edge("generate_answer", "validate_answer")
    builder.add_conditional_edges("validate_answer", route_validation, {  # 按 answer_valid 决定结束或安全兜底。
        END: END,  # 回复校验通过，结束本轮流程。
        "safe_fallback": "safe_fallback",  # 回复校验失败，输出安全回复后结束。
    })
    builder.add_edge("safe_fallback", END)
    return builder.compile(checkpointer=checkpointer, store=store)


if __name__ == "__main__":
    build_workflow().get_graph().print_ascii()
    build_complex_agent().get_graph().print_ascii()
