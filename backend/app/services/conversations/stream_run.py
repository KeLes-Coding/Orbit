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
            # 先记录真实 producer task，后续 cancel 才能准确打断模型流。
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

            effective_chat_mode = getattr(assistant_message, "chat_mode", None) or conversation.chat_mode
            async for chunk in self.llm_client.stream(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
                model=assistant_message.model,
                enable_tools=effective_chat_mode in {"tool", "agent"},
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
                    # 流式工具调用会分多片到达，这里边广播增量、边维护当前 assistant 的聚合态。
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
                    # tool result 目前按“一次完整执行结果”广播，便于前端直接展示每次工具返回。
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
                    # reasoning 和正文分成两类事件，方便前端分别渲染 thinking 与正文。
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
            # completed/failed/cancelled 之前更新会话运行态摘要。
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
        finally:
            # 运行结束后保留一个短暂 replay 窗口，给刚断线的客户端补齐尾流。
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
