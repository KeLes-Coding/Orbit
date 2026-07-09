"""Sandbox 后端协议定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SandboxSession:
    """一次 run 级别的 sandbox 工作区。"""

    id: str
    root_path: str


@dataclass(frozen=True)
class SandboxFile:
    """sandbox 内可见的文件索引。"""

    path: str
    size: int


@dataclass(frozen=True)
class ExecResult:
    """sandbox 命令执行结果。"""

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxBackendProtocol(Protocol):
    """Agent 插件使用的执行与文件系统边界。"""

    async def create(self, *, run_id: str) -> SandboxSession:
        ...

    async def destroy(self, session_id: str) -> None:
        ...

    async def upload_bytes(self, session_id: str, path: str, content: bytes) -> None:
        ...

    async def download_bytes(self, session_id: str, path: str) -> bytes:
        ...

    async def list_files(self, session_id: str, path: str) -> list[SandboxFile]:
        ...

    async def exec(
        self,
        session_id: str,
        command: list[str],
        timeout_seconds: float,
    ) -> ExecResult:
        ...
