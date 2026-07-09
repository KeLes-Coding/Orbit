from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AgentArtifact(Base):
    # AgentArtifact 保存 manifest 中的产物引用和轻量 preview。
    # 第一版不保存完整二进制文件；后续可把 path 映射到对象存储或本地 artifact storage。
    __tablename__ = "agent_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_agent_artifacts_run_name"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    preview_path: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    preview: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run = relationship("AgentRun", back_populates="artifacts")


Index("idx_agent_artifacts_run", AgentArtifact.run_id)
Index("idx_agent_artifacts_type", AgentArtifact.type)
