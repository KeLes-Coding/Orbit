"""Artifact manifest 协议结构。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ManifestArtifact(BaseModel):
    """单个 /workspace/output 下的产物引用。"""

    type: Literal["table", "chart", "code", "text", "report", "json"]
    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=400)
    preview: str | None = Field(default=None, max_length=400)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("path", "preview")
    @classmethod
    def validate_relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("artifact 路径必须是 output 内的相对路径")
        return normalized


class ArtifactManifest(BaseModel):
    """sandbox runtime 写出的产物清单。"""

    artifacts: list[ManifestArtifact] = Field(default_factory=list)
