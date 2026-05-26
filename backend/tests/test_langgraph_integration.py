"""LangGraph Chat Runtime 端到端集成测试。

测试配置统一从仓库根目录的 `config_text.txt` 读取，避免把密钥硬编码在测试文件里。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage

from app.core.crypto import encrypt_secret
from app.services.langgraph_runtime.chat_runtime import LangGraphChatRuntime
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext, OrbitRuntimeRequest
from app.services.langgraph_runtime.state import ChatState
from app.services.langgraph_runtime.stream_adapter import StreamAdapter
from app.services.llm_client import LLMClient
from app.services.streaming import conversation_stream_store


CONFIG_TEXT_PATH = Path(__file__).resolve().parents[2] / "config_text.txt"


def _load_deepseek_test_config() -> dict[str, str] | None:
    """从 config_text.txt 中提取 DeepSeek 测试配置。

    当前文件格式不是标准 JSON，因此这里做一个轻量解析：
    - 第一组 `key:` 视为 DeepSeek key
    - 第一行 OpenAI 格式 BASE URL 视为 DeepSeek base_url
    - DeepSeek 段落下的第一个非空模型行视为默认模型
    """
    if not CONFIG_TEXT_PATH.exists():
        return None

    lines = [line.strip() for line in CONFIG_TEXT_PATH.read_text(encoding="utf-8").splitlines()]
    non_empty_lines = [line for line in lines if line]
    if len(non_empty_lines) < 4:
        return None

    api_key = ""
    base_url = ""
    model = ""

    for line in non_empty_lines:
        normalized = line.lower()
        if not api_key and normalized.startswith("key"):
            api_key = line.split(":", 1)[1].strip()
            continue
        if not base_url and "base url" in normalized and "openai" in normalized:
            base_url = line.split()[-1].strip()
            continue
        if api_key and base_url and not model:
            if "deepseek" in normalized and "base url" not in normalized and "key" not in normalized:
                model = line.strip()
                break

    if not (api_key and base_url and model):
        return None

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }


DEEPSEEK_TEST_CONFIG = _load_deepseek_test_config()

pytestmark = pytest.mark.skipif(
    DEEPSEEK_TEST_CONFIG is None,
    reason="未能从 config_text.txt 解析出 DeepSeek 测试配置，跳过真实 API 集成测试",
)


def run(coro):
    return asyncio.run(coro)


def build_chat_state(**overrides) -> ChatState:
    """构建测试用 ChatState。"""
    defaults = {
        "input_messages": [],
        "chat_mode": "chat",
        "execution_mode": "",
        "thought_events": [],
        "workspace_files": [],
        "response_text": "",
        "reasoning_text": "",
        "token_usage": {},
        "response_metadata": {},
        "error": None,
    }
    defaults.update(overrides)
    return ChatState(**defaults)


def build_runtime_context(
    *,
    stream_id: str,
    message_id: str,
    chat_mode: str = "chat",
    input_messages: list | None = None,
) -> OrbitRuntimeContext:
    """构建真实 API 集成测试所需的 runtime_context。"""
    return OrbitRuntimeContext(
        request=OrbitRuntimeRequest(
            conversation_id=str(uuid4()),
            assistant_message_id=message_id,
            stream_id=stream_id,
            thread_id=f"thread_{uuid4()}",
            chat_mode=chat_mode,
            agent_type="web_agent" if chat_mode == "agent" else None,
            input_messages=input_messages or [],
            llm_config=None,
            model=DEEPSEEK_TEST_CONFIG["model"] if DEEPSEEK_TEST_CONFIG else "deepseek-v4-flash",
        ),
        tool_runtime=None,
    )


class DummyConfig:
    """只提供本次测试所需字段的轻量配置对象。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        provider_options: dict[str, Any] | None = None,
    ) -> None:
        self.provider = "openai"
        self.models = [model]
        self.base_url = base_url
        self.api_key_ciphertext = encrypt_secret(api_key)
        self.provider_options = provider_options or {}
        self.is_enabled = True
        self.supports_vision = False


