import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.llm_client import LLMClient
from app.services.llm.providers.anthropic_provider import AnthropicProvider
from app.services.llm.providers.base import LLMRuntimeConfig
from app.services.llm.providers.openai_provider import OpenAICompatibleProvider


def run(coro):
    return asyncio.run(coro)


def test_anthropic_build_chat_model_maps_generation_options():
    provider = AnthropicProvider()
    config = LLMRuntimeConfig(
        provider="anthropic",
        model="claude-sonnet-test",
        base_url="https://api.deepseek.com/anthropic",
        api_key="test-key",
        provider_options={
            "generation": {
                "max_tokens": 2048,
                "stop_sequences": ["DONE"],
                "thinking": {"type": "enabled"},
            }
        },
    )

    model = provider.build_chat_model(config)

    assert model.max_tokens == 2048
    assert model.stop_sequences == ["DONE"]
    assert model.thinking == {"type": "enabled"}
    assert str(model.anthropic_api_url).rstrip("/") == "https://api.deepseek.com/anthropic"


def test_anthropic_stream_chat_emits_reasoning_and_finish_reason(monkeypatch):
    captured = {}

    class FakeStream:
        def __init__(self, events):
            self._events = events

        def __aiter__(self):
            self._iter = iter(self._events)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeMessagesAPI:
        async def create(self, **kwargs):
            captured["request_kwargs"] = kwargs
            return FakeStream(
                [
                    SimpleNamespace(
                        type="message_start",
                        message=SimpleNamespace(id="msg_123", model="deepseek-v4-pro"),
                    ),
                    SimpleNamespace(
                        type="content_block_delta",
                        delta=SimpleNamespace(type="thinking_delta", thinking="step-1"),
                    ),
                    SimpleNamespace(
                        type="content_block_delta",
                        delta=SimpleNamespace(type="text_delta", text="final-answer"),
                    ),
                    SimpleNamespace(
                        type="message_delta",
                        delta=SimpleNamespace(stop_reason="end_turn"),
                        usage={"input_tokens": 12, "output_tokens": 34},
                    ),
                ]
            )

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.messages = FakeMessagesAPI()

    monkeypatch.setattr("app.services.llm.providers.anthropic_provider.anthropic.AsyncAnthropic", FakeAsyncAnthropic)

    provider = AnthropicProvider()
    config = LLMRuntimeConfig(
        provider="anthropic",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/anthropic",
        api_key="test-key",
        provider_options={"generation": {"thinking": {"type": "enabled"}}},
    )

    async def collect_chunks():
        chunks = []
        async for chunk in provider.stream_chat(
            config=config,
            messages=[
                SystemMessage(content="You are helpful."),
                HumanMessage(content="Hello"),
            ],
        ):
            chunks.append(chunk)
        return chunks

    chunks = run(collect_chunks())

    assert captured["client_kwargs"]["base_url"] == "https://api.deepseek.com/anthropic"
    assert captured["request_kwargs"]["stream"] is True
    assert captured["request_kwargs"]["max_tokens"] == 4096
    assert captured["request_kwargs"]["thinking"] == {"type": "enabled"}
    assert captured["request_kwargs"]["system"] == [{"type": "text", "text": "You are helpful."}]
    assert captured["request_kwargs"]["messages"] == [{"role": "user", "content": "Hello"}]

    assert [chunk.reasoning_delta for chunk in chunks if chunk.reasoning_delta] == ["step-1"]
    assert [chunk.content_delta for chunk in chunks if chunk.content_delta] == ["final-answer"]
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].token_usage == {
        "input_tokens": 12,
        "output_tokens": 34,
        "total_tokens": 46,
        "raw": {"input_tokens": 12, "output_tokens": 34},
    }
    assert chunks[-1].response_metadata["raw_id"] == "msg_123"
    assert chunks[-1].response_metadata["provider_finish_reason"] == "end_turn"
    assert chunks[-1].response_metadata["finish_reason"] == "stop"


def test_anthropic_message_blocks_convert_image_url_and_drop_reasoning():
    provider = AnthropicProvider()
    content = [
        {"type": "text", "text": "look at this"},
        {"type": "reasoning", "text": "hidden chain of thought"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,ZmFrZS1pbWFnZQ=="},
        },
    ]

    blocks = provider._to_anthropic_content_blocks(content)

    assert blocks == [
        {"type": "text", "text": "look at this"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": "ZmFrZS1pbWFnZQ==",
            },
        },
    ]


