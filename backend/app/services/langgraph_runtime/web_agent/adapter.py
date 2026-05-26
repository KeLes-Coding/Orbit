"""WebAgent 的 Orbit adapter。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.agent_contract import LlmInvoker
from app.services.langgraph_runtime.agent_types import AgentBudget, AgentExecutionResult
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext
from app.services.langgraph_runtime.web_agent.runtime import WebAgentRuntime
from app.services.tools import OrbitToolRuntime


class WebAgentAdapter:
    """对外暴露统一 Agent 协议，内部调用 WebAgentRuntime。"""

    agent_type = "web_agent"

    def __init__(
        self,
        *,
        llm_invoke: LlmInvoker,
        tool_runtime: OrbitToolRuntime,
        budget: AgentBudget | None = None,
    ) -> None:
        self._llm_invoke = llm_invoke
        self._tool_runtime = tool_runtime
        self._budget = budget or AgentBudget(
            max_rounds=3,
            max_tool_calls=6,
            max_search_calls_per_round=2,
            timeout_seconds=120,
        )

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        runtime = WebAgentRuntime(
            llm_invoke=self._llm_invoke,
            tool_runtime=self._tool_runtime,
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
