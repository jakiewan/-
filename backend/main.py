from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()


class ChatRequest(BaseModel):
    message: str = Field(pattern=r"\S")


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, str]:
    return {"reply": "helloword"}
