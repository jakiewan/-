import logging
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException
from openai import APIError
from pydantic import BaseModel, Field

from .minimax_client import MiniMaxClient
from .auth import current_user, router as auth_router

app = FastAPI()
app.include_router(auth_router)
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(pattern=r"\S")


@lru_cache
def get_minimax() -> MiniMaxClient:
    return MiniMaxClient()


@app.post("/api/chat", dependencies=[Depends(current_user)])
async def chat(request: ChatRequest) -> dict[str, str]:
    try:
        return {"reply": await get_minimax().chat(request.message)}
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except APIError as exc:
        logger.warning("MiniMax 调用失败：%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="MiniMax 调用失败，请检查密钥、模型权限和服务状态") from exc
