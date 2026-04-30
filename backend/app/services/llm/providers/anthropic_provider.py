from __future__ import annotations

import anthropic
from langchain_anthropic import ChatAnthropic

from app.services.llm.providers.base import BaseLLMProvider, LLMModelInfo, LLMRuntimeConfig


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
        model_kwargs = {
            "model_name": model,
            "api_key": api_key,
            "timeout": options.timeout,
            **options.generation,
            **options.langchain,
        }
        base_url = self.resolve_base_url(config)
        if base_url:
            model_kwargs["base_url"] = base_url
        return ChatAnthropic(**model_kwargs)

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
