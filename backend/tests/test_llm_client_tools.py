import asyncio
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import ToolMessage

from app.services.llm_client import LLMClient
from app.services.llm.providers.base import LLMRuntimeConfig
from app.services.tools import ToolExecutionResult


def run(coro):
    return asyncio.run(coro)


def make_history_message(*, role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        conversation_id=uuid4(),
        sequence_no=1,
        langgraph_message_id=None,
        parent_message_id=None,
        active_child_message_id=None,
        depth=0,
        source_message_id=None,
        revision_type="normal",
        role=role,
        content=content,
        reasoning_content="",
        content_parts=[],
        status="completed",
        llm_config_id=None,
        provider=None,
        model=None,
        token_usage={},
        response_metadata={},
        created_at="2026-05-12T00:00:00Z",
    )


class FakeProvider:
    def normalize_finish_reason(self, finish_reason):
        if finish_reason == "end_turn":
            return "stop"
        return finish_reason

    def normalize_token_usage(self, usage):
        return usage if isinstance(usage, dict) else {}


class FakeToolRuntime:
    def get_langchain_tools(self):
        return []

    async def execute_tool_calls(self, tool_calls):
        return [
            ToolExecutionResult(
                tool_call_id=tool_calls[0]["id"],
                name=tool_calls[0]["name"],
                args={"location": "Beijing"},
                output="北京当前天气：晴朗，气温 25°C。",
            )
        ]


class FakeToolLoopModel:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return SimpleNamespace(
                content="",
                response_metadata={},
                usage_metadata={"input_tokens": 10, "output_tokens": 3},
                tool_calls=[
                    {
                        "id": "call_weather_1",
                        "name": "getweather",
                        "args": {"location": "Beijing"},
                        "type": "tool_call",
                    }
                ],
            )
        return SimpleNamespace(
            content="北京今天晴朗，约 25°C。",
            response_metadata={"finish_reason": "stop"},
            usage_metadata={"input_tokens": 12, "output_tokens": 6},
            tool_calls=[],
        )


class FakeStreamingToolLoopModel:
    def __init__(self):
        self.calls = []

    async def astream(self, messages):
        self.calls.append(messages)
        if len(self.calls) == 1:
            yield SimpleNamespace(
                content="",
                response_metadata={},
                usage_metadata={},
                tool_call_chunks=[
                    {
                        "id": "call_search_1",
                        "name": "websearch",
                        "args": "{\"query\":\"Orbit\"}",
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
            )
            return

        yield SimpleNamespace(
            content="我已经查到结果。",
            response_metadata={"finish_reason": "stop"},
            usage_metadata={"input_tokens": 8, "output_tokens": 5},
            tool_call_chunks=[],
        )


def test_ainvoke_with_tool_loop_executes_tools_and_returns_final_answer():
    client = LLMClient(tool_runtime=FakeToolRuntime())
    provider = FakeProvider()
    model = FakeToolLoopModel()
    history = [make_history_message(role="user", content="北京今天天气怎么样？")]
    chat_messages = client._build_langchain_messages(messages=history, summary=None)

    completion = run(
        client._ainvoke_with_tool_loop(
            model=model,
            messages=chat_messages,
            config=SimpleNamespace(provider="openai", models=["gpt-test"]),
            runtime_config=SimpleNamespace(model="gpt-test"),
            provider=provider,
        )
    )

    assert completion.content == "北京今天晴朗，约 25°C。"
    assert completion.tool_calls == [
        {
            "id": "call_weather_1",
            "name": "getweather",
            "args": {"location": "Beijing"},
            "type": "tool_call",
        }
    ]
    assert completion.tool_results == [
        {
            "tool_call_id": "call_weather_1",
            "name": "getweather",
            "args": {"location": "Beijing"},
            "output": "北京当前天气：晴朗，气温 25°C。",
            "is_error": False,
        }
    ]
    assert isinstance(model.calls[1][-1], ToolMessage)


def test_astream_with_tool_loop_emits_tool_events_then_final_answer():
    client = LLMClient(tool_runtime=FakeToolRuntime())
    provider = FakeProvider()
    model = FakeStreamingToolLoopModel()
    history = [make_history_message(role="user", content="先搜索 Orbit，再告诉我结果。")]
    chat_messages = client._build_langchain_messages(messages=history, summary=None)

    async def collect():
        chunks = []
        async for chunk in client._astream_with_tool_loop(
            model=model,
            messages=chat_messages,
            provider=provider,
            fallback_provider="openai",
            fallback_model="gpt-test",
        ):
            chunks.append(chunk)
        return chunks

    chunks = run(collect())

    assert any(chunk.tool_calls for chunk in chunks)
    assert any(chunk.tool_results for chunk in chunks)
    assert chunks[-1].content_delta == "我已经查到结果。"
    assert isinstance(model.calls[1][-1], ToolMessage)


def test_merge_tool_call_chunks_merges_argument_only_followup_chunks():
    client = LLMClient(tool_runtime=FakeToolRuntime())

    merged = client._merge_tool_call_chunks(
        existing=[],
        incoming=[
            {
                "id": "call_weather_1",
                "name": "getweather",
                "args": "",
                "index": 0,
                "type": "tool_call_chunk",
            },
            {
                "id": None,
                "name": None,
                "args": '{"location": "北京"}',
                "index": 0,
                "type": "tool_call_chunk",
            },
        ],
    )

    assert merged == [
        {
            "id": "call_weather_1",
            "name": "getweather",
            "args": '{"location": "北京"}',
            "index": 0,
            "type": "tool_call_chunk",
        }
    ]


def test_prepare_runtime_config_for_tools_disables_deepseek_thinking():
    client = LLMClient(tool_runtime=FakeToolRuntime())
    provider = SimpleNamespace(provider="deepseek")
    runtime_config = LLMRuntimeConfig(
        provider="deepseek",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key="secret",
        provider_options={},
    )

    next_config = client._prepare_runtime_config_for_tools(
        provider=provider,
        runtime_config=runtime_config,
    )

    assert next_config.provider_options["extra_body"]["thinking"] == {"type": "disabled"}
