from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message


class ConversationRepository:
    # ConversationRepository 只处理会话元信息，不直接处理模型调用。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self, user_id: UUID) -> list[Conversation]:
        # 会话列表按最近更新时间倒序，符合聊天产品的常见展示方式。
        statement = (
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.archived_at.is_(None))
            .order_by(Conversation.updated_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_active(self, *, user_id: UUID, conversation_id: UUID) -> Conversation | None:
        # 会话读取带 user_id，确保用户只能访问自己的会话。
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.archived_at.is_(None),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: UUID,
        title: str | None,
        llm_config_id: UUID | None,
        chat_mode: str,
        metadata: dict,
    ) -> Conversation:
        # thread_id 由数据库默认生成，供后续 LangGraph checkpointer 使用。
        conversation = Conversation(
            user_id=user_id,
            title=title,
            llm_config_id=llm_config_id,
            chat_mode=chat_mode,
            metadata_=metadata,
        )
        self.session.add(conversation)
        await self.session.flush()
        await self.session.refresh(conversation)
        return conversation

    async def touch(self, conversation_id: UUID) -> None:
        # 新增消息或更新摘要后刷新 updated_at，用于会话列表排序。
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )

    async def archive(self, conversation: Conversation) -> None:
        # 归档会话不删除消息，便于后续恢复、审计或导出。
        conversation.archived_at = datetime.now(timezone.utc)


class MessageRepository:
    # MessageRepository 负责消息事实源的读写，顺序由 sequence_no 保证。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_conversation(self, conversation_id: UUID) -> list[Message]:
        # 读取历史消息必须按 sequence_no 排序，避免时间戳并发写入导致顺序漂移。
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence_no.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, *, conversation_id: UUID, message_id: UUID) -> Message | None:
        statement = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.id == message_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def next_sequence_no(self, conversation_id: UUID) -> int:
        # MVP 先用 max(sequence_no)+1；高并发发送时可升级为行锁或独立计数器。
        statement = select(func.coalesce(func.max(Message.sequence_no), 0) + 1).where(
            Message.conversation_id == conversation_id
        )
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    async def create_user_message(self, *, conversation_id: UUID, content: str) -> Message:
        # 用户消息写入后立即完成；assistant 消息会在模型调用阶段单独创建。
        sequence_no = await self.next_sequence_no(conversation_id)
        message = Message(
            conversation_id=conversation_id,
            sequence_no=sequence_no,
            role="user",
            content=content,
            status="completed",
        )
        self.session.add(message)
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def create_assistant_placeholder(
        self,
        *,
        conversation_id: UUID,
        llm_config_id: UUID,
        provider: str,
        model: str,
    ) -> Message:
        # 先写入 streaming 占位，后续无论成功或失败都有一条可追踪的 assistant 消息。
        sequence_no = await self.next_sequence_no(conversation_id)
        message = Message(
            conversation_id=conversation_id,
            sequence_no=sequence_no,
            role="assistant",
            content="",
            status="streaming",
            llm_config_id=llm_config_id,
            provider=provider,
            model=model,
        )
        self.session.add(message)
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def complete_assistant_message(
        self,
        *,
        message: Message,
        content: str,
        token_usage: dict[str, Any],
        response_metadata: dict[str, Any],
        reasoning_content: str = "",
    ) -> Message:
        # 模型调用成功后，把占位消息推进到 completed，并保存用量和供应商元信息。
        message.content = content
        message.reasoning_content = reasoning_content
        message.status = "completed"
        message.token_usage = token_usage
        message.response_metadata = response_metadata
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def fail_assistant_message(self, *, message: Message, error: str) -> Message:
        # 调用失败也保留 assistant 消息，前端可以根据 failed 状态展示重试入口。
        message.status = "failed"
        message.response_metadata = {"error": error}
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def partial_assistant_message(
        self,
        *,
        message: Message,
        content: str,
        error: str,
        token_usage: dict[str, Any] | None = None,
        response_metadata: dict[str, Any] | None = None,
        reasoning_content: str = "",
    ) -> Message:
        # 流式生成中途失败但已有内容时，保留部分回复并标记为 partial。
        message.content = content
        message.reasoning_content = reasoning_content
        message.status = "partial"
        message.token_usage = token_usage or {}
        message.response_metadata = {**(response_metadata or {}), "error": error}
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def cancel_assistant_message(
        self,
        *,
        message: Message,
        content: str,
        reasoning_content: str = "",
        token_usage: dict[str, Any] | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> Message:
        # 用户主动停止或连接断开时，保存已生成内容并标记为 cancelled。
        message.content = content
        message.reasoning_content = reasoning_content
        message.status = "cancelled"
        message.token_usage = token_usage or {}
        message.response_metadata = {**(response_metadata or {}), "error": "cancelled_by_user"}
        await self.session.flush()
        await self.session.refresh(message)
        return message
