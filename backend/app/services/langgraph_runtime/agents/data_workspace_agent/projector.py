"""Data Workspace Agent 的事件投影。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
from app.services.langgraph_runtime.core.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.artifacts.manifest import ArtifactManifest


@dataclass
class DataWorkspaceProjector:
    """收集状态、日志和产物事件，并收口为统一 AgentExecutionResult。

    events 由 Harness 组装进 AgentRuntimeServices 后注入，projector 不再自建 emitter。
    """

    events: AgentEventEmitter

    @property
    def _events(self) -> AgentEventEmitter:
        return self.events

    def merge_token_usage(self, usage: dict[str, Any]) -> None:
        self._events.merge_token_usage(usage)

    def emit_status(self, status: str, *, meta: dict[str, Any] | None = None) -> None:
        # 先复用 thought_events 通道承载 agent.run.*，前端协议稳定后可升级为独立事件流。
        event = {
            "type": "agent.run.status",
            "phase": "workspace",
            "text": status,
            "meta": meta or {},
        }
        self._events.emit_event(event)

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
        self._events.emit_step(
            event_type=event_type,
            step_id=step_id,
            step_kind=step_kind,
            title=title,
            phase=phase,
            status=status,
            meta=meta,
            input=input,
            output=output,
            error=error,
        )

    def emit_log(
        self,
        text: str,
        *,
        stream: str = "stdout",
        step_id: str | None = None,
        step_kind: str | None = None,
    ) -> None:
        self._events.emit_log(
            text,
            stream=stream,
            step_id=step_id,
            step_kind=step_kind,
        )

    def emit_artifact(self, artifact: dict[str, Any], *, step_id: str | None = None) -> None:
        self._events.emit_artifact(artifact, step_id=step_id)

    def emit_content_delta(self, delta: str) -> None:
        self._events.emit_content_delta(delta)

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
        if not error and not self._events.content_text:
            self.emit_content_delta(final_content)

        metadata = dict(response_metadata)
        metadata["artifacts"] = artifacts
        return AgentExecutionResult(
            final_content=self._events.content_text or final_content,
            thought_events=self._events.events,
            workspace_files=workspace_files,
            token_usage=self._events.token_usage,
            response_metadata=metadata,
            error=error,
        )
