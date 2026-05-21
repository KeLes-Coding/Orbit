import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status

from app.db.session import AsyncSessionLocal
from app.models.conversation import Conversation
from app.schemas.conversation import ActiveStreamRead, ConversationRead, MessageRead
from app.services.conversations.base import (
    ConversationBaseService,
    ConversationStreamEvent,
)
from app.services.langgraph_runtime.chat_runtime import LangGraphChatRuntime
from app.services.langgraph_runtime.runtime_context import OrbitRuntimeContext, OrbitRuntimeRequest
from app.services.langgraph_runtime.state import ChatState
from app.services.langgraph_runtime.stream_adapter import StreamAdapter
from app.services.llm_client import LLMClientError
from app.services.streaming import StreamEventRecord, conversation_stream_store


class ConversationStreamRunService(ConversationBaseService):
    # 负责运行时流订阅、producer 生命周期、取消和缺失 runtime 的收口处理。
    async def subscribe_stream(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        stream_id: str,
    ) -> AsyncIterator[ConversationStreamEvent]:
        await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        # 这个入口给"刚创建完流的当前请求"使用，直接按 stream_id 订阅可避开竞争窗口。
        stream = await conversation_stream_store.get_stream(stream_id)
        if stream is None or stream.conversation_id != conversation_id or stream.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="流不存在或已过期")

        async for record in conversation_stream_store.subscribe(stream_id):
            yield await self._to_stream_event(record)

    async def get_message_active_stream(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> ActiveStreamRead:
        await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        return await self._resolve_message_active_stream(
            conversation_id=conversation_id,
            message_id=message_id,
        )

    async def cancel_message_generation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> MessageRead:
        await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        message = await self.messages.get_by_id(
            conversation_id=conversation_id,
            message_id=message_id,
        )
        if message is None or message.role != "assistant":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        if message.status != "streaming":
            return await self._message_read(message)

        # 新的 runtime store 只在显式 cancel 时中断 producer；连接断开不会走这里。
        did_signal = await conversation_stream_store.cancel(message_id=message_id)
        if did_signal:
            return await self._message_read(message)

        # 找不到活跃流时兜底更新数据库，避免遗留 streaming 状态。
        message = await self.messages.cancel_assistant_message(
            message=message,
            content=message.content,
            reasoning_content=message.reasoning_content,
            token_usage=message.token_usage,
            response_metadata=message.response_metadata,
        )
        await self.conversations.recompute_has_active_run(conversation_id)
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return await self._message_read(message)

    async def _resolve_message_active_stream(
        self,
        *,
        conversation_id: UUID,
        message_id: UUID,
    ) -> ActiveStreamRead:
        message = await self.messages.get_by_id(
            conversation_id=conversation_id, message_id=message_id
        )
        if message is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")

        candidate = message
        if message.role != "assistant":
            candidate = await self.messages.resolve_active_leaf_from(
                conversation_id=conversation_id,
                message=message,
            )

        if candidate.role != "assistant" or candidate.status != "streaming":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="当前分支没有活跃流")

        stream = await conversation_stream_store.get_stream_by_message_id(candidate.id)
        if stream is None or stream.conversation_id != conversation_id:
            await self._mark_missing_runtime_stream_failed(
                conversation_id=conversation_id,
                assistant_message=candidate,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="流不存在或已过期")

        return ActiveStreamRead(
            conversation_id=conversation_id,
            message_id=message_id,
            assistant_message_id=candidate.id,
            stream_id=stream.stream_id,
        )

    async def _mark_missing_runtime_stream_failed(
        self,
        *,
        conversation_id: UUID,
        assistant_message,
    ) -> None:
        if assistant_message.status != "streaming":
            return
        await self.messages.fail_assistant_message(
            message=assistant_message,
            error="流运行态不存在或已过期，请重新生成",
        )
        await self.conversations.recompute_has_active_run(conversation_id)
        await self.conversations.touch(conversation_id)
        await self.session.commit()

    def _spawn_stream_producer(self, *, stream_id: str, conversation_id: UUID) -> None:
        # producer 脱离当前 SSE 请求生命周期独立运行，连接断开不会影响模型生成。
        task = asyncio.create_task(
            self._run_stream_producer(
                stream_id=stream_id,
                conversation_id=conversation_id,
            )
        )
        task.add_done_callback(self._handle_stream_producer_result)

    def _spawn_title_producer(
        self,
        *,
        conversation_id: UUID,
        stream_id: str,
        user_id: UUID,
        user_message: str,
        expected_title: str,
    ) -> None:
        task = asyncio.create_task(
            self._run_title_producer(
                conversation_id=conversation_id,
                stream_id=stream_id,
                user_id=user_id,
                user_message=user_message,
                expected_title=expected_title,
            )
        )
        task.add_done_callback(self._handle_stream_producer_result)

    def _handle_stream_producer_result(self, task: asyncio.Task) -> None:
        # 后台任务异常不能静默吞掉，否则消息运行态可能长期卡住且难排查。
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            # 这里不再向外抛异常，避免事件循环日志以外再触发额外级联失败。
            return

    async def _run_stream_producer(self, *, stream_id: str, conversation_id: UUID) -> None:
        # producer 使用独立数据库会话，避免复用请求会话导致生命周期混乱。
        from app.services.conversation import ConversationService

        async with AsyncSessionLocal() as session:
            worker = ConversationService(session)
            try:
                await worker._produce_stream(stream_id=stream_id, conversation_id=conversation_id)
            except Exception as exc:
                await worker._handle_unexpected_stream_failure(
                    stream_id=stream_id,
                    conversation_id=conversation_id,
                    error=str(exc),
                )

    async def _run_title_producer(
        self,
        *,
        conversation_id: UUID,
        stream_id: str,
        user_id: UUID,
        user_message: str,
        expected_title: str,
    ) -> None:
        # 标题生成与主回复解耦，避免 New Chat 首轮等待额外的 LLM 请求。
        from app.services.conversation import ConversationService

        async with AsyncSessionLocal() as session:
            worker = ConversationService(session)
            await worker._produce_conversation_title(
                conversation_id=conversation_id,
                stream_id=stream_id,
                user_id=user_id,
                user_message=user_message,
                expected_title=expected_title,
            )

    async def _produce_conversation_title(
        self,
        *,
        conversation_id: UUID,
        stream_id: str,
        user_id: UUID,
        user_message: str,
        expected_title: str,
    ) -> None:
        conversation = await self._get_conversation_by_id(conversation_id)
        if conversation is None or conversation.user_id != user_id:
            return
        if conversation.title != expected_title:
            return

        llm_config = None
        if conversation.llm_config_id is not None:
            llm_config = await self.llm_configs.get_active(
                user_id=user_id,
                config_id=conversation.llm_config_id,
            )

        title = await self.title_generator.generate(
            user_message=user_message,
            fallback_config=llm_config,
        )
        if not title or title == expected_title:
            return

        # 只在标题仍保持初始 fallback 时覆盖，避免异步任务把用户手动重命名冲掉。
        conversation = await self._get_conversation_by_id(conversation_id)
        if conversation is None or conversation.title != expected_title:
            return

        conversation.title = title
        await self.session.commit()
        await self.session.refresh(conversation)

        try:
            await conversation_stream_store.append_event(
                stream_id,
                event="conversation.updated",
                data={
                    "conversation": ConversationRead.model_validate(conversation).model_dump(
                        mode="json"
                    )
                },
            )
        except KeyError:
            # replay 窗口过期时只保留数据库更新，前端后续列表刷新仍能拿到新标题。
            return

    async def _produce_stream(self, *, stream_id: str, conversation_id: UUID) -> None:
        # Phase 1：将执行内核从直接调用 LLMClient 切换到 LangGraphChatRuntime。
        # 验证 LangGraph 是否适合作为 Orbit 的 Chat 执行容器。
        stream = await conversation_stream_store.get_stream(stream_id)
        if stream is None:
            return

        assistant_message = await self.messages.get_by_id(
            conversation_id=conversation_id,
            message_id=stream.message_id,
        )
        if assistant_message is None:
            await conversation_stream_store.complete_stream(
                stream_id,
                retention_seconds=self.STREAM_RETENTION_SECONDS,
            )
            return

        conversation = await self._get_conversation_by_id(conversation_id)
        if conversation is None:
            await conversation_stream_store.complete_stream(
                stream_id,
                retention_seconds=self.STREAM_RETENTION_SECONDS,
            )
            return

        llm_config_id = assistant_message.llm_config_id
        if llm_config_id is None:
            raise LLMClientError("assistant 消息缺少模型配置快照")
        llm_config = await self.llm_configs.get_active(
            user_id=conversation.user_id, config_id=llm_config_id
        )
        if llm_config is None or not llm_config.is_enabled:
            raise LLMClientError("当前会话的模型配置不可用")
        if assistant_message.parent_message_id is None:
            raise LLMClientError("无法找到生成上下文")
        history_messages = await self.messages.list_path_to_message(
            conversation_id=conversation_id,
            message_id=assistant_message.parent_message_id,
        )

        # 判断执行模式：tool 走旧路径，agent 走 LangGraph agentic_chat，chat 走 LangGraph normal_chat
        effective_chat_mode = (
            getattr(assistant_message, "chat_mode", None) or conversation.chat_mode
        )
        if effective_chat_mode == "tool":
            await self._produce_stream_legacy(
                stream_id=stream_id,
                conversation=conversation,
                assistant_message=assistant_message,
                llm_config=llm_config,
                history_messages=history_messages,
                enable_tools=True,
            )
            return

        # 构建 LangGraph ChatState。敏感配置不进入 state，其余上下文字段保留给 graph 使用。
        initial_state = self._build_langgraph_state(
            stream_id=stream_id,
            conversation_id=conversation_id,
            assistant_message=assistant_message,
            conversation=conversation,
            llm_config=llm_config,
            history_messages=history_messages,
            chat_mode=effective_chat_mode,
        )

        # 创建流事件适配器，负责将 LangGraph 自定义事件写入 stream_store
        stream_adapter = StreamAdapter(
            stream_id=stream_id,
            message_id=assistant_message.id,
        )

        # 创建 LangGraph runtime 并执行
        # 提前捕获 model/config，避免类型检查器认为在闭包中可能为 None
        _model = assistant_message.model
        _llm_config = llm_config
        _llm_client = self.llm_client

        # agentic_chat 的 LLM 调用闭包：封装 stream_with_messages，
        # 使 DeepAgent 可多次调用 LLM（planning / agent loop / final）
        async def _agent_llm_invoke(
            messages,
            system_prompt,
            enable_tools,
            tools,
            tool_runtime,
            max_tool_rounds,
        ):
            async for chunk in _llm_client.stream_with_messages(
                config=_llm_config,
                messages=messages,
                model=_model,
                enable_tools=enable_tools,
                system_prompt=system_prompt,
                tools=tools,
                tool_runtime=tool_runtime,
                max_tool_rounds=max_tool_rounds,
            ):
                yield chunk

        runtime_request = OrbitRuntimeRequest(
            conversation_id=str(conversation_id),
            assistant_message_id=str(assistant_message.id),
            stream_id=stream_id,
            thread_id=conversation.thread_id,
            chat_mode=effective_chat_mode,
            agent_type="web_agent" if effective_chat_mode == "agent" else None,
            # 这里显式复制一份输入消息列表，避免后续 state / runtime 在不同层被意外共享修改。
            input_messages=list(initial_state.get("input_messages", [])),
            llm_config=llm_config,
            model=_model,
        )
        runtime_context = OrbitRuntimeContext(
            request=runtime_request,
            tool_runtime=self.llm_client.tool_runtime,
            stream_writer=None,
        )

        runtime_kwargs = {
            # normal_chat 路径仍复用现有 LLMClient.stream()，保证普通 chat 的行为尽量不变。
            "stream_factory": lambda: self.llm_client.stream(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
                model=_model,
                enable_tools=False,
            ),
            # agentic_chat 路径则改走 BaseMessage + runtime_context 模式，
            # 便于后续继续把 agent 执行器与宿主链路解耦。
            "llm_invoke": _agent_llm_invoke,
            "tool_runtime": self.llm_client.tool_runtime,
            "runtime_context": runtime_context,
        }
        try:
            runtime = LangGraphChatRuntime(**runtime_kwargs)
        except TypeError:
            # 兼容测试替身或旧构造签名。
            runtime_kwargs.pop("runtime_context", None)
            runtime = LangGraphChatRuntime(**runtime_kwargs)
        final_state = initial_state.copy()

        try:
            # 记录真实 producer task，后续 cancel 才能准确打断模型流
            await conversation_stream_store.attach_producer_task(stream_id)

            # 启动前检查是否已被取消
            if await conversation_stream_store.is_cancelled(stream_id):
                cancelled_message = await self._cancel_streaming_message(
                    conversation_id=conversation_id,
                    assistant_message=assistant_message,
                    content="",
                    token_usage={},
                    response_metadata={
                        "provider": llm_config.provider,
                        "model": _model or "",
                    },
                )
                await self._finalize_stream_conversation_state(
                    conversation=conversation,
                    stream_id=stream_id,
                )
                await conversation_stream_store.append_event(
                    stream_id,
                    event="message.cancelled",
                    data={
                        "message": (await self._message_read(cancelled_message)).model_dump(
                            mode="json"
                        )
                    },
                )
                return

            # 通过 LangGraph runtime 执行 Chat
            final_state = await runtime.run_stream(
                state=initial_state,
                stream_adapter=stream_adapter,
            )

        except asyncio.CancelledError:
            # 外部取消（task.cancel()）
            cancelled_message = await self._cancel_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content=stream_adapter.get_accumulated_state().get("response_text", ""),
                reasoning_content=stream_adapter.get_accumulated_state().get("reasoning_text", ""),
                token_usage=stream_adapter.get_accumulated_state().get("token_usage", {}),
                response_metadata=stream_adapter.get_accumulated_state().get("response_metadata", {}),
            )
            await self._finalize_stream_conversation_state(
                conversation=conversation,
                stream_id=stream_id,
            )
            await conversation_stream_store.append_event(
                stream_id,
                event="message.cancelled",
                data={
                    "message": (await self._message_read(cancelled_message)).model_dump(mode="json")
                },
            )
            return
        except LLMClientError as exc:
            # 模型调用层面的已知异常
            accumulated = stream_adapter.get_accumulated_state()
            failed_message = await self._fail_or_partial_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content=accumulated.get("response_text", ""),
                reasoning_content=accumulated.get("reasoning_text", ""),
                error=str(exc),
                token_usage=accumulated.get("token_usage", {}),
                response_metadata=accumulated.get("response_metadata", {}),
            )
            await self._finalize_stream_conversation_state(
                conversation=conversation,
                stream_id=stream_id,
            )
            await conversation_stream_store.append_event(
                stream_id,
                event="message.failed",
                data={
                    "message": (await self._message_read(failed_message)).model_dump(mode="json")
                },
            )
            return
        except Exception:
            # 非预期异常通过 _handle_unexpected_stream_failure 收口
            raise
        else:
            # 处理 LangGraph 执行结果
            error = final_state.get("error")
            accumulated = stream_adapter.get_accumulated_state()
            persisted_output = self._merge_langgraph_persisted_output(
                accumulated=accumulated,
                final_state=final_state,
            )

            if error == "cancelled":
                # call_model 节点检测到 cancel 信号
                cancelled_message = await self._cancel_streaming_message(
                    conversation_id=conversation_id,
                    assistant_message=assistant_message,
                    content=persisted_output["response_text"],
                    reasoning_content=persisted_output["reasoning_text"],
                    token_usage=persisted_output["token_usage"],
                    response_metadata=persisted_output["response_metadata"],
                )
                await self._finalize_stream_conversation_state(
                    conversation=conversation,
                    stream_id=stream_id,
                )
                await conversation_stream_store.append_event(
                    stream_id,
                    event="message.cancelled",
                    data={
                        "message": (await self._message_read(cancelled_message)).model_dump(
                            mode="json"
                        )
                    },
                )
                return

            if error:
                # graph 执行过程中出现异常
                failed_message = await self._fail_or_partial_streaming_message(
                    conversation_id=conversation_id,
                    assistant_message=assistant_message,
                    content=persisted_output["response_text"],
                    reasoning_content=persisted_output["reasoning_text"],
                    error=error,
                    token_usage=persisted_output["token_usage"],
                    response_metadata=persisted_output["response_metadata"],
                )
                await self._finalize_stream_conversation_state(
                    conversation=conversation,
                    stream_id=stream_id,
                )
                await conversation_stream_store.append_event(
                    stream_id,
                    event="message.failed",
                    data={
                        "message": (await self._message_read(failed_message)).model_dump(mode="json")
                    },
                )
                return

            # 正常完成：持久化 assistant message
            assistant_message = await self.messages.complete_assistant_message(
                message=assistant_message,
                content=persisted_output["response_text"],
                reasoning_content=persisted_output["reasoning_text"],
                token_usage=persisted_output["token_usage"],
                response_metadata=persisted_output["response_metadata"],
            )
            await self._finalize_stream_conversation_state(
                conversation=conversation,
                stream_id=stream_id,
            )
            await conversation_stream_store.append_event(
                stream_id,
                event="message.completed",
                data={
                    "message": (await self._message_read(assistant_message)).model_dump(mode="json")
                },
            )
            # 正常完成时也 return，让 finally 块清理 stream
            return

        finally:
            # 运行结束后保留一个短暂 replay 窗口，给刚断线的客户端补齐尾流
            await conversation_stream_store.complete_stream(
                stream_id,
                retention_seconds=self.STREAM_RETENTION_SECONDS,
            )

    @staticmethod
    def _convert_messages_to_langchain(
        messages: list,
        summary: str | None = None,
        llm_config=None,
    ) -> list:
        """将 DB Message ORM 对象转为 LangChain BaseMessage 列表。

        提取为独立函数，方便测试 mock 和 Phase 2 复用。
        """
        from app.services.llm_client import LLMClient

        client = LLMClient()
        return client._build_langchain_messages(
            messages=messages,
            summary=summary,
            config=llm_config,
        )

    def _build_langgraph_state(
        self,
        *,
        stream_id: str,
        conversation_id: UUID,
        assistant_message,
        conversation,
        llm_config,
        history_messages: list,
        chat_mode: str = "chat",
    ) -> ChatState:
        """根据当前请求上下文构建 LangGraph ChatState。

        不把 API Key 等敏感配置放进 state，避免 checkpoint 中出现明文密钥。
        """
        resolved_model = assistant_message.model or (
            llm_config.models[0] if llm_config.models else ""
        )

        # 将 DB 中的 Message ORM 对象转为 LangChain BaseMessage 列表
        input_messages = self._convert_messages_to_langchain(
            messages=history_messages,
            summary=conversation.summary,
            llm_config=llm_config,
        )

        return ChatState(
            input_messages=input_messages,
            # Phase 2 新增
            chat_mode=chat_mode,
            execution_mode="",
            thought_events=[],
            workspace_files=[],
            # 输出
            response_text="",
            reasoning_text="",
            token_usage={},
            response_metadata={
                "provider": llm_config.provider,
                "model": resolved_model,
            },
            error=None,
        )

    @staticmethod
    def _merge_langgraph_persisted_output(
        *,
        accumulated: dict[str, Any],
        final_state: ChatState,
    ) -> dict[str, Any]:
        """合并流式累积态和 graph 最终态，避免只依赖 SSE delta 导致落库为空。"""
        response_metadata = dict(final_state.get("response_metadata") or {})
        response_metadata.update(accumulated.get("response_metadata", {}) or {})
        thought_events = accumulated.get("thought_events") or final_state.get("thought_events", [])
        if thought_events:
            response_metadata["thought_events"] = thought_events
        return {
            "response_text": accumulated.get("response_text") or final_state.get("response_text", ""),
            "reasoning_text": accumulated.get("reasoning_text") or final_state.get("reasoning_text", ""),
            "token_usage": accumulated.get("token_usage") or final_state.get("token_usage", {}),
            "response_metadata": response_metadata,
        }

    async def _produce_stream_legacy(
        self,
        *,
        stream_id: str,
        conversation,
        assistant_message,
        llm_config,
        history_messages: list,
        enable_tools: bool,
    ) -> None:
        """旧执行路径：使用 LLMClient.stream() 直接调用，保留 tool/agent 模式支持。

        Phase 2 将移除此方法，统一到 LangGraph 内部处理 agent loop。
        """
        conversation_id = conversation.id
        full_content_parts: list[str] = []
        full_reasoning_parts: list[str] = []
        normalized_tool_calls: list[dict[str, Any]] = []
        normalized_tool_results: list[dict[str, Any]] = []
        token_usage: dict[str, Any] = {}
        response_metadata: dict[str, Any] = {
            "provider": llm_config.provider,
            "model": assistant_message.model or "",
        }
        finish_reason: str | None = None

        try:
            await conversation_stream_store.attach_producer_task(stream_id)
            if await conversation_stream_store.is_cancelled(stream_id):
                cancelled_message = await self._cancel_streaming_message(
                    conversation_id=conversation_id,
                    assistant_message=assistant_message,
                    content="",
                    token_usage=token_usage,
                    response_metadata=response_metadata,
                )
                await self._finalize_stream_conversation_state(
                    conversation=conversation,
                    stream_id=stream_id,
                )
                await conversation_stream_store.append_event(
                    stream_id,
                    event="message.cancelled",
                    data={
                        "message": (await self._message_read(cancelled_message)).model_dump(
                            mode="json"
                        )
                    },
                )
                return

            async for chunk in self.llm_client.stream(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
                model=assistant_message.model,
                enable_tools=enable_tools,
            ):
                if await conversation_stream_store.is_cancelled(stream_id):
                    cancelled_message = await self._cancel_streaming_message(
                        conversation_id=conversation_id,
                        assistant_message=assistant_message,
                        content="".join(full_content_parts),
                        reasoning_content="".join(full_reasoning_parts),
                        token_usage=token_usage,
                        response_metadata=response_metadata,
                    )
                    await self._finalize_stream_conversation_state(
                        conversation=conversation,
                        stream_id=stream_id,
                    )
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.cancelled",
                        data={
                            "message": (await self._message_read(cancelled_message)).model_dump(
                                mode="json"
                            )
                        },
                    )
                    return

                if chunk.token_usage:
                    token_usage = chunk.token_usage
                if chunk.response_metadata:
                    response_metadata.update(chunk.response_metadata)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

                chunk_tool_calls = getattr(chunk, "tool_calls", None) or []
                if chunk_tool_calls:
                    normalized_tool_calls = self._merge_tool_call_chunks(
                        existing=normalized_tool_calls,
                        incoming=chunk_tool_calls,
                    )
                    response_metadata["normalized_tool_calls"] = normalized_tool_calls
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.tool_call_delta",
                        data={
                            "message_id": str(assistant_message.id),
                            "tool_calls": chunk_tool_calls,
                        },
                    )

                chunk_tool_results = getattr(chunk, "tool_results", None) or []
                if chunk_tool_results:
                    normalized_tool_results.extend(chunk_tool_results)
                    response_metadata["normalized_tool_results"] = normalized_tool_results
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.tool_result",
                        data={
                            "message_id": str(assistant_message.id),
                            "tool_results": chunk_tool_results,
                        },
                    )

                if chunk.reasoning_delta:
                    full_reasoning_parts.append(chunk.reasoning_delta)
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.reasoning_delta",
                        data={
                            "message_id": str(assistant_message.id),
                            "delta": chunk.reasoning_delta,
                        },
                    )

                if chunk.content_delta:
                    full_content_parts.append(chunk.content_delta)
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.delta",
                        data={
                            "message_id": str(assistant_message.id),
                            "delta": chunk.content_delta,
                        },
                    )

            full_content = "".join(full_content_parts)
            full_reasoning = "".join(full_reasoning_parts)
            if not full_content:
                raise LLMClientError("模型服务没有返回 assistant 内容")

            if normalized_tool_calls:
                response_metadata["normalized_tool_calls"] = normalized_tool_calls
            if normalized_tool_results:
                response_metadata["normalized_tool_results"] = normalized_tool_results
            if finish_reason:
                response_metadata["finish_reason"] = finish_reason
            assistant_message = await self.messages.complete_assistant_message(
                message=assistant_message,
                content=full_content,
                reasoning_content=full_reasoning,
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            await self._finalize_stream_conversation_state(
                conversation=conversation,
                stream_id=stream_id,
            )
            await conversation_stream_store.append_event(
                stream_id,
                event="message.completed",
                data={
                    "message": (await self._message_read(assistant_message)).model_dump(mode="json")
                },
            )
        except asyncio.CancelledError:
            cancelled_message = await self._cancel_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            await self._finalize_stream_conversation_state(
                conversation=conversation,
                stream_id=stream_id,
            )
            await conversation_stream_store.append_event(
                stream_id,
                event="message.cancelled",
                data={
                    "message": (await self._message_read(cancelled_message)).model_dump(mode="json")
                },
            )
            return
        except LLMClientError as exc:
            failed_message = await self._fail_or_partial_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                error=str(exc),
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
            await self._finalize_stream_conversation_state(
                conversation=conversation,
                stream_id=stream_id,
            )
            await conversation_stream_store.append_event(
                stream_id,
                event="message.failed",
                data={
                    "message": (await self._message_read(failed_message)).model_dump(mode="json")
                },
            )
            return
        finally:
            await conversation_stream_store.complete_stream(
                stream_id,
                retention_seconds=self.STREAM_RETENTION_SECONDS,
            )

    async def _handle_unexpected_stream_failure(
        self,
        *,
        stream_id: str,
        conversation_id: UUID,
        error: str,
    ) -> None:
        # producer 的非预期异常也必须收口到 failed/partial，避免会话永久卡在 active 状态。
        stream = await conversation_stream_store.get_stream(stream_id)
        if stream is None:
            return

        assistant_message = await self.messages.get_by_id(
            conversation_id=conversation_id,
            message_id=stream.message_id,
        )
        conversation = await self._get_conversation_by_id(conversation_id)
        if (
            assistant_message is not None
            and conversation is not None
            and assistant_message.status == "streaming"
        ):
            failed_message = await self._fail_or_partial_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content=assistant_message.content,
                reasoning_content=assistant_message.reasoning_content,
                error=error,
                token_usage=assistant_message.token_usage,
                response_metadata=assistant_message.response_metadata,
            )
            await self._finalize_stream_conversation_state(
                conversation=conversation,
                stream_id=stream_id,
            )
            await conversation_stream_store.append_event(
                stream_id,
                event="message.failed",
                data={
                    "message": (await self._message_read(failed_message)).model_dump(mode="json")
                },
            )

        await conversation_stream_store.complete_stream(
            stream_id,
            retention_seconds=self.STREAM_RETENTION_SECONDS,
        )

    async def _emit_run_state_changed_if_needed(
        self,
        *,
        stream_id: str,
        conversation_id: UUID,
        previous_has_active_run: bool,
        has_active_run: bool,
    ) -> None:
        # 只有 run_state 真正发生跳变时才发事件，避免前端在并行流场景下反复收到噪音广播。
        if previous_has_active_run == has_active_run:
            return
        try:
            await conversation_stream_store.append_event(
                stream_id,
                event="conversation.run_state_changed",
                data={
                    "conversation_id": str(conversation_id),
                    "has_active_run": has_active_run,
                },
            )
        except KeyError:
            return

    async def _finalize_stream_conversation_state(
        self,
        *,
        conversation: Conversation,
        stream_id: str,
    ) -> None:
        previous_has_active_run = bool(conversation.has_active_run)
        has_active_run = await self.conversations.recompute_has_active_run(conversation.id)
        await self.conversations.touch(conversation.id)
        await self.session.commit()
        await self._emit_run_state_changed_if_needed(
            stream_id=stream_id,
            conversation_id=conversation.id,
            previous_has_active_run=previous_has_active_run,
            has_active_run=has_active_run,
        )

    async def _to_stream_event(self, record: StreamEventRecord) -> ConversationStreamEvent:
        # 对外统一补齐 stream_id / seq / event_id；seq 仅用于调试和未来事件日志后端。
        payload = {
            "stream_id": record.stream_id,
            "seq": record.seq,
            "event_id": record.event_id,
            **record.data,
        }
        if record.event == "message.created":
            payload = await self._refresh_created_event_payload(payload)
        return ConversationStreamEvent(
            event=record.event,
            data=payload,
            event_id=record.event_id,
        )

    async def _refresh_created_event_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        # message.created 会被 replay 给后来恢复的客户端；其中 sibling 导航等字段是派生态，
        # 不能长期信任创建当时序列化出来的快照，需要在发送前按数据库当前状态刷新。
        refreshed = dict(payload)
        for field in ("user_message", "assistant_message"):
            raw_message = refreshed.get(field)
            if not isinstance(raw_message, dict):
                continue

            raw_id = raw_message.get("id")
            if not isinstance(raw_id, str):
                continue

            try:
                message_id = UUID(raw_id)
            except ValueError:
                continue

            message = await self.messages.get_by_id(
                conversation_id=UUID(str(raw_message["conversation_id"])),
                message_id=message_id,
            )
            if message is None:
                continue
            refreshed[field] = (await self._message_read(message)).model_dump(mode="json")
        return refreshed

    async def _cancel_streaming_message(
        self,
        *,
        conversation_id: UUID,
        assistant_message,
        content: str,
        token_usage: dict[str, Any],
        response_metadata: dict[str, Any],
        reasoning_content: str = "",
    ):
        # 取消时不丢弃 assistant 占位消息，而是保存已生成内容并落成最终状态。
        assistant_message = await self.messages.cancel_assistant_message(
            message=assistant_message,
            content=content,
            reasoning_content=reasoning_content,
            token_usage=token_usage,
            response_metadata=response_metadata,
        )
        # 这里提交事务，确保即使 SSE 连接随后关闭，取消状态也已经持久化。
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return assistant_message

    def _merge_tool_call_chunks(
        self,
        *,
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # tool call 的参数常按 chunk 逐步追加，这里在 runtime 内维护一个可序列化的聚合版本。
        merged: list[dict[str, Any]] = [dict(item) for item in existing]
        for item in incoming:
            if not isinstance(item, dict):
                continue
            key = self._tool_call_key(item)
            matched = next((current for current in merged if self._tool_call_key(current) == key), None)
            if matched is None:
                merged.append(dict(item))
                continue

            if item.get("name"):
                matched["name"] = item["name"]
            if item.get("type"):
                matched["type"] = item["type"]
            if item.get("id"):
                matched["id"] = item["id"]
            if item.get("index") is not None:
                matched["index"] = item["index"]

            incoming_args = item.get("args")
            if isinstance(incoming_args, str) and incoming_args:
                previous_args = matched.get("args")
                if isinstance(previous_args, str):
                    matched["args"] = f"{previous_args}{incoming_args}"
                elif previous_args is None:
                    matched["args"] = incoming_args
                else:
                    matched["args"] = incoming_args
            elif incoming_args is not None:
                matched["args"] = incoming_args
        return merged

    def _tool_call_key(self, item: dict[str, Any]) -> str:
        # 优先用 provider 给的稳定 id；缺失时退回到 name/index 组合键。
        tool_call_id = item.get("id")
        if tool_call_id:
            return str(tool_call_id)
        return f"{item.get('name') or ''}:{item.get('index') if item.get('index') is not None else ''}"

    async def _fail_or_partial_streaming_message(
        self,
        *,
        conversation_id: UUID,
        assistant_message,
        content: str,
        error: str,
        token_usage: dict[str, Any],
        response_metadata: dict[str, Any],
        reasoning_content: str = "",
    ):
        # 流式失败时，有正文或 reasoning 就保留 partial，完全没有增量才标记 failed。
        if content or reasoning_content:
            assistant_message = await self.messages.partial_assistant_message(
                message=assistant_message,
                content=content,
                reasoning_content=reasoning_content,
                error=error,
                token_usage=token_usage,
                response_metadata=response_metadata,
            )
        else:
            assistant_message = await self.messages.fail_assistant_message(
                message=assistant_message,
                error=error,
            )
        # 失败/部分失败同样需要落库，前端刷新后才能看到重试或部分结果状态。
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return assistant_message
