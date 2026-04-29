from __future__ import annotations

import httpx
from langchain_ollama import ChatOllama

from app.services.llm.providers.base import BaseLLMProvider, LLMModelInfo, LLMRuntimeConfig


class OllamaProvider(BaseLLMProvider):
    # Ollama 默认本地运行，不要求 API Key。
    provider = "ollama"
    name = "Ollama"
    requires_api_key = False
    default_base_url = "http://127.0.0.1:11434"

    def build_chat_model(self, config: LLMRuntimeConfig) -> ChatOllama:
        # 聊天调用交给 LangChain Ollama integration，保持与远程 provider 同一接口。
        model = self.require_model(config)
        options = self.parse_provider_options(config.provider_options, default_timeout=120)
        model_kwargs = {
            "model": model,
            "base_url": self.resolve_base_url(config),
            **options.generation,
            **options.langchain,
        }
        if options.timeout is not None:
            model_kwargs["timeout"] = options.timeout
        return ChatOllama(**model_kwargs)

    async def list_models(self, config: LLMRuntimeConfig) -> list[LLMModelInfo]:
        # Ollama 的本地模型列表不走 OpenAI-compatible /models，而是 /api/tags。
        options = self.parse_provider_options(config.provider_options, default_timeout=120)
        base_url = self.resolve_base_url(config)
        async with httpx.AsyncClient(timeout=options.timeout) as client:
            response = await client.get(f"{base_url}/api/tags")
            response.raise_for_status()
        data = response.json()
        return [
            LLMModelInfo(
                id=model.get("name", ""),
                name=model.get("name"),
                description=model.get("details", {}).get("family"),
            )
            for model in data.get("models", [])
            if model.get("name")
        ]
