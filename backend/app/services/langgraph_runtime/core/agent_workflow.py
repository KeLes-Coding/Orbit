"""Standard workflow contracts for Orbit Agent harnesses."""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.langgraph_runtime.core.agent_types import AgentExecutionResult

from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext


class AgentWorkflowState(TypedDict, total=False):
    """Common state fields every Agent workflow can rely on."""

    user_query: str
    input_summary: dict[str, Any]
    plan_text: str
    step_index: int
    next_action: str
    final_content: str
    reasoning_text: str
    token_usage: dict[str, Any]
    response_metadata: dict[str, Any]
    thought_events: list[dict[str, Any]]
    workspace_files: list[dict[str, Any]]
    error: str | None


class AgentWorkflow(Protocol):
    """Executable workflow produced by an Agent adapter.

    run() 的唯一注入入口是 AgentRuntimeServices。workflow 只从 services 取执行依赖
    （llm / tool / sandbox / artifact / budget / events），不再自定义构造参数。
    """

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        services: AgentRuntimeServices,
    ) -> AgentExecutionResult:
        ...