import os
from collections.abc import AsyncIterator
from contextlib import aclosing
from time import perf_counter

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from ..common.constants import PROJECT_ROOT
from ..graph.nodes import create_intent_middleware
from ..memory.context import UserContext
from ..memory.prompts import user_memory_prompt
from ..tools.memory import forget_user_info, remember_user_info
from ..tools.weather import get_weather


class MiniMaxClient:
    def __init__(self, checkpointer: AsyncPostgresSaver, store: AsyncPostgresStore):
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
        intent_middleware = create_intent_middleware()
        self.node_labels = {
            f"{intent_middleware.name}.before_agent": "意图识别",
            "model": "生成回复",
            "tools": "调用工具",
        }
        self.agent = create_agent(
            model=model,
            checkpointer=checkpointer,
            store=store,
            context_schema=UserContext,
            tools=[remember_user_info, forget_user_info, get_weather],
            middleware=[intent_middleware, user_memory_prompt],
        )

    async def chat(self, message: str, thread_id: str, user_id: str) -> str:
        # shortcut: 当前保留完整对话状态，长对话接近模型上下文上限时需增加裁剪或摘要。
        result = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            {"configurable": {"thread_id": thread_id}},
            context=UserContext(user_id=user_id),
        )
        reply = result["messages"][-1].text
        if not reply.strip():
            raise ValueError("MiniMax 未返回有效回复")
        return reply

    async def stream(self, message: str, thread_id: str, user_id: str) -> AsyncIterator[tuple[str, dict]]:
        config = {"configurable": {"thread_id": thread_id}}
        has_text = False
        node_starts = {}
        async with aclosing(self.agent.astream(
            {"messages": [{"role": "user", "content": message}]},
            config,
            context=UserContext(user_id=user_id),
            stream_mode=["messages", "tasks", "custom"],
        )) as events:
            async for mode, data in events:
                if mode == "messages":
                    chunk, metadata = data
                    if metadata.get("langgraph_node") == "model" and chunk.type == "AIMessageChunk" and chunk.text:
                        has_text = True
                        yield "delta", {"text": chunk.text}
                elif mode == "custom":
                    yield "intent", data
                elif mode == "tasks":
                    node = {
                        "id": data["id"], "name": data["name"],
                        "label": self.node_labels.get(data["name"], data["name"]),
                    }
                    if "input" in data:
                        node_starts[data["id"]] = perf_counter()
                        if data["name"] == "tools":
                            node["tools"] = [call["name"] for call in data["input"]]
                        yield "node", {**node, "status": "running"}
                        if data["name"] == "tools":
                            for call in data["input"]:
                                yield "progress", {
                                    "step": f"tool:{data['id']}:{call['id']}",
                                    "status": "running", "label": "执行工具：" + call["name"],
                                }
                    else:
                        yield "node", {
                            **node, "status": "error" if data["error"] else "done",
                            "elapsed_ms": round((perf_counter() - node_starts.pop(data["id"])) * 1000),
                        }
                        if data["name"] == "tools":
                            for item in data["result"].get("messages", []):
                                yield "progress", {
                                    "step": f"tool:{data['id']}:{item.tool_call_id}",
                                    "status": "error" if item.status == "error" else "done",
                                    "label": "执行工具：" + item.name,
                                }
                                if item.artifact and item.artifact.get("sources"):
                                    yield "sources", {"sources": item.artifact["sources"]}
        if not has_text:
            raise ValueError("MiniMax 未返回有效回复")
        # astream 正常结束后，检查点写入也已完成。
        yield "progress", {"step": "save", "status": "done", "label": "检查点已保存（非节点）"}
