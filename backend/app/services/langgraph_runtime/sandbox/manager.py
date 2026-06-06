"""Sandbox 编排辅助层。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
import sys

from app.services.langgraph_runtime.sandbox.base import ExecResult, SandboxBackendProtocol, SandboxSession
from app.services.langgraph_runtime.sandbox.local_sandbox import LocalSandboxBackend


@dataclass(frozen=True)
class SandboxInputFile:
    """同步到 /workspace/input 的输入文件。"""

    name: str
    content: bytes


class SandboxManager:
    """为一次 agent run 准备输入文件与 Orbit runtime bundle。"""

    def __init__(self, backend: SandboxBackendProtocol | None = None) -> None:
        self._backend = backend or LocalSandboxBackend()

    async def create_run_sandbox(
        self,
        *,
        run_id: str,
        input_files: list[SandboxInputFile] | None = None,
    ) -> SandboxSession:
        session = await self._backend.create(run_id=run_id)
        for item in input_files or []:
            await self._backend.upload_bytes(
                session.id,
                f"/workspace/input/{Path(item.name).name}",
                item.content,
            )
        await self.sync_runtime_bundle(session)
        return session

    async def destroy(self, session: SandboxSession) -> None:
        await self._backend.destroy(session.id)

    async def sync_runtime_bundle(self, session: SandboxSession) -> None:
        # runtime bundle 是只读 helper 集合，脚本通过 sys.path 引入，不依赖宿主源码路径。
        package_root = resources.files("app.services.langgraph_runtime.runtime_bundle.orbit_data")
        for resource in package_root.iterdir():
            if resource.name == "__pycache__" or not resource.name.endswith(".py"):
                continue
            content = resource.read_bytes()
            await self._backend.upload_bytes(
                session.id,
                f"/workspace/runtime/orbit_data/{resource.name}",
                content,
            )

    async def upload_text(self, session: SandboxSession, path: str, content: str) -> None:
        await self._backend.upload_bytes(session.id, path, content.encode("utf-8"))

    async def download_text(self, session: SandboxSession, path: str) -> str:
        return (await self._backend.download_bytes(session.id, path)).decode("utf-8")

    async def exec_python(
        self,
        session: SandboxSession,
        script_path: str,
        *,
        timeout_seconds: float,
    ) -> ExecResult:
        return await self._backend.exec(
            session.id,
            # 使用当前解释器，保证测试与本地运行使用同一套 Python 环境。
            [sys.executable, script_path],
            timeout_seconds,
        )

    async def list_output_files(self, session: SandboxSession) -> list[dict[str, str]]:
        files = await self._backend.list_files(session.id, "/workspace/output")
        return [{"path": item.path.removeprefix("/workspace/output/"), "size": str(item.size)} for item in files]

    async def download_output_bytes(self, session: SandboxSession, path: str) -> bytes:
        return await self._backend.download_bytes(session.id, f"/workspace/output/{path}")
