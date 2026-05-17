"""AgentWorkspace —— 隔离工作区。

为 DeepAgent 提供最小文件存储，只允许操作白名单内的文件。
第一版使用内存 dict 存储，不暴露真实文件系统。
"""

from __future__ import annotations

from pathlib import PurePosixPath

_ALLOWED_FILES = frozenset({"plan.md", "notes.md", "final.md"})


class WorkspaceSecurityError(Exception):
    """路径越权或文件不在白名单内。"""


class AgentWorkspace:
    """隔离的 agent 工作区。

    对外提供 write_file / read_file / list_files 三个工具接口，
    返回字符串便于直接作为 LangChain Tool 的返回值（不需要额外序列化）。
    """

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._store: dict[str, str] = {}

    def _resolve(self, path: str) -> str:
        """校验并归一化路径。"""
        normalized = PurePosixPath(path).name
        if normalized not in _ALLOWED_FILES:
            raise WorkspaceSecurityError(
                f"不允许操作文件：'{path}'，只允许 {sorted(_ALLOWED_FILES)}"
            )
        return normalized

    # ── 工具接口（返回 str 以适配 LangChain Tool）──────────────────

    async def write_file(self, path: str, content: str) -> str:
        """写入文件内容，返回确认信息。"""
        key = self._resolve(path)
        self._store[key] = content
        return f"已写入 {key}（{len(content)} 字符）"

    async def read_file(self, path: str) -> str:
        """读取文件内容，返回全文。"""
        key = self._resolve(path)
        content = self._store.get(key)
        if content is None:
            return f"文件 {key} 不存在"
        return content

    async def list_files(self) -> str:
        """列出 workspace 中的所有文件。"""
        files = self.get_file_index()
        if not files:
            return "工作区中暂无文件"
        lines = ["工作区文件："]
        for f in files:
            lines.append(f"  {f['path']}（{f['size']} 字符）")
        return "\n".join(lines)

    # ── 内部接口 ──────────────────────────────────────────────────

    def get_file_index(self) -> list[dict[str, str]]:
        """返回文件索引 [{path, size}]，供 state 持久化。"""
        return [
            {"path": key, "size": str(len(content))}
            for key, content in self._store.items()
        ]

    def has_file(self, path: str) -> bool:
        try:
            key = self._resolve(path)
        except WorkspaceSecurityError:
            return False
        return key in self._store

    def get_content(self, path: str) -> str | None:
        try:
            key = self._resolve(path)
        except WorkspaceSecurityError:
            return None
        return self._store.get(key)
