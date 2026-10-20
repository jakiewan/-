import asyncio

from langchain.agents.middleware import before_agent
from langgraph.config import get_stream_writer

from .intent_classifier import BertIntentClassifier, get_intent_classifier
from .intent_input import previous_exchange
from .state import CustomerServiceState


async def recognize_intent(state: CustomerServiceState, *, classifier: BertIntentClassifier | None = None) -> dict:
    """使用微调 BERT 分类本轮诉求，按模型配置参考上一轮完整问答。"""
    messages = [
        message
        for message in state["messages"]
        if message.type in ("human", "ai") and message.text
    ]
    if not messages or messages[-1].type != "human":
        raise ValueError("意图分类需要最后一条消息为用户消息")
    result = await asyncio.to_thread(
        (classifier or get_intent_classifier()).predict, messages[-1].text, previous_exchange(messages),
    )
    return {**result, "tool_rounds": 0}


def create_intent_middleware():
    classifier = get_intent_classifier()

    @before_agent(name=recognize_intent.__name__, state_schema=CustomerServiceState)
    async def identify(state, runtime):
        result = await recognize_intent(state, classifier=classifier)
        get_stream_writer()({
            "intent": result["intent"], "reason": result["intent_reason"],
            "confidence": result["intent_confidence"],
            "context_used": result["intent_context_used"],
        })
        return result

    return identify


async def retrieve_knowledge(state: CustomerServiceState) -> dict:
    """待实现：检索知识库，将检索结果写入 context。"""
    raise NotImplementedError("RAG 检索节点待实现")


async def generate_answer(state: CustomerServiceState) -> dict:
    """待实现：生成模型消息，追加到 messages 并更新 requires_tools。"""
    raise NotImplementedError("生成回答节点待实现")


async def execute_tools(state: CustomerServiceState) -> dict:
    """待实现：执行本轮工具调用，追加工具结果，将 tool_rounds 加 1。"""
    raise NotImplementedError("工具执行节点待实现")


async def check_handoff(state: CustomerServiceState) -> dict:
    """待实现：依据明确转人工诉求、执行结果等设置 requires_handoff。"""
    raise NotImplementedError("转人工检查节点待实现")


async def handoff(state: CustomerServiceState) -> dict:
    """待实现：对接人工客服服务，记录真实转接结果。"""
    raise NotImplementedError("转人工节点待实现")
