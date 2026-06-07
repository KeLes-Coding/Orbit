"""Orbit Agent adapter 协议定义。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.core.agent_types import AgentDescriptor, AgentExecutionResult
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext

LlmInvoker = Callable[
    [list[BaseMessage], str | None, bool, list | None, Any | None, int | None],
    AsyncIterator[Any],
]


class BaseOrbitAgentAdapter(Protocol):
    """所有 Agent adapter 的统一协议。"""

    descriptor: AgentDescriptor

    @property
    def agent_type(self) -> str:
        return self.descriptor.agent_type

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        ...
