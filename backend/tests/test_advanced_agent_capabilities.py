"""Agent 高级能力测试（Iteration 4）。

覆盖：
  - Memory 注入（AGENTS.md 路径解析）
  - HITL interrupt 配置构建
  - 文件系统权限规则构建
  - RunStatus.interrupted 枚举值
  - Subagent 事件结构
  - GraphInterrupt 检测
  - run 中断状态持久化
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.enums import RunStatus


def run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════
# Memory 注入测试
# ═══════════════════════════════════════════════════════════════════

class TestMemoryInjection:
    """AGENTS.md 内存注入测试。"""

    def test_resolve_memory_returns_none_when_missing(self):
        """AGENTS.md 文件不存在时返回 None。"""
        from app.services.agents.factory import _resolve_memory_sources
        from pathlib import Path

        # 指向一个不存在的路径
        import app.services.agents.factory as factory_mod
        original = factory_mod._PROJECT_AGENTS_MD
        factory_mod._PROJECT_AGENTS_MD = Path("/nonexistent/path/AGENTS.md")
        try:
            assert _resolve_memory_sources() is None
        finally:
            factory_mod._PROJECT_AGENTS_MD = original

    def test_resolve_memory_returns_path_when_exists(self, tmp_path):
        """AGENTS.md 文件存在时返回路径列表。"""
        from app.services.agents.factory import _resolve_memory_sources
        import app.services.agents.factory as factory_mod

        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Project Context")
        original = factory_mod._PROJECT_AGENTS_MD
        factory_mod._PROJECT_AGENTS_MD = agents_md
        try:
            result = _resolve_memory_sources()
            assert result is not None
            assert len(result) == 1
            assert result[0] == str(agents_md)
        finally:
            factory_mod._PROJECT_AGENTS_MD = original


class TestDeepSeekAnthropicRouting:
    """DeepSeek V4 Pro 在 agent 模式下的协议切换测试。"""

    @pytest.mark.parametrize("model_name", ["deepseek-v4-pro", "deepseek-v4-flash"])
    def test_routes_deepseek_v4_models_to_anthropic_provider(self, model_name):
        from app.services.agents.factory import _resolve_agent_model_provider
        from app.core.crypto import encrypt_secret

        llm_config = SimpleNamespace(
            provider="deepseek",
            models=[model_name],
            base_url="https://api.deepseek.com",
            api_key_ciphertext=encrypt_secret("sk-test"),
            provider_options={},
        )

        provider, runtime_config = _resolve_agent_model_provider(llm_config)
        assert provider.provider == "anthropic"
        assert runtime_config.provider == "anthropic"
        assert runtime_config.base_url == "https://api.deepseek.com/anthropic"
        assert runtime_config.model == model_name

    def test_keeps_non_v4_deepseek_on_original_provider(self):
        from app.services.agents.factory import _resolve_agent_model_provider
        from app.core.crypto import encrypt_secret

        llm_config = SimpleNamespace(
            provider="deepseek",
            models=["deepseek-chat"],
            base_url="https://api.deepseek.com",
            api_key_ciphertext=encrypt_secret("sk-test"),
            provider_options={},
        )

        provider, runtime_config = _resolve_agent_model_provider(llm_config)
        assert provider.provider == "deepseek"
        assert runtime_config.provider == "deepseek"
        assert runtime_config.base_url == "https://api.deepseek.com"

    @pytest.mark.parametrize(
        ("base_url", "expected"),
        [
            (None, "https://api.deepseek.com/anthropic"),
            ("https://api.deepseek.com", "https://api.deepseek.com/anthropic"),
            ("https://api.deepseek.com/anthropic", "https://api.deepseek.com/anthropic"),
            ("https://proxy.example.com/deepseek", "https://proxy.example.com/deepseek/anthropic"),
        ],
    )
    def test_normalizes_anthropic_compatible_base_url(self, base_url, expected):
        from app.services.agents.factory import _to_anthropic_compatible_base_url

        assert _to_anthropic_compatible_base_url(base_url) == expected


class TestBuiltinToolsToggle:
    """deepagents 内置工具开关测试。"""

    def test_builtin_tools_disabled_by_default(self, monkeypatch):
        from app.services.agents.factory import _enable_builtin_tools

        monkeypatch.delenv("ORBIT_AGENT_ENABLE_BUILTIN_TOOLS", raising=False)
        assert _enable_builtin_tools() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_builtin_tools_can_be_enabled(self, monkeypatch, value):
        from app.services.agents.factory import _enable_builtin_tools

        monkeypatch.setenv("ORBIT_AGENT_ENABLE_BUILTIN_TOOLS", value)
        assert _enable_builtin_tools() is True


# ═══════════════════════════════════════════════════════════════════
# HITL 配置测试
# ═══════════════════════════════════════════════════════════════════

class TestHITLConfig:
    """interrupt_on 配置构建测试。"""

    def test_build_interrupt_on_covers_write_operations(self):
        from app.services.agents.factory import _build_interrupt_on

        config = _build_interrupt_on()
        assert "write_file" in config
        assert "edit_file" in config
        assert "execute" in config

    def test_write_file_allows_all_decisions(self):
        from app.services.agents.factory import _build_interrupt_on

        config = _build_interrupt_on()
        # True 表示允许所有决策类型（approve / edit / reject / respond）
        assert config["write_file"] is True

    def test_execute_restricts_to_approve_reject(self):
        from app.services.agents.factory import _build_interrupt_on

        config = _build_interrupt_on()
        execute_config = config["execute"]
        assert execute_config["allowed_decisions"] == ["approve", "reject"]


# ═══════════════════════════════════════════════════════════════════
# 权限配置测试
# ═══════════════════════════════════════════════════════════════════

class TestPermissionsConfig:
    """FilesystemPermission 规则构建测试。"""

    def test_build_permissions_returns_two_rules(self):
        from app.services.agents.factory import _build_permissions

        rules = _build_permissions()
        assert len(rules) == 2

    def test_first_rule_allows_read_everywhere(self):
        from app.services.agents.factory import _build_permissions

        rules = _build_permissions()
        read_rule = rules[0]
        assert read_rule.operations == ["read"]
        assert read_rule.paths == ["/"]
        assert read_rule.mode == "allow"

    def test_second_rule_allows_write_in_sandbox(self):
        from app.services.agents.factory import _build_permissions

        rules = _build_permissions()
        write_rule = rules[1]
        assert write_rule.operations == ["write"]
        assert "/tmp/sandbox/" in write_rule.paths
        assert write_rule.mode == "allow"


# ═══════════════════════════════════════════════════════════════════
# RunStatus.interrupted 测试
# ═══════════════════════════════════════════════════════════════════

class TestRunStatusInterrupted:
    """RunStatus 枚举新增 interrupted 状态测试。"""

    def test_interrupted_is_valid_status(self):
        assert RunStatus.interrupted == "interrupted"

    def test_all_statuses_defined(self):
        expected = {"streaming", "completed", "failed", "cancelled", "interrupted"}
        actual = {member.value for member in RunStatus}
        assert expected == actual


# ═══════════════════════════════════════════════════════════════════
# Subagent 事件测试
# ═══════════════════════════════════════════════════════════════════

class TestSubagentEvents:
    """Subagent 生命周期事件结构测试。"""

    def test_subagent_started_event_structure(self):
        msg_id = str(uuid4())
        event_data = {
            "message_id": msg_id,
            "subagent_name": "code_reviewer",
        }
        assert event_data["subagent_name"] == "code_reviewer"
        assert "message_id" in event_data

    def test_subagent_delta_event_structure(self):
        msg_id = str(uuid4())
        event_data = {
            "message_id": msg_id,
            "subagent_name": "code_reviewer",
            "delta": "Found 3 issues in app.py",
        }
        assert event_data["delta"]
        assert event_data["subagent_name"] == "code_reviewer"

    def test_subagent_completed_event_structure(self):
        msg_id = str(uuid4())
        event_data = {
            "message_id": msg_id,
            "subagent_name": "code_reviewer",
            "result": "Review complete: 0 critical, 3 minor",
        }
        assert event_data["result"]
        assert event_data["subagent_name"] == "code_reviewer"

    def test_standard_node_names_excludes_subagent_nodes(self):
        from app.services.agents.stream_adapter import _STANDARD_NODE_NAMES

        # 标准节点名不应被误判为 subagent
        assert "agent" in _STANDARD_NODE_NAMES
        assert "tools" in _STANDARD_NODE_NAMES
        # 自定义 subagent 节点名不在标准集合中
        assert "code_reviewer" not in _STANDARD_NODE_NAMES
        assert "task" not in _STANDARD_NODE_NAMES


# ═══════════════════════════════════════════════════════════════════
# GraphInterrupt 检测测试
# ═══════════════════════════════════════════════════════════════════

class TestGraphInterruptDetection:
    """_is_graph_interrupt 检测函数的正确性。"""

    def test_recognizes_graph_interrupt(self):
        from app.services.conversations.stream_run import _is_graph_interrupt
        from langgraph.errors import GraphInterrupt

        exc = GraphInterrupt()
        assert _is_graph_interrupt(exc) is True

    def test_rejects_regular_exception(self):
        from app.services.conversations.stream_run import _is_graph_interrupt

        exc = ValueError("something went wrong")
        assert _is_graph_interrupt(exc) is False

    def test_rejects_runtime_error(self):
        from app.services.conversations.stream_run import _is_graph_interrupt

        exc = RuntimeError("generic error")
        assert _is_graph_interrupt(exc) is False


# ═══════════════════════════════════════════════════════════════════
# Run 中断状态持久化测试
# ═══════════════════════════════════════════════════════════════════

class TestInterruptRunPersistence:
    """ConversationRunRepository.interrupt_run 测试。"""

    def test_interrupt_run_sets_status_without_finished_at(self):
        from app.repositories.conversation_run import ConversationRunRepository
        from app.models.conversation_run import ConversationRun
        from datetime import datetime, timezone

        class FakeInterruptSession:
            def __init__(self):
                self._added = []
                self.commits = 0

            def add(self, obj):
                self._added.append(obj)

            async def flush(self):
                return None

            async def refresh(self, obj):
                return None

        session = FakeInterruptSession()

        repo = ConversationRunRepository(session)

        run_record = ConversationRun(
            conversation_id=uuid4(),
            user_id=uuid4(),
            thread_id="th_int",
            runtime_kind="langgraph_agent",
            chat_mode="agent",
            status="streaming",
            started_at=datetime.now(timezone.utc),
            metadata_={},
        )

        updated = run(repo.interrupt_run(run_record))
        assert updated.status == "interrupted"
        assert updated.finished_at is None  # interrupted 不设置 finished_at
