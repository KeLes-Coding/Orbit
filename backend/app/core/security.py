from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from jwt import InvalidTokenError

from app.core.config import settings


PASSWORD_HASH_ITERATIONS = 210_000
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
ACCESS_TOKEN_ALGORITHM = "HS256"


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    # 使用标准库 PBKDF2 保存密码哈希，避免数据库中出现明文密码。
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return "$".join(
        [
            PASSWORD_HASH_ALGORITHM,
            str(PASSWORD_HASH_ITERATIONS),
            _base64url_encode(salt),
            _base64url_encode(digest),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    # 固定时间比较可以避免泄露哈希匹配进度。
    try:
        algorithm, iterations, salt, expected_digest = password_hash.split("$", 3)
        if algorithm != PASSWORD_HASH_ALGORITHM:
            return False

        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _base64url_decode(salt),
            int(iterations),
        )
    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(_base64url_encode(digest), expected_digest)


def create_access_token(user_id: UUID) -> str:
    # 使用标准 JWT 作为 Bearer Token，便于后续接入网关、移动端或第三方生态。
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "exp": expires_at,
    }
    return jwt.encode(
        payload,
        settings.auth_secret_key,
        algorithm=ACCESS_TOKEN_ALGORITHM,
    )


def verify_access_token(token: str) -> UUID | None:
    # 返回 None 表示 JWT 格式错误、签名不匹配或已经过期。
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret_key,
            algorithms=[ACCESS_TOKEN_ALGORITHM],
        )
        return UUID(str(payload["sub"]))
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        return None
