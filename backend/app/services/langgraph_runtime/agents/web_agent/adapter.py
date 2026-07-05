"""WebAgent 的 Orbit adapter。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.agents.web_agent.runtime import WebAgentWorkflow
from app.services.langgraph_runtime.core.agent_contract import LlmInvoker
from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.langgraph_runtime.core.agent_types import (
    AgentBudget,
    AgentDescriptor,
    AgentExecutionResult,
)
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext
from app.services.langgraph_runtime.middleware import AgentMiddleware
from app.services.tools import OrbitToolRuntime


WEB_AGENT_BUDGET = AgentBudget(
    max_rounds=3,
    max_tool_calls=6,
    max_search_calls_per_round=2,
    timeout_seconds=120,
)

WEB_AGENT_DESCRIPTOR = AgentDescriptor(
    agent_type="web_agent",
    display_name="Web Agent",
    description="Searches and browses web sources, then answers with cited research context.",
    capabilities=frozenset({"tool_calling", "web_fetch", "web_search", "workspace_notes"}),
    default_budget=WEB_AGENT_BUDGET,
    skill_type="web_research",
    execution_mode="workflow",
)


class WebAgentAdapter:
    """对外暴露统一 Agent 协议，内部构造 WebAgentWorkflow。"""

    descriptor = WEB_AGENT_DESCRIPTOR

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
        llm_invoke: LlmInvoker | None = None,
        tool_runtime: OrbitToolRuntime | None = None,
        budget: AgentBudget | None = None,
    ) -> None:
        if middleware is None:
            middleware = AgentMiddleware(
                llm_invoke=llm_invoke,
                tool_runtime=tool_runtime or OrbitToolRuntime(),
                budget=budget,
            )
        if middleware.llm_invoke is None:
            raise ValueError("WebAgentAdapter requires middleware.llm_invoke")
        self._middleware = middleware
        self._llm_invoke = middleware.llm_invoke
        self._budget = budget or middleware.budget or self.descriptor.default_budget

    def build_workflow(self) -> WebAgentWorkflow:
        """构造 WebAgent 标准 workflow。"""
        return WebAgentWorkflow(
            llm_invoke=self._llm_invoke,
            tool_runtime=self._middleware.tool_runtime,
            budget=self._budget,
        )

    def build_services(self, events: AgentEventEmitter) -> AgentRuntimeServices:
        """把宿主依赖 + 本次 run 的 events 组装成唯一注入入口。"""
        return AgentRuntimeServices(
            llm_invoke=self._llm_invoke,
            tool_runtime=self._middleware.tool_runtime,
            events=events,
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
