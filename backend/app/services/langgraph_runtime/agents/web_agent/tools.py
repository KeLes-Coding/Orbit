"""Web Agent 工具过滤。"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from app.services.tools import OrbitToolRuntime

WEB_AGENT_TOOL_NAMES = {"websearch", "webfetch"}


def build_web_agent_tools(tool_runtime: OrbitToolRuntime) -> list[StructuredTool]:
    """只暴露 web agent 需要的工具。"""
    return [tool for tool in tool_runtime.get_langchain_tools() if tool.name in WEB_AGENT_TOOL_NAMES]
