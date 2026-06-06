"""收集并校验 sandbox 输出产物。"""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.services.langgraph_runtime.artifacts.manifest import ArtifactManifest
from app.services.langgraph_runtime.sandbox import SandboxManager, SandboxSession


class ArtifactCollector:
    """校验 artifact_manifest.json 以及其中引用的输出文件。"""

    def __init__(self, sandbox_manager: SandboxManager) -> None:
        self._sandbox_manager = sandbox_manager

    async def collect(self, session: SandboxSession) -> ArtifactManifest:
        try:
            raw = await self._sandbox_manager.download_output_bytes(
                session,
                "artifact_manifest.json",
            )
        except FileNotFoundError as exc:
            raise ValueError("缺少 /workspace/output/artifact_manifest.json") from exc

        try:
            # Orbit 后端只信任 manifest 协议，不信任模型口头声明“执行完成”。
            manifest = ArtifactManifest.model_validate(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"artifact_manifest.json 校验失败：{exc}") from exc

        for artifact in manifest.artifacts:
            await self._assert_output_file_exists(session, artifact.path)
            if artifact.preview:
                await self._assert_output_file_exists(session, artifact.preview)
        return manifest

    async def _assert_output_file_exists(self, session: SandboxSession, path: str) -> None:
        try:
            await self._sandbox_manager.download_output_bytes(session, path)
        except FileNotFoundError as exc:
            raise ValueError(f"manifest 引用的产物不存在：{path}") from exc
