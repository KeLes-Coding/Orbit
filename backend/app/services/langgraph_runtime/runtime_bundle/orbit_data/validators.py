"""sandbox runtime 侧产物校验 helper。"""

from __future__ import annotations

from typing import Any


def validate_artifact_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """执行最小 manifest 结构校验。"""

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("manifest.artifacts 必须是 list")
    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError("manifest artifact 必须是 object")
        if not item.get("type") or not item.get("name") or not item.get("path"):
            raise ValueError("manifest artifact 必须包含 type、name 和 path")
    return manifest
