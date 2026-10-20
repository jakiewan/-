from .state import CustomerServiceState


async def recognize_intent(state: CustomerServiceState) -> dict:
    """待实现：识别本轮意图，将 tool_rounds 重置为 0。"""
    raise NotImplementedError("意图识别节点待实现")


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
