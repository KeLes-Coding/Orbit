"""Standard workflow contracts for Orbit Agent harnesses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

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
    """Executable workflow produced by an Agent adapter."""

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        ...