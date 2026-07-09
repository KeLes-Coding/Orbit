"""Agent Workflow Harness：workflow 执行的统一中枢。

Harness 独占 skill 之外的运行期职责：发生命周期事件、强制超时、归一化失败、
补齐执行元信息。第一版保持薄，后续 budget / permission / trace / compact 收口都往这里加，
不再改每个 skill 的构造函数。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter, compact_events
from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.langgraph_runtime.core.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.core.agent_workflow import AgentWorkflow
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext


class AgentWorkflowHarness:
    """驱动 workflow 执行并收口生命周期与执行元信息。"""

    async def run_workflow(
        self,
        workflow: AgentWorkflow,
        *,
        agent_type: str,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        on_event: Callable[[dict[str, Any]], None],
        services_factory: Callable[[AgentEventEmitter], AgentRuntimeServices],
        execution_backend: str | None = None,
        execution_kind: str = "workflow",
        skill_type: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AgentExecutionResult:
        # events 由 Harness 建立并作为标准服务注入 workflow，skill 不再自建 emitter。
        events = AgentEventEmitter(on_event=on_event)
        services = services_factory(events)

        self._emit_lifecycle(events, "agent.run.started", agent_type)

        backend = execution_backend or getattr(workflow, "execution_backend", "workflow")
        skill = skill_type or agent_type

        coro = workflow.run(
            user_query=user_query,
            history_messages=history_messages,
            runtime_context=runtime_context,
            services=services,
        )

        try:
            if timeout_seconds and timeout_seconds > 0:
                result = await asyncio.wait_for(coro, timeout=timeout_seconds)
            else:
                result = await coro
        except asyncio.CancelledError:
            self._emit_lifecycle(events, "agent.run.cancelled", agent_type)
            raise
        except asyncio.TimeoutError:
            message = f"agent 执行超时（>{timeout_seconds}s）"
            self._emit_lifecycle(events, "agent.run.failed", agent_type, error=message)
            return self._error_result(agent_type, backend, execution_kind, skill, message)
        except Exception as exc:  # noqa: BLE001 - Harness 统一归一化失败
            message = str(exc) or exc.__class__.__name__
            self._emit_lifecycle(events, "agent.run.failed", agent_type, error=message)
            return self._error_result(agent_type, backend, execution_kind, skill, message)

        # skill 只负责生产业务结果，执行元信息与压缩策略统一由 Harness 收口。
        result.response_metadata.setdefault("agent_type", agent_type)
        result.response_metadata.setdefault("skill_type", skill)
        result.response_metadata.setdefault("execution_backend", backend)
        result.response_metadata.setdefault("execution_kind", execution_kind)
        result.thought_events = compact_events(result.thought_events)

        if result.error:
            self._emit_lifecycle(events, "agent.run.failed", agent_type, error=result.error)
        else:
            self._emit_lifecycle(events, "agent.run.completed", agent_type)
        return result

    @staticmethod
    def _emit_lifecycle(
        events: AgentEventEmitter,
        event_type: str,
        agent_type: str,
        *,
        error: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": event_type,
            "phase": "lifecycle",
            "text": "",
            "meta": {"agent_type": agent_type},
        }
        if error is not None:
            event["error"] = error
        # 生命周期事件只投 SSE，不进 thought_events（accumulate=False），保留锁定的持久化边界。
        events.emit_event(event, accumulate=False)

    @staticmethod
    def _error_result(
        agent_type: str,
        backend: str,
        execution_kind: str,
        skill_type: str,
        error: str,
    ) -> AgentExecutionResult:
        return AgentExecutionResult(
            response_metadata={
                "agent_type": agent_type,
                "skill_type": skill_type,
                "execution_backend": backend,
                "execution_kind": execution_kind,
            },
            error=error,
        )
