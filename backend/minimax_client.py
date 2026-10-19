import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI


class MiniMaxClient:
    def __init__(self):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
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
            system_prompt="你是客服助手，请用简洁准确的中文回答，不要编造无法确认的业务信息。",
        )

    async def chat(self, message: str) -> str:
        result = await self.agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]}
        )
        reply = result["messages"][-1].text
        if not reply.strip():
            raise ValueError("MiniMax 未返回有效回复")
        return reply
