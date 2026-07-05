"""统一的 Agent 执行调度器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.core.agent_contract import is_workflow_backed
from app.services.langgraph_runtime.core.agent_registry import AgentRegistry
from app.services.langgraph_runtime.core.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.core.agent_workflow_harness import AgentWorkflowHarness
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext


class AgentRunner:
    """从 registry 中解析 agent 并执行。"""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        harness: AgentWorkflowHarness | None = None,
    ) -> None:
        self._registry = registry
        self._harness = harness or AgentWorkflowHarness()

    async def run(
        self,
        *,
        agent_type: str,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        # 这里刻意保持很薄：只做解析和分发，不掺杂任何 agent-specific 逻辑。
        # workflow-backed 的 adapter 走 Harness 统一中枢，其余走 adapter.run() legacy fallback。
        agent = self._registry.resolve(agent_type)
        if is_workflow_backed(agent):
            workflow = agent.build_workflow()
            return await self._harness.run_workflow(
                workflow,
                agent_type=agent.agent_type,
                user_query=user_query,
                history_messages=history_messages,
                runtime_context=runtime_context,
                on_event=on_event,
                services_factory=agent.build_services,
                skill_type=getattr(agent, "skill_type", None),
            )
        return await agent.run(
            user_query=user_query,
            history_messages=history_messages,
            runtime_context=runtime_context,
            on_event=on_event,
        )

