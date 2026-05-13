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
from app.services.llm_client import LLMClientError
from app.services.streaming import StreamEventRecord, conversation_stream_store


def _is_graph_interrupt(exc: Exception) -> bool:
    """检测异常是否为 LangGraph 的 GraphInterrupt（HITL 触发）。"""
    try:
        from langgraph.errors import GraphInterrupt
        return isinstance(exc, GraphInterrupt)
    except ImportError:
        return False


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
        # 这个入口给“刚创建完流的当前请求”使用，直接按 stream_id 订阅可避开竞争窗口。
        stream = await conversation_stream_store.get_stream(stream_id)
        if stream is None or stream.conversation_id != conversation_id or stream.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="流不存在或已过期")

        async for record in conversation_stream_store.subscribe(stream_id):
            yield self._to_stream_event(record)

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

        # ── runtime 分发 ──
        # agent 模式走专用路径：需要工具消息持久化、HITL interrupt 和 subagent 事件处理。
        if conversation.chat_mode == "agent":
            await self._produce_agent_stream(
                stream_id=stream_id,
                stream=stream,
                conversation=conversation,
                assistant_message=assistant_message,
                llm_config=llm_config,
                history_messages=history_messages,
            )
            return

        # chat / rag / tool 走统一编排路径。
        await self._produce_runtime_stream(
            stream_id=stream_id,
            stream=stream,
            conversation=conversation,
            conversation_id=conversation_id,
            assistant_message=assistant_message,
            llm_config=llm_config,
            history_messages=history_messages,
        )

    async def _produce_runtime_stream(
        self,
        *,
        stream_id: str,
        stream,
        conversation,
        conversation_id: UUID,
        assistant_message,
        llm_config,
        history_messages: list,
    ) -> None:
        """统一运行时执行编排（chat / rag / tool 模式）。

        通过 RuntimeDispatcher 获取对应 runtime，
        迭代 execute() 产出的 UnifiedStreamEvent，统一处理：
          完成 → 持久化消息和 run 记录
          失败 → 保留 partial 内容并标记 run
          取消 → 保留已生成内容并标记 run
          其他 → 写入 stream store
        agent 模式走 _produce_agent_stream()，因为它有额外的工具持久化
        和 HITL interrupt 逻辑。
        """
        from app.services.runtime.types import RunContext
        from app.services.runtime.dispatcher import RuntimeDispatcher

        # 构建运行时上下文
        ctx = RunContext(
            session=self.session,
            conversation=conversation,
            assistant_message=assistant_message,
            llm_config=llm_config,
            history_messages=history_messages,
            stream_id=stream_id,
            cancel_event=stream.cancel_event,
        )

        dispatcher = RuntimeDispatcher()
        runtime = dispatcher.dispatch(conversation.chat_mode)

        full_content_parts: list[str] = []
        full_reasoning_parts: list[str] = []

        try:
            await conversation_stream_store.attach_producer_task(stream_id)

            # 迭代 runtime 产出的事件
            async for event in runtime.execute(ctx):
                # 协作式取消检查
                if await conversation_stream_store.is_cancelled(stream_id):
                    cancelled_message = await self._cancel_streaming_message(
                        conversation_id=conversation_id,
                        assistant_message=assistant_message,
                        content="".join(full_content_parts),
                        reasoning_content="".join(full_reasoning_parts),
                        token_usage={},
                        response_metadata={
                            "provider": llm_config.provider,
                            "model": assistant_message.model or "",
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

                # 累积内容 delta
                if event.event == "message.delta":
                    full_content_parts.append(event.data.get("delta", ""))
                elif event.event == "message.reasoning_delta":
                    full_reasoning_parts.append(event.data.get("delta", ""))

                # 完成事件 → DB 持久化
                if event.event == "message.completed":
                    full_content = event.data.get("content", "") or "".join(
                        full_content_parts
                    )
                    full_reasoning = event.data.get(
                        "reasoning_content", ""
                    ) or "".join(full_reasoning_parts)
                    if not full_content:
                        full_content = "".join(full_content_parts)
                    assistant_message = await self.messages.complete_assistant_message(
                        message=assistant_message,
                        content=full_content,
                        reasoning_content=full_reasoning,
                        token_usage=event.data.get("token_usage", {}),
                        response_metadata=event.data.get("response_metadata", {}),
                    )
                    await self._finalize_stream_conversation_state(
                        conversation=conversation,
                        stream_id=stream_id,
                    )
                    await self._complete_active_run(
                        conversation_id=conversation_id,
                        token_usage=event.data.get("token_usage", {}),
                        response_metadata=event.data.get("response_metadata", {}),
                    )
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.completed",
                        data={
                            "message": (await self._message_read(assistant_message)).model_dump(
                                mode="json"
                            )
                        },
                    )
                    return

                # 失败事件 → DB 持久化
                if event.event == "message.failed":
                    failed_message = await self._fail_or_partial_streaming_message(
                        conversation_id=conversation_id,
                        assistant_message=assistant_message,
                        content="".join(full_content_parts),
                        reasoning_content="".join(full_reasoning_parts),
                        error=event.data.get("error", "runtime 执行失败"),
                        token_usage=event.data.get("token_usage", {}),
                        response_metadata=event.data.get("response_metadata", {}),
                    )
                    await self._finalize_stream_conversation_state(
                        conversation=conversation,
                        stream_id=stream_id,
                    )
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.failed",
                        data={
                            "message": (await self._message_read(failed_message)).model_dump(
                                mode="json"
                            )
                        },
                    )
                    return

                # 其他事件直接写入 stream store
                await conversation_stream_store.append_event(
                    stream_id,
                    event=event.event,
                    data=event.data,
                )

        except asyncio.CancelledError:
            cancelled_message = await self._cancel_streaming_message(
                conversation_id=conversation_id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                token_usage={},
                response_metadata={
                    "provider": llm_config.provider,
                    "model": assistant_message.model or "",
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
                    "message": (await self._message_read(cancelled_message)).model_dump(mode="json")
                },
            )
            return
        except Exception as exc:
            await self._handle_unexpected_stream_failure(
                stream_id=stream_id,
                conversation_id=conversation_id,
                error=str(exc),
            )
        finally:
            await conversation_stream_store.complete_stream(
                stream_id,
                retention_seconds=self.STREAM_RETENTION_SECONDS,
            )

    async def _produce_agent_stream(
        self,
        *,
        stream_id: str,
        stream,
        conversation,
        assistant_message,
        llm_config,
        history_messages: list,
    ) -> None:
        """Agent 模式的流式生成路径。

        使用 LangGraphAgentRuntime 替代原有的单轮 LLMClient.stream()，
        产出包含 tool_call / tool_result 等扩展事件的完整 agent 执行流。
        """
        from app.services.runtime.types import RunContext
        from app.services.runtime.dispatcher import RuntimeDispatcher

        ctx = RunContext(
            session=self.session,
            conversation=conversation,
            assistant_message=assistant_message,
            llm_config=llm_config,
            history_messages=history_messages,
            stream_id=stream_id,
            cancel_event=stream.cancel_event,
        )

        dispatcher = RuntimeDispatcher()
        runtime = dispatcher.dispatch(conversation.chat_mode)

        full_content_parts: list[str] = []
        full_reasoning_parts: list[str] = []

        try:
            await conversation_stream_store.attach_producer_task(stream_id)

            async for event in runtime.execute(ctx):
                # 协作式取消检查：每个事件产出后都检查一次
                if await conversation_stream_store.is_cancelled(stream_id):
                    cancelled_message = await self._cancel_streaming_message(
                        conversation_id=conversation.id,
                        assistant_message=assistant_message,
                        content="".join(full_content_parts),
                        reasoning_content="".join(full_reasoning_parts),
                        token_usage={},
                        response_metadata={
                            "provider": llm_config.provider,
                            "model": assistant_message.model or "",
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

                # 累积 delta 用于最终持久化
                if event.event == "message.delta":
                    full_content_parts.append(event.data.get("delta", ""))
                elif event.event == "message.reasoning_delta":
                    full_reasoning_parts.append(event.data.get("delta", ""))

                # agent 工具结果落库：作为 assistant 的子节点，不在可见路径上。
                if event.event == "message.agent_delta" and event.data.get("type") == "tool_result":
                    tool_call_id = event.data.get("tool_call_id", "")
                    tool_content = event.data.get("content", "")
                    if tool_call_id and tool_content:
                        await self.messages.create_tool_message(
                            conversation_id=conversation.id,
                            parent_message=assistant_message,
                            tool_call_id=tool_call_id,
                            content=tool_content,
                        )

                # message.completed / message.failed 由 runtime 产出后在这里完成持久化
                if event.event == "message.completed":
                    full_content = event.data.get("content", "") or "".join(
                        full_content_parts
                    )
                    full_reasoning = event.data.get(
                        "reasoning_content", ""
                    ) or "".join(full_reasoning_parts)
                    assistant_message = await self.messages.complete_assistant_message(
                        message=assistant_message,
                        content=full_content,
                        reasoning_content=full_reasoning,
                        token_usage=event.data.get("token_usage", {}),
                        response_metadata=event.data.get("response_metadata", {}),
                    )
                    await self._finalize_stream_conversation_state(
                        conversation=conversation,
                        stream_id=stream_id,
                    )
                    # 更新对应的 run 记录为 completed。
                    await self._complete_active_run(
                        conversation_id=conversation.id,
                        token_usage=event.data.get("token_usage", {}),
                        response_metadata=event.data.get("response_metadata", {}),
                    )
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.completed",
                        data={
                            "message": (await self._message_read(assistant_message)).model_dump(
                                mode="json"
                            )
                        },
                    )
                    return

                if event.event == "message.failed":
                    failed_message = await self._fail_or_partial_streaming_message(
                        conversation_id=conversation.id,
                        assistant_message=assistant_message,
                        content="".join(full_content_parts),
                        reasoning_content="".join(full_reasoning_parts),
                        error=event.data.get("error", "Agent 执行失败"),
                        token_usage={},
                        response_metadata={
                            "provider": llm_config.provider,
                            "model": assistant_message.model or "",
                        },
                    )
                    await self._finalize_stream_conversation_state(
                        conversation=conversation,
                        stream_id=stream_id,
                    )
                    await conversation_stream_store.append_event(
                        stream_id,
                        event="message.failed",
                        data={
                            "message": (await self._message_read(failed_message)).model_dump(
                                mode="json"
                            )
                        },
                    )
                    return

                # 通用事件和 agent 扩展事件直接写入 stream store
                await conversation_stream_store.append_event(
                    stream_id,
                    event=event.event,
                    data=event.data,
                )

        except asyncio.CancelledError:
            cancelled_message = await self._cancel_streaming_message(
                conversation_id=conversation.id,
                assistant_message=assistant_message,
                content="".join(full_content_parts),
                reasoning_content="".join(full_reasoning_parts),
                token_usage={},
                response_metadata={
                    "provider": llm_config.provider,
                    "model": assistant_message.model or "",
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
                    "message": (await self._message_read(cancelled_message)).model_dump(mode="json")
                },
            )
            return
        except Exception as exc:
            # 检查是否为 HITL interrupt 异常。
            # LangGraph 的 interrupt() 通过 GraphInterrupt 抛到 astream 外部，
            # agent 执行在此暂停，等待用户审批后恢复。
            if _is_graph_interrupt(exc):
                await self._interrupt_active_run(
                    conversation_id=conversation.id,
                    stream_id=stream_id,
                    assistant_message=assistant_message,
                )
                # interrupt 时不 complete_stream，保留 replay 窗口供 resume 时使用。
                return

            await self._handle_unexpected_stream_failure(
                stream_id=stream_id,
                conversation_id=conversation.id,
                error=str(exc),
            )
        finally:
            await conversation_stream_store.complete_stream(
                stream_id,
                retention_seconds=self.STREAM_RETENTION_SECONDS,
            )

    async def _complete_active_run(
        self,
        *,
        conversation_id: UUID,
        token_usage: dict | None = None,
        response_metadata: dict | None = None,
    ) -> None:
        """将当前活跃的 run 记录标记为 completed。"""
        run = await self.runs.get_active_run(conversation_id)
        if run is not None:
            extra = {}
            if token_usage:
                extra["token_usage"] = token_usage
            if response_metadata:
                extra["response_metadata"] = response_metadata
            await self.runs.complete_run(run, metadata=extra if extra else None)

    async def _fail_active_run(
        self, *, conversation_id: UUID, error: str
    ) -> None:
        """将当前活跃的 run 记录标记为 failed。"""
        run = await self.runs.get_active_run(conversation_id)
        if run is not None:
            await self.runs.fail_run(run, error=error)

    async def _cancel_active_run(self, *, conversation_id: UUID) -> None:
        """将当前活跃的 run 记录标记为 cancelled。"""
        run = await self.runs.get_active_run(conversation_id)
        if run is not None:
            await self.runs.cancel_run(run)

    async def _interrupt_active_run(
        self,
        *,
        conversation_id: UUID,
        stream_id: str,
        assistant_message,
    ) -> None:
        """将当前活跃的 run 标记为 interrupted（HITL 等待审批）。

        与 cancelled/failed 的区别：
          - finished_at 不设置（run 尚未结束）
          - stream 不立即 complete（保留 replay 窗口供 resume 使用）
        """
        run = await self.runs.get_active_run(conversation_id)
        if run is not None:
            await self.runs.interrupt_run(run)

        # 向前端发送审批等待事件
        await conversation_stream_store.append_event(
            stream_id,
            event="agent.run.awaiting_approval",
            data={
                "message_id": str(assistant_message.id),
                "status": "interrupted",
            },
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

    def _to_stream_event(self, record: StreamEventRecord) -> ConversationStreamEvent:
        # 对外统一补齐 stream_id / seq / event_id；seq 仅用于调试和未来事件日志后端。
        payload = {
            "stream_id": record.stream_id,
            "seq": record.seq,
            "event_id": record.event_id,
            **record.data,
        }
        return ConversationStreamEvent(
            event=record.event,
            data=payload,
            event_id=record.event_id,
        )

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
        # 同步更新 run 记录为 cancelled。
        await self._cancel_active_run(conversation_id=conversation_id)
        # 这里提交事务，确保即使 SSE 连接随后关闭，取消状态也已经持久化。
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return assistant_message

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
        # 同步更新 run 记录为 failed。
        await self._fail_active_run(conversation_id=conversation_id, error=error)
        # 失败/部分失败同样需要落库，前端刷新后才能看到重试或部分结果状态。
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return assistant_message
