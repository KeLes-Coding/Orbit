"""Data Workspace Agent 的事件投影。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.langgraph_runtime.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.artifacts.manifest import ArtifactManifest


@dataclass
class DataWorkspaceProjector:
    """收集状态、日志和产物事件，并收口为统一 AgentExecutionResult。"""

    on_event: Callable[[dict[str, Any]], None]
    _thought_events: list[dict[str, Any]] = field(default_factory=list)

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

    def emit_log(self, text: str, *, stream: str = "stdout") -> None:
        if not text:
            return
        event = {
            "type": "agent.run.log",
            "phase": "execute",
            "text": text,
            "meta": {"stream": stream},
        }
        self._thought_events.append(event)
        self.on_event(event)

    def emit_artifact(self, artifact: dict[str, Any]) -> None:
        event = {
            "type": "agent.run.artifact",
            "phase": "artifact",
            "text": artifact.get("name", artifact.get("path", "artifact")),
            "meta": artifact,
        }
        self._thought_events.append(event)
        self.on_event(event)

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

        metadata = dict(response_metadata)
        metadata["artifacts"] = artifacts
        return AgentExecutionResult(
            final_content=final_content,
            thought_events=list(self._thought_events),
            workspace_files=workspace_files,
            response_metadata=metadata,
            error=error,
        )
