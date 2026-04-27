from datetime import datetime, timezone
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
