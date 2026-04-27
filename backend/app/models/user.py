from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression

from app.db.base import Base


class User(Base):
    # 用户是 MVP 的顶层租户实体，模型配置和会话都通过 user_id 隔离。
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    # MVP 使用本地邮箱密码登录，因此这里保存密码哈希，不保存明文密码。
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=expression.true())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    llm_configs = relationship("LLMConfig", back_populates="user")
    conversations = relationship("Conversation", back_populates="user")


Index(
    "uq_users_email_active",
    func.lower(User.email),
    unique=True,
    postgresql_where=User.archived_at.is_(None),
)
# 软删除用户后允许邮箱重新注册；正常活跃用户邮箱保持大小写不敏感唯一。
Index("idx_users_enabled", User.is_enabled, postgresql_where=User.archived_at.is_(None))
