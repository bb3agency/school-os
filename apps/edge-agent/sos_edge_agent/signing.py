"""Request signing for SchoolOS (ADR-0032 §3). Must match ``app.tally.agent_auth`` exactly.

``canonical = "SOS-EDGE-HMAC-SHA256\\n" + METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + NONCE
+ "\\n" + hex(SHA-256(body))`` and ``X-SOS-Signature = "v1=" + hex(HMAC-SHA256(secret,
canonical))``. The test vector in ``tests/test_signing.py`` is also pinned by the API's tests.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from typing import Final

SCHEME: Final = "SOS-EDGE-HMAC-SHA256"


def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join((SCHEME, method.upper(), path, timestamp, nonce, digest)).encode("utf-8")


def sign(secret: bytes, *, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    mac = hmac.new(secret, canonical(method, path, timestamp, nonce, body), hashlib.sha256)
    return "v1=" + mac.hexdigest()


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def signed_headers(
    *,
    secret: bytes,
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    key_id: str,
    method: str,
    path: str,
    body: bytes,
    agent_version: str,
    now: float | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = str(int(now if now is not None else time.time()))
    n = nonce or new_nonce()
    return {
        "X-SOS-Tenant": str(tenant_id),
        "X-SOS-Device": str(device_id),
        "X-SOS-Key-Id": key_id,
        "X-SOS-Timestamp": timestamp,
        "X-SOS-Nonce": n,
        "X-SOS-Agent-Version": agent_version,
        "X-SOS-Signature": sign(
            secret, method=method, path=path, timestamp=timestamp, nonce=n, body=body
        ),
    }


__all__ = ["SCHEME", "canonical", "new_nonce", "sign", "signed_headers"]
