"""Data Workspace Agent 的 sandbox/artifact 回归测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.services.langgraph_runtime.agent_catalog import AgentCatalog
from app.services.langgraph_runtime.agents.data_workspace_agent.code_validator import DataCodeValidator
from app.services.langgraph_runtime.agents.data_workspace_agent.runtime import DataWorkspaceAgentRuntime
from app.services.langgraph_runtime.core.runtime_context import OrbitRuntimeContext, OrbitRuntimeRequest
from app.services.llm_client import LLMStreamChunk
from app.services.tools.runtime import OrbitToolRuntime


def run(coro):
    return asyncio.run(coro)


def test_resolve_agent_type_routes_data_files_to_data_workspace_agent():
    assert (
        AgentCatalog.resolve_agent_type(
            chat_mode="agent",
            requested_agent_type=None,
            file_refs=[{"name": "sales.csv", "mime_type": "text/csv"}],
        )
        == "data_workspace_agent"
    )
    assert (
        AgentCatalog.resolve_agent_type(
            chat_mode="agent",
            requested_agent_type=None,
            file_refs=[{"name": "notes.md", "mime_type": "text/markdown"}],
        )
        == "web_agent"
    )
    assert (
        AgentCatalog.resolve_agent_type(
            chat_mode="chat",
            requested_agent_type=None,
            file_refs=[{"name": "sales.csv", "mime_type": "text/csv"}],
        )
        is None
    )


def test_data_files_promote_chat_mode_to_agent_execution():
    assert (
        AgentCatalog.resolve_execution_chat_mode(
            chat_mode="chat",
            file_refs=[{"name": "sales.csv", "mime_type": "text/csv"}],
        )
        == "agent"
    )
    assert (
        AgentCatalog.resolve_execution_chat_mode(
            chat_mode="chat",
            file_refs=[{"name": "notes.md", "mime_type": "text/markdown"}],
        )
        == "chat"
    )


def test_data_workspace_agent_generates_manifest_and_artifacts(tmp_path: Path):
    data_file = tmp_path / "sales.csv"
    data_file.write_text("region,amount\nEast,10\nWest,20\nEast,15\n", encoding="utf-8")
    events: list[dict[str, Any]] = []

    result = run(
        DataWorkspaceAgentRuntime().run(
            user_query="按区域分析销售额",
            history_messages=[HumanMessage(content="按区域分析销售额")],
            runtime_context=_runtime_context(data_file),
            on_event=events.append,
        )
    )

    assert result.error is None
    assert "数据工作区分析完成" in result.final_content
    artifacts = result.response_metadata.get("artifacts")
    assert isinstance(artifacts, list)
    assert {item["name"] for item in artifacts} == {
        "input_profiles",
        "analysis_report",
        "analysis_code",
    }
    profile_file = next(item for item in artifacts if item["name"] == "input_profiles")
    assert profile_file["path"] == "profiles/input_profiles.json"
    assert any(event.get("type") == "agent.step.started" for event in events)
    assert any(event.get("step_kind") == "sandbox.exec" for event in events)
    assert any(event.get("type") == "agent.run.artifact" for event in events)
    assert any(event.get("type") == "content_delta" for event in events)
    assert {"path": "artifact_manifest.json", "size": str(len(json.dumps({"artifacts": []})))} not in result.workspace_files
    assert any(item["path"] == "profiles/input_profiles.json" for item in result.workspace_files)


def test_data_workspace_agent_repairs_generated_code(tmp_path: Path):
    data_file = tmp_path / "sales.csv"
    data_file.write_text("region,amount\nEast,10\nWest,20\n", encoding="utf-8")
    calls = 0

    broken_code = "raise RuntimeError('first attempt failed')\n"
    repaired_code = """
import sys
from pathlib import Path

sys.path.insert(0, "runtime")

from orbit_data.artifacts import save_text_artifact, write_artifact_manifest

save_text_artifact("report.md", "repair ok")
save_text_artifact("analysis.py", Path("work/analysis.py").read_text(encoding="utf-8"))
write_artifact_manifest([
    {"type": "report", "name": "repair_report", "path": "report.md"},
    {"type": "code", "name": "analysis_code", "path": "analysis.py"},
])
print("repair complete")
"""

    async def fake_llm_invoke(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield LLMStreamChunk(
            content_delta=f"```python\n{broken_code if calls == 1 else repaired_code}\n```",
            token_usage={"output_tokens": 3},
        )

    events: list[dict[str, Any]] = []
    result = run(
        DataWorkspaceAgentRuntime(llm_invoke=fake_llm_invoke).run(
            user_query="生成销售报告",
            history_messages=[HumanMessage(content="生成销售报告")],
            runtime_context=_runtime_context(data_file),
            on_event=events.append,
        )
    )

    assert result.error is None
    assert calls == 2
    assert result.response_metadata["repair_attempts"] == 1
    assert result.response_metadata["codegen_backend"] == "llm"
    assert {item["name"] for item in result.response_metadata["artifacts"]} == {
        "repair_report",
        "analysis_code",
    }
    assert result.token_usage["output_tokens"] == 6
    assert any(event.get("text") == "修复分析脚本" for event in events)
    assert any(event.get("step_kind") == "llm.repair" for event in events)
    assert any("预检失败" in event.get("text", "") for event in events)
    assert any(event.get("type") == "content_delta" for event in events)


def test_data_workspace_agent_repairs_wrong_helper_signature_before_exec(tmp_path: Path):
    data_file = tmp_path / "sales.csv"
    data_file.write_text("region,amount\nEast,10\nWest,20\n", encoding="utf-8")
    calls = 0

    wrong_signature_code = """
