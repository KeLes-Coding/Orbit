"""LangGraphChatRuntime 单元测试。"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.services.langgraph_runtime.chat_runtime import LangGraphChatRuntime
from app.services.langgraph_runtime.state import ChatState
from app.services.langgraph_runtime.stream_adapter import StreamAdapter
from app.services.llm_client import LLMClientError, LLMStreamChunk
from app.services.streaming import conversation_stream_store


def run(coro):
    return asyncio.run(coro)


def make_minimal_state(**overrides) -> ChatState:
    """构建测试用最小 ChatState。"""
    defaults = {
        "conversation_id": str(uuid4()),
        "assistant_message_id": str(uuid4()),
        "stream_id": f"stream_{uuid4()}",
        "thread_id": f"thread_{uuid4()}",
        "llm_config_id": str(uuid4()),
        "provider": "openai",
        "model": "gpt-test",
        "input_messages": [],
        "response_text": "",
        "reasoning_text": "",
        "token_usage": {},
        "response_metadata": {},
        "error": None,
    }
    defaults.update(overrides)
    return ChatState(**defaults)


def test_graph_compiles():
    """runtime 应能编译出 3 节点 graph。"""

    async def fake_stream():
        if False:
            yield None

    runtime = LangGraphChatRuntime(stream_factory=fake_stream)
    nodes = list(runtime._graph.nodes.keys())
    assert "prepare_context" in nodes
    assert "call_model" in nodes
    assert "finalize_message" in nodes


def test_prepare_context_is_noop():
    """Phase 1 的 prepare_context 仍然是空节点。"""

    async def fake_stream():
        if False:
            yield None

    runtime = LangGraphChatRuntime(stream_factory=fake_stream)
    state = make_minimal_state()
    assert runtime._prepare_context(state) == {}


def test_finalize_message_is_noop():
    """Phase 1 的 finalize_message 只保留节点边界。"""

    async def fake_stream():
        if False:
            yield None

    runtime = LangGraphChatRuntime(stream_factory=fake_stream)
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
        runtime = LangGraphChatRuntime(stream_factory=fake_stream)
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        try:
            final_state = await runtime.run_stream(
                state=make_minimal_state(
                    stream_id=stream_id,
                    assistant_message_id=str(message_id),
                ),
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
        runtime = LangGraphChatRuntime(stream_factory=failing_stream)
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        try:
            final_state = await runtime.run_stream(
                state=make_minimal_state(
                    stream_id=stream_id,
                    assistant_message_id=str(message_id),
                ),
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
        runtime = LangGraphChatRuntime(stream_factory=cancellable_stream)
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        try:
            final_state = await runtime.run_stream(
                state=make_minimal_state(
                    stream_id=stream_id,
                    assistant_message_id=str(message_id),
                ),
                stream_adapter=adapter,
            )
            assert final_state["error"] == "cancelled"
        finally:
            await conversation_stream_store.complete_stream(stream_id, retention_seconds=0)

    run(_test())
