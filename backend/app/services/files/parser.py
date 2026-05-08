import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class DocumentParser:
    # 支持解析的 MIME 类型白名单：图片本版跳过，文档做文本提取。
    ALLOWED_MIME_PREFIXES = {"text/", "image/"}
    ALLOWED_MIME_EXACT = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    def __init__(self, max_chars: int = 10000) -> None:
        # 每个文件最多提取 max_chars 个字符，避免超长文档撑爆上下文。
        self.max_chars = max_chars

    def is_supported(self, mime_type: str) -> bool:
        # 校验上传文件的 MIME 是否在白名单内。
        if any(mime_type.startswith(prefix) for prefix in self.ALLOWED_MIME_PREFIXES):
            return True
        return mime_type in self.ALLOWED_MIME_EXACT

    async def extract(self, file_path: str, mime_type: str) -> tuple[str | None, str | None]:
        # 按 MIME 类型分发到对应的解析器，返回 (extracted_text, error)。
        try:
            if mime_type == "application/pdf":
                text = await self._extract_pdf(file_path)
            elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                text = await self._extract_docx(file_path)
            elif mime_type.startswith("text/"):
                text = await self._extract_text(file_path)
            elif mime_type.startswith("image/"):
                # MVP 阶段图片不解析，只保留文件引用供前端展示和下载。
                text = None
            else:
                return None, f"Unsupported mime type for extraction: {mime_type}"

            if text is not None and len(text) > self.max_chars:
                text = text[:self.max_chars] + "..."
            return text, None
        except Exception as exc:
            logger.exception(f"Extraction failed for {file_path} ({mime_type})")
            return None, str(exc)

    async def _extract_pdf(self, file_path: str) -> str:
        # 使用 pymupdf (fitz) 提取 PDF 所有页面的文本。
        import fitz
        doc = fitz.open(file_path)
        try:
            parts: list[str] = []
            for page in doc:
                text = page.get_text()
                if isinstance(text, str) and text:
                    parts.append(text)
            return "\n\n".join(parts)
        finally:
            doc.close()

    async def _extract_docx(self, file_path: str) -> str:
        # 使用 python-docx 提取 Word 文档所有段落的文本。
        from docx import Document
        doc = Document(file_path)
        parts: list[str] = []
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        return "\n\n".join(parts)

    async def _extract_text(self, file_path: str) -> str:
        # 纯文本文件先用 UTF-8 读取，失败时回退到 Latin-1。
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return Path(file_path).read_text(encoding="latin-1")
