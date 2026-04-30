from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "proxy_authorization",
    "x-api-key",
    "access_token",
    "refresh_token",
    "id_token",
    "password",
    "secret",
    "api_key_ciphertext",
}


def log_llm_object(
    *,
    phase: str,
    provider: str,
    model: str | None,
    value: Any,
    extracted: dict[str, Any] | None = None,
) -> None:
    if not settings.llm_debug_logging:
        return

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "provider": provider,
        "model": model,
        "object_type": type(value).__name__,
        "raw": _to_debug_value(value),
        "extracted": _to_debug_value(extracted or {}),
    }
    _append_debug_log(_truncate(repr(payload)))


def _append_debug_log(line: str) -> None:
    # LLM 调试日志落本地文件，避免在终端刷屏；文件路径可通过 ORBIT_LLM_DEBUG_LOG_PATH 调整。
    path = Path(settings.llm_debug_log_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(line)
        file.write("\n")


def _to_debug_value(value: Any, *, depth: int = 0) -> Any:
    # 调试日志要能看清 LangChain chunk 形状，但不能因为复杂对象递归过深拖垮请求。
    if depth > 5:
        return f"<{type(value).__name__}>"

    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, dict):
        return {
            str(key): _redact_or_convert(str(key), item, depth=depth + 1)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [_to_debug_value(item, depth=depth + 1) for item in value]

    if hasattr(value, "model_dump"):
        try:
            return _to_debug_value(value.model_dump(mode="json"), depth=depth + 1)
        except Exception:
            pass

    result: dict[str, Any] = {}
    for attr in (
        "id",
        "content",
        "content_blocks",
        "additional_kwargs",
        "response_metadata",
        "usage_metadata",
        "tool_calls",
        "invalid_tool_calls",
    ):
        if hasattr(value, attr):
            result[attr] = _to_debug_value(getattr(value, attr), depth=depth + 1)

    return result or repr(value)


def _redact_or_convert(key: str, value: Any, *, depth: int) -> Any:
    normalized = key.lower().replace("-", "_")
    if normalized in SENSITIVE_KEYS or any(part in normalized for part in ("api_key", "token", "secret")):
        return "<redacted>"
    return _to_debug_value(value, depth=depth)


def _truncate(text: str) -> str:
    max_chars = max(500, settings.llm_debug_max_chars)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...<truncated {len(text) - max_chars} chars>"
