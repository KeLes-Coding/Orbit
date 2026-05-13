import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.repositories.conversation import ConversationRepository, MessageRepository
from app.repositories.conversation_run import ConversationRunRepository
from app.repositories.llm_config import LLMConfigRepository
from app.schemas.conversation import MessageRead
from app.services.generation.title import ConversationTitleGenerator
from app.services.llm_client import LLMClient

_base_message_locks: dict[str, asyncio.Lock] = {}
_base_message_locks_guard = asyncio.Lock()


@dataclass(frozen=True)
class ConversationStreamEvent:
    # 这是路由层最终输出给 SSE 编码器的统一事件结构。
    event: str
    data: dict[str, Any]
    event_id: str | None = None


class ConversationBaseService:
    # 基础 mixin 只放共享依赖和跨模块 helper，避免 CRUD/run/stream 模块互相重复初始化。
    STREAM_RETENTION_SECONDS = 30

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.runs = ConversationRunRepository(session)
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

    def _base_message_lock_key(
        self, *, conversation_id: UUID, parent_message_id: UUID | None
    ) -> str:
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

    async def _get_usable_llm_config(
        self,
        *,
        user_id: UUID,
        conversation: Conversation,
        llm_config_id: UUID | None = None,
    ):
        llm_config_id = (
            llm_config_id
            or conversation.llm_config_id
            or await self._get_default_llm_config_id(user_id)
        )
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

    async def _get_conversation_by_id(self, conversation_id: UUID) -> Conversation | None:
        # 后台 producer 只按 conversation_id 读取自身上下文，不做用户态鉴权。
        return await self.session.get(Conversation, conversation_id)
