from __future__ import annotations

import asyncio

from google import genai
from langchain_google_genai import ChatGoogleGenerativeAI

from app.services.llm.providers.base import BaseLLMProvider, LLMModelInfo, LLMRuntimeConfig


class GeminiProvider(BaseLLMProvider):
    # Gemini 当前使用 Google GenAI API Key 模式，暂不暴露自定义 base_url。
    provider = "gemini"
    name = "Gemini"
    supports_custom_base_url = False

    def build_chat_model(self, config: LLMRuntimeConfig) -> ChatGoogleGenerativeAI:
        # LangChain Google integration 使用 request_timeout，而不是通用 timeout。
        api_key = self.require_api_key(config)
        model = self.require_model(config)
        options = self.parse_provider_options(config.provider_options, default_timeout=60)
        model_kwargs = {
            "model": model,
            "api_key": api_key,
            "request_timeout": options.timeout,
            **options.generation,
            **options.langchain,
        }
        return ChatGoogleGenerativeAI(**model_kwargs)

    async def list_models(self, config: LLMRuntimeConfig) -> list[LLMModelInfo]:
        # google-genai 的 list 是同步迭代接口，这里用线程包装避免阻塞事件循环。
        api_key = self.require_api_key(config)
        client = genai.Client(api_key=api_key)
        models = await asyncio.to_thread(lambda: list(client.models.list()))
        return [
            LLMModelInfo(
                id=self._normalize_model_id(model.name),
                name=getattr(model, "display_name", None) or self._normalize_model_id(model.name),
                description=getattr(model, "description", None),
            )
            for model in models
            if getattr(model, "name", None)
        ]

    def _normalize_model_id(self, model_name: str) -> str:
        # Google 返回的模型名常带 models/ 前缀，前端配置时只需要短 ID。
        return model_name.removeprefix("models/")
