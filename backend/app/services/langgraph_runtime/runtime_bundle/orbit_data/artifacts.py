"""sandbox 脚本使用的产物写入 helper。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


OUTPUT_ROOT = Path("/workspace/output")
if not OUTPUT_ROOT.exists():
    OUTPUT_ROOT = Path("output")


def _resolve_output(path: str) -> Path:
    normalized = Path(path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"artifact 路径必须留在 output 内：{path}")
    target = OUTPUT_ROOT / normalized
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def save_json_artifact(path: str, data: Any) -> dict[str, Any]:
    """将 JSON 数据写入 /workspace/output。"""

    target = _resolve_output(path)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": path, "size": target.stat().st_size}


def save_text_artifact(path: str, text: str) -> dict[str, Any]:
    """将文本写入 /workspace/output。"""

    target = _resolve_output(path)
    target.write_text(text, encoding="utf-8")
    return {"path": path, "size": target.stat().st_size}


def write_artifact_manifest(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """写出 /workspace/output/artifact_manifest.json。"""

    manifest = {"artifacts": artifacts}
    save_json_artifact("artifact_manifest.json", manifest)
    return manifest
