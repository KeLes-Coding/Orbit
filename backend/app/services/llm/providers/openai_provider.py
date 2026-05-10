from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.services.llm.providers.base import (
    BaseLLMProvider,
    LLMModelInfo,
    LLMProviderError,
    LLMProviderStreamChunk,
    LLMRuntimeConfig,
)


class OpenAICompatibleProvider(BaseLLMProvider):
    # OpenAI-compatible 是 DeepSeek、Qwen 和用户自定义网关的共同基类。
    provider = "openai_compatible"
    name = "OpenAI Compatible"
    default_base_url: str | None = None

    def build_chat_model(self, config: LLMRuntimeConfig) -> ChatOpenAI:
        # LangChain 的 ChatOpenAI 同时支持官方 OpenAI 和 compatible base_url。
        api_key = self.require_api_key(config)
        model = self.require_model(config)
        base_url = self.resolve_base_url(config)
        if not base_url:
            raise LLMProviderError("OpenAI-compatible provider 需要配置 base_url")

        options = self.parse_provider_options(config.provider_options, default_timeout=60)
        model_kwargs = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "timeout": options.timeout,
            **options.generation,
            **options.langchain,
        }
        if options.extra_body:
            model_kwargs["extra_body"] = options.extra_body
        return ChatOpenAI(**model_kwargs)

    def supports_native_stream(self, config: LLMRuntimeConfig) -> bool:
        # OpenAI-compatible 的原生 stream 可以保留第三方扩展字段，如 DeepSeek delta.reasoning_content。
        return True

    async def stream_chat(
        self,
        *,
        config: LLMRuntimeConfig,
        messages: list[BaseMessage],
    ) -> AsyncIterator[LLMProviderStreamChunk]:
        api_key = self.require_api_key(config)
        model = self.require_model(config)
        base_url = self.resolve_base_url(config)
        if not base_url:
            raise LLMProviderError("OpenAI-compatible provider 需要配置 base_url")

        options = self.parse_provider_options(config.provider_options, default_timeout=60)
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=options.timeout)
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [self._to_openai_message(message) for message in messages],
            "stream": True,
            **options.generation,
        }
        if options.extra_body:
            request_kwargs["extra_body"] = options.extra_body

        try:
            stream = await client.chat.completions.create(**request_kwargs)
            async for chunk in stream:
                yield self._to_provider_stream_chunk(
                    chunk=chunk,
                    provider=config.provider,
                    model=model,
                )
        except LLMProviderError:
            raise
        except Exception as exc:
            raise LLMProviderError(f"{self.name} 流式请求失败：{exc}") from exc

    def _to_openai_message(self, message: BaseMessage) -> dict[str, Any]:
        # 这里仅发送正文，不把本地保存的 reasoning_content 带回上下文。
        if isinstance(message, SystemMessage):
            return {"role": "system", "content": self._message_content_text(message.content)}
        if isinstance(message, HumanMessage):
            return {"role": "user", "content": self._message_content_text(message.content)}
        if isinstance(message, AIMessage):
            return {"role": "assistant", "content": self._message_content_text(message.content)}
        if isinstance(message, ToolMessage):
            return {
                "role": "tool",
                "content": self._message_content_text(message.content),
                "tool_call_id": message.tool_call_id,
            }
        role = getattr(message, "role", None) or message.type
        return {"role": role, "content": self._message_content_text(message.content)}

    def _message_content_text(self, content: Any) -> str | list:
        # 多模态 content_blocks 直接透传给 OpenAI API，只对纯文本做字符串化。
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # 检查是否包含非文本块（如 image_url）：有则原样返回 list，否则拼接文本。
            for block in content:
                if isinstance(block, dict):
                    block_type = str(block.get("type") or "").lower()
                    if block_type not in {"text", "reasoning", "thinking"}:
                        return content
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    block_type = str(block.get("type") or "").lower()
                    if block_type not in {"reasoning", "thinking"} and isinstance(block.get("text"), str):
                        parts.append(block["text"])
            return "".join(parts)
        return str(content) if content is not None else ""

    def _to_provider_stream_chunk(
        self,
        *,
        chunk: Any,
        provider: str,
        model: str,
    ) -> LLMProviderStreamChunk:
        choices = self._get_field(chunk, "choices")
        choice = choices[0] if choices else None
        delta = self._get_field(choice, "delta")
        content_delta = self._coerce_text(self._get_field(delta, "content"))
        # DeepSeek 兼容接口在 reasoning 阶段使用 delta.reasoning_content；其他三方可能叫 reasoning/thinking。
        reasoning_delta = (
            self._coerce_text(self._get_field(delta, "reasoning_content"))
            or self._coerce_text(self._get_field(delta, "reasoning"))
            or self._coerce_text(self._get_field(delta, "thinking"))
        )
        finish_reason = self._get_field(choice, "finish_reason") if choice is not None else None
        response_metadata = {
            "provider": provider,
            "model": self._get_field(chunk, "model") or model,
        }
        raw_id = self._get_field(chunk, "id")
        if raw_id:
            response_metadata["raw_id"] = raw_id
        if finish_reason is not None:
            response_metadata["finish_reason"] = str(finish_reason)

        return LLMProviderStreamChunk(
            content_delta=content_delta,
            reasoning_delta=reasoning_delta,
            token_usage=self._extract_usage(getattr(chunk, "usage", None)),
            response_metadata=response_metadata,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
            raw=chunk,
        )

    def _get_field(self, value: Any, key: str) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return value.get(key)
        if hasattr(value, key):
            return getattr(value, key)
        model_extra = getattr(value, "model_extra", None)
        if isinstance(model_extra, dict) and key in model_extra:
            return model_extra[key]
        if hasattr(value, "model_dump"):
            try:
                dumped = value.model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                return dumped.get(key)
        return None

    def _coerce_text(self, value: Any) -> str:
        return value if isinstance(value, str) else ""

    def _extract_usage(self, usage: Any) -> dict[str, Any]:
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        if isinstance(usage, dict):
            return usage
        return {}

    async def list_models(self, config: LLMRuntimeConfig) -> list[LLMModelInfo]:
        # 兼容协议通常提供 /models；DeepSeek/Qwen 也优先复用这条路径。
        api_key = self.require_api_key(config)
        base_url = self.resolve_base_url(config)
        if not base_url:
            raise LLMProviderError("OpenAI-compatible provider 需要配置 base_url")

        options = self.parse_provider_options(config.provider_options, default_timeout=60)
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=options.timeout)
        models = await client.models.list()
        return [
            LLMModelInfo(
                id=model.id,
                name=model.id,
                owned_by=getattr(model, "owned_by", None),
            )
            for model in models.data
        ]


class OpenAIProvider(OpenAICompatibleProvider):
    # 官方 OpenAI 只是在 compatible 基类上补默认 base_url。
    provider = "openai"
    name = "OpenAI"
    default_base_url = "https://api.openai.com/v1"


class DeepSeekProvider(OpenAICompatibleProvider):
    # DeepSeek 使用 OpenAI-compatible 协议，保留独立 provider 便于前端少填 base_url。
    provider = "deepseek"
    name = "DeepSeek"
    default_base_url = "https://api.deepseek.com"


class QwenProvider(OpenAICompatibleProvider):
    # Qwen 走 DashScope compatible mode，后续如果接原生 DashScope 可在这里替换实现。
    provider = "qwen"
    name = "Qwen"
    default_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