import sys
sys.path.insert(0, "runtime")
from orbit_data.artifacts import save_text_artifact, write_artifact_manifest
save_text_artifact(name="report", content="bad", group="report")
write_artifact_manifest([
    {"type": "report", "name": "report", "path": "report.md"},
    {"type": "code", "name": "analysis_code", "path": "analysis.py"},
])
"""
    repaired_code = """
import sys
from pathlib import Path
sys.path.insert(0, "runtime")
from orbit_data.artifacts import save_text_artifact, write_artifact_manifest
save_text_artifact("report.md", "repair ok")
save_text_artifact("analysis.py", Path("work/analysis.py").read_text(encoding="utf-8"))
write_artifact_manifest([
    {"type": "report", "name": "repair_report", "path": "report.md"},
    {"type": "code", "name": "analysis_code", "path": "analysis.py"},
])
"""

    async def fake_llm_invoke(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield LLMStreamChunk(
            content_delta=f"```python\n{wrong_signature_code if calls == 1 else repaired_code}\n```",
            token_usage={"output_tokens": 1},
        )

    events: list[dict[str, Any]] = []
    result = run(
        DataWorkspaceAgentRuntime(llm_invoke=fake_llm_invoke).run(
            user_query="生成销售报告",
            history_messages=[HumanMessage(content="生成销售报告")],
            runtime_context=_runtime_context(data_file),
            on_event=events.append,
        )
    )

    assert result.error is None
    assert calls == 2
    assert any(event.get("step_id") == "code.preflight" for event in events)
    assert not any(event.get("step_id") == "sandbox.exec.1" for event in events)
    assert any(event.get("step_id") == "sandbox.exec.2" for event in events)
    assert sum(1 for event in events if event.get("step_kind") == "sandbox.upload") == 1


def test_data_workspace_code_validator_rejects_wrong_artifact_helper_signature():
    code = """
import sys
sys.path.insert(0, "runtime")
from orbit_data.artifacts import save_text_artifact, write_artifact_manifest
save_text_artifact(name="report", content="bad", group="report")
write_artifact_manifest([
    {"type": "report", "name": "report", "path": "report.md"},
    {"type": "code", "name": "analysis_code", "path": "analysis.py"},
])
"""

    error = DataCodeValidator().validate(code)

    assert error is not None
    assert "save_text_artifact" in error
    assert "name/content/group" in error


def test_data_workspace_code_validator_rejects_syntax_error():
    error = DataCodeValidator().validate("x = 'unterminated\n")

    assert error is not None
    assert "语法错误" in error


def test_data_workspace_code_validator_rejects_banned_import():
    code = """
import pandas as pd
from orbit_data.artifacts import save_text_artifact, write_artifact_manifest
save_text_artifact("report.md", "bad")
save_text_artifact("analysis.py", "bad")
write_artifact_manifest([
    {"type": "report", "name": "report", "path": "report.md"},
    {"type": "code", "name": "analysis_code", "path": "analysis.py"},
])
"""

    error = DataCodeValidator().validate(code)

    assert error is not None
    assert "pandas" in error


def test_data_workspace_code_validator_accepts_contract_compliant_code():
    code = """
import sys
from pathlib import Path
sys.path.insert(0, "runtime")
from orbit_data.artifacts import save_json_artifact, save_text_artifact, write_artifact_manifest
save_json_artifact("result.json", {"ok": True})
save_text_artifact("report.md", "ok")
save_text_artifact("analysis.py", Path("work/analysis.py").read_text(encoding="utf-8"))
write_artifact_manifest([
    {"type": "json", "name": "result", "path": "result.json"},
    {"type": "report", "name": "report", "path": "report.md"},
    {"type": "code", "name": "analysis_code", "path": "analysis.py"},
])
"""

    assert DataCodeValidator().validate(code) is None


def _runtime_context(path: Path) -> OrbitRuntimeContext:
    return OrbitRuntimeContext(
        request=OrbitRuntimeRequest(
            conversation_id="conv-1",
            assistant_message_id="msg-1",
            stream_id="stream-1",
            thread_id="thread-1",
            chat_mode="agent",
            agent_type="data_workspace_agent",
            input_messages=[HumanMessage(content="test")],
            llm_config=None,
            model="test-model",
            file_refs=[
                {
                    "file_id": "file-1",
                    "name": path.name,
                    "mime_type": "text/csv",
                    "file_size": path.stat().st_size,
                    "storage_path": str(path),
                }
            ],
        ),
        tool_runtime=OrbitToolRuntime(),
        stream_writer=None,
    )
