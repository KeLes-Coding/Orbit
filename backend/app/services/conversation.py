import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.conversation import Conversation
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.llm_config import LLMConfigRepository
from app.schemas.conversation import (
    ActiveStreamRead,
    BranchSwitchRead,
    ConversationCreate,
    ConversationForkCreate,
    ConversationForkRead,
    ConversationMessageCreate,
    ConversationRead,
    ConversationUpdate,
    MessageEdit,
    MessageExchangeRead,
    MessageRead,
)
from app.services.generation.title import ConversationTitleGenerator
from app.services.llm_client import LLMClient, LLMClientError
from app.services.streaming import StreamEventRecord, conversation_stream_store

_base_message_locks: dict[str, asyncio.Lock] = {}
_base_message_locks_guard = asyncio.Lock()


@dataclass(frozen=True)
class ConversationStreamEvent:
    # 这是路由层最终输出给 SSE 编码器的统一事件结构。
    event: str
    data: dict[str, Any]
    event_id: str | None = None


class ConversationService:
    STREAM_RETENTION_SECONDS = 30

    # 会话服务负责会话归属校验、默认模型选择和消息顺序写入。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.llm_configs = LLMConfigRepository(session)
        self.llm_client = LLMClient()
        self.title_generator = ConversationTitleGenerator()

    @asynccontextmanager
    async def _acquire_base_message_lock(self, lock_key: str) -> AsyncIterator[None]:
        # 当前阶段 runtime 仍是单进程内存实现，base_message 写锁也先保持同一粒度。
        async with _base_message_locks_guard:
            lock = _base_message_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            yield

    async def start_stream_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
        parent_message_id: UUID | None = None,
        idempotency_key: str | None = None,
        model: str | None = None,
        file_ids: list[UUID] | None = None,
    ) -> str:
        conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
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
            conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
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
                stream = await conversation_stream_store.get_stream_by_message_id(assistant_message.id)
                if stream is not None:
                    return stream.stream_id
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="幂等请求已存在，请刷新当前分支消息状态",
                )

            llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
            resolved_model = model or (llm_config.models[0] if llm_config.models else None)

            content_parts: list = []
            if file_ids:
                from app.services.file_service import FileService
                file_service = FileService(self.session)
                bound_files = await file_service.bind_pending_files(
                    user_id=user_id, file_ids=file_ids, conversation_id=conversation_id
                )
                bound_files = await file_service.wait_for_extraction(files=bound_files)
                content_parts = file_service.build_content_parts(content=content, files=bound_files)

            user_message = await self.messages.create_user_message(
                conversation_id=conversation_id,
                content=content,
                parent_message=parent_message,
                idempotency_key=idempotency_key,
                content_parts=content_parts,
            )
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
            assistant_message = await self.messages.create_assistant_placeholder(
                conversation_id=conversation_id,
                llm_config_id=llm_config.id,
                provider=llm_config.provider,
                model=resolved_model or "",
                parent_message=user_message,
            )
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
            stream_id = self._build_stream_id(assistant_message.id)
            conversation.has_active_run = True
            await self.conversations.touch(conversation_id)
            await self.session.commit()

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
                        "user_message": (await self._message_read(user_message)).model_dump(mode="json"),
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

        content_parts: list = []
        file_ids = getattr(payload, "file_ids", None) or None
        if file_ids:
            from app.services.file_service import FileService
            file_service = FileService(self.session)
            bound_files = await file_service.bind_pending_files(
                user_id=user_id, file_ids=file_ids, conversation_id=conversation.id
            )
            bound_files = await file_service.wait_for_extraction(files=bound_files)
            content_parts = file_service.build_content_parts(content=payload.content, files=bound_files)

        user_message = await self.messages.create_user_message(
            conversation_id=conversation.id,
            content=payload.content,
            idempotency_key=payload.idempotency_key,
            content_parts=content_parts,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
        assistant_message = await self.messages.create_assistant_placeholder(
            conversation_id=conversation.id,
            llm_config_id=llm_config.id,
            provider=llm_config.provider,
            model=resolved_model or "",
            parent_message=user_message,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
        stream_id = self._build_stream_id(assistant_message.id)
        conversation.has_active_run = True
        await self.conversations.touch(conversation.id)
        await self.session.commit()
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
                        "conversation": ConversationRead.model_validate(conversation).model_dump(mode="json")
                    },
                ),
                (
                    "message.created",
                    {
                        "user_message": (await self._message_read(user_message)).model_dump(mode="json"),
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
        idempotency_key: str | None = None,
        model: str | None = None,
    ) -> str:
        conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None or target.role != "assistant" or target.parent_message_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        lock_key = self._base_message_lock_key(
            conversation_id=conversation_id,
            parent_message_id=target.parent_message_id,
        )

        async with self._acquire_base_message_lock(lock_key):
            conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
            target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
            if target is None or target.role != "assistant" or target.parent_message_id is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
            parent = await self.messages.get_by_id_for_update(
                conversation_id=conversation_id,
                message_id=target.parent_message_id,
            )
            if parent is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到重发上下文")

            existing_assistant = await self._find_idempotent_regenerate_message(
                conversation_id=conversation_id,
                parent_message_id=parent.id,
                source_message_id=target.id,
                idempotency_key=idempotency_key,
            )
            if existing_assistant is not None:
                stream = await conversation_stream_store.get_stream_by_message_id(existing_assistant.id)
                if stream is not None:
                    return stream.stream_id
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="幂等请求已存在，请刷新当前分支消息状态",
                )

            llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
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
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
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
        conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None or target.role != "user":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        lock_key = self._base_message_lock_key(
            conversation_id=conversation_id,
            parent_message_id=target.parent_message_id,
        )

        async with self._acquire_base_message_lock(lock_key):
            conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
            target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
            if target is None or target.role != "user":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")

            parent = None
            if target.parent_message_id is not None:
                parent = await self.messages.get_by_id_for_update(
                    conversation_id=conversation_id,
                    message_id=target.parent_message_id,
                )
                if parent is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到编辑上下文")

            llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
            resolved_model = getattr(payload, "model", None) or (llm_config.models[0] if llm_config.models else None)
            existing_exchange = await self._find_idempotent_exchange(
                conversation_id=conversation_id,
                parent_message_id=parent.id if parent else None,
                idempotency_key=payload.idempotency_key,
            )
            if existing_exchange is not None:
                _, assistant_message = existing_exchange
                stream = await conversation_stream_store.get_stream_by_message_id(assistant_message.id)
                if stream is not None:
                    return stream.stream_id
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="幂等请求已存在，请刷新当前分支消息状态",
                )

            file_ids = getattr(payload, "file_ids", None) or None
            content_parts: list = []
            if file_ids:
                from app.services.file_service import FileService
                file_service = FileService(self.session)
                bound_files = await file_service.bind_pending_files(
                    user_id=user_id, file_ids=file_ids, conversation_id=conversation_id
                )
                bound_files = await file_service.wait_for_extraction(files=bound_files)
                content_parts = file_service.build_content_parts(
                    content=payload.content, files=bound_files
                )

            user_message = await self.messages.create_user_message(
                conversation_id=conversation_id,
                content=payload.content,
                parent_message=parent,
                source_message_id=target.id,
                revision_type="edit",
                idempotency_key=payload.idempotency_key,
                content_parts=content_parts,
            )
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
            assistant_message = await self.messages.create_assistant_placeholder(
                conversation_id=conversation_id,
                llm_config_id=llm_config.id,
                provider=llm_config.provider,
                model=resolved_model or "",
                parent_message=user_message,
            )
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
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
                        "user_message": (await self._message_read(user_message)).model_dump(mode="json"),
                        "assistant_message": (
                            await self._message_read(assistant_message)
                        ).model_dump(mode="json"),
                    },
                )
            ],
        )
        self._spawn_stream_producer(stream_id=stream_id, conversation_id=conversation_id)
        return stream_id

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

    async def list_conversations(self, user_id: UUID) -> list[ConversationRead]:
        # 会话列表只返回未归档会话，并按 updated_at 倒序排列。
        conversations = await self.conversations.list_active(user_id)
        return [ConversationRead.model_validate(conversation) for conversation in conversations]

    async def get_conversation(self, *, user_id: UUID, conversation_id: UUID) -> ConversationRead:
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return ConversationRead.model_validate(conversation)

    async def create_conversation(
        self,
        *,
        user_id: UUID,
        payload: ConversationCreate,
    ) -> ConversationRead:
        # 创建会话时如果前端没指定模型配置，就尝试使用当前用户的默认配置。
        llm_config_id = payload.llm_config_id
        if llm_config_id is None:
            llm_config_id = await self._get_default_llm_config_id(user_id)
        elif await self.llm_configs.get_active(user_id=user_id, config_id=llm_config_id) is None:
            # 指定的模型配置必须属于当前用户，不能跨用户引用。
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")

        conversation = await self.conversations.create(
            user_id=user_id,
            title=payload.title,
            llm_config_id=llm_config_id,
            chat_mode=payload.chat_mode,
            metadata=payload.metadata,
        )
        await self.session.commit()
        return ConversationRead.model_validate(conversation)

    async def update_conversation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        payload: ConversationUpdate,
    ) -> ConversationRead:
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        update_data = payload.model_dump(exclude_unset=True)

        # PATCH 只更新显式传入的会话字段。
        if "title" in update_data:
            conversation.title = update_data["title"]
        if "llm_config_id" in update_data:
            llm_config_id = update_data["llm_config_id"]
            if llm_config_id is not None and await self.llm_configs.get_active(
                user_id=user_id,
                config_id=llm_config_id,
            ) is None:
                # 切换会话默认模型时，也必须确认配置属于当前用户。
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")
            conversation.llm_config_id = llm_config_id
        if "metadata" in update_data:
            conversation.metadata_ = update_data["metadata"] or {}

        await self.session.commit()
        await self.session.refresh(conversation)
        return ConversationRead.model_validate(conversation)

    async def archive_conversation(self, *, user_id: UUID, conversation_id: UUID) -> None:
        # 会话删除同样使用软删除，消息历史仍保留在数据库中。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        await self.conversations.archive(conversation)
        await self.session.commit()

    async def list_messages(self, *, user_id: UUID, conversation_id: UUID) -> list[MessageRead]:
        # 先校验会话归属，再读取消息，避免用户枚举 conversation_id 读取他人历史。
        conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        messages = await self.messages.list_visible_path(conversation)
        return await self._message_reads(messages)

    async def create_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
        parent_message_id: UUID | None = None,
        idempotency_key: str | None = None,
        model: str | None = None,
        file_ids: list[UUID] | None = None,
    ) -> MessageExchangeRead:
        conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
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
            conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
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
                user_message, assistant_message = existing_exchange
                return MessageExchangeRead(
                    user_message=await self._message_read(user_message),
                    assistant_message=await self._message_read(assistant_message),
                )

            llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
            resolved_model = model or (llm_config.models[0] if llm_config.models else None)

            content_parts: list = []
            if file_ids:
                from app.services.file_service import FileService
                file_service = FileService(self.session)
                bound_files = await file_service.bind_pending_files(
                    user_id=user_id, file_ids=file_ids, conversation_id=conversation_id
                )
                bound_files = await file_service.wait_for_extraction(files=bound_files)
                content_parts = file_service.build_content_parts(content=content, files=bound_files)

            user_message = await self.messages.create_user_message(
                conversation_id=conversation_id,
                content=content,
                parent_message=parent_message,
                idempotency_key=idempotency_key,
                content_parts=content_parts,
            )
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
            history_messages = await self.messages.list_path_to_message(
                conversation_id=conversation_id,
                message_id=user_message.id,
            )
            assistant_message = await self.messages.create_assistant_placeholder(
                conversation_id=conversation_id,
                llm_config_id=llm_config.id,
                provider=llm_config.provider,
                model=resolved_model or "",
                parent_message=user_message,
            )
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
            conversation.has_active_run = True
            # 先提交占位消息，长时间模型调用时数据库里也能看到 streaming 状态。
            await self.conversations.touch(conversation_id)
            await self.session.commit()

        try:
            completion = await self.llm_client.generate(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
                model=resolved_model or None,
            )
        except LLMClientError as exc:
            assistant_message = await self.messages.fail_assistant_message(
                message=assistant_message,
                error=str(exc),
            )
            await self.conversations.recompute_has_active_run(conversation_id)
            await self.conversations.touch(conversation_id)
            await self.session.commit()
            return MessageExchangeRead(
                user_message=await self._message_read(user_message),
                assistant_message=await self._message_read(assistant_message),
            )

        assistant_message = await self.messages.complete_assistant_message(
            message=assistant_message,
            content=completion.content,
            reasoning_content=completion.reasoning_content,
            token_usage=completion.token_usage,
            response_metadata=completion.response_metadata,
        )
        await self.conversations.recompute_has_active_run(conversation_id)
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return MessageExchangeRead(
            user_message=await self._message_read(user_message),
            assistant_message=await self._message_read(assistant_message),
        )

    async def regenerate_assistant(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        idempotency_key: str | None = None,
        model: str | None = None,
    ) -> MessageRead:
        # Regenerate 不覆盖旧 assistant，而是在同一个 user parent 下创建 assistant sibling。
        conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None or target.role != "assistant" or target.parent_message_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        lock_key = self._base_message_lock_key(
            conversation_id=conversation_id,
            parent_message_id=target.parent_message_id,
        )

        async with self._acquire_base_message_lock(lock_key):
            conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
            target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
            if target is None or target.role != "assistant" or target.parent_message_id is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
            parent = await self.messages.get_by_id_for_update(
                conversation_id=conversation_id,
                message_id=target.parent_message_id,
            )
            if parent is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到重发上下文")

            existing_assistant = await self._find_idempotent_regenerate_message(
                conversation_id=conversation_id,
                parent_message_id=parent.id,
                source_message_id=target.id,
                idempotency_key=idempotency_key,
            )
            if existing_assistant is not None:
                return await self._message_read(existing_assistant)

            llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
            resolved_model = model or (llm_config.models[0] if llm_config.models else None)
            history_messages = await self.messages.list_path_to_message(
                conversation_id=conversation_id,
                message_id=parent.id,
            )
            # 新 assistant 创建时会把 parent.active_child 切到新版本。
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
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
            conversation.has_active_run = True
            await self.conversations.touch(conversation_id)
            await self.session.commit()

        try:
            completion = await self.llm_client.generate(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
                model=resolved_model or None,
            )
        except LLMClientError as exc:
            assistant_message = await self.messages.fail_assistant_message(
                message=assistant_message,
                error=str(exc),
            )
            await self.conversations.recompute_has_active_run(conversation_id)
            await self.conversations.touch(conversation_id)
            await self.session.commit()
            return await self._message_read(assistant_message)

        assistant_message = await self.messages.complete_assistant_message(
            message=assistant_message,
            content=completion.content,
            reasoning_content=completion.reasoning_content,
            token_usage=completion.token_usage,
            response_metadata=completion.response_metadata,
        )
        await self.conversations.recompute_has_active_run(conversation_id)
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return await self._message_read(assistant_message)

    async def edit_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: MessageEdit,
        model: str | None = None,
    ) -> MessageExchangeRead:
        # Edit 不修改旧 user，而是在原 parent 下创建新的 user sibling。
        conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None or target.role != "user":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        lock_key = self._base_message_lock_key(
            conversation_id=conversation_id,
            parent_message_id=target.parent_message_id,
        )

        async with self._acquire_base_message_lock(lock_key):
            conversation = await self._get_owned_conversation(user_id=user_id, conversation_id=conversation_id)
            target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
            if target is None or target.role != "user":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")

            parent = None
            if target.parent_message_id is not None:
                parent = await self.messages.get_by_id_for_update(
                    conversation_id=conversation_id,
                    message_id=target.parent_message_id,
                )
                if parent is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到编辑上下文")

            llm_config = await self._get_usable_llm_config(user_id=user_id, conversation=conversation)
            resolved_model = model or (llm_config.models[0] if llm_config.models else None)
            existing_exchange = await self._find_idempotent_exchange(
                conversation_id=conversation_id,
                parent_message_id=parent.id if parent else None,
                idempotency_key=payload.idempotency_key,
            )
            if existing_exchange is not None:
                user_message, assistant_message = existing_exchange
                return MessageExchangeRead(
                    user_message=await self._message_read(user_message),
                    assistant_message=await self._message_read(assistant_message),
                )
            user_message = await self.messages.create_user_message(
                conversation_id=conversation_id,
                content=payload.content,
                parent_message=parent,
                source_message_id=target.id,
                revision_type="edit",
                idempotency_key=payload.idempotency_key,
            )
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=user_message)
            history_messages = await self.messages.list_path_to_message(
                conversation_id=conversation_id,
                message_id=user_message.id,
            )
            # 编辑后的 user 会继续生成一个新的 assistant child，形成新的 visible path。
            assistant_message = await self.messages.create_assistant_placeholder(
                conversation_id=conversation_id,
                llm_config_id=llm_config.id,
                provider=llm_config.provider,
                model=resolved_model or "",
                parent_message=user_message,
            )
            await self.messages.set_conversation_active_leaf(conversation=conversation, message=assistant_message)
            conversation.has_active_run = True
            await self.conversations.touch(conversation_id)
            await self.session.commit()

        try:
            completion = await self.llm_client.generate(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
                model=resolved_model or None,
            )
        except LLMClientError as exc:
            assistant_message = await self.messages.fail_assistant_message(
                message=assistant_message,
                error=str(exc),
            )
            await self.conversations.recompute_has_active_run(conversation_id)
            await self.conversations.touch(conversation_id)
            await self.session.commit()
            return MessageExchangeRead(
                user_message=await self._message_read(user_message),
                assistant_message=await self._message_read(assistant_message),
            )

        assistant_message = await self.messages.complete_assistant_message(
            message=assistant_message,
            content=completion.content,
            reasoning_content=completion.reasoning_content,
            token_usage=completion.token_usage,
            response_metadata=completion.response_metadata,
        )
        await self.conversations.recompute_has_active_run(conversation_id)
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        return MessageExchangeRead(
            user_message=await self._message_read(user_message),
            assistant_message=await self._message_read(assistant_message),
        )

    async def stream_regenerate_assistant(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # Regenerate 的产品语义是重发 assistant，因此也必须走流式生成。
        stream_id = await self.start_stream_regenerate_assistant(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            idempotency_key=idempotency_key,
        )
        async for event in self.subscribe_stream(
            user_id=user_id,
            conversation_id=conversation_id,
            stream_id=stream_id,
        ):
            yield event

    async def stream_edit_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: MessageEdit,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # Edit 创建新的 user sibling 后，也立刻流式生成其 assistant child。
        stream_id = await self.start_stream_edit_user_message(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            payload=payload,
        )
        async for event in self.subscribe_stream(
            user_id=user_id,
            conversation_id=conversation_id,
            stream_id=stream_id,
        ):
            yield event

    async def switch_branch(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> BranchSwitchRead:
        # 切换 branch 只改局部分叉点的 active_child，再沿 active_child 链恢复子路径。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=True,
        )
        target = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        if target.parent_message_id is not None:
            parent = await self.messages.get_by_id(
                conversation_id=conversation_id,
                message_id=target.parent_message_id,
            )
            if parent is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法找到分支父节点")
            # message_id 是目标 sibling；其 parent 需要把 active_child 指向它。
            parent.active_child_message_id = target.id

        # 不找最深 leaf，而是沿目标 sibling 自己的 active_child 选择向下恢复。
        leaf = await self.messages.resolve_active_leaf_from(
            conversation_id=conversation_id,
            message=target,
        )
        await self.messages.set_conversation_active_leaf(conversation=conversation, message=leaf)
        await self.conversations.touch(conversation_id)
        await self.session.commit()
        messages = await self.messages.list_visible_path(conversation)
        return BranchSwitchRead(
            active_leaf_message_id=conversation.active_leaf_message_id,
            messages=await self._message_reads(messages),
        )

    async def fork_conversation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: ConversationForkCreate,
    ) -> ConversationForkRead:
        # Fork v1 只允许从当前 visible path 上的节点复制，避免复制未选中的 sibling 子树。
        conversation = await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        visible_path = await self.messages.list_visible_path(conversation)
        target_index = next((index for index, message in enumerate(visible_path) if message.id == message_id), None)
        if target_index is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="只能从当前可见路径 fork")

        # 新会话独立拥有 thread、summary 和 active path，只保留来源字段用于追溯。
        new_conversation = await self.conversations.create(
            user_id=user_id,
            title=payload.title or conversation.title,
            llm_config_id=conversation.llm_config_id,
            chat_mode=conversation.chat_mode,
            metadata=dict(conversation.metadata_ or {}),
            forked_from_conversation_id=conversation.id,
            forked_from_message_id=message_id,
        )
        copied_messages = []
        parent_copy = None
        for source_message in visible_path[: target_index + 1]:
            # 按 root -> target 顺序复制，逐条重建 parent 和 active_child。
            copied = await self.messages.clone_message_to_conversation(
                source=source_message,
                target_conversation_id=new_conversation.id,
                parent_message=parent_copy,
            )
            copied_messages.append(copied)
            parent_copy = copied

        await self.messages.set_conversation_active_leaf(conversation=new_conversation, message=parent_copy)
        await self.conversations.touch(new_conversation.id)
        await self.session.commit()
        await self.session.refresh(new_conversation)
        return ConversationForkRead(
            conversation=ConversationRead.model_validate(new_conversation),
            messages=await self._message_reads(copied_messages),
        )

    async def stream_user_message(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # 流式接口沿用一问一答写入顺序，只是 assistant 内容通过 SSE 增量返回。
        stream_id = await self.start_stream_user_message(
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
        )
        async for event in self.subscribe_stream(
            user_id=user_id,
            conversation_id=conversation_id,
            stream_id=stream_id,
        ):
            yield event

    async def stream_new_conversation_message(
        self,
        *,
        user_id: UUID,
        payload: ConversationMessageCreate,
    ) -> AsyncIterator[ConversationStreamEvent]:
        # New Chat 的首条消息会从这里进入：先确定可用模型，再创建会话与消息。
        conversation_id, stream_id = await self.start_stream_new_conversation_message(
            user_id=user_id,
            payload=payload,
        )
        async for event in self.subscribe_stream(
            user_id=user_id,
            conversation_id=conversation_id,
            stream_id=stream_id,
        ):
            yield event

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

    async def _get_owned_conversation(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        for_update: bool = False,
    ) -> Conversation:
        # 所有会话操作都必须带 user_id，保证多用户数据隔离。
        conversation = await self.conversations.get_active(
            user_id=user_id,
            conversation_id=conversation_id,
            for_update=for_update,
        )
        if conversation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        return conversation

    async def _get_active_leaf(self, conversation: Conversation):
        if conversation.active_leaf_message_id is None:
            return None
        message = await self.messages.get_by_id(
            conversation_id=conversation.id,
            message_id=conversation.active_leaf_message_id,
        )
        if message is None:
            return None
        return message

    async def _resolve_parent_message(
        self,
        *,
        conversation_id: UUID,
        conversation: Conversation,
        parent_message_id: UUID | None,
    ):
        # 并行阶段允许显式指定 base message；未传时暂时兼容旧 active_leaf 语义。
        if parent_message_id is None:
            return await self._get_active_leaf(conversation)

        parent_message = await self.messages.get_by_id(
            conversation_id=conversation_id,
            message_id=parent_message_id,
        )
        if parent_message is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="父消息不存在")
        return parent_message

    def _base_message_lock_key(self, *, conversation_id: UUID, parent_message_id: UUID | None) -> str:
        if parent_message_id is None:
            return f"conversation:{conversation_id}:root"
        return f"message:{parent_message_id}"

    async def _find_idempotent_exchange(
        self,
        *,
        conversation_id: UUID,
        parent_message_id: UUID | None,
        idempotency_key: str | None,
    ) -> tuple[Any, Any] | None:
        if not idempotency_key:
            return None

        user_message = await self.messages.find_user_message_by_idempotency(
            conversation_id=conversation_id,
            parent_message_id=parent_message_id,
            idempotency_key=idempotency_key,
        )
        if user_message is None:
            return None

        assistant_message = None
        if user_message.active_child_message_id is not None:
            assistant_message = await self.messages.get_by_id(
                conversation_id=conversation_id,
                message_id=user_message.active_child_message_id,
            )
        if assistant_message is None or assistant_message.parent_message_id != user_message.id:
            assistant_message = await self.messages.get_first_assistant_child(
                conversation_id=conversation_id,
                parent_message_id=user_message.id,
            )
        if assistant_message is None:
            return None
        return user_message, assistant_message

    async def _find_idempotent_regenerate_message(
        self,
        *,
        conversation_id: UUID,
        parent_message_id: UUID,
        source_message_id: UUID,
        idempotency_key: str | None,
    ):
        # regenerate 的幂等作用域不是“任意 assistant child”，
        # 而是“同一个 parent 下、针对同一个 source assistant 的同一次重发”。
        if not idempotency_key:
            return None
        return await self.messages.find_assistant_message_by_idempotency(
            conversation_id=conversation_id,
            parent_message_id=parent_message_id,
            source_message_id=source_message_id,
            revision_type="regenerate",
            idempotency_key=idempotency_key,
        )

    async def _resolve_message_active_stream(
        self,
        *,
        conversation_id: UUID,
        message_id: UUID,
    ) -> ActiveStreamRead:
        message = await self.messages.get_by_id(conversation_id=conversation_id, message_id=message_id)
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

    async def _get_usable_llm_config(self, *, user_id: UUID, conversation: Conversation):
        llm_config_id = conversation.llm_config_id or await self._get_default_llm_config_id(user_id)
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
        return llm_config

    async def _message_read(self, message) -> MessageRead:
        state = await self.messages.get_message_read_state(message)
        return MessageRead.model_validate(message).model_copy(update=state)

    async def _message_reads(self, messages) -> list[MessageRead]:
        return [await self._message_read(message) for message in messages]

    async def _get_default_llm_config_id(self, user_id: UUID) -> UUID | None:
        # 没有默认配置时允许创建无模型会话，后续发送模型请求前再做强校验。
        configs = await self.llm_configs.list_active(user_id)
        for config in configs:
            if config.is_default and config.is_enabled:
                return config.id
        return None

    def _build_stream_id(self, message_id: UUID) -> str:
        # 先复用 assistant message_id 生成稳定 stream_id，后续如引入 stream attempt 再扩展。
        return f"stream_{message_id}"

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
                    "conversation": ConversationRead.model_validate(conversation).model_dump(mode="json")
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
        llm_config = await self.llm_configs.get_active(user_id=conversation.user_id, config_id=llm_config_id)
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
        token_usage: dict[str, Any] = {}
        response_metadata: dict[str, Any] = {"provider": llm_config.provider, "model": assistant_message.model or ""}
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
                    data={"message": (await self._message_read(cancelled_message)).model_dump(mode="json")},
                )
                return

            async for chunk in self.llm_client.stream(
                config=llm_config,
                messages=history_messages,
                summary=conversation.summary,
                model=assistant_message.model,
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
                        data={"message": (await self._message_read(cancelled_message)).model_dump(mode="json")},
                    )
                    return

                if chunk.token_usage:
                    token_usage = chunk.token_usage
                if chunk.response_metadata:
                    response_metadata.update(chunk.response_metadata)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

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
                data={"message": (await self._message_read(assistant_message)).model_dump(mode="json")},
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
                data={"message": (await self._message_read(cancelled_message)).model_dump(mode="json")},
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
                data={"message": (await self._message_read(failed_message)).model_dump(mode="json")},
            )
        finally:
            # 运行结束后保留一个短暂 replay 窗口，给刚断线的客户端补齐尾流。
            await conversation_stream_store.complete_stream(
                stream_id,
                retention_seconds=self.STREAM_RETENTION_SECONDS,
            )

    async def _get_conversation_by_id(self, conversation_id: UUID) -> Conversation | None:
        # 后台 producer 只按 conversation_id 读取自身上下文，不做用户态鉴权。
        return await self.session.get(Conversation, conversation_id)

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
        if assistant_message is not None and conversation is not None and assistant_message.status == "streaming":
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
                data={"message": (await self._message_read(failed_message)).model_dump(mode="json")},
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
