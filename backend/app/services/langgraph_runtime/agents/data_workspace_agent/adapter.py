"""Data Workspace Agent 的 Orbit adapter。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.agent_types import AgentBudget, AgentExecutionResult
from app.services.langgraph_runtime.agents.data_workspace_agent.runtime import DataWorkspaceAgentRuntime
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext
from app.services.langgraph_runtime.sandbox import SandboxManager


class DataWorkspaceAgentAdapter:
    """通过统一 Agent 协议暴露数据工作区分析能力。"""

    agent_type = "data_workspace_agent"

    def __init__(
        self,
        *,
        sandbox_manager: SandboxManager | None = None,
        budget: AgentBudget | None = None,
    ) -> None:
        self._sandbox_manager = sandbox_manager
        self._budget = budget

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        runtime = DataWorkspaceAgentRuntime(
            sandbox_manager=self._sandbox_manager,
            budget=self._budget,
        )
        result = await runtime.run(
            user_query=user_query,
            history_messages=history_messages,
            runtime_context=runtime_context,
            on_event=on_event,
        )
        result.response_metadata.setdefault("agent_type", self.agent_type)
        return result
