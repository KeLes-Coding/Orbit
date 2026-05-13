"""Agent runtime 单元测试。

测试范围：
  - 工具安全路径检查
  - 运行时调度器分发逻辑
  - ClassicChatRuntime 基本事件产出
  - Agent 流式事件适配

遵循现有测试模式：手写 fake、同步函数 + asyncio.run()、monkeypatch。
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.agents.tools import _safe_path, list_files, read_file, search_code
from app.services.runtime.dispatcher import RuntimeDispatcher


def run(coro):
    """将异步协程封装为同步调用，与现有测试模式保持一致。"""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════
# 工具安全测试
# ═══════════════════════════════════════════════════════════════════

class TestSafePath:
    """路径防逃逸测试。"""

    def test_normal_path_within_root(self):
        path = _safe_path("backend/app")
        assert path.is_absolute()
        assert str(path).startswith("/home/keles/WorkSpace/Orbit")

    def test_empty_path_defaults_to_root(self):
        path = _safe_path("")
        assert str(path).endswith("Orbit")

    def test_dot_path_defaults_to_root(self):
        path = _safe_path(".")
        assert str(path).endswith("Orbit")

    def test_parent_traversal_rejected(self):
        with pytest.raises(ValueError, match="超出项目范围"):
            _safe_path("../../etc/passwd")

    def test_absolute_path_escape_rejected(self):
        with pytest.raises(ValueError, match="超出项目范围"):
            _safe_path("/etc/passwd")


class TestListFiles:
    """list_files 工具测试。"""

    def test_lists_directory_contents(self, tmp_path, monkeypatch):
        import app.services.agents.tools as tools_mod
        monkeypatch.setattr(tools_mod, "_PROJECT_ROOT", tmp_path)
        (tmp_path / "file_a.py").write_text("print('a')")
        (tmp_path / "subdir").mkdir()
        result = run(list_files.ainvoke({"directory": "."}))
        assert "file_a.py" in result
        assert "subdir/" in result

    def test_nonexistent_directory(self):
        result = run(list_files.ainvoke({"directory": "nonexistent_xyz"}))
        assert "不存在" in result

    def test_file_not_directory(self, tmp_path, monkeypatch):
        import app.services.agents.tools as tools_mod
        monkeypatch.setattr(tools_mod, "_PROJECT_ROOT", tmp_path)
        (tmp_path / "file.txt").write_text("content")
        result = run(list_files.ainvoke({"directory": "file.txt"}))
        assert "不是目录" in result


class TestReadFile:
    """read_file 工具测试。"""

    def test_reads_file_content(self, tmp_path, monkeypatch):
        import app.services.agents.tools as tools_mod
        monkeypatch.setattr(tools_mod, "_PROJECT_ROOT", tmp_path)
        (tmp_path / "test.py").write_text("hello world\\nline 2")
        result = run(read_file.ainvoke({"file_path": "test.py"}))
        assert "hello world" in result
        assert "line 2" in result

    def test_nonexistent_file(self):
        result = run(read_file.ainvoke({"file_path": "nonexistent.py"}))
        assert "不存在" in result

    def test_truncates_long_file(self, tmp_path, monkeypatch):
        import app.services.agents.tools as tools_mod
        monkeypatch.setattr(tools_mod, "_PROJECT_ROOT", tmp_path)
        content = "\n".join(f"line {i}" for i in range(3000))
        (tmp_path / "long.txt").write_text(content)
        result = run(read_file.ainvoke({"file_path": "long.txt"}))
        assert "已截断" in result
        assert "3000" in result


class TestSearchCode:
    """search_code 工具测试。"""

    def test_finds_matches(self, tmp_path, monkeypatch):
        import app.services.agents.tools as tools_mod
        monkeypatch.setattr(tools_mod, "_PROJECT_ROOT", tmp_path)
        (tmp_path / "code.py").write_text("def hello():\\n    return 'world'")
        result = run(search_code.ainvoke({"query": "hello", "path": "."}))
        assert "hello" in result

    def test_no_match_found(self, tmp_path, monkeypatch):
        import app.services.agents.tools as tools_mod
        monkeypatch.setattr(tools_mod, "_PROJECT_ROOT", tmp_path)
        (tmp_path / "code.py").write_text("x = 1")
        result = run(search_code.ainvoke({"query": "nonexistent_xyzzy", "path": "."}))
        assert "未找到" in result


# ═══════════════════════════════════════════════════════════════════
# 运行时调度器测试
# ═══════════════════════════════════════════════════════════════════

class TestRuntimeDispatcher:
    """RuntimeDispatcher 分发逻辑测试。"""

    def test_chat_mode_returns_classic(self):
        dispatcher = RuntimeDispatcher()
        runtime = dispatcher.dispatch("chat")
        from app.services.runtime.classic import ClassicChatRuntime
        assert isinstance(runtime, ClassicChatRuntime)

    def test_rag_mode_returns_classic(self):
        dispatcher = RuntimeDispatcher()
        runtime = dispatcher.dispatch("rag")
        from app.services.runtime.classic import ClassicChatRuntime
        assert isinstance(runtime, ClassicChatRuntime)

    def test_tool_mode_returns_classic(self):
        dispatcher = RuntimeDispatcher()
        runtime = dispatcher.dispatch("tool")
        from app.services.runtime.classic import ClassicChatRuntime
        assert isinstance(runtime, ClassicChatRuntime)

    def test_agent_mode_returns_agent_runtime(self):
        dispatcher = RuntimeDispatcher()
        runtime = dispatcher.dispatch("agent")
        from app.services.agents.runtime import LangGraphAgentRuntime
        assert isinstance(runtime, LangGraphAgentRuntime)

    def test_unknown_mode_falls_back_to_classic(self):
        dispatcher = RuntimeDispatcher()
        runtime = dispatcher.dispatch("unknown_mode")
        from app.services.runtime.classic import ClassicChatRuntime
        assert isinstance(runtime, ClassicChatRuntime)


# ═══════════════════════════════════════════════════════════════════
# ClassicChatRuntime 基本事件测试
# ═══════════════════════════════════════════════════════════════════

class TestClassicChatRuntime:
    """ClassicChatRuntime 基本功能测试。"""

    def test_emits_completed_event(self, monkeypatch):
        """模拟 LLM 返回单条内容，验证产出 message.completed 事件。"""
        from app.services.runtime.classic import ClassicChatRuntime
        from app.services.runtime.types import RunContext, UnifiedStreamEvent
        from app.services.llm_client import LLMStreamChunk

        # 构造 fake LLMClient 的 stream 方法
        async def fake_stream(*, config, messages, summary, model):
            yield LLMStreamChunk(content_delta="Hello", token_usage={"total": 10})
            yield LLMStreamChunk(content_delta=" World", finish_reason="stop")

        # 用 SimpleNamespace 构造最小 RunContext
        cancel_event = asyncio.Event()
        fake_config = SimpleNamespace(provider="openai")
        fake_assistant_msg = SimpleNamespace(id=uuid4(), model="gpt-4o")

        ctx = RunContext(
            session=SimpleNamespace(),
            conversation=SimpleNamespace(summary=None),
            assistant_message=fake_assistant_msg,
            llm_config=fake_config,
            history_messages=[],
            stream_id="test_stream",
            cancel_event=cancel_event,
        )

        # 使用 monkeypatch 替换 LLMClient.stream
        import app.services.runtime.classic as classic_mod
        original_client = classic_mod.LLMClient

        class FakeLLMClient:
            def stream(self, *, config, messages, summary, model):
                return fake_stream(config=config, messages=messages, summary=summary, model=model)

        monkeypatch.setattr(classic_mod, "LLMClient", FakeLLMClient)

        runtime = ClassicChatRuntime()
        events = list(run(_collect_events(runtime.execute(ctx))))

        # 应该有 message.delta 和 message.completed 事件
        delta_events = [e for e in events if e.event == "message.delta"]
        completed_events = [e for e in events if e.event == "message.completed"]
        assert len(delta_events) == 2
        assert delta_events[0].data["delta"] == "Hello"
        assert delta_events[1].data["delta"] == " World"
        assert len(completed_events) == 1
        assert completed_events[0].data["content"] == "Hello World"
        assert "stop" in str(completed_events[0].data.get("response_metadata", {}))


async def _collect_events(iterator):
    """辅助：收集 async iterator 中的所有事件。"""
    events = []
    async for event in iterator:
        events.append(event)
    return events
