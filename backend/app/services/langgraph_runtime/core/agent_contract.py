"""Orbit Agent adapter 协议定义。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.core.agent_types import AgentDescriptor, AgentExecutionResult
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext

if TYPE_CHECKING:
    from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
    from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
    from app.services.langgraph_runtime.core.agent_workflow import AgentWorkflow

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


@runtime_checkable
class WorkflowBackedOrbitAgentAdapter(Protocol):
    """能通过 build_workflow() 交出标准 workflow 的 adapter。

    Runner 对实现该协议的 adapter 走 Harness 分流，其余走 adapter.run() legacy fallback。
    """

    descriptor: AgentDescriptor

    @property
    def agent_type(self) -> str:
        return self.descriptor.agent_type

    def build_workflow(self) -> AgentWorkflow:
        ...

    def build_services(self, events: AgentEventEmitter) -> AgentRuntimeServices:
        ...


def is_workflow_backed(agent: object) -> bool:
    """判断 adapter 是否提供 build_workflow() 和 build_services()。"""
    build_workflow = getattr(agent, "build_workflow", None)
    build_services = getattr(agent, "build_services", None)
    return callable(build_workflow) and callable(build_services)
