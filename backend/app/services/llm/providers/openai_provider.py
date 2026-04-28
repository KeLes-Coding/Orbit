from __future__ import annotations

from openai import AsyncOpenAI

from langchain_openai import ChatOpenAI

from app.services.llm.providers.base import (
    BaseLLMProvider,
    LLMModelInfo,
    LLMProviderError,
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
