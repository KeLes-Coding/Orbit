"""StreamAdapter 单元测试：验证自定义事件映射和状态累积。"""

import asyncio
from uuid import uuid4

from app.services.langgraph_runtime.stream_adapter import StreamAdapter
from app.services.streaming import conversation_stream_store


def run(coro):
    return asyncio.run(coro)


def test_emit_content_delta_writes_to_stream_store_and_accumulates():
    """content_delta 事件应写入 stream_store 并累积到 content_parts。"""
    stream_id = f"stream_{uuid4()}"
    message_id = uuid4()

    async def _test():
        await conversation_stream_store.create_stream(
            stream_id=stream_id,
            conversation_id=uuid4(),
            message_id=message_id,
            user_id=uuid4(),
        )
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        await adapter.emit_custom_event({"type": "content_delta", "delta": "Hello"})
        await adapter.emit_custom_event({"type": "content_delta", "delta": " World"})

        accumulated = adapter.get_accumulated_state()
        assert accumulated["response_text"] == "Hello World"

        # 验证事件已写入 stream_store（replay 日志中应有两条 delta）
        stream = await conversation_stream_store.get_stream(stream_id)
        assert stream is not None
        delta_events = [e for e in stream.event_log if e.event == "message.delta"]
        assert len(delta_events) == 2
        assert delta_events[0].data["delta"] == "Hello"
        assert delta_events[1].data["delta"] == " World"

    run(_test())


def test_emit_reasoning_delta_accumulates_separately():
    """reasoning_delta 应独立于 content_delta 累积。"""
    stream_id = f"stream_{uuid4()}"
    message_id = uuid4()

    async def _test():
        await conversation_stream_store.create_stream(
            stream_id=stream_id,
            conversation_id=uuid4(),
            message_id=message_id,
            user_id=uuid4(),
        )
        adapter = StreamAdapter(stream_id=stream_id, message_id=message_id)

        await adapter.emit_custom_event({"type": "content_delta", "delta": "answer"})
        await adapter.emit_custom_event({"type": "reasoning_delta", "delta": "thinking"})

        accumulated = adapter.get_accumulated_state()
        assert accumulated["response_text"] == "answer"
        assert accumulated["reasoning_text"] == "thinking"

    run(_test())


def test_token_usage_is_stored():
    """token_usage 事件应存储用量信息。"""
    adapter = StreamAdapter(stream_id="test", message_id=uuid4())

    async def _test():
        await adapter.emit_custom_event(
            {"type": "token_usage", "usage": {"input_tokens": 10, "output_tokens": 20}}
        )
        accumulated = adapter.get_accumulated_state()
        assert accumulated["token_usage"]["input_tokens"] == 10
        assert accumulated["token_usage"]["output_tokens"] == 20

    run(_test())


def test_response_metadata_is_merged():
    """response_metadata 事件应合并到累积的 metadata 中。"""
    adapter = StreamAdapter(stream_id="test", message_id=uuid4())

    async def _test():
        await adapter.emit_custom_event(
            {"type": "response_metadata", "metadata": {"a": 1}}
        )
        await adapter.emit_custom_event(
            {"type": "response_metadata", "metadata": {"b": 2}}
        )
        accumulated = adapter.get_accumulated_state()
        assert accumulated["response_metadata"]["a"] == 1
        assert accumulated["response_metadata"]["b"] == 2

    run(_test())


def test_finish_reason_is_recorded():
    """finish_reason 事件应记录到 response_metadata。"""
    adapter = StreamAdapter(stream_id="test", message_id=uuid4())

    async def _test():
        await adapter.emit_custom_event(
            {"type": "finish_reason", "finish_reason": "stop"}
        )
        accumulated = adapter.get_accumulated_state()
        assert accumulated["response_metadata"]["finish_reason"] == "stop"

    run(_test())


def test_empty_event_is_ignored():
    """空类型事件不应产生副作用。"""
    adapter = StreamAdapter(stream_id="test", message_id=uuid4())

    async def _test():
        await adapter.emit_custom_event({})
        await adapter.emit_custom_event({"type": ""})
        accumulated = adapter.get_accumulated_state()
        assert accumulated["response_text"] == ""
        assert accumulated["reasoning_text"] == ""
        assert accumulated["token_usage"] == {}

    run(_test())


def test_get_accumulated_state_defaults():
    """未收到任何事件时，get_accumulated_state 应返回空默认值。"""
    adapter = StreamAdapter(stream_id="test", message_id=uuid4())
    state = adapter.get_accumulated_state()
    assert state["response_text"] == ""
    assert state["reasoning_text"] == ""
    assert state["token_usage"] == {}
    assert state["response_metadata"] == {}
