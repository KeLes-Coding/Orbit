"""官方 backend/sandbox 的 Orbit 适配入口。

本轮先保留最小桥接定义，后续按具体 Agent 类型逐步接入官方 backend。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BackendBridgeConfig:
    """runtime 内部 backend 选择配置。"""

    backend_type: str = "local"
    options: dict[str, Any] | None = None
