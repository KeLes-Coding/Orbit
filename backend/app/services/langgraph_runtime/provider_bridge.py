"""Provider roundtrip 兼容桥。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


def build_assistant_message_content(*, content: Any, reasoning_content: str) -> Any:
    """为 assistant 消息保留正文与 reasoning 的结构化表示。"""
    # thinking/reasoning provider 在多轮继续调用时，往往要求把上轮 reasoning 一并带回；
    # 这里统一把正文与 reasoning 打成兼容 content blocks，避免在 tool roundtrip 中丢字段。
    if not reasoning_content:
        return content
    if isinstance(content, list):
        return list(content) + [{"type": "reasoning", "text": reasoning_content}]
    if isinstance(content, str):
        blocks: list[dict[str, str]] = []
        if content:
            blocks.append({"type": "text", "text": content})
        blocks.append({"type": "reasoning", "text": reasoning_content})
        return blocks
    return [
        {"type": "text", "text": str(content) if content is not None else ""},
        {"type": "reasoning", "text": reasoning_content},
    ]


def build_assistant_message(
    *,
    content: Any,
    reasoning_content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> AIMessage:
    """构建带 reasoning roundtrip 元信息的 assistant message。"""
    additional_kwargs: dict[str, Any] = {}
    if reasoning_content:
        # 某些 OpenAI-compatible provider 更偏好从额外字段读取 reasoning_content，
        # 因此 content blocks 和 additional_kwargs 两侧都保留一份。
        additional_kwargs["reasoning_content"] = reasoning_content
    return AIMessage(
        content=build_assistant_message_content(content=content, reasoning_content=reasoning_content),
        additional_kwargs=additional_kwargs,
        tool_calls=tool_calls or [],
    )
