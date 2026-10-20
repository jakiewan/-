import logging
import re
from typing import Annotated

from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from langchain_core.messages import AIMessage, SystemMessage
from langchain.tools import tool
from pydantic import Field

from ..utils.qweather_client import QWeatherClient

logger = logging.getLogger(__name__)


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


def is_weather_call_text(text: str) -> bool:
    # shortcut: 识别已复现的调用文本前缀，出现其他协议错误格式时再扩展检测。
    return bool(re.match(r"\s*(?:```[a-zA-Z]*\s*)?functions\.get_weather\s*\(", text))


@wrap_model_call
async def repair_weather_call(request: ModelRequest, handler) -> ModelResponse:
    response = await handler(request)
    message = response.result[-1]
    if message.type != "ai" or message.tool_calls or not is_weather_call_text(message.text):
        return response
    if request.runtime.context.weather_repair_attempted:
        return ModelResponse(result=[AIMessage(content="天气查询暂时未能完成，请稍后重试。")])

    # 只纠正调用协议，不从正文提取参数或执行代码。
    request.runtime.context.weather_repair_attempted = True
    logger.warning("天气工具调用被写成正文，尝试一次协议纠正")
    response = await handler(request.override(
        tools=[get_weather],
        messages=[*request.messages, message],
        system_message=SystemMessage(content=(request.system_message.text if request.system_message else "") + (
            "\n上一条回复把 functions.get_weather 写成了正文，这不是有效的工具调用。"
            "请根据本轮用户的真实请求重新处理：需要实时天气且地点明确时，使用提供的 get_weather 工具，"
            "通过结构化 tool_calls 传入参数；地点不明确时追问；用户仅要求代码示例时正常解释，不调用工具。"
            "如果本轮已经有天气工具结果，直接根据结果回答，不要重复查询。"
            "不要输出 functions.get_weather 调用文本，不得编造天气数据。"
        )),
    ))
    message = response.result[-1]
    if message.type == "ai" and not message.tool_calls and is_weather_call_text(message.text):
        logger.warning("天气工具调用协议纠正失败，返回查询未完成提示")
        return ModelResponse(result=[AIMessage(content="天气查询暂时未能完成，请稍后重试。")])
    return response
