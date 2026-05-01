from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Message(Base):
    # 消息表是聊天历史的事实源，UI 展示、导出、审计都以这里为准。
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_no", name="uq_messages_conversation_sequence"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # sequence_no 由后端生成，保证同一会话内消息顺序稳定。
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    langgraph_message_id: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    reasoning_content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    content_parts: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'completed'"))
    # assistant 消息保存当次调用的模型快照，避免后续切换配置影响历史追溯。
    llm_config_id: Mapped[UUID | None] = mapped_column(ForeignKey("llm_configs.id", ondelete="SET NULL"))
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(120))
    token_usage: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    response_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")
    llm_config = relationship("LLMConfig", back_populates="messages")


Index("idx_messages_conversation_created", Message.conversation_id, Message.created_at)
# 读取聊天历史时优先按 conversation_id + sequence_no 排序。
Index("idx_messages_conversation_sequence", Message.conversation_id, Message.sequence_no)
Index(
    "idx_messages_langgraph_message_id",
    Message.langgraph_message_id,
    postgresql_where=Message.langgraph_message_id.is_not(None),
)
