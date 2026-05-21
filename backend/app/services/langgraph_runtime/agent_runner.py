"""统一的 Agent 执行调度器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.agent_registry import AgentRegistry
from app.services.langgraph_runtime.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext


class AgentRunner:
    """从 registry 中解析 agent 并执行。"""

    def __init__(self, *, registry: AgentRegistry) -> None:
        self._registry = registry

    async def run(
        self,
        *,
        agent_type: str,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        # 这里刻意保持很薄：只做解析和分发，不掺杂任何 agent-specific 逻辑，
        # 这样后续替换具体执行后端时不会把控制流再次拉回 runtime 主干。
        agent = self._registry.resolve(agent_type)
        return await agent.run(
            user_query=user_query,
            history_messages=history_messages,
            runtime_context=runtime_context,
            on_event=on_event,
        )
