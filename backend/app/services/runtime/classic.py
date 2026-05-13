"""经典聊天运行时。

封装现有的 LLMClient.stream() 路径，将 LLM 流式输出转换为统一的 UnifiedStreamEvent。
后续 PR 会把 _produce_stream() 中的 chat 逻辑完整提取到这里。
"""
from collections.abc import AsyncIterator

from app.services.llm_client import LLMClient, LLMClientError
from app.services.runtime.base import BaseRuntime
from app.services.runtime.types import RunContext, UnifiedStreamEvent


class ClassicChatRuntime(BaseRuntime):
    """经典聊天运行时：单轮 LLM 对话式生成。

    当前为占位实现，实际 chat 路径仍保留在 ConversationStreamRunService 中。
    后续 PR 会将现有 _produce_stream() 逻辑提取到此处。
    """

    async def execute(self, ctx: RunContext) -> AsyncIterator[UnifiedStreamEvent]:
        llm_client = LLMClient()
        full_content_parts: list[str] = []
        full_reasoning_parts: list[str] = []
        token_usage: dict = {}
        response_metadata: dict = {
            "provider": ctx.llm_config.provider,
            "model": ctx.assistant_message.model or "",
        }
        finish_reason: str | None = None

        try:
            if ctx.cancel_event.is_set():
                yield UnifiedStreamEvent(
                    event="message.cancelled",
                    data={"message_id": str(ctx.assistant_message.id)},
                )
                return

            async for chunk in llm_client.stream(
                config=ctx.llm_config,
                messages=ctx.history_messages,
                summary=ctx.conversation.summary,
                model=ctx.assistant_message.model,
            ):
                if ctx.cancel_event.is_set():
                    yield UnifiedStreamEvent(
                        event="message.cancelled",
                        data={"message_id": str(ctx.assistant_message.id)},
                    )
                    return

                if chunk.token_usage:
                    token_usage = chunk.token_usage
                if chunk.response_metadata:
                    response_metadata.update(chunk.response_metadata)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

                if chunk.reasoning_delta:
                    full_reasoning_parts.append(chunk.reasoning_delta)
                    yield UnifiedStreamEvent(
                        event="message.reasoning_delta",
                        data={
                            "message_id": str(ctx.assistant_message.id),
                            "delta": chunk.reasoning_delta,
                        },
                    )

                if chunk.content_delta:
                    full_content_parts.append(chunk.content_delta)
                    yield UnifiedStreamEvent(
                        event="message.delta",
                        data={
                            "message_id": str(ctx.assistant_message.id),
                            "delta": chunk.content_delta,
                        },
                    )

            full_content = "".join(full_content_parts)
            full_reasoning = "".join(full_reasoning_parts)

            if finish_reason:
                response_metadata["finish_reason"] = finish_reason

            yield UnifiedStreamEvent(
                event="message.completed",
                data={
                    "content": full_content,
                    "reasoning_content": full_reasoning,
                    "token_usage": token_usage,
                    "response_metadata": response_metadata,
                },
            )
        except LLMClientError as exc:
            yield UnifiedStreamEvent(
                event="message.failed",
                data={
                    "content": "".join(full_content_parts),
                    "reasoning_content": "".join(full_reasoning_parts),
                    "error": str(exc),
                    "token_usage": token_usage,
                    "response_metadata": response_metadata,
                },
            )
