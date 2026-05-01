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
    # active_leaf 是当前路径终点的缓存，真实分支选择仍以 messages.active_child 为准。
    active_leaf_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    # fork 来源只做追溯记录，新会话会复制消息并独立演进。
    forked_from_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    forked_from_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    # 摘要和 branch 绑定时，用 leaf 标记摘要覆盖的那条 visible path。
    summary_leaf_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="conversations")
    llm_config = relationship("LLMConfig", back_populates="conversations")
    # Conversation 与 Message 之间现在有多个外键，必须显式指定真正的一对多外键。
    messages = relationship("Message", back_populates="conversation", foreign_keys="Message.conversation_id")


Index("uq_conversations_thread_id", Conversation.thread_id, unique=True)
# 会话列表通常按用户筛选，并按最近更新时间倒序展示。
Index(
    "idx_conversations_user_updated",
    Conversation.user_id,
    Conversation.updated_at.desc(),
    postgresql_where=Conversation.archived_at.is_(None),
)
Index(
    "idx_conversations_active_leaf",
    Conversation.active_leaf_message_id,
    postgresql_where=Conversation.active_leaf_message_id.is_not(None),
)
