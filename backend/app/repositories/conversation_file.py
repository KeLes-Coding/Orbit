from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_file import ConversationFile


class ConversationFileRepository:
    # 文件仓储遵循 ConversationRepository 的代码模式：AsyncSession 构造注入，每次查询带 user_id 校验。
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs) -> ConversationFile:
        file = ConversationFile(**kwargs)
        self.session.add(file)
        await self.session.flush()
        await self.session.refresh(file)
        return file

    async def get_by_id(self, *, user_id: UUID, file_id: UUID) -> ConversationFile | None:
        # 按 file_id + user_id 查找，确保跨用户数据隔离。
        statement = select(ConversationFile).where(
            ConversationFile.id == file_id,
            ConversationFile.user_id == user_id,
            ConversationFile.deleted_at.is_(None),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id_for_update(
        self, *, user_id: UUID, file_id: UUID
    ) -> ConversationFile | None:
        # 带行锁的查找，用于绑定和状态更新时防止竞态。
        statement = (
            select(ConversationFile)
            .where(
                ConversationFile.id == file_id,
                ConversationFile.user_id == user_id,
                ConversationFile.deleted_at.is_(None),
            )
            .with_for_update()
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def find_by_checksum(
        self, *, user_id: UUID, checksum: str
    ) -> ConversationFile | None:
        # 按用户 + SHA-256 查找已有文件，用于上传去重。
        statement = select(ConversationFile).where(
            ConversationFile.user_id == user_id,
            ConversationFile.checksum_sha256 == checksum,
            ConversationFile.deleted_at.is_(None),
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def list_by_conversation(self, *, conversation_id: UUID) -> list[ConversationFile]:
        # 返回当前会话已绑定的文件，按创建时间升序。
        statement = (
            select(ConversationFile)
            .where(
                ConversationFile.conversation_id == conversation_id,
                ConversationFile.bind_status == "bound",
                ConversationFile.deleted_at.is_(None),
            )
            .order_by(ConversationFile.created_at.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def list_pending_by_user(self, *, user_id: UUID) -> list[ConversationFile]:
        # 返回用户未绑定且未过期的 pending 文件。
        statement = (
            select(ConversationFile)
            .where(
                ConversationFile.user_id == user_id,
                ConversationFile.bind_status == "pending",
                ConversationFile.conversation_id.is_(None),
                ConversationFile.deleted_at.is_(None),
                ConversationFile.expires_at > func.now(),
            )
            .order_by(ConversationFile.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def list_expired(self) -> list[ConversationFile]:
        # 返回所有过期未删除的 pending 文件，供定期清理任务使用。
        statement = select(ConversationFile).where(
            ConversationFile.bind_status == "pending",
            ConversationFile.expires_at < func.now(),
            ConversationFile.deleted_at.is_(None),
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def bind_to_conversation(
        self, *, file: ConversationFile, conversation_id: UUID
    ) -> ConversationFile:
        # 将 pending 文件绑定到指定会话：设置 conversation_id、清除过期时间。
        file.conversation_id = conversation_id
        file.bind_status = "bound"
        file.expires_at = None
        await self.session.flush()
        await self.session.refresh(file)
        return file

    async def update_extraction_result(
        self,
        *,
        file: ConversationFile,
        status: str,
        text: str | None = None,
        error: str | None = None,
    ) -> ConversationFile:
        # 后台解析完成后更新文件解析状态、提取文本和错误信息。
        file.extraction_status = status
        if text is not None:
            file.extracted_text = text
        if error is not None:
            file.extraction_error = error
        if status in ("success", "failed", "skipped"):
            file.extracted_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(file)
        return file

    async def mark_deleted(self, *, file: ConversationFile) -> ConversationFile:
        # 软删除：设置 deleted_at 并更新 bind_status，索引仍会过滤掉已删除记录。
        file.deleted_at = datetime.now(timezone.utc)
        file.bind_status = "deleted"
        await self.session.flush()
        await self.session.refresh(file)
        return file

    async def delete_expired(self) -> int:
        # 批量清理过期 pending 文件，返回清理数量。
        expired = await self.list_expired()
        count = 0
        for f in expired:
            f.deleted_at = datetime.now(timezone.utc)
            f.bind_status = "deleted"
            count += 1
        if count:
            await self.session.flush()
        return count