def make_stream_factory(prompt: str, *, provider_options: dict[str, Any] | None = None):
    """复用现有 LLMClient.stream() 作为 LangGraph runtime 的标准化 chunk 来源。"""
    assert DEEPSEEK_TEST_CONFIG is not None
    model_name = DEEPSEEK_TEST_CONFIG["model"]

    llm_client = LLMClient()
    config = DummyConfig(
        model=model_name,
        api_key=DEEPSEEK_TEST_CONFIG["api_key"],
        base_url=DEEPSEEK_TEST_CONFIG["base_url"],
        provider_options=provider_options,
    )

    async def stream_factory():
        # 这里构造最小 user message，保持与生产路径同样的归一化逻辑。
        user_message = SimpleNamespace(
            id=uuid4(),
            role="user",
            status="completed",
            content=prompt,
            content_parts=[],
            langgraph_message_id=None,
        )
        async for chunk in llm_client.stream(
            config=cast(Any, config),
            messages=cast(Any, [user_message]),
            summary=None,
            model=model_name,
            enable_tools=False,
        ):
            yield chunk

    return stream_factory


def test_langgraph_simple_chat_with_deepseek():
    """最简对话测试：验证真实 API + 标准化 chunk + stream adapter 闭环。"""
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
        runtime = LangGraphChatRuntime(
            stream_factory=make_stream_factory("你好，请用一句话介绍你自己。"),
            runtime_context=build_runtime_context(
                stream_id=stream_id,
                message_id=str(message_id),
            ),
        )

        state = build_chat_state(
            input_messages=[HumanMessage(content="你好，请用一句话介绍你自己。")],
        )

        try:
            final_state = await runtime.run_stream(state=state, stream_adapter=adapter)
            assert final_state.get("error") is None
            assert final_state.get("response_text", "")

            stream = await conversation_stream_store.get_stream(stream_id)
            assert stream is not None
            delta_events = [e for e in stream.event_log if e.event == "message.delta"]
            assert delta_events
            assert "".join(e.data["delta"] for e in delta_events) == final_state["response_text"]
            assert final_state.get("token_usage", {})
        finally:
            await conversation_stream_store.complete_stream(stream_id, retention_seconds=0)

    run(_test())


def test_langgraph_streaming_with_deepseek():
    """验证真实模型返回会被拆成一条或多条 delta，并正确累积。"""
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
        runtime = LangGraphChatRuntime(
            stream_factory=make_stream_factory("从1数到5，用逗号分隔，不要其他文字。"),
            runtime_context=build_runtime_context(
                stream_id=stream_id,
                message_id=str(message_id),
            ),
        )

        try:
            final_state = await runtime.run_stream(
                state=build_chat_state(
                    input_messages=[HumanMessage(content="从1数到5，用逗号分隔，不要其他文字。")],
                ),
                stream_adapter=adapter,
            )

            assert final_state.get("error") is None
            response_text = final_state.get("response_text", "")
            for num in range(1, 6):
                assert str(num) in response_text
        finally:
            await conversation_stream_store.complete_stream(stream_id, retention_seconds=0)

    run(_test())


def test_langgraph_reasoning_with_deepseek_thinking():
    """验证开启 thinking 后，reasoning_delta 仍走统一标准化路径。"""
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
        runtime = LangGraphChatRuntime(
            stream_factory=make_stream_factory("1+1等于几？只回答数字。"),
            runtime_context=build_runtime_context(
                stream_id=stream_id,
                message_id=str(message_id),
            ),
        )

        try:
            final_state = await runtime.run_stream(
                state=build_chat_state(
                    input_messages=[HumanMessage(content="1+1等于几？只回答数字。")],
                ),
                stream_adapter=adapter,
            )

            assert final_state.get("error") is None
            assert final_state.get("response_text", "") or final_state.get("reasoning_text", "")
        finally:
            await conversation_stream_store.complete_stream(stream_id, retention_seconds=0)

    run(_test())
