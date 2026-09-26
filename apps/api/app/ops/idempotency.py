"""Idempotency-Key store (docs/09 §2; docs/16 §8).

Same key + same request hash -> replay the original status and resource (no response body is
stored, so no personal data is duplicated); same key + different hash -> 422
``idempotency_key_reused``; key still running -> 409 ``idempotency_in_progress``.

- Control plane: :class:`KVIdempotencyStore` on the shared ``app.authz.kv`` store (Valkey,
  24 h, per operator), because ``sos_platform`` cannot use ``ops.idempotency_keys``.
- Tenant API: ``app.ops.service.begin_idempotent`` / ``complete_idempotent`` on
  ``ops.idempotency_keys`` inside the request's ``tenant_session``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Literal, Protocol

from app.authz.kv import KVStore, KVUnavailable
from app.core.errors import Conflict, ServiceUnavailable, ValidationFailed

DEFAULT_TTL = timedelta(hours=24)


def request_hash(method: str, route: str, body: bytes) -> str:
    return hashlib.sha256(method.encode() + b" " + route.encode() + b"\n" + body).hexdigest()


def check_key(key: str) -> str:
    if not 8 <= len(key) <= 128 or not all(c.isalnum() or c in "-_.:" for c in key):
        raise ValidationFailed(
            [
                {
                    "field": "Idempotency-Key",
                    "code": "invalid_idempotency_key",
                    "message_key": "errors.idempotency_key",
                }
            ]
        )
    return key


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    request_sha256: str
    state: Literal["in_progress", "completed"]
    status_code: int | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    location: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | bytes) -> IdempotencyRecord:
        return cls(**json.loads(raw))


class IdempotencyStore(Protocol):
    def begin(self, scope: str, key: str, request_sha256: str) -> IdempotencyRecord | None:
        """Claim ``key``: ``None`` if newly claimed, else the existing record."""
        ...

    def complete(self, scope: str, key: str, record: IdempotencyRecord) -> None: ...

    def abandon(self, scope: str, key: str) -> None:
        """Release a claim after a failure so the client can retry with the same key."""
        ...


class KVIdempotencyStore:
    """Idempotency records in the shared key-value store (Valkey; in-process locally/CI).

    Reuses ``app.authz.kv`` (the store behind tenant-route idempotency in ``app.authz.http``) so
    there is one Valkey client and one key namespace (``sos:idem:``). Records hold a request
    hash, status, resource type/id and Location only, never a response body.
    """

    def __init__(
        self, kv: KVStore, *, ttl: timedelta = DEFAULT_TTL, prefix: str = "sos:idem:"
    ) -> None:
        self._kv = kv
        self._ttl = int(ttl.total_seconds())
        self._prefix = prefix

    def _k(self, scope: str, key: str) -> str:
        digest = hashlib.sha256(f"{scope}:{key}".encode()).hexdigest()
        return f"{self._prefix}{scope}:{digest}"

    def begin(self, scope: str, key: str, request_sha256: str) -> IdempotencyRecord | None:
        k = self._k(scope, key)
        pending = IdempotencyRecord(request_sha256, "in_progress").to_json().encode()
        try:
            if self._kv.set(k, pending, ttl_s=self._ttl, nx=True):
                return None
            raw = self._kv.get(k)
        except KVUnavailable:
            raise ServiceUnavailable() from None
        if raw is None:  # expired in between: claim again
            return self.begin(scope, key, request_sha256)
        return IdempotencyRecord.from_json(raw)

    def complete(self, scope: str, key: str, record: IdempotencyRecord) -> None:
        try:
            self._kv.set(self._k(scope, key), record.to_json().encode(), ttl_s=self._ttl)
        except KVUnavailable:
            raise ServiceUnavailable() from None

    def abandon(self, scope: str, key: str) -> None:
        try:
            self._kv.delete(self._k(scope, key))
        except KVUnavailable:
            raise ServiceUnavailable() from None


def resolve_existing(existing: IdempotencyRecord, request_sha256: str) -> IdempotencyRecord:
    """Apply the replay rules to an existing record (raise or return it for replay)."""
    if existing.request_sha256 != request_sha256:
        raise ValidationFailed(
            [
                {
                    "field": "Idempotency-Key",
                    "code": "idempotency_key_reused",
                    "message_key": "errors.idempotency_key_reused",
                }
            ],
            detail="This Idempotency-Key was used for a different request.",
        )
    if existing.state != "completed":
        raise Conflict("The original request is still running.", code="idempotency_in_progress")
    return existing


def as_uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None
