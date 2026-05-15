from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.models.llm_config import LLMConfig
from app.models.message import Message
from app.services.llm_debug import log_llm_object
from app.services.llm.providers.base import BaseLLMProvider, LLMProviderError
from app.services.llm.providers.registry import get_provider
from app.services.tools import OrbitToolRuntime, ToolExecutionResult


@dataclass
class LLMCompletion:
    # 统一不同供应商的返回结构，服务层只关心正文、推理文本、用量和原始元信息。
    content: str
    reasoning_content: str = ""
    token_usage: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LLMStreamChunk:
    # 流式调用的内部统一 chunk，避免会话服务直接依赖 LangChain 的返回形状。
    content_delta: str = ""
    reasoning_delta: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None


class LLMClientError(Exception):
    # 模型适配层抛出的业务异常，ConversationService 会转换成消息 failed 状态。
    pass


class LLMClient:
    # MVP 先通过 LangChain 支持 OpenAI Chat Completions 兼容协议和 Ollama。
    MAX_TOOL_ROUNDS = 4

    def __init__(self, tool_runtime: OrbitToolRuntime | None = None) -> None:
        # 工具运行时独立成一层，后续引入 agent runtime 时可以直接复用同一套工具定义。
        self.tool_runtime = tool_runtime or OrbitToolRuntime()

    async def generate(
        self,
        *,
        config: LLMConfig,
        messages: list[Message],
        summary: str | None = None,
        model: str | None = None,
        enable_tools: bool = False,
    ) -> LLMCompletion:
        # LLMClient 只做运行时编排；具体 provider 差异交给 registry 下的 provider 实现。
        provider = get_provider(config.provider)
        if provider is None:
            raise LLMClientError(f"暂不支持的模型供应商：{config.provider}")

        chat_messages = self._build_langchain_messages(messages=messages, summary=summary, config=config)

        try:
            runtime_config = provider.from_model_config(config, model=model)
            if enable_tools:
                runtime_config = self._prepare_runtime_config_for_tools(
                    provider=provider,
                    runtime_config=runtime_config,
                )
            chat_model = provider.build_chat_model(runtime_config)
            if enable_tools:
                chat_model = self._bind_tools(chat_model)
        except LLMProviderError as exc:
            raise LLMClientError(str(exc)) from exc
        except Exception as exc:
            raise LLMClientError(f"模型配置初始化失败：{exc}") from exc

        return await self._ainvoke(
            model=chat_model,
            messages=chat_messages,
            config=config,
            runtime_config=runtime_config,
            provider=provider,
            enable_tools=enable_tools,
        )

    async def stream(
        self,
        *,
        config: LLMConfig,
        messages: list[Message],
        summary: str | None = None,
        model: str | None = None,
        enable_tools: bool = False,
    ) -> AsyncIterator[LLMStreamChunk]:
        # 与 generate 复用同一套上下文组装和 provider 初始化，只把 ainvoke 换成 astream。
        provider = get_provider(config.provider)
        if provider is None:
            raise LLMClientError(f"暂不支持的模型供应商：{config.provider}")

        chat_messages = self._build_langchain_messages(messages=messages, summary=summary, config=config)
        resolved_model = model or (config.models[0] if config.models else "")

        try:
            runtime_config = provider.from_model_config(config, model=model)
            if enable_tools:
                runtime_config = self._prepare_runtime_config_for_tools(
                    provider=provider,
                    runtime_config=runtime_config,
                )
            if provider.supports_native_stream(runtime_config) and not enable_tools:
                async for chunk in provider.stream_chat(config=runtime_config, messages=chat_messages):
                    response_metadata = self._normalize_response_metadata(
                        provider=provider,
                        response_metadata=chunk.response_metadata,
                        fallback_provider=config.provider,
                        fallback_model=resolved_model,
                    )
                    log_llm_object(
                        phase="provider.stream.chunk",
                        provider=config.provider,
                        model=resolved_model,
                        value=chunk.raw,
                        extracted={
                            "content_delta": chunk.content_delta,
                            "reasoning_delta": chunk.reasoning_delta,
                            "tool_calls": chunk.tool_calls,
                            "tool_results": chunk.tool_results,
                            "token_usage": chunk.token_usage,
                            "finish_reason": chunk.finish_reason,
                        },
                    )
                    yield LLMStreamChunk(
                        content_delta=chunk.content_delta,
                        reasoning_delta=chunk.reasoning_delta,
                        tool_calls=chunk.tool_calls,
                        tool_results=chunk.tool_results,
                        token_usage=chunk.token_usage,
                        response_metadata=response_metadata,
                        finish_reason=chunk.finish_reason,
                    )
                return

            chat_model = provider.build_chat_model(runtime_config)
            if enable_tools:
                chat_model = self._bind_tools(chat_model)
        except LLMProviderError as exc:
            raise LLMClientError(str(exc)) from exc
        except Exception as exc:
            raise LLMClientError(f"模型配置初始化失败：{exc}") from exc

        if enable_tools:
            async for chunk in self._astream_with_tool_loop(
                model=chat_model,
                messages=chat_messages,
                provider=provider,
                fallback_provider=config.provider,
                fallback_model=resolved_model,
            ):
                yield chunk
            return

        try:
            async for chunk in chat_model.astream(chat_messages):
                response_metadata = self._normalize_response_metadata(
                    provider=provider,
                    response_metadata=getattr(chunk, "response_metadata", None),
                    fallback_provider=config.provider,
                    fallback_model=resolved_model,
                )
                token_usage = self._extract_token_usage(
                    provider=provider,
                    response=chunk,
                    response_metadata=response_metadata,
                )
                content_delta, reasoning_delta = self._split_message_content(chunk)
                tool_calls = self._extract_stream_tool_calls(chunk)
                log_llm_object(
                    phase="stream.chunk",
                    provider=config.provider,
                    model=resolved_model,
                    value=chunk,
                    extracted={
                        "content_delta": content_delta,
                        "reasoning_delta": reasoning_delta,
                        "tool_calls": tool_calls,
                        "tool_results": [],
                        "token_usage": token_usage,
                        "finish_reason": self._extract_finish_reason(
                            provider=provider,
                            response_metadata=response_metadata,
                        ),
                    },
                )
                yield LLMStreamChunk(
                    content_delta=content_delta,
                    reasoning_delta=reasoning_delta,
                    tool_calls=tool_calls,
                    tool_results=[],
                    token_usage=token_usage,
                    response_metadata=response_metadata,
                    finish_reason=self._extract_finish_reason(
                        provider=provider,
                        response_metadata=response_metadata,
                    ),
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
        provider: BaseLLMProvider,
        enable_tools: bool = False,
    ) -> LLMCompletion:
        if enable_tools:
            return await self._ainvoke_with_tool_loop(
                model=model,
                messages=messages,
                config=config,
                runtime_config=runtime_config,
                provider=provider,
            )

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

        response_metadata = self._normalize_response_metadata(
            provider=provider,
            response_metadata=getattr(response, "response_metadata", None),
            fallback_provider=config.provider,
            fallback_model=resolved_model,
        )
        # 非流式响应先把 tool_calls 收敛成 Orbit 自己的最小结构，后续接 LangGraph/agent 直接复用。
        tool_calls = self._extract_tool_calls(response)
        if tool_calls:
            response_metadata["normalized_tool_calls"] = tool_calls
        token_usage = self._extract_token_usage(
            provider=provider,
            response=response,
            response_metadata=response_metadata,
        )

        if getattr(response, "id", None):
            response_metadata.setdefault("raw_id", response.id)

        return LLMCompletion(
            content=content,
            reasoning_content=reasoning_content,
            token_usage=token_usage,
            response_metadata=response_metadata,
            tool_calls=tool_calls,
            tool_results=[],
        )

    def _extract_finish_reason(
        self,
        *,
        provider: BaseLLMProvider,
        response_metadata: dict[str, Any],
    ) -> str | None:
        finish_reason = (
            response_metadata.get("finish_reason")
            or response_metadata.get("provider_finish_reason")
            or response_metadata.get("done_reason")
        )
        return provider.normalize_finish_reason(finish_reason)

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
        provider: BaseLLMProvider,
        response: Any,
        response_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        # LangChain 标准字段优先；兼容部分 provider 把用量放在 response_metadata 里的情况。
        usage_metadata = getattr(response, "usage_metadata", None)
        if isinstance(usage_metadata, dict):
            return provider.normalize_token_usage(usage_metadata)

        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, dict):
            return provider.normalize_token_usage(token_usage)

        usage = response_metadata.get("usage")
        if isinstance(usage, dict):
            return provider.normalize_token_usage(usage)

        return {}

    def _normalize_response_metadata(
        self,
        *,
        provider: BaseLLMProvider,
        response_metadata: dict[str, Any] | None,
        fallback_provider: str,
        fallback_model: str,
    ) -> dict[str, Any]:
        metadata = dict(response_metadata or {})
        metadata.setdefault("provider", fallback_provider)
        metadata.setdefault("model", fallback_model)

        raw_finish_reason = (
            metadata.get("provider_finish_reason")
            or metadata.get("done_reason")
            or metadata.get("finish_reason")
        )
        if raw_finish_reason is not None:
            metadata["provider_finish_reason"] = str(raw_finish_reason)

        normalized_finish_reason = self._extract_finish_reason(
            provider=provider,
            response_metadata=metadata,
        )
        if normalized_finish_reason is not None:
            metadata["finish_reason"] = normalized_finish_reason
        return metadata

    def _extract_tool_calls(self, response: Any) -> list[dict[str, Any]]:
        # LangChain 优先把 provider tool call 归一化到 tool_calls；缺失时再退回 additional_kwargs。
        normalized_calls = getattr(response, "tool_calls", None)
        if isinstance(normalized_calls, list) and normalized_calls:
            result: list[dict[str, Any]] = []
            for item in normalized_calls:
                if not isinstance(item, dict):
                    continue
                result.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "args": item.get("args"),
                        "type": item.get("type") or "tool_call",
                    }
                )
            return [item for item in result if item.get("name")]

        additional_kwargs = getattr(response, "additional_kwargs", None)
        if not isinstance(additional_kwargs, dict):
            return []
        raw_tool_calls = additional_kwargs.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            return []

        result: list[dict[str, Any]] = []
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                continue
            # OpenAI 兼容格式里 tool call 的函数信息嵌在 function 对象中，这里先显式收敛成 dict。
            function_value = item.get("function")
            function: dict[str, Any] = function_value if isinstance(function_value, dict) else {}
            result.append(
                {
                    "id": item.get("id"),
                    "name": function.get("name"),
                    "args": function.get("arguments"),
                    "type": item.get("type") or "tool_call",
                }
            )
        return [item for item in result if item.get("name")]

    def _extract_stream_tool_calls(self, chunk: Any) -> list[dict[str, Any]]:
        # 流式场景先兼容 LangChain 标准的 tool_call_chunks；没有时再退回完整 tool_calls。
        raw_chunks = getattr(chunk, "tool_call_chunks", None)
        if isinstance(raw_chunks, list) and raw_chunks:
            result: list[dict[str, Any]] = []
            for item in raw_chunks:
                if isinstance(item, dict):
                    result.append(
                        {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "args": item.get("args"),
                            "index": item.get("index"),
                            "type": item.get("type") or "tool_call_chunk",
                        }
                    )
                    continue
                result.append(
                    {
                        "id": getattr(item, "id", None),
                        "name": getattr(item, "name", None),
                        "args": getattr(item, "args", None),
                        "index": getattr(item, "index", None),
                        "type": getattr(item, "type", None) or "tool_call_chunk",
                    }
                )
            return [item for item in result if item.get("name") or item.get("args")]

        return self._extract_tool_calls(chunk)

    def _bind_tools(self, model: Any) -> Any:
        # 仅在 tool 模式下给模型挂工具 schema，避免影响普通 chat 模式的流式行为和返回形状。
        try:
            return model.bind_tools(self.tool_runtime.get_langchain_tools())
        except Exception as exc:
            raise LLMClientError(f"当前模型不支持工具调用：{exc}") from exc

    def _prepare_runtime_config_for_tools(
        self,
        *,
        provider: BaseLLMProvider,
        runtime_config: Any,
    ) -> Any:
        # DeepSeek V4 默认开启 thinking；若直接进入工具循环，又不回传完整 reasoning/thinking block，
        # OpenAI/Anthropic 两种兼容接口都会返回 400。这里先在工具模式下强制切到 non-thinking，
        # 让当前前后端 tool use 链路稳定可用；后续如果要保留 thinking，需要补原始 block 级回传。
        if not self._should_disable_thinking_for_tool_mode(provider=provider, runtime_config=runtime_config):
            return runtime_config

        provider_options = dict(getattr(runtime_config, "provider_options", {}) or {})
        generation = dict(provider_options.get("generation") or {})
        extra_body = dict(provider_options.get("extra_body") or {})

        if provider.provider == "anthropic":
            generation["thinking"] = {"type": "disabled"}
            provider_options["generation"] = generation
        else:
            extra_body["thinking"] = {"type": "disabled"}
            provider_options["extra_body"] = extra_body

        return replace(runtime_config, provider_options=provider_options)

    def _should_disable_thinking_for_tool_mode(
        self,
        *,
        provider: BaseLLMProvider,
        runtime_config: Any,
    ) -> bool:
        base_url = str(getattr(runtime_config, "base_url", "") or "").lower()
        model = str(getattr(runtime_config, "model", "") or "").lower()
        if "api.deepseek.com" in base_url:
            return True
        if provider.provider == "deepseek":
            return True
        return model.startswith("deepseek-v4")

    async def _ainvoke_with_tool_loop(
        self,
        *,
        model: Any,
        messages: list[BaseMessage],
        config: LLMConfig,
        runtime_config: Any,
        provider: BaseLLMProvider,
    ) -> LLMCompletion:
        # 非流式工具模式：模型提工具 -> 后端执行 -> tool result 回填 -> 模型继续，直到产出最终答复。
        runtime_messages = list(messages)
        aggregated_tool_calls: list[dict[str, Any]] = []
        aggregated_tool_results: list[dict[str, Any]] = []
        resolved_model = getattr(runtime_config, "model", None) or (config.models[0] if config.models else "")

        for _ in range(self.MAX_TOOL_ROUNDS + 1):
            try:
                response = await model.ainvoke(runtime_messages)
            except Exception as exc:
                raise LLMClientError(f"模型服务请求失败：{exc}") from exc

            completion = self._completion_from_response(
                response=response,
                provider=provider,
                fallback_provider=config.provider,
                fallback_model=resolved_model,
            )
            if not completion.tool_calls:
                completion.tool_calls = aggregated_tool_calls
                completion.tool_results = aggregated_tool_results
                if aggregated_tool_calls:
                    completion.response_metadata["normalized_tool_calls"] = aggregated_tool_calls
                if aggregated_tool_results:
                    completion.response_metadata["normalized_tool_results"] = aggregated_tool_results
                if completion.content == "":
                    raise LLMClientError("模型服务没有返回 assistant 内容")
                return completion

            aggregated_tool_calls = self._merge_tool_call_chunks(
                existing=aggregated_tool_calls,
                incoming=completion.tool_calls,
            )
            tool_results = await self.tool_runtime.execute_tool_calls(completion.tool_calls)
            aggregated_tool_results.extend(self._tool_result_to_dict(item) for item in tool_results)
            runtime_messages.extend(
                self._build_tool_roundtrip_messages(
                    completion=completion,
                    tool_results=tool_results,
                )
            )

        raise LLMClientError("工具调用轮数超过上限，请重试或缩小任务范围")

    async def _astream_with_tool_loop(
        self,
        *,
        model: Any,
        messages: list[BaseMessage],
        provider: BaseLLMProvider,
        fallback_provider: str,
        fallback_model: str,
    ) -> AsyncIterator[LLMStreamChunk]:
        # 流式工具模式与非流式共用同一套工具执行语义，只是把中间 tool call / tool result 增量继续向外广播。
        runtime_messages = list(messages)
        aggregated_tool_calls: list[dict[str, Any]] = []

        for _ in range(self.MAX_TOOL_ROUNDS + 1):
            round_tool_calls: list[dict[str, Any]] = []
            saw_any_chunk = False

            async for raw_chunk in model.astream(runtime_messages):
                saw_any_chunk = True
                response_metadata = self._normalize_response_metadata(
                    provider=provider,
                    response_metadata=getattr(raw_chunk, "response_metadata", None),
                    fallback_provider=fallback_provider,
                    fallback_model=fallback_model,
                )
                token_usage = self._extract_token_usage(
                    provider=provider,
                    response=raw_chunk,
                    response_metadata=response_metadata,
                )
                content_delta, reasoning_delta = self._split_message_content(raw_chunk)
                tool_calls = self._extract_stream_tool_calls(raw_chunk)
                if tool_calls:
                    round_tool_calls = self._merge_tool_call_chunks(
                        existing=round_tool_calls,
                        incoming=tool_calls,
                    )
                    aggregated_tool_calls = self._merge_tool_call_chunks(
                        existing=aggregated_tool_calls,
                        incoming=tool_calls,
                    )
                    response_metadata["normalized_tool_calls"] = aggregated_tool_calls
                yield LLMStreamChunk(
                    content_delta=content_delta,
                    reasoning_delta=reasoning_delta,
                    tool_calls=tool_calls,
                    tool_results=[],
                    token_usage=token_usage,
                    response_metadata=response_metadata,
                    finish_reason=self._extract_finish_reason(
                        provider=provider,
                        response_metadata=response_metadata,
                    ),
                )

            if round_tool_calls:
                tool_results = await self.tool_runtime.execute_tool_calls(round_tool_calls)
                tool_result_payload = [self._tool_result_to_dict(item) for item in tool_results]
                yield LLMStreamChunk(
                    tool_results=tool_result_payload,
                    response_metadata={
                        "provider": fallback_provider,
                        "model": fallback_model,
                        "normalized_tool_calls": aggregated_tool_calls,
                        "normalized_tool_results": tool_result_payload,
                    },
                )
                runtime_messages.extend(
                    self._build_tool_roundtrip_messages(
                        completion=LLMCompletion(
                            content="",
                            reasoning_content="",
                            tool_calls=round_tool_calls,
                            tool_results=tool_result_payload,
                            response_metadata={
                                "provider": fallback_provider,
                                "model": fallback_model,
                            },
                        ),
                        tool_results=tool_results,
                    )
                )
                continue

            if saw_any_chunk:
                return

        raise LLMClientError("工具调用轮数超过上限，请重试或缩小任务范围")

    def _completion_from_response(
        self,
        *,
        response: Any,
        provider: BaseLLMProvider,
        fallback_provider: str,
        fallback_model: str,
    ) -> LLMCompletion:
        # 把单轮模型响应统一转成 LLMCompletion，工具模式和普通模式都复用这一层。
        content, reasoning_content = self._split_message_content(response)
        response_metadata = self._normalize_response_metadata(
            provider=provider,
            response_metadata=getattr(response, "response_metadata", None),
            fallback_provider=fallback_provider,
            fallback_model=fallback_model,
        )
        tool_calls = self._extract_tool_calls(response)
        if tool_calls:
            response_metadata["normalized_tool_calls"] = tool_calls
        token_usage = self._extract_token_usage(
            provider=provider,
            response=response,
            response_metadata=response_metadata,
        )
        if getattr(response, "id", None):
            response_metadata.setdefault("raw_id", response.id)
        return LLMCompletion(
            content=content,
            reasoning_content=reasoning_content,
            token_usage=token_usage,
            response_metadata=response_metadata,
            tool_calls=tool_calls,
            tool_results=[],
        )

    def _build_tool_roundtrip_messages(
        self,
        *,
        completion: LLMCompletion,
        tool_results: list[ToolExecutionResult],
    ) -> list[BaseMessage]:
        # 工具循环需要把“assistant 发起 tool call”与“tool 返回结果”都回填进下一轮上下文。
        messages: list[BaseMessage] = [
            AIMessage(
                content=completion.content,
                tool_calls=[self._tool_call_to_langchain(item) for item in completion.tool_calls],
            )
        ]
        for result in tool_results:
            messages.append(
                ToolMessage(
                    content=result.output,
                    tool_call_id=result.tool_call_id or result.name,
                    status="error" if result.is_error else "success",
                )
            )
        return messages

    def _tool_call_to_langchain(self, item: dict[str, Any]) -> dict[str, Any]:
        # LangChain tool call 的 args 更偏向 dict；如果当前还是 JSON 字符串，这里尽量先解析一次。
        args = item.get("args")
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except json.JSONDecodeError:
                parsed = {"input": args}
        else:
            parsed = args if isinstance(args, dict) else {}
        return {
            "id": item.get("id") or item.get("name"),
            "name": item.get("name"),
            "args": parsed,
            "type": "tool_call",
        }

    def _tool_result_to_dict(self, result: ToolExecutionResult) -> dict[str, Any]:
        return {
            "tool_call_id": result.tool_call_id,
            "name": result.name,
            "args": result.args,
            "output": result.output,
            "is_error": result.is_error,
        }

    def _merge_tool_call_chunks(
        self,
        *,
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 模型在流式 tool call 场景下经常把 arguments 拆成多段，需要在 llm_client 先做一层聚合。
        merged: list[dict[str, Any]] = [dict(item) for item in existing]
        for item in incoming:
            if not isinstance(item, dict):
                continue
            matched = self._find_matching_tool_call(existing=merged, incoming=item)
            if matched is None:
                merged.append(dict(item))
                continue
            for field_name in ("id", "name", "type", "index"):
                if item.get(field_name) is not None:
                    matched[field_name] = item[field_name]
            incoming_args = item.get("args")
            if isinstance(incoming_args, str) and incoming_args:
                previous_args = matched.get("args")
                matched["args"] = f"{previous_args}{incoming_args}" if isinstance(previous_args, str) else incoming_args
            elif incoming_args is not None:
                matched["args"] = incoming_args
        return merged

    def _find_matching_tool_call(
        self,
        *,
        existing: list[dict[str, Any]],
        incoming: dict[str, Any],
    ) -> dict[str, Any] | None:
        incoming_id = incoming.get("id")
        if incoming_id:
            matched = next((current for current in existing if current.get("id") == incoming_id), None)
            if matched is not None:
                return matched

        incoming_index = incoming.get("index")
        if incoming_index is not None:
            matched = next((current for current in existing if current.get("index") == incoming_index), None)
            if matched is not None:
                return matched

        incoming_key = self._tool_call_key(incoming)
        return next((current for current in existing if self._tool_call_key(current) == incoming_key), None)

    def _tool_call_key(self, item: dict[str, Any]) -> str:
        # 真实模型在流式 tool call 里常见“首块给 id/name，后续块只给 index + arguments 增量”。
        # 这里优先按 id 合并；没有 id 时退回 index，避免把同一次工具调用错误拆成多条。
        tool_call_id = item.get("id")
        if tool_call_id:
            return str(tool_call_id)
        index = item.get("index")
        if index is not None:
            return f"index:{index}"
        return f"{item.get('name') or ''}:fallback"
