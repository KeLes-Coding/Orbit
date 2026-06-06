"""本地文件系统 sandbox 后端。

这个实现刻意保持很薄：先给 Orbit 一层可替换的 sandbox 执行面，
避免模型生成代码直接在后端进程内执行。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from app.services.langgraph_runtime.sandbox.base import ExecResult, SandboxFile, SandboxSession


class SandboxPathError(ValueError):
    """路径试图逃逸 sandbox 根目录时抛出。"""


class LocalSandboxBackend:
    """基于临时目录的一次 run 级别本地 sandbox。"""

    def __init__(self, *, root_dir: str | None = None) -> None:
        self._root_dir = Path(root_dir or tempfile.gettempdir()) / "orbit-sandboxes"
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Path] = {}

    async def create(self, *, run_id: str) -> SandboxSession:
        safe_run_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in run_id)
        root = Path(tempfile.mkdtemp(prefix=f"{safe_run_id}-", dir=self._root_dir))
        # 目录语义对齐计划文档：input/runtime 默认由宿主同步，work/output 供执行脚本写入。
        for name in ("input", "runtime", "work", "output"):
            (root / name).mkdir(parents=True, exist_ok=True)
        session_id = root.name
        self._sessions[session_id] = root
        return SandboxSession(id=session_id, root_path=str(root))

    async def destroy(self, session_id: str) -> None:
        root = self._sessions.pop(session_id, None)
        if root is not None and root.exists():
            shutil.rmtree(root)

    async def upload_bytes(self, session_id: str, path: str, content: bytes) -> None:
        target = self._resolve(session_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def download_bytes(self, session_id: str, path: str) -> bytes:
        return self._resolve(session_id, path).read_bytes()

    async def list_files(self, session_id: str, path: str) -> list[SandboxFile]:
        root = self._resolve(session_id, path)
        if not root.exists():
            return []
        files: list[SandboxFile] = []
        paths = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        session_root = self._session_root(session_id)
        for item in paths:
            files.append(
                SandboxFile(
                    path=f"/workspace/{item.relative_to(session_root).as_posix()}",
                    size=item.stat().st_size,
                )
            )
        return sorted(files, key=lambda item: item.path)

    async def exec(
        self,
        session_id: str,
        command: list[str],
        timeout_seconds: float,
    ) -> ExecResult:
        if not command:
            raise ValueError("sandbox 命令不能为空")

        root = self._session_root(session_id)
        try:
            # 使用子进程和固定 cwd 承载执行面；后续替换成 Docker/远端 sandbox 时保持协议不变。
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
            return ExecResult(
                command=list(command),
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            stdout, stderr = await proc.communicate()
            return ExecResult(
                command=list(command),
                exit_code=-1,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                timed_out=True,
            )

    def _session_root(self, session_id: str) -> Path:
        root = self._sessions.get(session_id)
        if root is None:
            raise KeyError(f"sandbox session 不存在：{session_id}")
        return root

    def _resolve(self, session_id: str, path: str) -> Path:
        root = self._session_root(session_id)
        posix_path = PurePosixPath(path)
        if posix_path.is_absolute():
            parts = posix_path.parts
            if len(parts) < 2 or parts[1] != "workspace":
                raise SandboxPathError(f"路径必须位于 /workspace 下：{path}")
            posix_path = PurePosixPath(*parts[2:])

        candidate = (root / Path(*posix_path.parts)).resolve()
        if root.resolve() not in (candidate, *candidate.parents):
            raise SandboxPathError(f"路径逃逸 sandbox 根目录：{path}")
        return candidate
