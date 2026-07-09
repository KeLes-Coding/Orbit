from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class ToolExecutionResult:
    # 单次工具执行的统一返回结构，后续可直接挂到 SSE 事件和 message metadata。
    tool_call_id: str | None
    name: str
    args: Any
    output: str
    is_error: bool = False


class GetWeatherArgs(BaseModel):
    # 天气工具只要求地点字符串，地理编码和天气查询由后端内部完成。
    location: str = Field(description="需要查询天气的城市或地区名称")


class WebFetchArgs(BaseModel):
    # 单页抓取工具：优先返回正文文本，默认限制字符数避免撑爆上下文。
    url: str = Field(description="需要抓取的网页 URL")
    max_chars: int = Field(default=6000, ge=500, le=20000, description="返回文本的最大字符数")


class WebSearchArgs(BaseModel):
    # 搜索工具返回简洁结果列表，结果详情可再配合 webfetch 使用。
    query: str = Field(description="搜索关键词")
    max_results: int = Field(default=5, ge=1, le=10, description="返回结果数量上限")


class OrbitToolRuntime:
    # 这一层只负责“声明工具 + 执行工具”，不承担模型循环逻辑。
    USER_AGENT = "OrbitToolRuntime/1.0"

    def __init__(self) -> None:
        self._tool_specs = [
            StructuredTool.from_function(
                coroutine=self.getweather,
                name="getweather",
                description="查询某个城市或地区的当前天气情况，包括温度、风速和天气概况。",
                args_schema=GetWeatherArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.webfetch,
                name="webfetch",
                description="抓取指定网页的正文文本内容，适合读取文章、文档页面或官网说明。",
                args_schema=WebFetchArgs,
            ),
            StructuredTool.from_function(
                coroutine=self.websearch,
                name="websearch",
                description="执行网页搜索并返回简洁结果列表，适合先找相关网页再进一步抓取。",
                args_schema=WebSearchArgs,
            ),
        ]
        self._tool_map = {tool.name: tool for tool in self._tool_specs}

    def get_langchain_tools(self) -> list[StructuredTool]:
        # 给 LangChain/模型绑定工具 schema 时直接复用这一份定义。
        return list(self._tool_specs)

    def register_tools(self, tools: list[StructuredTool]) -> None:
        """注册额外的工具（如 workspace 工具），扩展 tool_map。

        Agent graph 会使用此方法把 run 级别的 workspace 工具注入到运行时。
        """
        for t in tools:
            self._tool_map[t.name] = t
            # 同名工具按“替换”而不是“追加”处理，避免长生命周期 runtime
            # 在多次 agent 执行后堆积出重复 schema。
            for idx, existing in enumerate(self._tool_specs):
                if existing.name == t.name:
                    self._tool_specs[idx] = t
                    break
            else:
                self._tool_specs.append(t)

    async def execute_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[ToolExecutionResult]:
        # tool_calls 来自模型输出，先做参数解析，再按名字分发到具体工具。
        results: list[ToolExecutionResult] = []
        for tool_call in tool_calls:
            name = str(tool_call.get("name") or "").strip()
            if not name:
                continue
            tool = self._tool_map.get(name)
            if tool is None:
                results.append(
                    ToolExecutionResult(
                        tool_call_id=self._coerce_optional_str(tool_call.get("id")),
                        name=name,
                        args=tool_call.get("args"),
                        output=f"工具 `{name}` 不存在",
                        is_error=True,
                    )
                )
                continue

            parsed_args = self._coerce_tool_args(tool_call.get("args"))
            try:
                output = await tool.ainvoke(parsed_args)
                results.append(
                    ToolExecutionResult(
                        tool_call_id=self._coerce_optional_str(tool_call.get("id")),
                        name=name,
                        args=parsed_args,
                        output=str(output),
                    )
                )
            except Exception as exc:
                results.append(
                    ToolExecutionResult(
                        tool_call_id=self._coerce_optional_str(tool_call.get("id")),
                        name=name,
                        args=parsed_args,
                        output=f"工具执行失败：{exc}",
                        is_error=True,
                    )
                )
        return results

    async def getweather(self, location: str) -> str:
        # 使用 open-meteo 免费接口：先地理编码，再查询当前天气。
        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": self.USER_AGENT},
            follow_redirects=True,
        ) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "zh", "format": "json"},
            )
            geo.raise_for_status()
            geo_data = geo.json()
            results = geo_data.get("results") or []
            if not results:
                return f"未找到地点：{location}"

            top = results[0]
            weather = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": top["latitude"],
                    "longitude": top["longitude"],
                    "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "timezone": "auto",
                },
            )
            weather.raise_for_status()
            weather_data = weather.json().get("current") or {}

        city = top.get("name") or location
        country = top.get("country") or ""
        weather_text = self._weather_code_to_text(weather_data.get("weather_code"))
        temperature = weather_data.get("temperature_2m")
        apparent = weather_data.get("apparent_temperature")
        wind_speed = weather_data.get("wind_speed_10m")
        return (
            f"{city}{f'，{country}' if country else ''} 当前天气：{weather_text}；"
            f"气温 {temperature}°C，体感 {apparent}°C，风速 {wind_speed} km/h。"
        )

    async def webfetch(self, url: str, max_chars: int = 6000) -> str:
        # 网页抓取优先走直连；如果站点正文难以提取，再退回 jina reader 文本代理。
        normalized_url = self._normalize_url(url)
        async with httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": self.USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(normalized_url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            text = response.text
            if "html" in content_type:
                text = self._html_to_text(text)
            if len(text.strip()) < 120:
                reader = await client.get(f"https://r.jina.ai/http://{normalized_url.removeprefix('https://').removeprefix('http://')}")
                reader.raise_for_status()
                text = reader.text

        text = text.strip()
        if len(text) > max_chars:
            text = f"{text[:max_chars]}\n\n...[内容已截断]"
        return f"网页：{normalized_url}\n\n{text}"

    async def websearch(self, query: str, max_results: int = 5) -> str:
        # 搜索先用 DuckDuckGo HTML 结果页，返回标题 + 链接 + 摘要的轻量列表。
        async with httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": self.USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            response.raise_for_status()
            html = response.text

        items = self._extract_search_results(html, limit=max_results)
        if not items:
            return f"没有找到与 `{query}` 相关的搜索结果。"

        lines = [f"关于 `{query}` 的搜索结果："]
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. {item['title']}")
            lines.append(f"链接：{item['url']}")
            if item["snippet"]:
                lines.append(f"摘要：{item['snippet']}")
        return "\n".join(lines)

    def _coerce_tool_args(self, raw_args: Any) -> Any:
        # 模型有时返回 JSON 字符串，有时直接返回 dict，这里先统一成可调用参数对象。
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            text = raw_args.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"input": text}
            return parsed if isinstance(parsed, dict) else {"input": parsed}
        return {}

    def _normalize_url(self, url: str) -> str:
        value = url.strip()
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        parsed = urlparse(value)
        if not parsed.netloc:
            raise ValueError(f"无效 URL：{url}")
        return value

    def _extract_search_results(self, html: str, *, limit: int) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
            r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
            flags=re.DOTALL,
        )
        for match in pattern.finditer(html):
            if len(items) >= limit:
                break
            items.append(
                {
                    "title": self._clean_html_fragment(match.group("title")),
                    "url": unescape(match.group("url")),
                    "snippet": self._clean_html_fragment(match.group("snippet")),
                }
            )
        return items

    def _html_to_text(self, html: str) -> str:
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p\\s*>", "\n\n", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _clean_html_fragment(self, text: str) -> str:
        return self._html_to_text(text).replace("\n", " ").strip()

    def _weather_code_to_text(self, code: Any) -> str:
        mapping = {
            0: "晴朗",
            1: "大体晴朗",
            2: "局部多云",
            3: "阴天",
            45: "雾",
            48: "冻雾",
            51: "毛毛雨",
            53: "中等毛毛雨",
            55: "浓毛毛雨",
            61: "小雨",
            63: "中雨",
            65: "大雨",
            71: "小雪",
            73: "中雪",
            75: "大雪",
            80: "阵雨",
            81: "较强阵雨",
            82: "强阵雨",
            95: "雷暴",
        }
        try:
            return mapping.get(int(code), f"天气代码 {code}")
        except (TypeError, ValueError):
            return "未知天气"

    def _coerce_optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
