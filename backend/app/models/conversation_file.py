from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ConversationFile(Base):
    # 统一管理对话中的图片和文档文件，通过 bind_status 区分 pending/bound/deleted。
    __tablename__ = "conversation_files"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # 新聊天未创建 conversation 时 conversation_id 为 NULL，首条消息发送后再绑定。
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_extension: Mapped[str | None] = mapped_column(String(20))
    # SHA-256 用于去重：同一用户下相同内容的文件只存一份。
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'local'"))
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    # storage_bucket 预留给后续 S3/MinIO 接入，当前 MVP 只使用本地存储。
    storage_bucket: Mapped[str | None] = mapped_column(Text)
    # 文档解析结果：图片跳过解析，PDF/DOCX/TXT 异步提取文本。
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    extraction_error: Mapped[str | None] = mapped_column(Text)
    # bind_status 控制文件生命周期：pending → bound / deleted。
    bind_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    # pending 文件 24 小时后过期，定期清理任务会扫描删除。
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 软删除标记，保留记录便于审计和去重索引。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="files")
    conversation = relationship(
        "Conversation", back_populates="files", foreign_keys=[conversation_id]
    )


# 按用户和时间排序，用于文件列表展示。
Index("idx_conversation_files_user_created", ConversationFile.user_id, ConversationFile.created_at.desc())
# 按会话查询已绑定文件。
Index("idx_conversation_files_conversation", ConversationFile.conversation_id)
# 后台解析任务按状态扫描待处理文件。
Index("idx_conversation_files_extraction_status", ConversationFile.extraction_status)
# 定期清理任务扫描过期 pending 文件。
Index(
    "idx_conversation_files_pending_expires",
    ConversationFile.expires_at,
    postgresql_where=ConversationFile.bind_status == "pending",
)
# 同一用户下未删除文件的 SHA-256 唯一，支持去重。
# 去重索引降级为普通索引：同一文件跨会话上传时共享物理存储但创建独立记录。
Index(
    "idx_conversation_files_user_checksum",
    ConversationFile.user_id,
    ConversationFile.checksum_sha256,
    postgresql_where=ConversationFile.deleted_at.is_(None),
)
