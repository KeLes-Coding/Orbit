from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

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
    # MVP 先支持 OpenAI Chat Completions 兼容协议和 Ollama，本地结构保留扩展点。
    async def generate(
        self,
        *,
        config: LLMConfig,
        messages: list[Message],
        summary: str | None = None,
    ) -> LLMCompletion:
        provider = config.provider.strip().lower()
        chat_messages = self._build_chat_messages(messages=messages, summary=summary)

        if provider in {"openai", "openai_compatible"}:
            return await self._generate_openai_compatible(
                config=config,
                messages=chat_messages,
            )
        if provider == "ollama":
            return await self._generate_ollama(config=config, messages=chat_messages)

        raise LLMClientError(f"暂不支持的模型供应商：{config.provider}")

    def _build_chat_messages(
        self,
        *,
        messages: list[Message],
        summary: str | None,
    ) -> list[dict[str, str]]:
        chat_messages: list[dict[str, str]] = []
        if summary:
            # 摘要作为 system 上下文注入，不改写原始 messages 事实源。
            chat_messages.append(
                {
                    "role": "system",
                    "content": f"以下是此前对话摘要，请在后续回复中作为上下文参考：\n{summary}",
                }
            )

        for message in messages:
            if message.status not in {"completed", "partial"}:
                continue
            if message.role not in {"system", "user", "assistant", "tool"}:
                continue
            if not message.content:
                continue
            chat_messages.append({"role": message.role, "content": message.content})

        if not chat_messages:
            raise LLMClientError("没有可用于模型调用的消息上下文")
        return chat_messages

    async def _generate_openai_compatible(
        self,
        *,
        config: LLMConfig,
        messages: list[dict[str, str]],
    ) -> LLMCompletion:
        api_key = decrypt_secret(config.api_key_ciphertext)
        if not api_key:
            raise LLMClientError("当前模型配置缺少 API Key")

        options = dict(config.provider_options or {})
        timeout = float(options.pop("timeout", 60))
        extra_body = options.pop("extra_body", None) or {}
        if not isinstance(extra_body, dict):
            raise LLMClientError("provider_options.extra_body 必须是对象")
        base_url = (config.base_url or "https://api.openai.com/v1").rstrip("/")
        payload = {
            **options,
            **extra_body,
            "model": config.model,
            "messages": messages,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._extract_error_detail(exc.response)
            raise LLMClientError(f"模型服务返回错误：{detail}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"模型服务请求失败：{exc}") from exc

        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )
        if not isinstance(content, str) or content == "":
            raise LLMClientError("模型服务没有返回 assistant 内容")

        return LLMCompletion(
            content=content,
            token_usage=data.get("usage") or {},
            response_metadata={
                "provider": config.provider,
                "finish_reason": data.get("choices", [{}])[0].get("finish_reason"),
                "raw_id": data.get("id"),
            },
        )

    async def _generate_ollama(
        self,
        *,
        config: LLMConfig,
        messages: list[dict[str, str]],
    ) -> LLMCompletion:
        options = dict(config.provider_options or {})
        timeout = float(options.pop("timeout", 120))
        base_url = (config.base_url or "http://127.0.0.1:11434").rstrip("/")
        payload = {
            "model": config.model,
            "messages": messages,
            "stream": False,
            "options": options,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{base_url}/api/chat", json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._extract_error_detail(exc.response)
            raise LLMClientError(f"Ollama 返回错误：{detail}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"Ollama 请求失败：{exc}") from exc

        data = response.json()
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str) or content == "":
            raise LLMClientError("Ollama 没有返回 assistant 内容")

        return LLMCompletion(
            content=content,
            token_usage={
                "prompt_eval_count": data.get("prompt_eval_count"),
                "eval_count": data.get("eval_count"),
            },
            response_metadata={
                "provider": config.provider,
                "done": data.get("done"),
                "total_duration": data.get("total_duration"),
            },
        )

    def _extract_error_detail(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text[:500]
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            if error is not None:
                return str(error)
        return str(data)[:500]
