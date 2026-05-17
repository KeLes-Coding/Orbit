"""流事件适配器：将 LangGraph 自定义流事件映射为 Orbit SSE 事件。

normal_chat / agentic_chat 节点通过 get_stream_writer() 发射的自定义事件，
经此适配器转换为 conversation_stream_store 中的标准 SSE 事件，
同时累积响应文本、推理文本、thought_events、token 用量等状态。

Phase 2 新增 thought.* 事件映射（message.thought SSE）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.services.streaming import conversation_stream_store


class StreamAdapter:
    """LangGraph 自定义事件 → Orbit SSE 事件的适配器。

    职责：
    1. 将 graph 节点的自定义流事件写入 conversation_stream_store
    2. 累积 response_text, reasoning_text, thought_events, token_usage, response_metadata
    3. 对外暴露 get_accumulated_state() 供 graph 节点使用
    """

    def __init__(self, *, stream_id: str, message_id: UUID) -> None:
        self._stream_id = stream_id
        self._message_id = message_id

        # 累积状态
        self._content_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._thought_events: list[dict[str, Any]] = []
        self._token_usage: dict[str, Any] = {}
        self._response_metadata: dict[str, Any] = {}
        self._finish_reason: str | None = None

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def message_id(self) -> UUID:
        return self._message_id

    async def emit_custom_event(self, event: dict[str, Any]) -> None:
        """处理 call_model 节点发射的单个自定义事件。

        event 是 call_model 通过 get_stream_writer() 发射的 dict，
        格式为 {"type": "...", ...额外字段}。
        """
        event_type = event.get("type", "")

        if event_type == "content_delta":
            delta = event.get("delta", "")
            if delta:
                self._content_parts.append(delta)
                await conversation_stream_store.append_event(
                    self._stream_id,
                    event="message.delta",
                    data={
                        "message_id": str(self._message_id),
                        "delta": delta,
                    },
                )

        elif event_type == "reasoning_delta":
            delta = event.get("delta", "")
            if delta:
                self._reasoning_parts.append(delta)
                await conversation_stream_store.append_event(
                    self._stream_id,
                    event="message.reasoning_delta",
                    data={
                        "message_id": str(self._message_id),
                        "delta": delta,
                    },
                )

        elif event_type == "token_usage":
            usage = event.get("usage")
            if isinstance(usage, dict):
                self._token_usage = usage

        elif event_type == "response_metadata":
            metadata = event.get("metadata")
            if isinstance(metadata, dict):
                self._response_metadata.update(metadata)

        elif event_type == "finish_reason":
            reason = event.get("finish_reason")
            if reason:
                self._finish_reason = reason
                self._response_metadata["finish_reason"] = reason

        elif event_type.startswith("thought."):
            # Phase 2: thought.planning / thought.tool / thought.summary / thought.reason
            await conversation_stream_store.append_event(
                self._stream_id,
                event="message.thought",
                data={
                    "message_id": str(self._message_id),
                    "type": event_type,
                    "phase": event.get("phase", ""),
                    "text": event.get("text", ""),
                    "meta": event.get("meta", {}),
                },
            )
            self._thought_events.append({
                "type": event_type,
                "phase": event.get("phase", ""),
                "text": event.get("text", ""),
                "meta": event.get("meta", {}),
            })

    def get_accumulated_state(self) -> dict[str, Any]:
        """返回累积的响应状态，供 graph 节点写入 ChatState。

        在 normal_chat / agentic_chat 节点返回时调用。
        """
        full_content = "".join(self._content_parts)
        full_reasoning = "".join(self._reasoning_parts)

        if self._finish_reason:
            self._response_metadata.setdefault("finish_reason", self._finish_reason)

        return {
            "response_text": full_content,
            "reasoning_text": full_reasoning,
            "token_usage": self._token_usage,
            "response_metadata": self._response_metadata,
            "thought_events": self._thought_events,
        }
