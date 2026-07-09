from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.services.llm.providers.base import (
    BaseLLMProvider,
    LLMModelInfo,
    LLMProviderError,
    LLMProviderStreamChunk,
    LLMRuntimeConfig,
)


class AnthropicProvider(BaseLLMProvider):
    # 对外使用 anthropic 作为规范 provider，registry 中保留 claude 作为别名。
    provider = "anthropic"
    name = "Claude / Anthropic"
    default_base_url = "https://api.anthropic.com"

    def build_chat_model(self, config: LLMRuntimeConfig) -> ChatAnthropic:
        # Anthropic integration 参数名使用 model_name，和其他 provider 略有不同。
        api_key = self.require_api_key(config)
        model = self.require_model(config)
        options = self.parse_provider_options(config.provider_options, default_timeout=60)
        # LangChain 的 ChatAnthropic 和 SDK 原生字段不完全同名，这里先做一次归一化。
        generation = self._normalize_langchain_generation_options(options.generation)
        model_kwargs = {
            "model_name": model,
            "api_key": api_key,
            "timeout": options.timeout,
            **generation,
            **options.langchain,
        }
        base_url = self.resolve_base_url(config)
        if base_url:
            model_kwargs["base_url"] = base_url
        return ChatAnthropic(**model_kwargs)

    def supports_native_stream(self, config: LLMRuntimeConfig) -> bool:
        # 原生 Anthropic stream 能稳定拿到 thinking_delta，便于前端实时展示 reasoning block。
        return True

    async def stream_chat(
        self,
        *,
        config: LLMRuntimeConfig,
        messages: list[BaseMessage],
    ) -> AsyncIterator[LLMProviderStreamChunk]:
        api_key = self.require_api_key(config)
        model = self.require_model(config)
        options = self.parse_provider_options(config.provider_options, default_timeout=60)
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=self.resolve_base_url(config),
            timeout=options.timeout,
        )
        request_kwargs = self._build_sdk_request_kwargs(model=model, messages=messages, generation=options.generation)
        if options.extra_body:
            request_kwargs["extra_body"] = options.extra_body

        try:
            # 直接走 Anthropic 原生 stream，才能稳定拿到 thinking_delta 并映射到前端 reasoning block。
            stream = await client.messages.create(stream=True, **request_kwargs)
            current_model = model
            raw_id: str | None = None
            async for event in stream:
                event_type = getattr(event, "type", None)
                if event_type == "message_start":
                    message = getattr(event, "message", None)
                    raw_id = getattr(message, "id", None) or raw_id
                    current_model = getattr(message, "model", None) or current_model
                    continue

                metadata = {
                    "provider": config.provider,
                    "model": current_model,
                }
                if raw_id:
                    metadata["raw_id"] = raw_id

                chunk = self._event_to_provider_stream_chunk(event=event, response_metadata=metadata)
                if chunk is not None:
                    yield chunk
        except LLMProviderError:
            raise
        except Exception as exc:
            raise LLMProviderError(f"{self.name} 流式请求失败：{exc}") from exc

    async def list_models(self, config: LLMRuntimeConfig) -> list[LLMModelInfo]:
        # 模型列表使用官方 SDK；返回值再转换成统一的 LLMModelInfo。
        api_key = self.require_api_key(config)
        options = self.parse_provider_options(config.provider_options, default_timeout=60)
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=self.resolve_base_url(config),
            timeout=options.timeout,
        )
        models = await client.models.list()
        return [
            LLMModelInfo(
                id=model.id,
                name=getattr(model, "display_name", None) or model.id,
            )
            for model in models.data
        ]

    def _normalize_langchain_generation_options(self, generation: dict[str, Any]) -> dict[str, Any]:
        # 配置页沿用通用 provider_options 结构，这里把通用字段翻译成 ChatAnthropic 能识别的名字。
        normalized = dict(generation)
        max_tokens = normalized.pop("max_tokens", None)
        if max_tokens is not None and "max_tokens_to_sample" not in normalized:
            normalized["max_tokens_to_sample"] = max_tokens
        stop_sequences = normalized.pop("stop_sequences", None)
        if stop_sequences is not None and "stop" not in normalized:
            normalized["stop"] = stop_sequences
        return normalized

    def _build_sdk_request_kwargs(
        self,
        *,
        model: str,
        messages: list[BaseMessage],
        generation: dict[str, Any],
    ) -> dict[str, Any]:
        # SDK 原生请求继续保留 Anthropic 风格字段，便于接 DeepSeek 等兼容端点。
        request_kwargs = dict(generation)
        request_kwargs.setdefault("max_tokens", 4096)
        request_kwargs["model"] = model
        request_kwargs["messages"] = self._to_anthropic_messages(messages)
        system_blocks = self._collect_system_blocks(messages)
        if system_blocks:
            request_kwargs["system"] = system_blocks
        return request_kwargs

    def _collect_system_blocks(self, messages: list[BaseMessage]) -> list[dict[str, str]]:
        # Anthropic 把 system 从普通消息数组里拆出去单独传递。
        blocks: list[dict[str, str]] = []
        for message in messages:
            if not isinstance(message, SystemMessage):
                continue
            text = self.extract_text_from_content(message.content)
            if text:
                blocks.append({"type": "text", "text": text})
        return blocks

    def _to_anthropic_messages(self, messages: list[BaseMessage]) -> list[dict[str, Any]]:
        # 这里把 Orbit 统一的 LangChain message 形状翻译成 Anthropic Messages API 需要的结构。
        anthropic_messages: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                continue
            if isinstance(message, HumanMessage):
                anthropic_messages.append(
                    {"role": "user", "content": self._to_anthropic_content_blocks(message.content)}
                )
                continue
            if isinstance(message, AIMessage):
                anthropic_messages.append(
                    {"role": "assistant", "content": self._to_anthropic_content_blocks(message.content)}
                )
                continue
            if isinstance(message, ToolMessage):
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                # ToolMessage 在 Anthropic 协议里要回填成 user 侧的 tool_result block。
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id,
                                "content": self.extract_text_from_content(message.content),
                            }
                        ],
                    }
                )
                continue
            role = getattr(message, "role", None) or message.type
            anthropic_messages.append(
                {"role": role, "content": self._to_anthropic_content_blocks(message.content)}
            )
        return anthropic_messages

    def _to_anthropic_content_blocks(self, content: Any) -> str | list[dict[str, Any]]:
        # Anthropic Messages API 的 content 是 block 数组；这里把 Orbit 的统一 content 形状映射过去。
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for item in content:
                if isinstance(item, str):
                    if item:
                        blocks.append({"type": "text", "text": item})
                    continue
                if not isinstance(item, dict):
                    continue
                block_type = str(item.get("type") or "").lower()
                if self.is_reasoning_block_type(block_type):
                    # 本地保存的 reasoning 不能再次喂回模型正文，否则会污染后续上下文。
                    continue
                if block_type == "text" and isinstance(item.get("text"), str):
                    blocks.append({"type": "text", "text": item["text"]})
                    continue
                if block_type == "image_url":
                    image_block = self._image_url_block_to_anthropic_image(item)
                    if image_block is not None:
                        blocks.append(image_block)
                    continue
                if block_type == "image":
                    blocks.append(item)
            if blocks:
                return blocks
        return self.extract_text_from_content(content)

    def _image_url_block_to_anthropic_image(self, block: dict[str, Any]) -> dict[str, Any] | None:
        # Orbit 内部统一存 image_url(data URL)，Anthropic 发送前再改写成 image/source/base64。
        image_url = block.get("image_url")
        if not isinstance(image_url, dict):
            return None
        url = image_url.get("url")
        if not isinstance(url, str):
            return None
        parsed = self.parse_base64_data_url(url)
        if parsed is None:
            return None
        media_type, data = parsed
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            },
        }

    def _event_to_provider_stream_chunk(
        self,
        *,
        event: Any,
        response_metadata: dict[str, Any],
    ) -> LLMProviderStreamChunk | None:
        # Anthropic stream 事件很多，这里只抽取 Orbit 当前需要的正文、reasoning 和结束态。
        event_type = getattr(event, "type", None)
        if event_type == "content_block_delta":
            delta = getattr(event, "delta", None)
            delta_type = getattr(delta, "type", None)
            content_delta = getattr(delta, "text", "") if delta_type == "text_delta" else ""
            reasoning_delta = getattr(delta, "thinking", "") if delta_type == "thinking_delta" else ""
            if not content_delta and not reasoning_delta:
                return None
            return LLMProviderStreamChunk(
                content_delta=content_delta,
                reasoning_delta=reasoning_delta,
                response_metadata=dict(response_metadata),
                raw=event,
            )

        if event_type == "message_delta":
            delta = getattr(event, "delta", None)
            raw_finish_reason = getattr(delta, "stop_reason", None)
            finish_reason = self.normalize_finish_reason(raw_finish_reason)
            metadata = dict(response_metadata)
            if raw_finish_reason is not None:
                # SDK 原始 stop_reason 继续保留，方便后面比较 Claude SDK 与兼容端点差异。
                metadata["provider_finish_reason"] = str(raw_finish_reason)
            if finish_reason is not None:
                metadata["finish_reason"] = finish_reason
            return LLMProviderStreamChunk(
                token_usage=self._extract_usage(getattr(event, "usage", None)),
                response_metadata=metadata,
                finish_reason=finish_reason,
                raw=event,
            )

        return None

    def _extract_usage(self, usage: Any) -> dict[str, Any]:
        return self.normalize_token_usage(usage)
