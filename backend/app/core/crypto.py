from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from app.core.config import settings


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _derive_key() -> bytes:
    return hashlib.sha256(settings.encryption_secret_key.encode("utf-8")).digest()


def _keystream(*, key: bytes, nonce: bytes, length: int) -> bytes:
    # MVP 阶段使用 HMAC 派生字节流保护本地 API Key；生产环境建议替换为 KMS/Fernet。
    chunks: list[bytes] = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < length:
        chunks.append(
            hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        )
        counter += 1
    return b"".join(chunks)[:length]


def encrypt_secret(value: str | None) -> str | None:
    if value is None or value == "":
        return None

    key = _derive_key()
    nonce = secrets.token_bytes(16)
    plaintext = value.encode("utf-8")
    stream = _keystream(key=key, nonce=nonce, length=len(plaintext))
    ciphertext = bytes(left ^ right for left, right in zip(plaintext, stream))
    tag = hmac.new(key, b"v1" + nonce + ciphertext, hashlib.sha256).digest()
    return ".".join(
        [
            "v1",
            _base64url_encode(nonce),
            _base64url_encode(ciphertext),
            _base64url_encode(tag),
        ]
    )


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None

    try:
        version, nonce_segment, ciphertext_segment, tag_segment = value.split(".", 3)
        if version != "v1":
            return None

        key = _derive_key()
        nonce = _base64url_decode(nonce_segment)
        ciphertext = _base64url_decode(ciphertext_segment)
        tag = _base64url_decode(tag_segment)
        expected_tag = hmac.new(key, b"v1" + nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            return None

        stream = _keystream(key=key, nonce=nonce, length=len(ciphertext))
        plaintext = bytes(left ^ right for left, right in zip(ciphertext, stream))
        return plaintext.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
