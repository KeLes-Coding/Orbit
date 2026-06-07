"""Data Workspace Agent 的事件投影。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.langgraph_runtime.core.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.artifacts.manifest import ArtifactManifest


@dataclass
class DataWorkspaceProjector:
    """收集状态、日志和产物事件，并收口为统一 AgentExecutionResult。"""

    on_event: Callable[[dict[str, Any]], None]
    _thought_events: list[dict[str, Any]] = field(default_factory=list)
    _content_parts: list[str] = field(default_factory=list)
    _token_usage: dict[str, Any] = field(default_factory=dict)

    def merge_token_usage(self, usage: dict[str, Any]) -> None:
        for key, value in usage.items():
            if isinstance(value, int):
                self._token_usage[key] = int(self._token_usage.get(key, 0)) + value
            else:
                self._token_usage[key] = value

    def emit_status(self, status: str, *, meta: dict[str, Any] | None = None) -> None:
        # 先复用 thought_events 通道承载 agent.run.*，前端协议稳定后可升级为独立事件流。
        event = {
            "type": "agent.run.status",
            "phase": "workspace",
            "text": status,
            "meta": meta or {},
        }
        self._thought_events.append(event)
        self.on_event(event)

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
        event = {
            "type": event_type,
            "phase": phase,
            "text": title,
            "meta": meta or {},
            "step_id": step_id,
            "step_kind": step_kind,
            "status": status,
        }
        if input is not None:
            event["input"] = input
        if output is not None:
            event["output"] = output
        if error is not None:
            event["error"] = error
        self._thought_events.append(event)
        self.on_event(event)

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
        event = {
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
        self._thought_events.append(event)
        self.on_event(event)

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
        self._thought_events.append(event)
        self.on_event(event)

    def emit_content_delta(self, delta: str) -> None:
        if not delta:
            return
        self._content_parts.append(delta)
        self.on_event({"type": "content_delta", "delta": delta})

    def build_result(
        self,
        *,
        manifest: ArtifactManifest | None,
        workspace_files: list[dict[str, str]],
        response_metadata: dict[str, Any],
        error: str | None = None,
    ) -> AgentExecutionResult:
        artifacts = response_metadata.get("artifacts")
        if not isinstance(artifacts, list):
            artifacts = [artifact.model_dump(mode="json") for artifact in manifest.artifacts] if manifest else []
        if error:
            final_content = f"Data Workspace Agent 执行失败：{error}"
        elif artifacts:
            names = "、".join(item["name"] for item in artifacts)
            final_content = f"数据工作区分析完成，已生成 {len(artifacts)} 个产物：{names}。"
        else:
            final_content = "数据工作区分析完成，但没有生成可展示产物。"
        if not error and not self._content_parts:
            self.emit_content_delta(final_content)

        metadata = dict(response_metadata)
        metadata["artifacts"] = artifacts
        return AgentExecutionResult(
            final_content="".join(self._content_parts) or final_content,
            thought_events=list(self._thought_events),
            workspace_files=workspace_files,
            token_usage=dict(self._token_usage),
            response_metadata=metadata,
            error=error,
        )
