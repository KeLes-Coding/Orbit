from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

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
class LLMProviderStreamChunk:
    # Provider 原生流式 chunk，用于保留 OpenAI-compatible 扩展字段（如 DeepSeek reasoning_content）。
    content_delta: str = ""
    reasoning_delta: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # tool result 不是所有 provider 都会原生返回，但统一字段能让上层类型稳定。
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None
    raw: Any = None


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

    def from_model_config(self, config: LLMConfig, *, model: str | None = None) -> LLMRuntimeConfig:
        # API Key 只在进入 provider 运行时前解密，不向 API schema 或日志暴露。
        resolved_model = model or (config.models[0] if config.models else None)
        return LLMRuntimeConfig(
            provider=config.provider,
            model=resolved_model,
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

    def is_reasoning_block_type(self, block_type: str | None) -> bool:
        # 不同 provider 对推理块命名不同，先在基础层统一判断，避免各实现重复写集合。
        normalized = (block_type or "").strip().lower()
        return normalized in {"reasoning", "thinking"}

    def extract_text_from_content(self, content: Any) -> str:
        # 从多模态/结构化 content 中抽取可回填上下文的正文文本，显式跳过 reasoning/thinking。
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                block_type = str(item.get("type") or "").lower()
                if self.is_reasoning_block_type(block_type):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            return "".join(parts)
        return str(content) if content is not None else ""

    def extract_usage_dict(self, usage: Any) -> dict[str, Any]:
        # SDK 的 usage 可能是 pydantic model、普通 dict 或空值，这里统一拍平成 dict。
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        if isinstance(usage, dict):
            return usage
        return {}

    def normalize_finish_reason(self, finish_reason: Any) -> str | None:
        # Orbit 内部统一使用较稳定的小集合，原始 provider 值仍保存在 response_metadata 里。
        if finish_reason is None:
            return None
        value = str(finish_reason).strip().lower()
        if not value:
            return None
        normalized_map = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "model_context_window_exceeded": "length",
        }
        return normalized_map.get(value, value)

    def normalize_token_usage(self, usage: Any) -> dict[str, Any]:
        # 统一成 input/output/total/reasoning 口径，后续聊天与 agent runtime 都直接消费这层。
        raw = self.extract_usage_dict(usage)
        if not raw:
            return {}

        normalized: dict[str, Any] = {
            "input_tokens": self._first_int(raw, "input_tokens", "prompt_tokens"),
            "output_tokens": self._first_int(raw, "output_tokens", "completion_tokens"),
            "total_tokens": self._first_int(raw, "total_tokens"),
            "reasoning_tokens": self._first_int(
                raw,
                "reasoning_tokens",
                "completion_tokens_details.reasoning_tokens",
                "output_token_details.reasoning",
            ),
            "cache_read_input_tokens": self._first_int(
                raw,
                "cache_read_input_tokens",
                "prompt_cache_hit_tokens",
            ),
            "cache_creation_input_tokens": self._first_int(raw, "cache_creation_input_tokens"),
            "raw": raw,
        }
        if normalized["total_tokens"] is None:
            input_tokens = normalized["input_tokens"]
            output_tokens = normalized["output_tokens"]
            if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                normalized["total_tokens"] = input_tokens + output_tokens
        return {key: value for key, value in normalized.items() if value is not None}

    def parse_base64_data_url(self, url: str) -> tuple[str, str] | None:
        # 视觉输入在 Orbit 内部统一先走 data URL，具体 provider 再按各自协议改写。
        match = re.match(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", url, flags=re.DOTALL)
        if match is None:
            return None
        media_type = match.group("mime").strip()
        data = match.group("data").strip()
        if not media_type or not data:
            return None
        return media_type, data

    def _first_int(self, payload: dict[str, Any], *paths: str) -> int | None:
        # usage 字段的层级和命名经常变化，按候选路径依次探测可减少各 provider 分支判断。
        for path in paths:
            value = self._get_nested_value(payload, path)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
        return None

    def _get_nested_value(self, payload: dict[str, Any], path: str) -> Any:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    @abstractmethod
    def build_chat_model(self, config: LLMRuntimeConfig) -> BaseChatModel:
        pass

    def supports_native_stream(self, config: LLMRuntimeConfig) -> bool:
        return False

    def stream_chat(
        self,
        *,
        config: LLMRuntimeConfig,
        messages: list[BaseMessage],
    ) -> AsyncIterator[LLMProviderStreamChunk]:
        raise LLMProviderError(f"{self.name} 暂不支持 provider 原生流式调用")

    async def list_models(self, config: LLMRuntimeConfig) -> list[LLMModelInfo]:
        if not self.supports_model_list:
            return []
        raise LLMProviderError(f"{self.name} 暂不支持自动获取模型列表")
