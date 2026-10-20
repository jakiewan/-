import os
import re
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from langchain_core.tools import ToolException

from ..common.constants import PROJECT_ROOT


class QWeatherClient:
    def __init__(self):
        load_dotenv(PROJECT_ROOT / ".env")
        self.host = os.getenv("QWEATHER_API_HOST", "").strip().removeprefix("https://").rstrip("/")
        self.api_key = os.getenv("QWEATHER_API_KEY", "").strip()
        if not self.host or not self.api_key:
            raise ToolException("天气服务尚未配置，暂时无法查询天气。")
        if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.qweatherapi\.com", self.host):
            raise ToolException("天气服务地址配置错误，请联系管理员。")

    @staticmethod
    async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
        try:
            response = await client.get(path, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ToolException(f"和风天气请求失败（HTTP {exc.response.status_code}），请稍后重试或联系管理员。") from None
        except httpx.HTTPError:
            raise ToolException("和风天气网络连接失败或超时，请稍后重试。") from None
        except ValueError:
            raise ToolException("和风天气返回了无效数据，请稍后重试。") from None
        if not isinstance(data, dict):
            raise ToolException("和风天气返回了无效数据，请稍后重试。")
        return data

    async def current(self, city: str, adm: str | None = None) -> dict:
        city = city.strip()
        params = {"location": city, "lang": "zh", "number": 10}
        if adm:
            params["adm"] = adm.strip()
        async with httpx.AsyncClient(
            base_url=f"https://{self.host}", headers={"X-QW-Api-Key": self.api_key}, timeout=10,
        ) as client:
            locations = await self._get(client, "/geo/v2/city/lookup", params)
            if locations.get("code") == "404" or locations.get("location") == []:
                raise ToolException("没有找到该城市，请提供更准确的城市或区县名称。")
            if locations.get("code") != "200":
                raise ToolException("和风天气城市查询失败，请稍后重试或检查服务权限。")
            try:
                candidates = locations["location"]
                exact = [item for item in candidates if item["id"] == city or item["name"] == city.removesuffix("市")]
                candidates = exact or candidates
                if len(candidates) != 1:
                    return {
                        "status": "needs_clarification",
                        "message": "找到多个同名或相近地点，请让用户确认省份或具体城市后重试。",
                        "candidates": [
                            {"city": item["name"], "province": item["adm1"], "country": item["country"], "location_id": item["id"]}
                            for item in candidates
                        ],
                    }
                location = candidates[0]
                # v1 路径按纬度、经度排列，精度要求为小数点后两位。
                point = f"{float(location['lat']):.2f}/{float(location['lon']):.2f}"
                data = await self._get(client, f"/weather/v1/current/{point}", {"lang": "zh", "localTime": "true"})
                return {
                    "status": "ok", "source": "和风天气",
                    "location": {"city": location["name"], "province": location["adm1"], "country": location["country"], "timezone": location["tz"]},
                    "queried_at": datetime.now(timezone.utc).isoformat(),
                    "condition": data["condition"]["text"],
                    "temperature": data["temperature"], "feels_like": data["feelsLike"],
                    "humidity_percent": round(data["humidity"] * 100, 1), "wind": data["wind"],
                    "source_url": location["fxLink"],
                    "attributions": data["metadata"]["attributions"],
                }
            except (KeyError, TypeError, ValueError, IndexError):
                raise ToolException("和风天气返回的数据不完整，请稍后重试。") from None
