"""Data Workspace 的 sandbox 执行 harness。"""

from __future__ import annotations

from app.services.langgraph_runtime.agents.data_workspace_agent.projector import DataWorkspaceProjector
from app.services.langgraph_runtime.artifacts import ArtifactCollector, ArtifactManifest
from app.services.langgraph_runtime.sandbox import ExecResult, SandboxManager, SandboxSession


class DataSandboxExecutor:
    """封装 analysis.py 上传、执行和 artifact manifest 收集。"""

    def __init__(
        self,
        *,
        sandbox_manager: SandboxManager,
        artifact_collector: ArtifactCollector,
        timeout_seconds: float,
    ) -> None:
        self._sandbox_manager = sandbox_manager
        self._artifact_collector = artifact_collector
        self._timeout_seconds = timeout_seconds

    async def upload_analysis_code(
        self,
        *,
        session: SandboxSession,
        analysis_code: str,
        projector: DataWorkspaceProjector,
    ) -> None:
        """把预检后的脚本写入 sandbox work 目录。"""

        projector.emit_step(
            event_type="agent.step.completed",
            step_id="sandbox.upload.analysis",
            step_kind="sandbox.upload",
            title="写入分析脚本",
            phase="workspace",
            status="completed",
            input={"path": "/workspace/work/analysis.py"},
            output={"bytes": len(analysis_code.encode("utf-8"))},
        )
        await self._sandbox_manager.upload_text(
            session,
            "/workspace/work/analysis.py",
            analysis_code,
        )

    async def execute_analysis_code(
        self,
        *,
        session: SandboxSession,
        attempt: int,
        projector: DataWorkspaceProjector,
    ) -> ExecResult:
        """执行 analysis.py 并把 stdout/stderr 投影到前端事件流。"""

        exec_step_id = f"sandbox.exec.{attempt}"
        projector.emit_step(
            event_type="agent.step.started",
            step_id=exec_step_id,
            step_kind="sandbox.exec",
            title="执行数据分析脚本",
            phase="execute",
            status="running",
            input={"command": "python work/analysis.py", "attempt": attempt},
        )
        exec_result = await self._sandbox_manager.exec_python(
            session,
            "work/analysis.py",
            timeout_seconds=self._timeout_seconds,
        )
        projector.emit_log(
            exec_result.stdout,
            stream="stdout",
            step_id=exec_step_id,
            step_kind="sandbox.exec",
        )
        projector.emit_log(
            exec_result.stderr,
            stream="stderr",
            step_id=exec_step_id,
            step_kind="sandbox.exec",
        )
        if exec_result.ok:
            projector.emit_step(
                event_type="agent.step.completed",
                step_id=exec_step_id,
                step_kind="sandbox.exec",
                title="数据分析脚本执行完成",
                phase="execute",
                status="completed",
                output={"exit_code": exec_result.exit_code, "timed_out": exec_result.timed_out},
            )
        else:
            projector.emit_step(
                event_type="agent.step.failed",
                step_id=exec_step_id,
                step_kind="sandbox.exec",
                title="数据分析脚本执行失败",
                phase="execute",
                status="failed",
                output={"exit_code": exec_result.exit_code, "timed_out": exec_result.timed_out},
                error=exec_result.stderr or "sandbox 执行失败",
            )
        return exec_result

    async def collect_manifest(
        self,
        *,
        session: SandboxSession,
        projector: DataWorkspaceProjector,
    ) -> tuple[ArtifactManifest | None, str | None]:
        """收集并校验 artifact manifest；失败时返回错误，交给 repair loop。"""

        projector.emit_step(
            event_type="agent.step.started",
            step_id="artifact.collect",
            step_kind="artifact.collect",
            title="校验 artifact manifest",
            phase="artifact",
            status="running",
            input={"path": "artifact_manifest.json"},
        )
        try:
            manifest = await self._artifact_collector.collect(session)
        except Exception as exc:
            error = str(exc)
            projector.emit_step(
                event_type="agent.step.failed",
                step_id="artifact.collect",
                step_kind="artifact.collect",
                title="artifact manifest 校验失败",
                phase="artifact",
                status="failed",
                error=error,
            )
            return None, error

        projector.emit_step(
            event_type="agent.step.completed",
            step_id="artifact.collect",
            step_kind="artifact.collect",
            title="artifact manifest 校验完成",
            phase="artifact",
            status="completed",
            output={"artifact_count": len(manifest.artifacts)},
        )
        return manifest, None

    @staticmethod
    def preflight_exec_result(error: str) -> ExecResult:
        """把预检失败归一化为一次未进入 sandbox 的执行结果。"""

        return ExecResult(
            command=["preflight", "analysis.py"],
            exit_code=2,
            stdout="",
            stderr=error,
        )
