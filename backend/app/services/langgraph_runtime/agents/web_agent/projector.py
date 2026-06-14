"""WebAgent projector：执行事件到 Orbit 协议的投影。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
from app.services.langgraph_runtime.core.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.agent_workspace import AgentWorkspace


@dataclass
class WebAgentProjector:
    """收集执行期事件，并构建统一结果。"""

    on_event: Callable[[dict[str, Any]], None]
    _events: AgentEventEmitter = field(init=False)

    def __post_init__(self) -> None:
        self._events = AgentEventEmitter(on_event=self.on_event)

    def emit_thought(
        self,
        *,
        event_type: str,
        phase: str,
        text: str,
        meta: dict[str, Any] | None = None,
        step_id: str | None = None,
        step_kind: str | None = None,
        status: str | None = None,
        input: Any | None = None,
        output: Any | None = None,
        error: str | None = None,
    ) -> None:
        """发射可供前端渲染的 thought 事件。"""
        self._events.emit_thought(
            event_type=event_type,
            phase=phase,
            text=text,
            meta=meta,
            step_id=step_id,
            step_kind=step_kind,
            status=status,
            input=input,
            output=output,
            error=error,
        )

    def emit_reasoning_delta(self, delta: str) -> None:
        """发射 reasoning 增量。"""
        self._events.emit_reasoning_delta(delta)

    def emit_content_delta(self, delta: str) -> None:
        """发射正文增量。"""
        self._events.emit_content_delta(delta)

    def merge_token_usage(self, usage: dict[str, Any]) -> None:
        """合并 token 用量。"""
        self._events.merge_token_usage(usage)

    def build_result(
        self,
        *,
        planning_text: str,
        loop_summaries: list[dict[str, Any]],
        workspace: AgentWorkspace,
        response_metadata: dict[str, Any],
        error: str | None,
    ) -> AgentExecutionResult:
        """收口成统一 AgentExecutionResult。"""
        final_content = self._events.content_text
        reasoning_text = self._events.reasoning_text
        return AgentExecutionResult(
            planning_text=planning_text,
            loop_summaries=list(loop_summaries),
            reasoning_text=reasoning_text,
            final_content=final_content,
            thought_events=self._events.compact_events(),
            workspace_files=workspace.get_file_index(),
            token_usage=self._events.token_usage,
            response_metadata=dict(response_metadata),
            error=error,
        )
