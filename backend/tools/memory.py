from typing import Annotated, Literal

from langchain.tools import ToolRuntime, tool
from pydantic import Field

from ..memory.context import MEMORY_NAMESPACE, UserContext

MemoryField = Literal["name", "language", "response_style"]
MemoryValue = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


@tool
async def remember_user_info(
    runtime: ToolRuntime[UserContext],
    name: MemoryValue | None = None,
    language: MemoryValue | None = None,
    response_style: MemoryValue | None = None,
) -> str:
    """一次保存当前用户明确要求记住或更正的全部信息：name 姓名、language 语言、response_style 回答偏好。未提供的字段保持不变。"""
    updates = {
        key: value.strip()
        for key, value in {"name": name, "language": language, "response_style": response_style}.items()
        if value is not None
    }
    if not updates:
        return "未提供需要保存的档案信息。"
    store = runtime.store
    assert store is not None
    async with runtime.context.memory_lock:
        item = await store.aget(MEMORY_NAMESPACE, runtime.context.user_id)
        profile = dict(item.value) if item else {}
        profile.update(updates)
        await store.aput(MEMORY_NAMESPACE, runtime.context.user_id, profile)
    return "已保存长期档案字段：" + "、".join(updates) + "。"


@tool
async def forget_user_info(field: MemoryField, runtime: ToolRuntime[UserContext]) -> str:
    """删除当前用户指定的长期档案字段。只删除长期档案，不删除原始聊天记录。"""
    store = runtime.store
    assert store is not None
    async with runtime.context.memory_lock:
        item = await store.aget(MEMORY_NAMESPACE, runtime.context.user_id)
        profile = dict(item.value) if item else {}
        profile.pop(field, None)
        await store.aput(MEMORY_NAMESPACE, runtime.context.user_id, profile)
    return f"已删除长期档案字段：{field}；原始聊天记录仍保留。"
