from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Conversation(Base):
    # 会话元信息表：保存标题、默认模型配置、摘要和 LangGraph thread_id。
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # thread_id 是运行时/checkpointer 标识，和产品侧 conversation.id 分开。
    thread_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("gen_random_uuid()::text"))
    title: Mapped[str | None] = mapped_column(String(200))
    llm_config_id: Mapped[UUID | None] = mapped_column(ForeignKey("llm_configs.id", ondelete="SET NULL"))
    chat_mode: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'chat'"))
    # summary 是业务侧长会话压缩结果，不替代 messages 原始消息。
    summary: Mapped[str | None] = mapped_column(Text)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="conversations")
    llm_config = relationship("LLMConfig", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")


Index("uq_conversations_thread_id", Conversation.thread_id, unique=True)
# 会话列表通常按用户筛选，并按最近更新时间倒序展示。
Index(
    "idx_conversations_user_updated",
    Conversation.user_id,
    Conversation.updated_at.desc(),
    postgresql_where=Conversation.archived_at.is_(None),
)
