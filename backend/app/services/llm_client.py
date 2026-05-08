from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.models.llm_config import LLMConfig
from app.models.message import Message
from app.services.llm_debug import log_llm_object
from app.services.llm.providers.base import LLMProviderError
from app.services.llm.providers.registry import get_provider


@dataclass
class LLMCompletion:
    # 统一不同供应商的返回结构，服务层只关心正文、推理文本、用量和原始元信息。
    content: str
    reasoning_content: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMStreamChunk:
    # 流式调用的内部统一 chunk，避免会话服务直接依赖 LangChain 的返回形状。
    content_delta: str = ""
    reasoning_delta: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None


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
        model: str | None = None,
    ) -> LLMCompletion:
        # LLMClient 只做运行时编排；具体 provider 差异交给 registry 下的 provider 实现。
        provider = get_provider(config.provider)
        if provider is None:
            raise LLMClientError(f"暂不支持的模型供应商：{config.provider}")

        chat_messages = self._build_langchain_messages(messages=messages, summary=summary, config=config)

        try:
            runtime_config = provider.from_model_config(config, model=model)
            chat_model = provider.build_chat_model(runtime_config)
        except LLMProviderError as exc:
            raise LLMClientError(str(exc)) from exc
        except Exception as exc:
            raise LLMClientError(f"模型配置初始化失败：{exc}") from exc

        return await self._ainvoke(
            model=chat_model,
            messages=chat_messages,
            config=config,
            runtime_config=runtime_config,
        )

    async def stream(
        self,
        *,
        config: LLMConfig,
        messages: list[Message],
        summary: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        # 与 generate 复用同一套上下文组装和 provider 初始化，只把 ainvoke 换成 astream。
        provider = get_provider(config.provider)
        if provider is None:
            raise LLMClientError(f"暂不支持的模型供应商：{config.provider}")

        chat_messages = self._build_langchain_messages(messages=messages, summary=summary, config=config)
        resolved_model = model or (config.models[0] if config.models else "")

        try:
            runtime_config = provider.from_model_config(config, model=model)
            if provider.supports_native_stream(runtime_config):
                async for chunk in provider.stream_chat(config=runtime_config, messages=chat_messages):
                    response_metadata = dict(chunk.response_metadata or {})
                    response_metadata.setdefault("provider", config.provider)
                    response_metadata.setdefault("model", resolved_model)
                    log_llm_object(
                        phase="provider.stream.chunk",
                        provider=config.provider,
                        model=resolved_model,
                        value=chunk.raw,
                        extracted={
                            "content_delta": chunk.content_delta,
                            "reasoning_delta": chunk.reasoning_delta,
                            "token_usage": chunk.token_usage,
                            "finish_reason": chunk.finish_reason,
                        },
                    )
                    yield LLMStreamChunk(
                        content_delta=chunk.content_delta,
                        reasoning_delta=chunk.reasoning_delta,
                        token_usage=chunk.token_usage,
                        response_metadata=response_metadata,
                        finish_reason=chunk.finish_reason,
                    )
                return

            chat_model = provider.build_chat_model(runtime_config)
        except LLMProviderError as exc:
            raise LLMClientError(str(exc)) from exc
        except Exception as exc:
            raise LLMClientError(f"模型配置初始化失败：{exc}") from exc

        try:
            async for chunk in chat_model.astream(chat_messages):
                response_metadata = dict(getattr(chunk, "response_metadata", None) or {})
                response_metadata.setdefault("provider", config.provider)
                response_metadata.setdefault("model", resolved_model)
                token_usage = self._extract_token_usage(
                    response=chunk,
                    response_metadata=response_metadata,
                )
                content_delta, reasoning_delta = self._split_message_content(chunk)
                log_llm_object(
                    phase="stream.chunk",
                    provider=config.provider,
                    model=resolved_model,
                    value=chunk,
                    extracted={
                        "content_delta": content_delta,
                        "reasoning_delta": reasoning_delta,
                        "token_usage": token_usage,
                        "finish_reason": self._extract_finish_reason(response_metadata),
                    },
                )
                yield LLMStreamChunk(
                    content_delta=content_delta,
                    reasoning_delta=reasoning_delta,
                    token_usage=token_usage,
                    response_metadata=response_metadata,
                    finish_reason=self._extract_finish_reason(response_metadata),
                )
        except LLMClientError:
            raise
        except Exception as exc:
            raise LLMClientError(f"模型服务流式请求失败：{exc}") from exc

    def _build_langchain_messages(
        self,
        *,
        messages: list[Message],
        summary: str | None,
        config: "LLMConfig | None" = None,
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
            if not message.content and (not message.content_parts or len(message.content_parts) == 0):
                continue
            chat_messages.append(self._to_langchain_message(message, config=config))

        if not chat_messages:
            raise LLMClientError("没有可用于模型调用的消息上下文")
        return chat_messages

    def _to_langchain_message(self, message: Message, *, config: "LLMConfig | None" = None) -> BaseMessage:
        content = self._build_message_content(message, config=config)
        if message.role == "system":
            return SystemMessage(content=content)
        if message.role == "user":
            return HumanMessage(content=content)
        if message.role == "assistant":
            return AIMessage(content=content)
        return ToolMessage(
            content=content,
            tool_call_id=message.langgraph_message_id or str(message.id),
        )

    def _build_message_content(
        self, message: Message, *, config: "LLMConfig | None" = None
    ) -> str | list[str | dict]:
        # 多模态返回 list[str | dict]，纯文本返回 str。
        content_parts = message.content_parts or []
        has_images = any(
            part.get("type") == "file" and (part.get("mime_type") or "").startswith("image/")
            for part in content_parts
        )
        supports_vision = config is not None and config.supports_vision

        if not has_images or not supports_vision:
            # 纯文本路径：拼接文本和附件引用。
            text = message.content or ""
            for part in content_parts:
                if part.get("type") == "file":
                    file_name = part.get("name", "unknown")
                    extracted = part.get("extracted_text", "")
                    text += f"\n\n[附件：{file_name}]"
                    if extracted:
                        text += f"\n{extracted}"
            return text

        # 多模态路径：构造 LangChain content_blocks 列表。
        import base64
        from app.services.files.storage import _resolve_storage_root

        blocks: list[str | dict] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})

        base_dir = _resolve_storage_root()
        for part in content_parts:
            if part.get("type") != "file":
                continue
            mime_type = part.get("mime_type", "")
            file_name = part.get("name", "unknown")
            if mime_type.startswith("image/"):
                storage_path = part.get("storage_path", "")
                if storage_path:
                    full_path = base_dir / storage_path
                    if full_path.exists():
                        image_bytes = full_path.read_bytes()
                        b64 = base64.b64encode(image_bytes).decode("ascii")
                        blocks.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                        })
                    else:
                        blocks.append({"type": "text", "text": f"\n[附件：{file_name}（文件丢失）]"})
                else:
                    blocks.append({"type": "text", "text": f"\n[附件：{file_name}]"})
            else:
                # 文档文件（PDF/DOCX 等）保留文本引用。
                extracted = part.get("extracted_text", "")
                ref = f"\n\n[附件：{file_name}]"
                if extracted:
                    ref += f"\n{extracted}"
                blocks.append({"type": "text", "text": ref})

        return blocks

    async def _ainvoke(
        self,
        *,
        model: Any,
        messages: list[BaseMessage],
        config: LLMConfig,
        runtime_config: Any = None,
    ) -> LLMCompletion:
        # LangChain 不同 provider 的返回元信息形状略有差异，这里统一收敛成 LLMCompletion。
        try:
            response = await model.ainvoke(messages)
        except Exception as exc:
            raise LLMClientError(f"模型服务请求失败：{exc}") from exc

        content, reasoning_content = self._split_message_content(response)
        resolved_model = getattr(runtime_config, "model", None) or (config.models[0] if config.models else "")
        log_llm_object(
            phase="generate.response",
            provider=config.provider,
            model=resolved_model,
            value=response,
            extracted={
                "content": content,
                "reasoning_content": reasoning_content,
            },
        )
        if content == "":
            raise LLMClientError("模型服务没有返回 assistant 内容")

        response_metadata = dict(getattr(response, "response_metadata", None) or {})
        response_metadata.setdefault("provider", config.provider)
        response_metadata.setdefault("model", resolved_model)
        token_usage = self._extract_token_usage(response=response, response_metadata=response_metadata)

        if getattr(response, "id", None):
            response_metadata.setdefault("raw_id", response.id)

        return LLMCompletion(
            content=content,
            reasoning_content=reasoning_content,
            token_usage=token_usage,
            response_metadata=response_metadata,
        )

    def _extract_finish_reason(self, response_metadata: dict[str, Any]) -> str | None:
        finish_reason = response_metadata.get("finish_reason") or response_metadata.get("done_reason")
        return str(finish_reason) if finish_reason is not None else None

    def _normalize_content(self, content: Any) -> str:
        # 预留多模态返回：当前 MVP 仍只把文本部分落入 messages.content。
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    # reasoning/thinking block 不能混入正文，否则历史上下文会把推理过程再次喂给模型。
                    block_type = str(part.get("type") or "").lower()
                    if block_type not in {"reasoning", "thinking"} and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
            return "".join(text_parts)
        return str(content) if content is not None else ""

    def _split_message_content(self, message: Any) -> tuple[str, str]:
        # LangChain 会把不同供应商的 text/reasoning 标准化到 content_blocks，优先使用这层契约。
        content_blocks = getattr(message, "content_blocks", None) or []
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        for block in content_blocks:
            block_type = self._get_block_value(block, "type")
            if block_type == "text":
                content_parts.append(self._extract_block_text(block, "text", "content"))
            elif block_type in {"reasoning", "thinking"}:
                reasoning_parts.append(
                    self._extract_block_text(block, "reasoning", "text", "content", "summary")
                )

        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)
        if content or reasoning:
            # 有些 integration 只标准化其中一种 block；缺失的一侧再从原始字段兜底。
            if not content:
                content = self._normalize_content(getattr(message, "content", ""))
            if not reasoning:
                reasoning = self._extract_reasoning_fallback(message)
            return content, reasoning

        return (
            self._normalize_content(getattr(message, "content", "")),
            self._extract_reasoning_fallback(message),
        )

    def _get_block_value(self, block: Any, key: str) -> str:
        if isinstance(block, dict):
            value = block.get(key)
        else:
            value = getattr(block, key, None)
        return str(value).lower() if value is not None else ""

    def _extract_block_text(self, block: Any, *keys: str) -> str:
        for key in keys:
            if isinstance(block, dict):
                value = block.get(key)
            else:
                value = getattr(block, key, None)
            text = self._normalize_text_value(value)
            if text:
                return text
        return ""

    def _normalize_text_value(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(self._normalize_text_value(item) for item in value)
        if isinstance(value, dict):
            for key in ("text", "content", "reasoning"):
                if key in value:
                    text = self._normalize_text_value(value[key])
                    if text:
                        return text
        return ""

    def _extract_reasoning_fallback(self, message: Any) -> str:
        # 少数 LangChain integration 还会把 reasoning 暴露在 raw kwargs/metadata，作为标准 block 缺失时的兜底。
        for container in (
            getattr(message, "additional_kwargs", None),
            getattr(message, "response_metadata", None),
        ):
            if not isinstance(container, dict):
                continue
            for key in ("reasoning_content", "reasoning", "thinking"):
                text = self._normalize_text_value(container.get(key))
                if text:
                    return text
        return ""

    def _extract_token_usage(
        self,
        *,
        response: Any,
        response_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        # LangChain 标准字段优先；兼容部分 provider 把用量放在 response_metadata 里的情况。
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
