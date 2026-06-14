"""Standard Agent event emitter for Orbit Agent workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentEventEmitter:
    """Build, emit, and accumulate standard Agent runtime events."""

    on_event: Callable[[dict[str, Any]], None]
    _events: list[dict[str, Any]] = field(default_factory=list)
    _content_parts: list[str] = field(default_factory=list)
    _reasoning_parts: list[str] = field(default_factory=list)
    _token_usage: dict[str, Any] = field(default_factory=dict)

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    @property
    def content_text(self) -> str:
        return "".join(self._content_parts)

    @property
    def reasoning_text(self) -> str:
        return "".join(self._reasoning_parts)

    @property
    def token_usage(self) -> dict[str, Any]:
        return dict(self._token_usage)

    def merge_token_usage(self, usage: dict[str, Any]) -> None:
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                self._token_usage[key] = self._token_usage.get(key, 0) + value
            else:
                self._token_usage[key] = value

    def emit_event(self, event: dict[str, Any], *, accumulate: bool = True) -> None:
        payload = dict(event)
        if accumulate:
            self._events.append(payload)
        self.on_event(payload)

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
        event = self._base_agent_event(
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
        self.emit_event(event)

    def emit_step(
        self,
        *,
        event_type: str,
        step_id: str,
        step_kind: str,
        title: str,
        phase: str,
        status: str,
        meta: dict[str, Any] | None = None,
        input: Any | None = None,
        output: Any | None = None,
        error: str | None = None,
    ) -> None:
        event = self._base_agent_event(
            event_type=event_type,
            phase=phase,
            text=title,
            meta=meta,
            step_id=step_id,
            step_kind=step_kind,
            status=status,
            input=input,
            output=output,
            error=error,
        )
        self.emit_event(event)

    def emit_log(
        self,
        text: str,
        *,
        stream: str = "stdout",
        step_id: str | None = None,
        step_kind: str | None = None,
    ) -> None:
        if not text:
            return
        event: dict[str, Any] = {
            "type": "agent.step.delta" if step_id else "agent.run.log",
            "phase": "execute",
            "text": text,
            "meta": {"stream": stream},
        }
        if step_id:
            event.update({
                "step_id": step_id,
                "step_kind": step_kind or "sandbox.exec",
                "status": "running",
                "output": {"stream": stream, "text": text},
            })
        self.emit_event(event)

    def emit_artifact(self, artifact: dict[str, Any], *, step_id: str | None = None) -> None:
        event = {
            "type": "agent.run.artifact",
            "phase": "artifact",
            "text": artifact.get("name", artifact.get("path", "artifact")),
            "meta": artifact,
        }
        if step_id:
            event.update({
                "step_id": step_id,
                "step_kind": "artifact.collect",
                "status": "completed",
                "output": artifact,
            })
        self.emit_event(event)

    def emit_content_delta(self, delta: str) -> None:
        if not delta:
            return
        self._content_parts.append(delta)
        self.emit_event({"type": "content_delta", "delta": delta}, accumulate=False)

    def emit_reasoning_delta(self, delta: str) -> None:
        if not delta:
            return
        self._reasoning_parts.append(delta)
        self.emit_event({"type": "reasoning_delta", "delta": delta}, accumulate=False)

    def compact_events(self) -> list[dict[str, Any]]:
        """Compact adjacent non-tool thought events for durable storage."""
        compacted: list[dict[str, Any]] = []
        for raw in self._events:
            event = dict(raw)
            if not compacted:
                compacted.append(event)
                continue

            previous = compacted[-1]
            same_type = previous.get("type") == event.get("type")
            same_phase = previous.get("phase") == event.get("phase")
            if not (same_type and same_phase):
                compacted.append(event)
                continue

            if event.get("type") == "thought.tool":
                compacted.append(event)
                continue
            if event.get("type") == "thought.summary":
                if (previous.get("meta") or {}).get("round") != (event.get("meta") or {}).get("round"):
                    compacted.append(event)
                    continue

            previous["text"] = f"{previous.get('text', '')}{event.get('text', '')}"
            if event.get("meta"):
                previous["meta"] = event["meta"]
        return compacted

    @staticmethod
    def _base_agent_event(
        *,
        event_type: str,
        phase: str,
        text: str,
        meta: dict[str, Any] | None,
        step_id: str | None,
        step_kind: str | None,
        status: str | None,
        input: Any | None,
        output: Any | None,
        error: str | None,
    ) -> dict[str, Any]:
        event = {
            "type": event_type,
            "phase": phase,
            "text": text,
            "meta": meta or {},
        }
        if step_id:
            event["step_id"] = step_id
        if step_kind:
            event["step_kind"] = step_kind
        if status:
            event["status"] = status
        if input is not None:
            event["input"] = input
        if output is not None:
            event["output"] = output
        if error is not None:
            event["error"] = error
        return event