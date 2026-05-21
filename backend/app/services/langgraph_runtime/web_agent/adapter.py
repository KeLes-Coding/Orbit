"""当前 web agent 的 Orbit adapter。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.agent_contract import LlmInvoker
from app.services.langgraph_runtime.agent_types import AgentBudget, AgentExecutionResult
from app.services.langgraph_runtime.backend_bridge import BackendBridgeConfig
from app.services.langgraph_runtime.deep_agent import DeepAgentExecutionBridge
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext
from app.services.langgraph_runtime.web_agent.prompt import WEB_AGENT_PROMPT_GUIDANCE
from app.services.langgraph_runtime.web_agent.tools import build_web_agent_tools
from app.services.langgraph_runtime.agent_workspace import AgentWorkspace
from app.services.tools import OrbitToolRuntime


class WebAgentAdapter:
    """对外暴露统一 Agent 协议，内部暂时复用 DeepAgent。"""

    agent_type = "web_agent"

    def __init__(
        self,
        *,
        llm_invoke: LlmInvoker,
        tool_runtime: OrbitToolRuntime,
        budget: AgentBudget | None = None,
        backend_config: BackendBridgeConfig | None = None,
    ) -> None:
        self._llm_invoke = llm_invoke
        self._tool_runtime = tool_runtime
        self._budget = budget or AgentBudget(
            max_rounds=3,
            max_tool_calls=6,
            max_search_calls_per_round=2,
            timeout_seconds=120,
        )
        self._backend_config = backend_config or BackendBridgeConfig()

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
    ) -> AgentExecutionResult:
        # workspace 生命周期按单次 assistant message 隔离，
        # 避免不同会话/不同轮次之间共享同一份中间文件。
        workspace = AgentWorkspace(run_id=runtime_context.request.assistant_message_id or "default")
        agent = DeepAgentExecutionBridge(
            external_tools=build_web_agent_tools(self._tool_runtime),
            tool_runtime=self._tool_runtime,
            llm_invoke=self._llm_invoke,
            budget=self._budget,
            on_event=on_event,
            workspace=workspace,
            system_prompt_prefix=WEB_AGENT_PROMPT_GUIDANCE,
        )
        result = await agent.run(
            user_query=user_query,
            history_messages=history_messages,
        )
        # 这些元信息用于 Orbit 外层持久化和调试，不要求前端理解执行器内部细节。
        result.response_metadata.setdefault("agent_type", self.agent_type)
        result.response_metadata.setdefault("backend_type", self._backend_config.backend_type)
        return result
