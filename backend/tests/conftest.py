"""测试基础配置。

让 `pytest` 直接在 `backend/` 目录下运行时，也能稳定导入 `app.*` 模块，
避免依赖外部手工设置 `PYTHONPATH`。
"""

from __future__ import annotations

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
backend_root_str = str(BACKEND_ROOT)
if backend_root_str not in sys.path:
    sys.path.insert(0, backend_root_str)
