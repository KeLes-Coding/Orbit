"""LangGraphChatRuntime 单元测试。"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from langchain_core.messages import HumanMessage

from app.services.conversations.stream_run import ConversationStreamRunService
from app.services.langgraph_runtime.agent_registry import AgentRegistry
from app.services.langgraph_runtime.agent_types import AgentExecutionResult
from app.services.langgraph_runtime.chat_runtime import LangGraphChatRuntime
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext, OrbitRuntimeRequest
from app.services.langgraph_runtime.state import ChatState
from app.services.langgraph_runtime.stream_adapter import StreamAdapter
from app.services.llm_client import LLMClientError, LLMStreamChunk
from app.services.streaming import conversation_stream_store


def run(coro):
    return asyncio.run(coro)


def make_minimal_state(**overrides) -> ChatState:
    """构建测试用最小 ChatState。"""
    defaults = {
        "input_messages": [],
        # Phase 2 新增
        "chat_mode": "chat",
        "execution_mode": "",
        "thought_events": [],
        "workspace_files": [],
        # 输出
        "response_text": "",
        "reasoning_text": "",
        "token_usage": {},
        "response_metadata": {},
        "error": None,
    }
    defaults.update(overrides)
    return ChatState(**defaults)


def make_runtime_context(
    *,
    stream_id: str | None = None,
    message_id: str | None = None,
    thread_id: str | None = None,
    chat_mode: str = "chat",
    input_messages: list | None = None,
) -> OrbitRuntimeContext:
    """构建测试用 runtime_context。"""
    return OrbitRuntimeContext(
        request=OrbitRuntimeRequest(
            conversation_id=str(uuid4()),
            assistant_message_id=message_id or str(uuid4()),
            stream_id=stream_id or f"stream_{uuid4()}",
            thread_id=thread_id or f"thread_{uuid4()}",
            chat_mode=chat_mode,
            agent_type="web_agent" if chat_mode == "agent" else None,
            input_messages=input_messages or [],
            llm_config=None,
            model="gpt-test",
        ),
        tool_runtime=None,
    )


def test_graph_compiles():
    """runtime 应能编译出 5 节点 graph（Phase 2 结构）。"""

    async def fake_stream():
        if False:
            yield None

    runtime = LangGraphChatRuntime(
        stream_factory=fake_stream,
        runtime_context=make_runtime_context(),
    )
    nodes = list(runtime._graph.nodes.keys())
    assert "prepare_context" in nodes
    assert "route_execution" in nodes
    assert "normal_chat" in nodes
    assert "agentic_chat" in nodes
    assert "finalize_message" in nodes


def test_agentic_chat_uses_registry_adapter():
    class FakeAgent:
        agent_type = "web_agent"

        async def run(self, *, user_query, history_messages, runtime_context, on_event):
            on_event({"type": "thought.planning", "phase": "planning", "text": "先搜一下", "meta": {}})
            return AgentExecutionResult(
                final_content=f"agent:{user_query}",
                reasoning_text="reasoning",
                thought_events=[{"type": "thought.planning", "phase": "planning", "text": "先搜一下", "meta": {}}],
            )

    async def fake_stream():
        if False:
            yield None

    registry = AgentRegistry()
    registry.register(FakeAgent())
    runtime_context = OrbitRuntimeContext(
        request=OrbitRuntimeRequest(
            conversation_id=str(uuid4()),
            assistant_message_id=str(uuid4()),
            stream_id=f"stream_{uuid4()}",
            thread_id=f"thread_{uuid4()}",
            chat_mode="agent",
            agent_type="web_agent",
            input_messages=[],
            llm_config=None,
            model="gpt-test",
        ),
        tool_runtime=None,
    )
    runtime = LangGraphChatRuntime(
        stream_factory=fake_stream,
        llm_invoke=lambda *_args, **_kwargs: None,  # pragma: no cover - won't be used
        runtime_context=runtime_context,
        agent_registry=registry,
    )

    state = make_minimal_state(
        chat_mode="agent",
        input_messages=[HumanMessage(content="帮我查 Orbit")],
    )
    result = run(runtime._agentic_chat(state))

    assert result["response_text"] == "agent:帮我查 Orbit"
    assert result["reasoning_text"] == "reasoning"
    assert result["thought_events"][0]["type"] == "thought.planning"


def test_prepare_context_is_noop():
    """Phase 1 的 prepare_context 仍然是空节点。"""

    async def fake_stream():
        if False:
            yield None

    runtime = LangGraphChatRuntime(
        stream_factory=fake_stream,
        runtime_context=make_runtime_context(),
    )
    state = make_minimal_state()
    assert runtime._prepare_context(state) == {}


def test_finalize_message_is_noop():
    """Phase 1 的 finalize_message 只保留节点边界。"""

    async def fake_stream():
        if False:
            yield None

    runtime = LangGraphChatRuntime(
        stream_factory=fake_stream,
        runtime_context=make_runtime_context(),
    )
    state = make_minimal_state()
    assert run(runtime._finalize_message(state)) == {}


def test_run_stream_accumulates_normalized_chunks():
    """runtime 应复用标准化 chunk，正确累积 content/reasoning/metadata。"""
    stream_id = f"stream_{uuid4()}"
    message_id = uuid4()

    async def fake_stream():
        yield LLMStreamChunk(content_delta="你", reasoning_delta="想", response_metadata={"provider": "openai"})
        yield LLMStreamChunk(content_delta="好", reasoning_delta="法", token_usage={"output_tokens": 2})
        yield LLMStreamChunk(finish_reason="stop")

    async def _test():
        await conversation_stream_store.create_stream(
            stream_id=stream_id,
            conversation_id=uuid4(),
            message_id=message_id,
            user_id=uuid4(),
        )
        runtime = LangGraphChatRuntime(
            stream_factory=fake_stream,
            runtime_context=make_runtime_context(
                stream_id=stream_id,
                message_id=str(message_id),
            ),
        )
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        try:
            final_state = await runtime.run_stream(
                state=make_minimal_state(),
                stream_adapter=adapter,
            )

            assert final_state["error"] is None
            assert final_state["response_text"] == "你好"
            assert final_state["reasoning_text"] == "想法"
            assert final_state["token_usage"] == {"output_tokens": 2}
            assert final_state["response_metadata"]["finish_reason"] == "stop"

            stream = await conversation_stream_store.get_stream(stream_id)
            assert stream is not None
            delta_events = [e for e in stream.event_log if e.event == "message.delta"]
            reasoning_events = [
                e for e in stream.event_log if e.event == "message.reasoning_delta"
            ]
            assert "".join(e.data["delta"] for e in delta_events) == "你好"
            assert "".join(e.data["delta"] for e in reasoning_events) == "想法"
        finally:
            await conversation_stream_store.complete_stream(stream_id, retention_seconds=0)

    run(_test())


def test_run_stream_uses_runtime_context_thread_id_when_state_is_slim():
    stream_id = f"stream_{uuid4()}"
    message_id = uuid4()

    async def fake_stream():
        yield LLMStreamChunk(content_delta="ok")

    async def _test():
        await conversation_stream_store.create_stream(
            stream_id=stream_id,
            conversation_id=uuid4(),
            message_id=message_id,
            user_id=uuid4(),
        )
        runtime_context = make_runtime_context(
            stream_id=stream_id,
            message_id=str(message_id),
            thread_id="thread-from-context",
            chat_mode="chat",
        )
        runtime = LangGraphChatRuntime(stream_factory=fake_stream, runtime_context=runtime_context)
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        try:
            final_state = await runtime.run_stream(
                state=ChatState(
                    input_messages=[],
                    chat_mode="chat",
                    execution_mode="",
                    response_text="",
                    reasoning_text="",
                    token_usage={},
                    response_metadata={},
                    thought_events=[],
                    workspace_files=[],
                    error=None,
                ),
                stream_adapter=adapter,
            )
            assert final_state["response_text"] == "ok"
        finally:
            await conversation_stream_store.complete_stream(stream_id, retention_seconds=0)

    run(_test())


def test_run_stream_returns_llm_error():
    """LLMClientError 应被写回最终 state，而不是直接吞掉。"""
    stream_id = f"stream_{uuid4()}"
    message_id = uuid4()

    async def failing_stream():
        raise LLMClientError("模型服务请求失败")
        yield  # pragma: no cover

    async def _test():
        await conversation_stream_store.create_stream(
            stream_id=stream_id,
            conversation_id=uuid4(),
            message_id=message_id,
            user_id=uuid4(),
        )
        runtime = LangGraphChatRuntime(
            stream_factory=failing_stream,
            runtime_context=make_runtime_context(
                stream_id=stream_id,
                message_id=str(message_id),
            ),
        )
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        try:
            final_state = await runtime.run_stream(
                state=make_minimal_state(),
                stream_adapter=adapter,
            )
            assert final_state["error"] == "模型服务请求失败"
        finally:
            await conversation_stream_store.complete_stream(stream_id, retention_seconds=0)

    run(_test())


def test_run_stream_returns_cancelled_when_stream_store_is_cancelled():
    """cancel 信号应在节点内部被识别，并转成统一 cancelled 状态。"""
    stream_id = f"stream_{uuid4()}"
    message_id = uuid4()

    async def cancellable_stream():
        yield LLMStreamChunk(content_delta="已生成")
        yield LLMStreamChunk(content_delta="不会继续")

    async def _test():
        await conversation_stream_store.create_stream(
            stream_id=stream_id,
            conversation_id=uuid4(),
            message_id=message_id,
            user_id=uuid4(),
        )
        await conversation_stream_store.cancel(message_id=message_id)
        runtime = LangGraphChatRuntime(
            stream_factory=cancellable_stream,
            runtime_context=make_runtime_context(
                stream_id=stream_id,
                message_id=str(message_id),
            ),
        )
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        try:
            final_state = await runtime.run_stream(
                state=make_minimal_state(),
                stream_adapter=adapter,
            )
            assert final_state["error"] == "cancelled"
        finally:
            await conversation_stream_store.complete_stream(stream_id, retention_seconds=0)

    run(_test())


def test_merge_langgraph_persisted_output_uses_final_state_when_stream_deltas_are_empty():
    """agentic_chat 若未发出 content_delta，外层落库仍应能回退到 final_state。"""
    merged = ConversationStreamRunService._merge_langgraph_persisted_output(
        accumulated={
            "response_text": "",
            "reasoning_text": "",
            "token_usage": {},
            "response_metadata": {},
        },
        final_state=make_minimal_state(
            response_text="最终正文",
            reasoning_text="最终推理",
            token_usage={"output_tokens": 12},
            response_metadata={"provider": "deepseek", "model": "deepseek-v4"},
        ),
    )

    assert merged["response_text"] == "最终正文"
    assert merged["reasoning_text"] == "最终推理"
    assert merged["token_usage"] == {"output_tokens": 12}
    assert merged["response_metadata"]["provider"] == "deepseek"


def test_merge_langgraph_persisted_output_persists_thought_events():
    """thought events 应随 response_metadata 一起持久化，供刷新后恢复。"""
    merged = ConversationStreamRunService._merge_langgraph_persisted_output(
        accumulated={
            "response_text": "最终正文",
            "reasoning_text": "",
            "token_usage": {},
            "response_metadata": {},
            "thought_events": [
                {
                    "message_id": "mid",
                    "type": "thought.planning",
                    "phase": "planning",
                    "text": "先搜索。",
                    "meta": {},
                }
            ],
        },
        final_state=make_minimal_state(response_metadata={"provider": "deepseek"}),
    )

    thought_events = merged["response_metadata"].get("thought_events")
    assert isinstance(thought_events, list)
    assert thought_events[0]["type"] == "thought.planning"


def test_merge_langgraph_persisted_output_prefers_compacted_final_thought_events():
    """落库时应优先使用 final_state 中压缩后的 thought_events，而不是流式碎片。"""
    merged = ConversationStreamRunService._merge_langgraph_persisted_output(
        accumulated={
            "response_text": "最终正文",
            "reasoning_text": "推理",
            "token_usage": {},
            "response_metadata": {},
            "thought_events": [
                {"type": "thought.planning", "phase": "planning", "text": "先", "meta": {}},
                {"type": "thought.planning", "phase": "planning", "text": "搜索", "meta": {}},
            ],
        },
        final_state=make_minimal_state(
            response_metadata={"provider": "deepseek"},
            thought_events=[
                {"type": "thought.planning", "phase": "planning", "text": "先搜索", "meta": {}},
                {"type": "thought.tool", "phase": "loop", "text": "调用工具：websearch", "meta": {"tool": "websearch"}},
            ],
        ),
    )

    thought_events = merged["response_metadata"].get("thought_events")
    assert isinstance(thought_events, list)
    assert thought_events == [
        {"type": "thought.planning", "phase": "planning", "text": "先搜索", "meta": {}},
        {"type": "thought.tool", "phase": "loop", "text": "调用工具：websearch", "meta": {"tool": "websearch"}},
    ]
