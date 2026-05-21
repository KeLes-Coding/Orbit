"""Orbit Agent adapter 协议定义。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext

LlmInvoker = Callable[
    [list[BaseMessage], str | None, bool, list | None, Any | None, int | None],
    AsyncIterator[Any],
]


class BaseOrbitAgentAdapter(Protocol):
    """所有 Agent adapter 的统一协议。"""

    agent_type: str

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        ...
