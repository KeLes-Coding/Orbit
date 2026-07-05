"""Data Workspace Agent 的 Orbit adapter。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.agents.data_workspace_agent.runtime import DataWorkspaceAgentRuntime
from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.langgraph_runtime.core.agent_types import (
    AgentBudget,
    AgentDescriptor,
    AgentExecutionResult,
    ArtifactPolicy,
)
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext
from app.services.langgraph_runtime.middleware import AgentMiddleware
from app.services.langgraph_runtime.sandbox import SandboxManager
from app.services.tools import OrbitToolRuntime


DATA_WORKSPACE_AGENT_BUDGET = AgentBudget(
    max_rounds=4,
    max_tool_calls=12,
    max_search_calls_per_round=0,
    timeout_seconds=120,
)

DATA_WORKSPACE_AGENT_DESCRIPTOR = AgentDescriptor(
    agent_type="data_workspace_agent",
    display_name="Data Workspace",
    description="Analyzes uploaded CSV, JSON, and spreadsheet files inside an isolated workspace.",
    capabilities=frozenset({
        "artifact_generation",
        "data_profile",
        "file_analysis",
        "sandbox_execution",
    }),
    default_budget=DATA_WORKSPACE_AGENT_BUDGET,
    skill_type="data_workspace",
    execution_mode="workflow",
    artifact_policy=ArtifactPolicy(produces_artifacts=True),
)


class DataWorkspaceAgentAdapter:
    """通过统一 Agent 协议暴露数据工作区分析能力。"""

    descriptor = DATA_WORKSPACE_AGENT_DESCRIPTOR

    @property
    def agent_type(self) -> str:
        return self.descriptor.agent_type

    @property
    def skill_type(self) -> str:
        return self.descriptor.skill_type

    def __init__(
        self,
        *,
        middleware: AgentMiddleware | None = None,
        sandbox_manager: SandboxManager | None = None,
        budget: AgentBudget | None = None,
    ) -> None:
        if middleware is None:
            middleware = AgentMiddleware(
                llm_invoke=None,
                tool_runtime=OrbitToolRuntime(),
                sandbox_manager=sandbox_manager,
                budget=budget,
            )
        self._middleware = middleware
        self._budget = budget or middleware.budget or self.descriptor.default_budget

    def build_workflow(self) -> DataWorkspaceAgentRuntime:
        """构造 DataWorkspace 标准 workflow。"""
        return DataWorkspaceAgentRuntime(
            llm_invoke=self._middleware.llm_invoke,
            sandbox_manager=self._middleware.sandbox_manager,
            artifact_collector_factory=self._middleware.artifact_collector_factory,
            budget=self._budget,
        )

    def build_services(self, events: AgentEventEmitter) -> AgentRuntimeServices:
        """把宿主依赖 + 本次 run 的 events 组装成唯一注入入口。"""
        return AgentRuntimeServices(
            llm_invoke=self._middleware.llm_invoke,
            tool_runtime=self._middleware.tool_runtime,
            events=events,
            sandbox_manager=self._middleware.sandbox_manager,
            budget=self._budget,
            permissions=self._middleware.permissions,
        )

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        workflow = self.build_workflow()
        result = await workflow.run(
            user_query=user_query,
            history_messages=history_messages,
            runtime_context=runtime_context,
            on_event=on_event,
        )
        return result
