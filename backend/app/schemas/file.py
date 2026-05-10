from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FileRead(BaseModel):
    # 返回给前端的文件元数据，不包含存储路径和哈希等内部字段。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    conversation_id: UUID | None
    original_name: str
    file_type: str
    file_size: int
    file_extension: str | None
    storage_type: str
    extraction_status: str
    extraction_error: str | None
    bind_status: str
    created_at: datetime
    updated_at: datetime


class FileUploadResponse(BaseModel):
    file: FileRead
    upload_url: str | None = None
