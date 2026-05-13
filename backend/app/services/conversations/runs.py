from typing import Any
from uuid import UUID

from fastapi import HTTPException, status

from app.schemas.conversation import ConversationMessageCreate, ConversationRead, MessageEdit
from app.services.conversations.stream_run import ConversationStreamRunService
from app.services.streaming import conversation_stream_store


class ConversationRunService(ConversationStreamRunService):
    # 负责“创建一次流式运行”的写入阶段：占位消息、幂等、文件绑定和初始事件。
    async def start_stream_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
        llm_config_id: UUID | None = None,
        parent_message_id: UUID | None = None,
        idempotency_key: str | None = None,
        model: str | None = None,
        file_ids: list[UUID] | None = None,
    ) -> str:
        conversation = await self._get_owned_conversation(
            user_id=user_id, conversation_id=conversation_id
        )
        parent_message = await self._resolve_parent_message(
            conversation_id=conversation_id,
            conversation=conversation,
            parent_message_id=parent_message_id,
        )
        lock_key = self._base_message_lock_key(
            conversation_id=conversation_id,
            parent_message_id=parent_message.id if parent_message else None,
        )

        async with self._acquire_base_message_lock(lock_key):
            conversation = await self._get_owned_conversation(
                user_id=user_id, conversation_id=conversation_id
            )
            parent_message = await self._resolve_parent_message(
                conversation_id=conversation_id,
                conversation=conversation,
                parent_message_id=parent_message.id if parent_message else None,
            )

            existing_exchange = await self._find_idempotent_exchange(
                conversation_id=conversation_id,
                parent_message_id=parent_message.id if parent_message else None,
                idempotency_key=idempotency_key,
            )
            if existing_exchange is not None:
                _, assistant_message = existing_exchange
                stream = await conversation_stream_store.get_stream_by_message_id(
                    assistant_message.id
                )
                if stream is not None:
                    return stream.stream_id
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="幂等请求已存在，请刷新当前分支消息状态",
                )

            llm_config = await self._get_usable_llm_config(
                user_id=user_id,
                conversation=conversation,
                llm_config_id=llm_config_id,
            )
            resolved_model = model or (llm_config.models[0] if llm_config.models else None)

            content_parts = await self._build_content_parts(
                user_id=user_id,
                conversation_id=conversation_id,
                content=content,
                file_ids=file_ids,
            )
            (
                user_message,
                assistant_message,
                stream_id,
            ) = await self._create_user_assistant_stream_messages(
                conversation=conversation,
                conversation_id=conversation_id,
                parent_message=parent_message,
                idempotency_key=idempotency_key,
                content_parts=content_parts,
                content=content,
                llm_config=llm_config,
                model=resolved_model,
            )

        # 先写入 created 事件，再启动 producer；这样首个订阅者一定能先收到占位消息。
        await self._create_runtime_stream(
            stream_id=stream_id,
            conversation_id=conversation_id,
            message_id=assistant_message.id,
            user_id=user_id,
            initial_events=[
                (
                    "message.created",
                    {
                        "user_message": (await self._message_read(user_message)).model_dump(
                            mode="json"
                        ),
                        "assistant_message": (
                            await self._message_read(assistant_message)
                        ).model_dump(mode="json"),
                    },
                )
            ],
        )
        self._spawn_stream_producer(stream_id=stream_id, conversation_id=conversation_id)
        return stream_id

    async def start_stream_new_conversation_message(
        self,
        *,
        user_id: UUID,
        payload: ConversationMessageCreate,
    ) -> tuple[UUID, str]:
        llm_config_id = payload.llm_config_id or await self._get_default_llm_config_id(user_id)
        if llm_config_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请先创建并启用一个默认模型配置",
            )

        llm_config = await self.llm_configs.get_active(user_id=user_id, config_id=llm_config_id)
        if llm_config is None or not llm_config.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前会话的模型配置不可用",
            )
        resolved_model = payload.model or (llm_config.models[0] if llm_config.models else None)

        title = self.title_generator.fallback_title(payload.content)
        conversation = await self.conversations.create(
            user_id=user_id,
            title=title,
            llm_config_id=llm_config.id,
            chat_mode=payload.chat_mode,
            metadata=payload.metadata,
        )

        file_ids = getattr(payload, "file_ids", None) or None
        content_parts = await self._build_content_parts(
            user_id=user_id,
            conversation_id=conversation.id,
            content=payload.content,
            file_ids=file_ids,
        )
        (
            user_message,
            assistant_message,
            stream_id,
        ) = await self._create_user_assistant_stream_messages(
            conversation=conversation,
            conversation_id=conversation.id,
            idempotency_key=payload.idempotency_key,
            content_parts=content_parts,
            content=payload.content,
            llm_config=llm_config,
            model=resolved_model,
        )
        await self.session.refresh(conversation)

        # New Chat 需要把新会话和首轮消息都放进 replay 日志，方便刷新后完整追平。
        await self._create_runtime_stream(
            stream_id=stream_id,
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            user_id=user_id,
            initial_events=[
                (
                    "conversation.created",
                    {
                        "conversation": ConversationRead.model_validate(conversation).model_dump(
                            mode="json"
                        )
                    },
                ),
                (
                    "message.created",
                    {
                        "user_message": (await self._message_read(user_message)).model_dump(
                            mode="json"
                        ),
                        "assistant_message": (
                            await self._message_read(assistant_message)
                        ).model_dump(mode="json"),
                    },
                ),
            ],
        )
        self._spawn_stream_producer(stream_id=stream_id, conversation_id=conversation.id)
        self._spawn_title_producer(
            conversation_id=conversation.id,
            stream_id=stream_id,
            user_id=user_id,
            user_message=payload.content,
            expected_title=title,
        )
        return conversation.id, stream_id

    async def start_stream_regenerate_assistant(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        llm_config_id: UUID | None = None,
        idempotency_key: str | None = None,
        model: str | None = None,
    ) -> str:
        conversation = await self._get_owned_conversation(
            user_id=user_id, conversation_id=conversation_id
        )
        target = await self.messages.get_by_id(
            conversation_id=conversation_id, message_id=message_id
        )
        if target is None or target.role != "assistant" or target.parent_message_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        lock_key = self._base_message_lock_key(
            conversation_id=conversation_id,
            parent_message_id=target.parent_message_id,
        )

        async with self._acquire_base_message_lock(lock_key):
            conversation = await self._get_owned_conversation(
                user_id=user_id, conversation_id=conversation_id
            )
            target = await self.messages.get_by_id(
                conversation_id=conversation_id, message_id=message_id
            )
            if target is None or target.role != "assistant" or target.parent_message_id is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
            parent = await self.messages.get_by_id_for_update(
                conversation_id=conversation_id,
                message_id=target.parent_message_id,
            )
            if parent is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到重发上下文"
                )

            existing_assistant = await self._find_idempotent_regenerate_message(
                conversation_id=conversation_id,
                parent_message_id=parent.id,
                source_message_id=target.id,
                idempotency_key=idempotency_key,
            )
            if existing_assistant is not None:
                stream = await conversation_stream_store.get_stream_by_message_id(
                    existing_assistant.id
                )
                if stream is not None:
                    return stream.stream_id
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="幂等请求已存在，请刷新当前分支消息状态",
                )

            llm_config = await self._get_usable_llm_config(
                user_id=user_id,
                conversation=conversation,
                llm_config_id=llm_config_id,
            )
            resolved_model = model or (llm_config.models[0] if llm_config.models else None)
            assistant_message = await self.messages.create_assistant_placeholder(
                conversation_id=conversation_id,
                llm_config_id=llm_config.id,
                provider=llm_config.provider,
                model=resolved_model or "",
                parent_message=parent,
                source_message_id=target.id,
                revision_type="regenerate",
                idempotency_key=idempotency_key,
            )
            await self.messages.set_conversation_active_leaf(
                conversation=conversation, message=assistant_message
            )
            stream_id = self._build_stream_id(assistant_message.id)
            conversation.has_active_run = True
            await self.conversations.touch(conversation_id)
            await self.session.commit()

        await self._create_runtime_stream(
            stream_id=stream_id,
            conversation_id=conversation_id,
            message_id=assistant_message.id,
            user_id=user_id,
            initial_events=[
                (
                    "message.created",
                    {
                        "assistant_message": (
                            await self._message_read(assistant_message)
                        ).model_dump(mode="json")
                    },
                )
            ],
        )
        self._spawn_stream_producer(stream_id=stream_id, conversation_id=conversation_id)
        return stream_id

    async def start_stream_edit_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: MessageEdit,
    ) -> str:
        conversation = await self._get_owned_conversation(
            user_id=user_id, conversation_id=conversation_id
        )
        target = await self.messages.get_by_id(
            conversation_id=conversation_id, message_id=message_id
        )
        if target is None or target.role != "user":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        lock_key = self._base_message_lock_key(
            conversation_id=conversation_id,
            parent_message_id=target.parent_message_id,
        )

        async with self._acquire_base_message_lock(lock_key):
            conversation = await self._get_owned_conversation(
                user_id=user_id, conversation_id=conversation_id
            )
            target = await self.messages.get_by_id(
                conversation_id=conversation_id, message_id=message_id
            )
            if target is None or target.role != "user":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")

            parent = None
            if target.parent_message_id is not None:
                parent = await self.messages.get_by_id_for_update(
                    conversation_id=conversation_id,
                    message_id=target.parent_message_id,
                )
                if parent is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到编辑上下文"
                    )

            llm_config = await self._get_usable_llm_config(
                user_id=user_id,
                conversation=conversation,
                llm_config_id=payload.llm_config_id,
            )
            resolved_model = getattr(payload, "model", None) or (
                llm_config.models[0] if llm_config.models else None
            )
            existing_exchange = await self._find_idempotent_exchange(
                conversation_id=conversation_id,
                parent_message_id=parent.id if parent else None,
                idempotency_key=payload.idempotency_key,
            )
            if existing_exchange is not None:
                _, assistant_message = existing_exchange
                stream = await conversation_stream_store.get_stream_by_message_id(
                    assistant_message.id
                )
                if stream is not None:
                    return stream.stream_id
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="幂等请求已存在，请刷新当前分支消息状态",
                )

            content_parts = await self._build_content_parts(
                user_id=user_id,
                conversation_id=conversation_id,
                content=payload.content,
                file_ids=getattr(payload, "file_ids", None) or None,
            )
            (
                user_message,
                assistant_message,
                stream_id,
            ) = await self._create_user_assistant_stream_messages(
                conversation=conversation,
                conversation_id=conversation_id,
                parent_message=parent,
                source_message_id=target.id,
                revision_type="edit",
                idempotency_key=payload.idempotency_key,
                content_parts=content_parts,
                content=payload.content,
                llm_config=llm_config,
                model=resolved_model,
            )

        await self._create_runtime_stream(
            stream_id=stream_id,
            conversation_id=conversation_id,
            message_id=assistant_message.id,
            user_id=user_id,
            initial_events=[
                (
                    "message.created",
                    {
                        "user_message": (await self._message_read(user_message)).model_dump(
                            mode="json"
                        ),
                        "assistant_message": (
                            await self._message_read(assistant_message)
                        ).model_dump(mode="json"),
                    },
                )
            ],
        )
        self._spawn_stream_producer(stream_id=stream_id, conversation_id=conversation_id)
        return stream_id

    def _build_stream_id(self, message_id: UUID) -> str:
        # 先复用 assistant message_id 生成稳定 stream_id，后续如引入 stream attempt 再扩展。
        return f"stream_{message_id}"

    async def _build_content_parts(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
        file_ids: list[UUID] | None,
    ) -> list:
        # 附件绑定和解析等待统一走这里，避免 send/edit/new-chat 三条路径行为漂移。
        if not file_ids:
            return []

        from app.services.file_service import FileService

        file_service = FileService(self.session)
        bound_files = await file_service.bind_pending_files(
            user_id=user_id,
            file_ids=file_ids,
            conversation_id=conversation_id,
        )
        bound_files = await file_service.wait_for_extraction(files=bound_files)
        return file_service.build_content_parts(content=content, files=bound_files)

    async def _create_user_assistant_stream_messages(
        self,
        *,
        conversation,
        conversation_id: UUID,
        content: str,
        llm_config,
        model: str | None,
        parent_message=None,
        source_message_id: UUID | None = None,
        revision_type: str = "normal",
        idempotency_key: str | None = None,
        content_parts: list | None = None,
    ):
        # 所有 user -> assistant placeholder 的写入都走同一段，保证 active_leaf 与 has_active_run 一致。
        user_message = await self.messages.create_user_message(
            conversation_id=conversation_id,
            content=content,
            parent_message=parent_message,
            source_message_id=source_message_id,
            revision_type=revision_type,
            idempotency_key=idempotency_key,
            content_parts=content_parts or [],
        )
        await self.messages.set_conversation_active_leaf(
            conversation=conversation, message=user_message
        )
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation_id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=model or "",
            parent_message=user_message,
        )
        await self.messages.set_conversation_active_leaf(
            conversation=conversation, message=assistant_message
        )
        stream_id = self._build_stream_id(assistant_message.id)
        conversation.has_active_run = True

        # 创建统一 run 记录，所有 chat_mode 共享同一张表。
        runtime_kind = (
            "langgraph_agent" if conversation.chat_mode == "agent" else "classic_chat"
        )
        run_record = await self.runs.create(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message.id,
            user_id=conversation.user_id,
            thread_id=conversation.thread_id,
            runtime_kind=runtime_kind,
            chat_mode=conversation.chat_mode,
            metadata={
                "provider": llm_config.provider,
                "model": model or "",
            },
        )
        # 将 run_id 写入 assistant 消息的 langgraph_message_id，
        # 用于将来 resume 时根据 run 记录恢复完整的消息→图消息映射。
        assistant_message.langgraph_message_id = str(run_record.id)

        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return user_message, assistant_message, stream_id

    async def _create_runtime_stream(
        self,
        *,
        stream_id: str,
        conversation_id: UUID,
        message_id: UUID,
        user_id: UUID,
        initial_events: list[tuple[str, dict[str, Any]]],
    ) -> None:
        # 运行时 store 是 replay 事实源；初始事件也必须先落进去，不能只存在于当前连接内。
        await conversation_stream_store.create_stream(
            stream_id=stream_id,
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        for event_name, event_data in initial_events:
            await conversation_stream_store.append_event(
                stream_id,
                event=event_name,
                data=event_data,
            )
