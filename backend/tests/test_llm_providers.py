import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.llm.providers.anthropic_provider import AnthropicProvider
from app.services.llm.providers.base import LLMRuntimeConfig


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
    assert chunks[-1].finish_reason == "end_turn"
    assert chunks[-1].token_usage == {"input_tokens": 12, "output_tokens": 34}
    assert chunks[-1].response_metadata["raw_id"] == "msg_123"
