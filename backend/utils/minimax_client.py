import os
from collections.abc import AsyncIterator
from contextlib import aclosing
from time import perf_counter

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from ..common.constants import PROJECT_ROOT
from ..graph.intent_classifier import get_intent_classifier
from ..graph.workflow import build_workflow
from ..memory.context import UserContext


class MiniMaxClient:
    def __init__(self, checkpointer: AsyncPostgresSaver, store: AsyncPostgresStore, *, retriever=None, db_pool=None):
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.getenv("MINIMAX_API_KEY")
        if not api_key:
            raise ValueError("请在项目根目录 .env 中配置 MINIMAX_API_KEY")

        model = ChatOpenAI(
            model=os.getenv("MINIMAX_MODEL"), #type:ignore
            api_key=api_key,    #type:ignore
            base_url=os.getenv("MINIMAX_BASE_URL"),
            timeout=60,
            max_retries=0,
            use_responses_api=False,
            http_socket_options=(),
        )
        self.checkpointer = checkpointer
        self.db_pool = db_pool
        self.node_labels = {
            "prepare_context": "准备本轮状态", "recognize_intent": "BERT 意图识别",
            "fallback_intent": "LLM 意图兜底",
            "handle_chitchat": "闲聊处理", "retrieve_knowledge": "知识检索 Agent",
            "rag_decide": "检索决策与证据评估", "rag_search": "执行真实知识检索", "rag_result": "整理知识证据",
            "query_order": "查询本人订单物流", "handle_after_sales": "售后入口（暂未接入）",
            "handle_complaint": "投诉建议", "complex_agent": "复杂问题子图",
            "agent_decide": "Agent 决策", "execute_tools": "执行工具（暂未接入）",
            "mark_tool_limit": "工具轮次上限", "collect_agent_result": "整理子图结果",
            "aggregate_results": "结果聚合", "check_handoff": "检查人工需求",
            "handoff": "人工转接（演示）", "generate_answer": "生成回复候选",
            "validate_answer": "校验回复", "safe_fallback": "安全兜底",
        }
        self.agent = build_workflow(
            get_intent_classifier(), model=model, checkpointer=checkpointer, store=store, retriever=retriever,
        )

    async def _input(self, message: str, thread_id: str):
        # 新图不恢复旧 Agent 的节点位置；首次仅带入已完成的历史，旧检查点保持不变。
        config = {"configurable": {"thread_id": "manual:" + thread_id}}
        history = []
        if not (await self.agent.aget_state(config)).values:
            old = await self.checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
            if old:
                messages = old.checkpoint["channel_values"].get("messages", [])
                completed = [i for i, item in enumerate(messages) if item.type == "ai" and not item.tool_calls and item.text]
                if completed:
                    history = messages[:completed[-1] + 1]
        return {"messages": [*history, HumanMessage(content=message)]}, config

    async def chat(self, message: str, thread_id: str, user_id: str) -> str:
        inputs, config = await self._input(message, thread_id)
        result = await self.agent.ainvoke(inputs, config, context=UserContext(user_id=user_id, db_pool=self.db_pool))
        reply = result["messages"][-1].text
        if not reply.strip():
            raise ValueError("MiniMax 未返回有效回复")
        return reply

    async def stream(self, message: str, thread_id: str, user_id: str) -> AsyncIterator[tuple[str, dict]]:
        inputs, config = await self._input(message, thread_id)
        node_starts = {}
        intent_data = {}
        has_text = False
        evidence = []
        orders = []
        # 只发送校验后的最终消息，分类、路由和工具规划的模型输出不会成为正文。
        async with aclosing(self.agent.astream(
            inputs, config, context=UserContext(user_id=user_id, db_pool=self.db_pool),
            stream_mode=["tasks", "updates"], subgraphs=True,
        )) as events:
            async for namespace, mode, data in events:
                if mode == "tasks":
                    name = data["name"]
                    prefix = "/".join(part.split(":")[0] for part in namespace)
                    node = {
                        "id": data["id"], "name": f"{prefix}/{name}" if prefix else name,
                        "label": self.node_labels.get(name, name),
                    }
                    if "input" in data:
                        if name == "rag_search":
                            node["tools"] = [data["input"]["decision"]["action"]]
                        node_starts[data["id"]] = perf_counter()
                        yield "node", {**node, "status": "running"}
                    else:
                        yield "node", {
                            **node, "status": "error" if data.get("error") else "done",
                            "elapsed_ms": round((perf_counter() - node_starts.pop(data["id"])) * 1000),
                        }
                elif mode == "updates" and not namespace:
                    for name, update in data.items():
                        if name == "retrieve_knowledge":
                            evidence = update.get("evidence", [])
                        if name == "query_order":
                            order = update.get("business_result", {}).get("order")
                            orders = [order] if order is not None else []
                        if name in ("recognize_intent", "fallback_intent"):
                            for field, key in (("intent", "intent"), ("intent_reason", "reason"),
                                               ("intent_confidence", "confidence"), ("intent_context_used", "context_used")):
                                if field in update:
                                    intent_data[key] = update[field]
                            yield "intent", dict(intent_data)
                        if name in ("validate_answer", "safe_fallback"):
                            if name == "validate_answer" and update.get("answer_valid") and orders:
                                yield "orders", {"orders": orders}
                            if name == "validate_answer" and update.get("answer_valid") and evidence:
                                yield "sources", {"sources": [
                                    {"label": f"{item['title']}（版本 {item['version']}）" + (" · 模拟资料" if item["simulated"] else ""),
                                     "url": item["evidence_id"], "kind": item["kind"]}
                                    for item in evidence
                                ]}
                            for message in update.get("messages", []):
                                if message.type == "ai" and message.text and not message.tool_calls:
                                    has_text = True
                                    yield "delta", {"text": message.text}
        if not has_text:
            raise ValueError("MiniMax 未返回有效回复")
        yield "progress", {"step": "save", "status": "done", "label": "检查点已保存（非节点）"}
