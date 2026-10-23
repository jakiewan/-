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
from ..graph.workflow import build_workflow
from ..memory.context import UserContext


class MiniMaxClient:
    def __init__(self, checkpointer: AsyncPostgresSaver, store: AsyncPostgresStore, *, retriever=None, db_pool=None, model=None):
        load_dotenv(PROJECT_ROOT / ".env")
        if model is None:
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
            "prepare_context": "准备本轮状态", "recognize_intent": "LLM 意图识别",
            "handle_chitchat": "闲聊处理", "retrieve_knowledge": "固定知识检索流程",
            "rag_rewrite": "问题重写与条件提取", "faq_search": "检索 FAQ 标准答案",
            "phone_graph_search": "查询手机型号与参数关系",
            "document_search": "原查询 sparse + dense 混合检索", "hyde_generate": "生成 HyDE 检索文本",
            "hyde_search": "HyDE 混合检索", "rrf_fusion": "RRF 融合去重",
            "rag_rerank": "知识正文精排", "rag_result": "整理知识证据",
            "query_order": "订单条件核对与查询", "handle_after_sales": "售后抽槽、确认与申请",
            "handle_complaint": "投诉转人工", "complex_agent": "复杂问题子图",
            "agent_decide": "Agent 决策", "execute_tools": "执行工具（暂未接入）",
            "mark_tool_limit": "工具轮次上限", "collect_agent_result": "整理子图结果",
            "aggregate_results": "结果聚合", "check_handoff": "检查人工需求",
            "handoff": "人工客服转接", "generate_answer": "生成回复候选",
            "validate_answer": "校验回复", "safe_fallback": "安全兜底",
        }
        self.agent = build_workflow(
            model=model, checkpointer=checkpointer, store=store, retriever=retriever,
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
            raise ValueError("模型未返回有效回复")
        return reply

    def final_payload(self, state):
        result = state.get("business_result", {})
        evidence = state.get("evidence", [])
        return {
            "text": state["messages"][-1].text,
            "intent": state.get("intent"),
            "intent_details": {"intent": state.get("intent"), "source": state.get("intent_source"),
                               "reason": state.get("intent_reason"), "context_used": state.get("intent_context_used")},
            "is_complex": bool(state.get("is_complex")),
            "status": result.get("status", "ok"),
            "graph_message_id": state["messages"][-1].id,
            "orders": [result["order"]] if result.get("order") else [],
            "applications": [result["application"]] if result.get("application") else [],
            "confirmationRequired": bool(result.get("confirmation_required")),
            "confirmation": result.get('confirmation'),
            "tasks": [{'task_id': t['task_id'], 'version': t.get('version', 1), 'intent': t['intent'],
                       'status': t['status'], 'request': t['request']} for t in state.get('tasks', [])],
            "sources": [{"label": item["title"], "url": item.get("source", ""),
                         "evidence_id": item["evidence_id"]} for item in evidence],
        }

    async def stream(self, message: str, thread_id: str, user_id: str, *, run=None, context=None) -> AsyncIterator[tuple[str, dict]]:
        inputs, config = (run["inputs"], run["config"]) if run else await self._input(message, thread_id)
        node_starts = {}
        intent_data = {}
        has_text = False
        streamed_text = ""
        evidence = []
        orders = []
        applications = []
        confirmation_required = False
        # 只发送校验后的最终消息，分类、路由和工具规划的模型输出不会成为正文。
        async with aclosing(self.agent.astream(
            inputs, config, context=context or UserContext(user_id=user_id, db_pool=self.db_pool),
            stream_mode=["tasks", "updates", "custom"], subgraphs=True,
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
                        node_starts[data["id"]] = perf_counter()
                        yield "node", {**node, "status": "running"}
                    else:
                        yield "node", {
                            **node, "status": "error" if data.get("error") else "done",
                            "elapsed_ms": round((perf_counter() - node_starts.pop(data["id"])) * 1000),
                        }
                elif mode == "custom" and not namespace:
                    if data.get("kind") == "answer_delta":
                        has_text = True
                        streamed_text += data.get("text", "")
                        yield "delta", {"text": data.get("text", "")}
                elif mode == "updates" and not namespace:
                    for name, update in data.items():
                        if name == "retrieve_knowledge":
                            evidence = update.get("evidence", [])
                        if name in ("query_order", "handle_after_sales"):
                            order = update.get("business_result", {}).get("order")
                            orders = [order] if order is not None else []
                        if name == "handle_after_sales":
                            application = update.get("business_result", {}).get("application")
                            applications = [application] if application is not None else []
                            confirmation_required = update.get("business_result", {}).get("confirmation_required", False)
                        if name == "recognize_intent" or (name == "query_order" and "intent" in update):
                            for field, key in (("intent", "intent"), ("intent_reason", "reason"),
                                               ("intent_source", "source"),
                                               ("intent_context_used", "context_used")):
                                if field in update:
                                    intent_data[key] = update[field]
                            yield "intent", dict(intent_data)
                        if name in ("validate_answer", "safe_fallback"):
                            if name == "validate_answer" and update.get("answer_valid") and confirmation_required:
                                yield "after_sales_confirmation", {"required": True}
                            if name == "validate_answer" and update.get("answer_valid") and applications:
                                yield "applications", {"applications": applications}
                            if name == "validate_answer" and update.get("answer_valid") and orders:
                                yield "orders", {"orders": orders}
                            if name == "validate_answer" and update.get("answer_valid") and evidence:
                                yield "sources", {"sources": [
                                    {"label": f"{item['title']}（版本 {item['version']}）" + (" · 模拟资料" if item["simulated"] else ""),
                                     "url": item.get("source", "") if item.get("source", "").startswith(("https://", "http://")) else item["evidence_id"],
                                     "evidence_id": item["evidence_id"], "kind": item["kind"]}
                                    for item in evidence
                                ]}
                            for message in update.get("messages", []):
                                if message.type == "ai" and message.text and not message.tool_calls:
                                    has_text = True
                                    # 已经逐块流出且内容相同则不再整块推一次，避免正文重复。
                                    if message.text.strip() != streamed_text.strip():
                                        yield "delta", {"text": message.text}
        if run:
            snapshot = await self.agent.aget_state(config)
            payload = self.final_payload(snapshot.values)
            has_text = bool(payload["text"])
            yield "final", payload
        if not has_text:
            raise ValueError("模型未返回有效回复")
        yield "progress", {"step": "save", "status": "done", "label": "检查点已保存（非节点）"}
