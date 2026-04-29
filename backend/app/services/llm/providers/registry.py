from __future__ import annotations

from app.services.llm.providers.anthropic_provider import AnthropicProvider
from app.services.llm.providers.base import BaseLLMProvider, LLMProviderInfo
from app.services.llm.providers.google_provider import GeminiProvider
from app.services.llm.providers.ollama_provider import OllamaProvider
from app.services.llm.providers.openai_provider import (
    DeepSeekProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    QwenProvider,
)


_PROVIDERS: dict[str, BaseLLMProvider] = {
    # registry 是 provider 的唯一入口，LLMClient 和配置服务都通过它查找实现。
    provider.provider: provider
    for provider in (
        OpenAIProvider(),
        OpenAICompatibleProvider(),
        OllamaProvider(),
        AnthropicProvider(),
        GeminiProvider(),
        DeepSeekProvider(),
        QwenProvider(),
    )
}

_ALIASES = {
    # 允许前端或用户使用常见叫法，但入库前会规范化成 provider.provider。
    "claude": "anthropic",
    "google": "gemini",
    "google_genai": "gemini",
}


def normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    return _ALIASES.get(normalized, normalized)


def get_provider(provider: str) -> BaseLLMProvider | None:
    return _PROVIDERS.get(normalize_provider(provider))


def list_provider_infos() -> list[LLMProviderInfo]:
    return [provider.to_info() for provider in _PROVIDERS.values()]
