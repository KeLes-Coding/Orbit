"""Data Workspace Agent 运行时。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

from app.services.langgraph_runtime.core.agent_types import AgentBudget, AgentExecutionResult
from app.services.langgraph_runtime.core.agent_contract import LlmInvoker
from app.services.langgraph_runtime.core.agent_events import AgentEventEmitter
from app.services.langgraph_runtime.core.agent_services import AgentRuntimeServices
from app.services.langgraph_runtime.artifacts import ArtifactCollector, ArtifactManifest
from app.services.langgraph_runtime.agents.data_workspace_agent.code_cleaner import DataCodeCleaner
from app.services.langgraph_runtime.agents.data_workspace_agent.code_generator import DataCodeGenerator
from app.services.langgraph_runtime.agents.data_workspace_agent.code_validator import DataCodeValidator
from app.services.langgraph_runtime.agents.data_workspace_agent.projector import DataWorkspaceProjector
from app.services.langgraph_runtime.agents.data_workspace_agent.runtime_contract import DataRuntimeContract
from app.services.langgraph_runtime.agents.data_workspace_agent.sandbox_executor import DataSandboxExecutor
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext
from app.services.langgraph_runtime.sandbox import SandboxInputFile, SandboxManager
from app.services.tools import OrbitToolRuntime


class DataWorkspaceAgentRuntime:
    """基于 sandbox 后端的一次 run 级数据分析运行时。"""

    execution_backend = "sandbox"

    def __init__(
        self,
        *,
        llm_invoke: LlmInvoker | None = None,
        sandbox_manager: SandboxManager | None = None,
        artifact_collector_factory: Callable[[SandboxManager], ArtifactCollector] | None = None,
        budget: AgentBudget | None = None,
    ) -> None:
        self._llm_invoke = llm_invoke
        self._sandbox_manager = sandbox_manager or SandboxManager()
        self._budget = budget or AgentBudget(timeout_seconds=45)
        collector_factory = artifact_collector_factory or ArtifactCollector
        self._artifact_collector = collector_factory(self._sandbox_manager)
        self._runtime_contract = DataRuntimeContract()
        self._code_cleaner = DataCodeCleaner()
        self._code_validator = DataCodeValidator()
        self._code_generator = DataCodeGenerator(
            llm_invoke=self._llm_invoke,
            cleaner=self._code_cleaner,
            contract=self._runtime_contract,
        )
        self._sandbox_executor = DataSandboxExecutor(
            sandbox_manager=self._sandbox_manager,
            artifact_collector=self._artifact_collector,
            timeout_seconds=self._budget.timeout_seconds,
        )

    def _resolve_services(
        self,
        services: AgentRuntimeServices | None,
        on_event: Callable[[dict[str, Any]], None] | None,
    ) -> AgentRuntimeServices:
        # Harness 路径传 services；legacy / 直接调用传 on_event，用构造期依赖自组装。
        if services is not None:
            return services
        if on_event is None:
            raise ValueError("DataWorkspaceAgentRuntime.run 需要 services 或 on_event")
        return AgentRuntimeServices(
            llm_invoke=self._llm_invoke,
            tool_runtime=OrbitToolRuntime(),
            events=AgentEventEmitter(on_event=on_event),
            sandbox_manager=self._sandbox_manager,
            artifact_collector=self._artifact_collector,
            budget=self._budget,
        )

    async def run(
        self,
        *,
        user_query: str,
        history_messages: list[BaseMessage],
        runtime_context: OrbitRuntimeContext,
        services: AgentRuntimeServices | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentExecutionResult:
        services = self._resolve_services(services, on_event)
        projector = DataWorkspaceProjector(events=services.events)
        run_id = runtime_context.request.assistant_message_id or "data-workspace-run"
        input_files = self._load_input_files(runtime_context)
        if not input_files:
            return projector.build_result(
                manifest=None,
                workspace_files=[],
                response_metadata=self._metadata(),
                error="没有可分析的输入文件",
            )

        session = None
        manifest: ArtifactManifest | None = None
        workspace_files: list[dict[str, str]] = []
        try:
            # MVP 采用 run-level ephemeral sandbox：每次分析创建、同步、执行、回收、销毁。
            projector.emit_step(
                event_type="agent.step.started",
                step_id="sandbox.create",
                step_kind="sandbox.create",
                title="准备 sandbox workspace",
                phase="workspace",
                status="running",
                input={"file_count": len(input_files), "files": [item.name for item in input_files]},
            )
            session = await self._sandbox_manager.create_run_sandbox(
                run_id=run_id,
                input_files=input_files,
            )
            projector.emit_step(
                event_type="agent.step.completed",
                step_id="sandbox.create",
                step_kind="sandbox.create",
                title="sandbox workspace 已准备",
                phase="workspace",
                status="completed",
                output={"sandbox_id": session.id, "file_count": len(input_files)},
            )

            projector.emit_step(
                event_type="agent.step.started",
                step_id="llm.codegen",
                step_kind="llm.codegen",
                title="生成分析脚本",
                phase="codegen",
                status="running",
                input={"query": user_query, "files": [item.name for item in input_files]},
            )
            analysis_code = await self._generate_analysis_code(
                user_query=user_query,
                input_files=input_files,
                projector=projector,
            )
            projector.emit_step(
                event_type="agent.step.completed",
                step_id="llm.codegen",
                step_kind="llm.codegen",
                title="analysis.py 已生成",
                phase="codegen",
                status="completed",
                output={"bytes": len(analysis_code.encode("utf-8"))},
            )

            exec_result = None
            validation_error: str | None = None
            repair_attempts = 0
            max_repairs = 2 if self._llm_invoke is not None else 0
            while True:
                preflight_error = self._validate_generated_code(analysis_code)
                if preflight_error:
                    projector.emit_step(
                        event_type="agent.step.failed",
                        step_id="code.preflight",
                        step_kind="code.preflight",
                        title="analysis.py 预检失败",
                        phase="codegen",
                        status="failed",
                        error=preflight_error,
                    )
                    projector.emit_log(
                        preflight_error,
                        stream="stderr",
                        step_id="code.preflight",
                        step_kind="code.preflight",
                    )
                    exec_result = self._sandbox_executor.preflight_exec_result(preflight_error)
                    validation_error = preflight_error
                else:
                    await self._sandbox_executor.upload_analysis_code(
                        session=session,
                        analysis_code=analysis_code,
                        projector=projector,
                    )
                    exec_result = await self._sandbox_executor.execute_analysis_code(
                        session=session,
                        attempt=repair_attempts + 1,
                        projector=projector,
                    )
                    if exec_result.ok:
                        manifest, validation_error = await self._sandbox_executor.collect_manifest(
                            session=session,
                            projector=projector,
                        )
                        if manifest is not None:
                            break
                    else:
                        validation_error = exec_result.stderr or "sandbox 执行失败"

                if repair_attempts >= max_repairs:
                    workspace_files = await self._sandbox_manager.list_output_files(session)
                    return projector.build_result(
                        manifest=None,
                        workspace_files=workspace_files,
                        response_metadata=self._metadata(
                            exit_code=exec_result.exit_code,
                            timed_out=exec_result.timed_out,
                            repair_attempts=repair_attempts,
                            validation_error=validation_error,
                        ),
                        error=validation_error,
                    )

                repair_attempts += 1
                repair_step_id = f"llm.repair.{repair_attempts}"
                projector.emit_step(
                    event_type="agent.step.started",
                    step_id=repair_step_id,
                    step_kind="llm.repair",
                    title="修复分析脚本",
                    phase="codegen",
                    status="running",
                    input={"attempt": repair_attempts, "error": validation_error},
                )
                analysis_code = await self._repair_analysis_code(
                    user_query=user_query,
                    input_files=input_files,
                    previous_code=analysis_code,
                    stdout=exec_result.stdout,
                    stderr=exec_result.stderr,
                    validation_error=validation_error,
                    projector=projector,
                )
                projector.emit_step(
                    event_type="agent.step.completed",
                    step_id=repair_step_id,
                    step_kind="llm.repair",
                    title="analysis.py 修复完成",
                    phase="codegen",
                    status="completed",
                    output={
                        "attempt": repair_attempts,
                        "bytes": len(analysis_code.encode("utf-8")),
                    },
                )
            artifact_payloads = await self._build_artifact_payloads(
                session=session,
                manifest=manifest,
            )
            for artifact in manifest.artifacts:
                projector.emit_artifact(artifact.model_dump(mode="json"), step_id="artifact.collect")
            workspace_files = await self._sandbox_manager.list_output_files(session)

            return projector.build_result(
                manifest=manifest,
                workspace_files=workspace_files,
                response_metadata=self._metadata(
                    artifacts=artifact_payloads,
                    repair_attempts=repair_attempts,
                    codegen_backend="llm" if self._llm_invoke is not None else "deterministic",
                ),
            )
        except Exception as exc:
            return projector.build_result(
                manifest=manifest,
                workspace_files=workspace_files,
                response_metadata=self._metadata(),
                error=str(exc),
            )
        finally:
            if session is not None:
                await self._sandbox_manager.destroy(session)

    async def _generate_analysis_code(
        self,
        *,
        user_query: str,
        input_files: list[SandboxInputFile],
        projector: DataWorkspaceProjector,
    ) -> str:
        if self._llm_invoke is None:
            return self._build_analysis_script(user_query=user_query)
        return await self._code_generator.generate(
            user_query=user_query,
            input_files=input_files,
            fallback_code=self._build_analysis_script(user_query=user_query),
            token_usage_sink=projector.merge_token_usage,
        )

    async def _repair_analysis_code(
        self,
        *,
        user_query: str,
        input_files: list[SandboxInputFile],
        previous_code: str,
        stdout: str,
        stderr: str,
        validation_error: str | None,
        projector: DataWorkspaceProjector,
    ) -> str:
        if self._llm_invoke is None:
            return previous_code
        return await self._code_generator.repair(
            user_query=user_query,
            input_files=input_files,
            previous_code=previous_code,
            stdout=stdout,
            stderr=stderr,
            validation_error=validation_error,
            token_usage_sink=projector.merge_token_usage,
        )

    def _validate_generated_code(self, code: str) -> str | None:
        """执行前预检，避免明显错误代码进入 sandbox。"""

        return self._code_validator.validate(code)

    def _load_input_files(self, runtime_context: OrbitRuntimeContext) -> list[SandboxInputFile]:
        """从宿主上下文解析本轮要同步进 sandbox 的文件。"""
        files: list[SandboxInputFile] = []
        for ref in runtime_context.request.file_refs or []:
            storage_path = ref.get("storage_path")
            if not storage_path:
                continue
            path = Path(storage_path)
            if not path.is_absolute():
                from app.services.files.storage import LocalFileStorage

                path = LocalFileStorage().base_dir / storage_path
            if not path.exists() or not path.is_file():
                continue
            files.append(
                SandboxInputFile(
                    name=ref.get("name") or path.name,
                    content=path.read_bytes(),
                )
            )
        return files

    async def _build_artifact_payloads(
        self,
        *,
        session,
        manifest: ArtifactManifest,
    ) -> list[dict[str, Any]]:
        """在 sandbox 销毁前提取轻量 preview，保证刷新后仍能展示 artifact。"""
        payloads: list[dict[str, Any]] = []
        for artifact in manifest.artifacts:
            payload = artifact.model_dump(mode="json")
            payload["preview"] = await self._read_artifact_preview(
                session=session,
                path=artifact.preview or artifact.path,
                artifact_type=artifact.type,
            )
            if artifact.preview:
                payload["preview_path"] = artifact.preview
            payloads.append(payload)
        return payloads

    async def _read_artifact_preview(
        self,
        *,
        session,
        path: str,
        artifact_type: str,
    ) -> dict[str, Any]:
        """读取产物预览；只保存轻量 JSON/text，避免把大文件塞进消息。"""
        try:
            raw = await self._sandbox_manager.download_output_bytes(session, path)
        except FileNotFoundError:
            return {}
        max_bytes = 64 * 1024
        content = raw[:max_bytes].decode("utf-8", errors="replace")
        truncated = len(raw) > max_bytes
        if artifact_type in {"json", "chart", "table"}:
            try:
                return {
                    "kind": "json",
                    "data": json.loads(content),
                    "truncated": truncated,
                }
            except json.JSONDecodeError:
                pass
        return {
            "kind": "text",
            "text": content,
            "truncated": truncated,
        }

    @staticmethod
    def _build_analysis_script(*, user_query: str) -> str:
        """生成第一版固定分析脚本，确保不依赖 LLM 也能验证 sandbox/artifact 闭环。"""
        escaped_query = repr(user_query)
        return f'''from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "runtime")

from orbit_data.artifacts import save_json_artifact, save_text_artifact, write_artifact_manifest
from orbit_data.loaders import load_excel_sheets, load_json, load_table
from orbit_data.profiler import profile_table

INPUT_ROOT = Path("input")

def analyze_file(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        rows = load_table(path)
        profile = profile_table(rows)
        return {{"kind": "table", "name": path.name, "profile": profile, "sample": rows[:10]}}
    if suffix == ".json":
        data = load_json(path)
        if isinstance(data, list) and all(isinstance(item, dict) for item in data):
            profile = profile_table(data)
            return {{"kind": "json_table", "name": path.name, "profile": profile, "sample": data[:10]}}
        return {{"kind": "json", "name": path.name, "profile": {{"type": type(data).__name__}}, "sample": data}}
    if suffix == ".xlsx":
        sheets = load_excel_sheets(path, max_rows=50)
        return {{"kind": "workbook", "name": path.name, "profile": {{"sheets": list(sheets), "sheet_count": len(sheets)}}, "sample": sheets}}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {{"kind": "text", "name": path.name, "profile": {{"chars": len(text), "lines": text.count("\\n") + 1}}, "sample": text[:2000]}}

profiles = []
for input_file in sorted(INPUT_ROOT.iterdir()):
    if input_file.is_file():
        profiles.append(analyze_file(input_file))

summary = {{
    "query": {escaped_query},
    "file_count": len(profiles),
    "files": profiles,
}}

save_json_artifact("profiles/input_profiles.json", summary)
save_text_artifact(
    "report.md",
    "# Data Workspace Report\\n\\n"
    f"User query: {{summary['query']}}\\n\\n"
    f"Files analyzed: {{summary['file_count']}}\\n\\n"
    + "\\n".join(f"- {{item['name']}}: {{item['kind']}}" for item in profiles)
    + "\\n",
)
save_text_artifact("analysis.py", Path("work/analysis.py").read_text(encoding="utf-8"))
write_artifact_manifest([
    {{"type": "json", "name": "input_profiles", "path": "profiles/input_profiles.json"}},
    {{"type": "report", "name": "analysis_report", "path": "report.md"}},
    {{"type": "code", "name": "analysis_code", "path": "analysis.py"}},
])

print(json.dumps({{"status": "ok", "artifacts": 3}}, ensure_ascii=False))
'''

    @staticmethod
    def _metadata(**extra: Any) -> dict[str, Any]:
        """构造写回 message metadata 的 agent 执行摘要。"""
        metadata = {
            "agent_type": "data_workspace_agent",
            "execution_backend": "local_sandbox",
        }
        artifacts = extra.pop("artifacts", None)
        if artifacts is not None:
            metadata["artifacts"] = artifacts
        metadata.update(extra)
        return metadata
