"""DeepSeek 模型接入，复用现有客服 Graph、检查点及流式事件。"""

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from ..common.constants import PROJECT_ROOT
from .minimax_client import MiniMaxClient


class DeepSeekClient(MiniMaxClient):
    def __init__(self, checkpointer, store, *, retriever=None, db_pool=None):
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("请在项目根目录 .env 中配置 DEEPSEEK_API_KEY")

        model = ChatOpenAI(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            extra_body={"thinking": {"type": "disabled"}},
            timeout=60,
            max_retries=0,
            use_responses_api=False,
            http_socket_options=(),
        )
        super().__init__(checkpointer, store, retriever=retriever, db_pool=db_pool, model=model)
