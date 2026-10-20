from typing import Annotated

from langchain.tools import tool
from pydantic import Field

from ..utils.qweather_client import QWeatherClient


@tool(response_format="content_and_artifact")
async def get_weather(
    city: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")],
    adm: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")] | None = None,
) -> tuple[dict, dict]:
    """通过和风天气查询城市当前实时天气。city 填城市、区县名称或工具返回的 Location ID；adm 可填省份或上级城市用于区分重名地点。未明确地点时先询问用户；遇到 needs_clarification 时让用户确认候选地点。不支持历史天气或未来预报，也不是通用联网搜索工具。"""
    result = await QWeatherClient().current(city, adm)
    sources = []
    if result["status"] == "ok":
        sources.append({"label": result["source"], "url": result["source_url"]})
        sources.extend({"label": "数据归因", "url": url} for url in result["attributions"])
    return result, {"sources": sources}


get_weather.handle_tool_error = True
