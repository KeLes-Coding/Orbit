from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.core.crypto import decrypt_secret
from app.models.llm_config import LLMConfig
from app.models.message import Message


@dataclass
class LLMCompletion:
    # 统一不同供应商的返回结构，服务层只关心文本、用量和原始元信息。
    content: str
    token_usage: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)


class LLMClientError(Exception):
    # 模型适配层抛出的业务异常，ConversationService 会转换成消息 failed 状态。
    pass


class LLMClient:
    # MVP 先通过 LangChain 支持 OpenAI Chat Completions 兼容协议和 Ollama。
    async def generate(
        self,
        *,
        config: LLMConfig,
        messages: list[Message],
        summary: str | None = None,
    ) -> LLMCompletion:
        provider = config.provider.strip().lower()
        chat_messages = self._build_langchain_messages(messages=messages, summary=summary)

        if provider in {"openai", "openai_compatible"}:
            return await self._ainvoke(
                model=self._build_openai_model(config),
                messages=chat_messages,
                config=config,
            )
        if provider == "ollama":
            return await self._ainvoke(
                model=self._build_ollama_model(config),
                messages=chat_messages,
                config=config,
            )

        raise LLMClientError(f"暂不支持的模型供应商：{config.provider}")

    def _build_langchain_messages(
        self,
        *,
        messages: list[Message],
        summary: str | None,
    ) -> list[BaseMessage]:
        chat_messages: list[BaseMessage] = []
        if summary:
            # 摘要作为 system 上下文注入，不改写原始 messages 事实源。
            chat_messages.append(
                SystemMessage(content=f"以下是此前对话摘要，请在后续回复中作为上下文参考：\n{summary}")
            )

        for message in messages:
            if message.status not in {"completed", "partial"}:
                continue
            if message.role not in {"system", "user", "assistant", "tool"}:
                continue
            if not message.content:
                continue
            chat_messages.append(self._to_langchain_message(message))

        if not chat_messages:
            raise LLMClientError("没有可用于模型调用的消息上下文")
        return chat_messages

    def _to_langchain_message(self, message: Message) -> BaseMessage:
        if message.role == "system":
            return SystemMessage(content=message.content)
        if message.role == "user":
            return HumanMessage(content=message.content)
        if message.role == "assistant":
            return AIMessage(content=message.content)
        return ToolMessage(
            content=message.content,
            tool_call_id=message.langgraph_message_id or str(message.id),
        )

    def _build_openai_model(self, config: LLMConfig) -> ChatOpenAI:
        api_key = decrypt_secret(config.api_key_ciphertext)
        if not api_key:
            raise LLMClientError("当前模型配置缺少 API Key")

        options = self._parse_provider_options(
            config.provider_options,
            default_timeout=60,
        )
        model_kwargs = {
            "model": config.model,
            "api_key": api_key,
            "base_url": (config.base_url or "https://api.openai.com/v1").rstrip("/"),
            "timeout": options.timeout,
            **options.generation,
            **options.langchain,
        }
        if options.extra_body:
            model_kwargs["extra_body"] = options.extra_body

        try:
            return ChatOpenAI(**model_kwargs)
        except Exception as exc:
            raise LLMClientError(f"模型配置初始化失败：{exc}") from exc

    def _build_ollama_model(self, config: LLMConfig) -> ChatOllama:
        options = self._parse_provider_options(
            config.provider_options,
            default_timeout=120,
        )
        model_kwargs = {
            "model": config.model,
            "base_url": (config.base_url or "http://127.0.0.1:11434").rstrip("/"),
            **options.generation,
            **options.langchain,
        }
        if options.timeout is not None:
            model_kwargs["timeout"] = options.timeout

        try:
            return ChatOllama(**model_kwargs)
        except Exception as exc:
            raise LLMClientError(f"Ollama 配置初始化失败：{exc}") from exc

    async def _ainvoke(
        self,
        *,
        model: Any,
        messages: list[BaseMessage],
        config: LLMConfig,
    ) -> LLMCompletion:
        try:
            response = await model.ainvoke(messages)
        except Exception as exc:
            raise LLMClientError(f"模型服务请求失败：{exc}") from exc

        content = self._normalize_content(response.content)
        if content == "":
            raise LLMClientError("模型服务没有返回 assistant 内容")

        response_metadata = dict(getattr(response, "response_metadata", None) or {})
        response_metadata.setdefault("provider", config.provider)
        response_metadata.setdefault("model", config.model)
        token_usage = self._extract_token_usage(response=response, response_metadata=response_metadata)

        if getattr(response, "id", None):
            response_metadata.setdefault("raw_id", response.id)

        return LLMCompletion(
            content=content,
            token_usage=token_usage,
            response_metadata=response_metadata,
        )

    def _normalize_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
            return "".join(text_parts)
        return str(content) if content is not None else ""

    def _extract_token_usage(
        self,
        *,
        response: Any,
        response_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        usage_metadata = getattr(response, "usage_metadata", None)
        if isinstance(usage_metadata, dict):
            return usage_metadata

        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, dict):
            return token_usage

        usage = response_metadata.get("usage")
        if isinstance(usage, dict):
            return usage

        return {}

    def _parse_provider_options(
        self,
        raw_options: dict | None,
        *,
        default_timeout: float,
    ) -> LLMProviderOptions:
        options = dict(raw_options or {})
        connection = options.pop("connection", {}) or {}
        generation = options.pop("generation", {}) or {}
        langchain = options.pop("langchain", {}) or {}
        extra_body = options.pop("extra_body", {}) or {}

        if not isinstance(connection, dict):
            raise LLMClientError("provider_options.connection 必须是对象")
        if not isinstance(generation, dict):
            raise LLMClientError("provider_options.generation 必须是对象")
        if not isinstance(langchain, dict):
            raise LLMClientError("provider_options.langchain 必须是对象")
        if not isinstance(extra_body, dict):
            raise LLMClientError("provider_options.extra_body 必须是对象")

        timeout = connection.pop("timeout", options.pop("timeout", default_timeout))
        generation = {
            **options,
            **generation,
        }
        return LLMProviderOptions(
            timeout=float(timeout),
            generation=generation,
            langchain=langchain,
            extra_body=extra_body,
        )


@dataclass
class LLMProviderOptions:
    timeout: float
    generation: dict[str, Any] = field(default_factory=dict)
    langchain: dict[str, Any] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
