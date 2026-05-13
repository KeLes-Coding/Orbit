"""会话执行运行记录（ConversationRun）。

每次向模型/agent 发起一次生成请求，都会产生一条 run 记录，
用于统一追踪 chat/agent 的执行状态、恢复入口和监控审计。
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ConversationRun(Base):
    __tablename__ = "conversation_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # 关联的 assistant 消息，完成/失败后回写最终内容到这条消息。
    assistant_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # thread_id 对应 Conversation.thread_id，agent 路径下即为 LangGraph thread id。
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    # runtime_kind 区分 classic_chat / langgraph_agent，用于监控和调度分析。
    runtime_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'streaming'")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # metadata_ 存储模型名、token_usage 等扩展信息，不单独建列。
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    conversation = relationship("Conversation", foreign_keys=[conversation_id])
    assistant_message = relationship("Message", foreign_keys=[assistant_message_id])


# 按会话 + 时间排序，是最常用的查询模式（会话的 run 历史列表）。
Index(
    "idx_conversation_runs_conversation_started",
    ConversationRun.conversation_id,
    ConversationRun.started_at.desc(),
)
# 按会话 + 状态筛选活跃 run（用于判断是否还有运行中任务）。
Index(
    "idx_conversation_runs_conversation_status",
    ConversationRun.conversation_id,
    ConversationRun.status,
)
# 按 thread_id 查找 run（用于 agent 恢复场景）。
Index(
    "idx_conversation_runs_thread",
    ConversationRun.thread_id,
)
