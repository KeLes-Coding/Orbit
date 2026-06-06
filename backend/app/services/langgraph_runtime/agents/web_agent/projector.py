"""WebAgent projector：执行事件到 Orbit 协议的投影。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.langgraph_runtime.agent_types import AgentEvent, AgentExecutionResult
from app.services.langgraph_runtime.agent_workspace import AgentWorkspace


@dataclass
class WebAgentProjector:
    """收集执行期事件，并构建统一结果。"""

    on_event: Callable[[dict[str, Any]], None]
    _thought_events: list[dict[str, Any]] = field(default_factory=list)
    _content_parts: list[str] = field(default_factory=list)
    _reasoning_parts: list[str] = field(default_factory=list)
    _token_usage: dict[str, Any] = field(default_factory=dict)

    def emit_thought(
        self,
        *,
        event_type: str,
        phase: str,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """发射可供前端渲染的 thought 事件。"""
        event: AgentEvent = {
            "type": event_type,
            "phase": phase,
            "text": text,
            "meta": meta or {},
        }
        payload = {
            "type": event["type"],
            "phase": event["phase"],
            "text": event["text"],
            "meta": event["meta"],
        }
        self._thought_events.append(payload)
        self.on_event(payload)

    def emit_reasoning_delta(self, delta: str) -> None:
        """发射 reasoning 增量。"""
        if not delta:
            return
        self._reasoning_parts.append(delta)
        self.on_event({"type": "reasoning_delta", "delta": delta})

    def emit_content_delta(self, delta: str) -> None:
        """发射正文增量。"""
        if not delta:
            return
        self._content_parts.append(delta)
        self.on_event({"type": "content_delta", "delta": delta})

    def merge_token_usage(self, usage: dict[str, Any]) -> None:
        """合并 token 用量。"""
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                self._token_usage[key] = self._token_usage.get(key, 0) + value
            else:
                self._token_usage[key] = value

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
        final_content = "".join(self._content_parts)
        reasoning_text = "".join(self._reasoning_parts)
        return AgentExecutionResult(
            planning_text=planning_text,
            loop_summaries=list(loop_summaries),
            reasoning_text=reasoning_text,
            final_content=final_content,
            thought_events=self._compact_thought_events(),
            workspace_files=workspace.get_file_index(),
            token_usage=dict(self._token_usage),
            response_metadata=dict(response_metadata),
            error=error,
        )

    def _compact_thought_events(self) -> list[dict[str, Any]]:
        """压缩连续同阶段的非工具事件，减少持久化碎片。"""
        compacted: list[dict[str, Any]] = []
        for raw in self._thought_events:
            event = {
                "type": raw.get("type", ""),
                "phase": raw.get("phase", ""),
                "text": raw.get("text", ""),
                "meta": raw.get("meta", {}),
            }
            if not compacted:
                compacted.append(event)
                continue

            previous = compacted[-1]
            same_type = previous.get("type") == event.get("type")
            same_phase = previous.get("phase") == event.get("phase")
            if not (same_type and same_phase):
                compacted.append(event)
                continue

            if event["type"] == "thought.tool":
                compacted.append(event)
                continue
            if event["type"] == "thought.summary":
                if (previous.get("meta") or {}).get("round") != (event.get("meta") or {}).get("round"):
                    compacted.append(event)
                    continue

            previous["text"] = f"{previous.get('text', '')}{event.get('text', '')}"
            if event.get("meta"):
                previous["meta"] = event["meta"]
        return compacted

