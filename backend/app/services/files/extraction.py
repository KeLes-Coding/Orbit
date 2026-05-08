import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import update

from app.db.session import AsyncSessionLocal
from app.models.conversation_file import ConversationFile
from app.services.files.parser import DocumentParser

logger = logging.getLogger(__name__)


async def run_extraction_worker(*, file_id: UUID, storage_path: str, mime_type: str) -> None:
    # 后台异步解析任务：上传成功后由 FileService 通过 asyncio.create_task 启动。
    # 使用独立 DB session，遵循 _spawn_stream_producer 的后台任务模式。
    parser = DocumentParser()

    from app.core.config import get_settings

    base_dir = Path(get_settings().file_storage_dir).resolve()
    full_path = base_dir / storage_path
    try:
        text, error = await parser.extract(str(full_path), mime_type)
    except Exception as exc:
        # 解析过程中的意外异常也记录为 failed，避免文件永久卡在 processing。
        text, error = None, str(exc)

    status = "success"
    if error:
        status = "failed"
    elif text is None:
        # 图片等跳过解析的文件类型标记为 skipped。
        status = "skipped"

    async with AsyncSessionLocal() as session:
        from datetime import datetime, timezone

        await session.execute(
            update(ConversationFile)
            .where(ConversationFile.id == file_id)
            .values(
                extraction_status=status,
                extracted_text=text,
                extraction_error=error,
                extracted_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
