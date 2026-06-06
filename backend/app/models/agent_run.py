from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AgentRun(Base):
    # AgentRun 是一次 assistant message 背后的 agent 执行事实。
    # 它把执行状态从 message.response_metadata 中拆出来，便于后续按 run 查询和恢复产物。
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_agent_runs_message"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    agent_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sandbox_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    artifacts = relationship("AgentArtifact", back_populates="run", cascade="all, delete-orphan")


Index("idx_agent_runs_conversation_created", AgentRun.conversation_id, AgentRun.created_at.desc())
Index("idx_agent_runs_message", AgentRun.message_id)
