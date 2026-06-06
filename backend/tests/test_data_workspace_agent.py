"""Data Workspace Agent 的 sandbox/artifact 回归测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.services.conversations.stream_run import ConversationStreamRunService
from app.services.langgraph_runtime.agents.data_workspace_agent.runtime import DataWorkspaceAgentRuntime
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext, OrbitRuntimeRequest
from app.services.tools.runtime import OrbitToolRuntime


def run(coro):
    return asyncio.run(coro)


def test_resolve_agent_type_routes_data_files_to_data_workspace_agent():
    assert (
        ConversationStreamRunService._resolve_agent_type(
            chat_mode="agent",
            file_refs=[{"name": "sales.csv", "mime_type": "text/csv"}],
        )
        == "data_workspace_agent"
    )
    assert (
        ConversationStreamRunService._resolve_agent_type(
            chat_mode="agent",
            file_refs=[{"name": "notes.md", "mime_type": "text/markdown"}],
        )
        == "web_agent"
    )
    assert (
        ConversationStreamRunService._resolve_agent_type(
            chat_mode="chat",
            file_refs=[{"name": "sales.csv", "mime_type": "text/csv"}],
        )
        is None
    )


def test_data_files_promote_chat_mode_to_agent_execution():
    assert (
        ConversationStreamRunService._resolve_execution_chat_mode(
            chat_mode="chat",
            file_refs=[{"name": "sales.csv", "mime_type": "text/csv"}],
        )
        == "agent"
    )
    assert (
        ConversationStreamRunService._resolve_execution_chat_mode(
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
    assert any(event.get("type") == "agent.run.status" for event in events)
    assert any(event.get("type") == "agent.run.artifact" for event in events)
    assert {"path": "artifact_manifest.json", "size": str(len(json.dumps({"artifacts": []})))} not in result.workspace_files
    assert any(item["path"] == "profiles/input_profiles.json" for item in result.workspace_files)


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
