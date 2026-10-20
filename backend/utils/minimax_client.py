import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from ..common.constants import PROJECT_ROOT
from ..memory.context import UserContext
from ..memory.prompts import user_memory_prompt
from ..tools.memory import forget_user_info, remember_user_info


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
        self.agent = create_agent(
            model=model,
            checkpointer=checkpointer,
            store=store,
            context_schema=UserContext,
            tools=[remember_user_info, forget_user_info],
            middleware=[user_memory_prompt],
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
