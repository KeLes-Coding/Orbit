from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.crypto import decrypt_secret
from app.models.llm_config import LLMConfig


class LLMProviderError(Exception):
    # Provider 层只抛业务可读错误，service 层再转换成 HTTP 或消息 failed 状态。
    pass


@dataclass(frozen=True)
class LLMProviderOptions:
    # connection/generation/langchain/extra_body 是 provider_options 的第一阶段约定结构。
    timeout: float
    generation: dict[str, Any] = field(default_factory=dict)
    langchain: dict[str, Any] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMRuntimeConfig:
    # 运行时配置不绑定 SQLAlchemy ORM，便于已保存配置和临时探测配置复用同一 provider。
    provider: str
    model: str | None
    base_url: str | None
    api_key: str | None
    provider_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMModelInfo:
    # 不同供应商模型字段差异很大，先收敛成前端展示所需的最小集合。
    id: str
    name: str | None = None
    description: str | None = None
    owned_by: str | None = None


@dataclass(frozen=True)
class LLMProviderInfo:
    # Provider 能力描述给配置页使用，例如是否需要 API Key、是否支持自定义 base_url。
    id: str
    name: str
    requires_api_key: bool
    supports_custom_base_url: bool
    supports_model_list: bool
    default_base_url: str | None = None


class BaseLLMProvider(ABC):
    # 每个 provider 负责两件事：构造 LangChain chat model，以及按需获取模型列表。
    provider: str
    name: str
    requires_api_key = True
    supports_custom_base_url = True
    supports_model_list = True
    default_base_url: str | None = None

    def to_info(self) -> LLMProviderInfo:
        return LLMProviderInfo(
            id=self.provider,
            name=self.name,
            requires_api_key=self.requires_api_key,
            supports_custom_base_url=self.supports_custom_base_url,
            supports_model_list=self.supports_model_list,
            default_base_url=self.default_base_url,
        )

    def from_model_config(self, config: LLMConfig) -> LLMRuntimeConfig:
        # API Key 只在进入 provider 运行时前解密，不向 API schema 或日志暴露。
        return LLMRuntimeConfig(
            provider=config.provider,
            model=config.model,
            base_url=config.base_url,
            api_key=decrypt_secret(config.api_key_ciphertext),
            provider_options=config.provider_options or {},
        )

    def parse_provider_options(
        self,
        raw_options: dict | None,
        *,
        default_timeout: float,
    ) -> LLMProviderOptions:
        # 兼容旧的扁平写法，同时支持后续更清晰的分组写法。
        options = dict(raw_options or {})
        connection = options.pop("connection", {}) or {}
        generation = options.pop("generation", {}) or {}
        langchain = options.pop("langchain", {}) or {}
        extra_body = options.pop("extra_body", {}) or {}

        if not isinstance(connection, dict):
            raise LLMProviderError("provider_options.connection 必须是对象")
        if not isinstance(generation, dict):
            raise LLMProviderError("provider_options.generation 必须是对象")
        if not isinstance(langchain, dict):
            raise LLMProviderError("provider_options.langchain 必须是对象")
        if not isinstance(extra_body, dict):
            raise LLMProviderError("provider_options.extra_body 必须是对象")

        timeout = connection.pop("timeout", options.pop("timeout", default_timeout))
        return LLMProviderOptions(
            timeout=float(timeout),
            generation={**options, **generation},
            langchain=langchain,
            extra_body=extra_body,
        )

    def require_model(self, config: LLMRuntimeConfig) -> str:
        model = (config.model or "").strip()
        if not model:
            raise LLMProviderError("模型名称不能为空")
        return model

    def require_api_key(self, config: LLMRuntimeConfig) -> str:
        api_key = (config.api_key or "").strip()
        if self.requires_api_key and not api_key:
            raise LLMProviderError("当前模型配置缺少 API Key")
        return api_key

    def resolve_base_url(self, config: LLMRuntimeConfig) -> str | None:
        base_url = (config.base_url or self.default_base_url or "").strip()
        return base_url.rstrip("/") or None

    @abstractmethod
    def build_chat_model(self, config: LLMRuntimeConfig) -> BaseChatModel:
        pass

    async def list_models(self, config: LLMRuntimeConfig) -> list[LLMModelInfo]:
        if not self.supports_model_list:
            return []
        raise LLMProviderError(f"{self.name} 暂不支持自动获取模型列表")
