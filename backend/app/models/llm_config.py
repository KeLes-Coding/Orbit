from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression

from app.db.base import Base


class LLMConfig(Base):
    # 用户的模型接入配置：provider / model / base_url / API Key 密文都在这里。
    __tablename__ = "llm_configs"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    # API Key 只保存加密后的密文，后续接口不能把明文返回给前端。
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    provider_options: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # 每个用户只允许一个活跃默认配置，具体约束由下面的部分唯一索引保证。
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.false())
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.true())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="llm_configs")
    conversations = relationship("Conversation", back_populates="llm_config")
    messages = relationship("Message", back_populates="llm_config")


Index(
    "uq_llm_configs_user_name_active",
    LLMConfig.user_id,
    LLMConfig.name,
    unique=True,
    postgresql_where=LLMConfig.archived_at.is_(None),
)
Index(
    "uq_llm_configs_user_default_active",
    LLMConfig.user_id,
    unique=True,
    postgresql_where=LLMConfig.is_default.is_(True) & LLMConfig.archived_at.is_(None),
)
# 列表页和创建会话时常按用户查询可用配置。
Index(
    "idx_llm_configs_user_enabled",
    LLMConfig.user_id,
    LLMConfig.is_enabled,
    postgresql_where=LLMConfig.archived_at.is_(None),
)
