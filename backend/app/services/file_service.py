import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.models.conversation_file import ConversationFile
from app.repositories.conversation_file import ConversationFileRepository
from app.schemas.file import FileRead
from app.services.files import DocumentParser, LocalFileStorage, run_extraction_worker

logger = logging.getLogger(__name__)


class FileService:
    # 文件服务负责上传编排：校验 MIME / 大小、SHA-256 去重、存储落盘、启动后台解析。
    def __init__(self, session: "AsyncSession") -> None:  # type: ignore[valid-type]
        from sqlalchemy.ext.asyncio import AsyncSession as _AS
        self.session: _AS = session
        self.repo = ConversationFileRepository(session)
        self.storage = LocalFileStorage()
        self.parser = DocumentParser(max_chars=get_settings().file_max_extracted_chars)

    async def upload_pending_file(self, *, user_id: UUID, file: UploadFile) -> FileRead:
        # 新聊天预上传：conversation_id=NULL, bind_status=pending, 24h 后过期。
        conv_file = await self._upload_file(user_id=user_id, conversation_id=None, file=file)
        return FileRead.model_validate(conv_file)

    async def upload_to_conversation(
        self, *, user_id: UUID, conversation_id: UUID, file: UploadFile
    ) -> FileRead:
        # 已有会话上传：直接绑定到当前 conversation。
        conv_file = await self._upload_file(
            user_id=user_id, conversation_id=conversation_id, file=file
        )
        return FileRead.model_validate(conv_file)

    async def _upload_file(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID | None,
        file: UploadFile,
    ) -> ConversationFile:
        content = await file.read()
        max_bytes = get_settings().file_max_size_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {get_settings().file_max_size_mb}MB size limit",
            )

        # SHA-256 去重：同一用户相同内容共享物理文件，但每个会话独立记录。
        checksum = LocalFileStorage.checksum(content)
        existing = await self.repo.find_by_checksum(user_id=user_id, checksum=checksum)
        if existing is not None:
            if conversation_id is not None and existing.conversation_id == conversation_id:
                # 同一会话内已有完全相同的文件，直接复用。
                return existing
            if existing.bind_status == "pending" and existing.conversation_id is None:
                # Pending 文件恰好被请求绑定到 conversation，直接绑定。
                if conversation_id is not None:
                    await self.repo.bind_to_conversation(
                        file=existing, conversation_id=conversation_id
                    )
                    await self.session.commit()
                return existing
            # 已绑定到其他会话或已删除 → 创建新记录共享物理存储，避免重复落盘。
            mime_type = existing.file_type
            file_name = existing.original_name
            ext = existing.file_extension
        else:
            mime_type = file.content_type or "application/octet-stream"
            file_name = file.filename or "unknown"
            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else None

        if not self.parser.is_supported(mime_type):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file type: {mime_type}",
            )

        bind_status = "bound" if conversation_id is not None else "pending"
        expires_at = (
            None
            if conversation_id is not None
            else datetime.now(timezone.utc) + timedelta(hours=24)
        )

        # 创建数据库记录。
        conv_file = await self.repo.create(
            user_id=user_id,
            conversation_id=conversation_id,
            original_name=file_name,
            file_type=mime_type,
            file_size=len(content),
            file_extension=ext,
            checksum_sha256=checksum,
            storage_type="local",
            storage_path=existing.storage_path if existing else "",
            bind_status=bind_status,
            expires_at=expires_at,
        )

        if existing:
            # 复用已有物理文件，不重复落盘。
            storage_path = existing.storage_path
        else:
            storage_path = await self.storage.save(
                file_id=conv_file.id, content=content, original_name=file_name
            )
        conv_file.storage_path = storage_path

        await self.session.commit()
        await self.session.refresh(conv_file)

        # 上传成功后立即启动后台解析，不阻塞当前请求。
        asyncio.create_task(
            run_extraction_worker(
                file_id=conv_file.id,
                storage_path=storage_path,
                mime_type=mime_type,
            )
        )

        return conv_file

    async def bind_pending_files(
        self, *, user_id: UUID, file_ids: list[UUID], conversation_id: UUID
    ) -> list[ConversationFile]:
        # 首条消息发送时批量绑定 pending 文件到新创建的 conversation。
        files: list[ConversationFile] = []
        for file_id in file_ids:
            conv_file = await self.repo.get_by_id(user_id=user_id, file_id=file_id)
            if conv_file is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"File not found: {file_id}",
                )
            if conv_file.bind_status == "bound":
                if conv_file.conversation_id == conversation_id:
                    # Already bound to this conversation, reuse directly.
                    files.append(conv_file)
                    continue
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File is already bound to another conversation: {file_id}",
                )
            await self.repo.bind_to_conversation(
                file=conv_file, conversation_id=conversation_id
            )
            files.append(conv_file)
        await self.session.commit()
        return files

    async def wait_for_extraction(
        self, *, files: list[ConversationFile], timeout_seconds: float | None = None
    ) -> list[ConversationFile]:
        # 发送消息时轮询等待文件解析完成，超时则降级发送。
        if timeout_seconds is None:
            timeout_seconds = get_settings().file_extraction_wait_seconds

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        pending_ids = {
            f.id for f in files if f.extraction_status in ("pending", "processing")
        }

        if not pending_ids:
            return files

        user_id = files[0].user_id
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.15)
            for fid in list(pending_ids):
                updated = await self.repo.get_by_id(user_id=user_id, file_id=fid)
                if updated and updated.extraction_status not in ("pending", "processing"):
                    pending_ids.discard(fid)
            if not pending_ids:
                break

        # 重新加载最新状态后返回。
        result: list[ConversationFile] = []
        for f in files:
            refreshed = await self.repo.get_by_id(user_id=f.user_id, file_id=f.id)
            result.append(refreshed or f)
        return result

    @staticmethod
    def build_content_parts(*, content: str, files: list[ConversationFile]) -> list[dict[str, Any]]:
        # 构造 messages.content_parts 的 JSONB 结构，存入 message 用于展示和 LLM 上下文拼接。
        parts: list[dict[str, Any]] = []
        if content.strip():
            parts.append({"type": "text", "text": content})
        for f in files:
            part: dict[str, Any] = {
                "type": "file",
                "file_id": str(f.id),
                "name": f.original_name,
                "mime_type": f.file_type,
                "file_size": f.file_size,
                # storage_path 用于多模态 LLM 调用时从磁盘读取图片文件。
                "storage_path": f.storage_path,
            }
            # 解析完成的文件附带 extracted_text，后续 LLM 上下文拼接时使用。
            if f.extracted_text:
                part["extracted_text"] = f.extracted_text
            parts.append(part)
        return parts

    async def get_file_content(self, *, user_id: UUID, file_id: UUID) -> tuple[ConversationFile, bytes]:
        # 校验用户归属后返回文件元数据和内容，用于下载/预览。
        conv_file = await self.repo.get_by_id(user_id=user_id, file_id=file_id)
        if conv_file is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        content = await self.storage.get_content(
            file_id=conv_file.id, storage_path=conv_file.storage_path
        )
        return conv_file, content
