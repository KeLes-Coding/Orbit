import hashlib
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from app.core.config import get_settings


@lru_cache
def _resolve_storage_root() -> Path:
    # 相对路径只解析一次，避免后台 asyncio task 中 CWD 不同导致路径漂移。
    root = Path(get_settings().file_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


class LocalFileStorage:
    # MVP 使用本地文件系统存储上传文件，后续可替换为 S3/MinIO 实现。
    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir else _resolve_storage_root()

    def _file_dir(self, file_id: UUID) -> Path:
        # 每个文件存放在独立目录下，便于清理和管理。
        return self.base_dir / file_id.hex

    async def save(self, *, file_id: UUID, content: bytes, original_name: str) -> str:
        # 保存文件到 {base_dir}/{file_id_hex}/{original_name}，返回相对路径存入数据库。
        file_dir = self._file_dir(file_id)
        file_dir.mkdir(parents=True, exist_ok=True)
        file_path = file_dir / original_name
        file_path.write_bytes(content)
        return str(file_path.relative_to(self.base_dir))

    async def get_content(self, *, file_id: UUID, storage_path: str) -> bytes:
        # 根据数据库中的相对路径读取文件内容，用于下载和解析。
        full_path = self.base_dir / storage_path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {storage_path}")
        return full_path.read_bytes()

    async def delete(self, *, file_id: UUID) -> None:
        # 删除文件目录及所有内容，用于过期清理。
        file_dir = self._file_dir(file_id)
        if file_dir.exists():
            import shutil

            shutil.rmtree(file_dir)

    @staticmethod
    def checksum(content: bytes) -> str:
        # SHA-256 哈希用于文件去重和完整性校验。
        return hashlib.sha256(content).hexdigest()