def test_openai_message_content_value_keeps_image_blocks_and_filters_reasoning():
    provider = OpenAICompatibleProvider()
    content = [
        {"type": "text", "text": "describe"},
        {"type": "thinking", "text": "internal-only"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,ZmFrZS1pbWFnZQ=="},
        },
    ]

    value = provider._message_content_value(content)

    assert value == content


def test_openai_message_content_value_flattens_text_only_blocks():
    provider = OpenAICompatibleProvider()
    content = [
        {"type": "text", "text": "hello "},
        {"type": "reasoning", "text": "secret"},
        {"type": "text", "text": "world"},
    ]

    value = provider._message_content_value(content)

    assert value == "hello world"


def test_openai_stream_chunk_normalizes_finish_reason_and_usage():
    provider = OpenAICompatibleProvider()
    chunk = SimpleNamespace(
        id="chatcmpl_123",
        model="qwen3.6-flash",
        usage=SimpleNamespace(
            model_dump=lambda: {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "completion_tokens_details": {"reasoning_tokens": 3},
            }
        ),
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content="ok", reasoning_content="think"),
                finish_reason="stop",
            )
        ],
    )

    normalized = provider._to_provider_stream_chunk(
        chunk=chunk,
        provider="qwen",
        model="qwen3.6-flash",
    )

    assert normalized.finish_reason == "stop"
    assert normalized.token_usage == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "reasoning_tokens": 3,
        "raw": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }
    assert normalized.response_metadata["provider_finish_reason"] == "stop"


def test_openai_stream_chunk_extracts_tool_call_chunks():
    provider = OpenAICompatibleProvider()
    chunk = SimpleNamespace(
        id="chatcmpl_tool_1",
        model="qwen3.6-flash",
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "index": 0,
                            "type": "function",
                            "function": {
                                "name": "search_docs",
                                "arguments": "{\"query\":\"langgraph\"}",
                            },
                        }
                    ],
                ),
                finish_reason=None,
            )
        ],
    )

    normalized = provider._to_provider_stream_chunk(
        chunk=chunk,
        provider="qwen",
        model="qwen3.6-flash",
    )

    assert normalized.tool_calls == [
        {
            "id": "call_1",
            "name": "search_docs",
            "args": "{\"query\":\"langgraph\"}",
            "index": 0,
            "type": "function",
        }
    ]


def test_llm_client_extract_tool_calls_prefers_langchain_normalized_shape():
    client = LLMClient()
    response = SimpleNamespace(
        tool_calls=[
            {
                "id": "call_1",
                "name": "search_docs",
                "args": {"query": "langgraph"},
                "type": "tool_call",
            }
        ]
    )

    tool_calls = client._extract_tool_calls(response)

    assert tool_calls == [
        {
            "id": "call_1",
            "name": "search_docs",
            "args": {"query": "langgraph"},
            "type": "tool_call",
        }
    ]


def test_llm_client_extract_tool_calls_falls_back_to_raw_openai_shape():
    client = LLMClient()
    response = SimpleNamespace(
        additional_kwargs={
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": "{\"q\":\"deepseek v4\"}",
                    },
                }
            ]
        }
    )

    tool_calls = client._extract_tool_calls(response)

    assert tool_calls == [
        {
            "id": "call_2",
            "name": "web_search",
            "args": "{\"q\":\"deepseek v4\"}",
            "type": "function",
        }
    ]


def test_llm_client_extract_stream_tool_calls_prefers_tool_call_chunks():
    client = LLMClient()
    chunk = SimpleNamespace(
        tool_call_chunks=[
            {
                "id": "call_3",
                "name": "run_sql",
                "args": "{\"sql\":\"select 1\"}",
                "index": 1,
                "type": "tool_call_chunk",
            }
        ]
    )

    tool_calls = client._extract_stream_tool_calls(chunk)

    assert tool_calls == [
        {
            "id": "call_3",
            "name": "run_sql",
            "args": "{\"sql\":\"select 1\"}",
            "index": 1,
            "type": "tool_call_chunk",
        }
    ]
