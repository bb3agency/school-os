"""Idempotency-Key store (docs/09 §2; docs/16 §8).

Same key + same request hash -> replay the original status and resource (no response body is
stored, so no personal data is duplicated); same key + different hash -> 422
``idempotency_key_reused``; key still running -> 409 ``idempotency_in_progress``.

- Control plane: :class:`RedisIdempotencyStore` (Valkey, 24 h, per operator), because
  ``sos_platform`` cannot use ``ops.idempotency_keys``; :class:`InMemoryIdempotencyStore` for
  local development and tests.
- Tenant API: ``app.ops.service.begin_idempotent`` / ``complete_idempotent`` on
  ``ops.idempotency_keys`` inside the request's ``tenant_session``.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Literal, Protocol

import redis

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


class InMemoryIdempotencyStore:
    def __init__(
        self, *, ttl: timedelta = DEFAULT_TTL, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl.total_seconds()
        self._clock = clock
        self._data: dict[tuple[str, str], tuple[float, IdempotencyRecord]] = {}
        self._lock = threading.Lock()

    def begin(self, scope: str, key: str, request_sha256: str) -> IdempotencyRecord | None:
        with self._lock:
            current = self._clock()
            found = self._data.get((scope, key))
            if found is not None and found[0] > current:
                return found[1]
            self._data[(scope, key)] = (
                current + self._ttl,
                IdempotencyRecord(request_sha256, "in_progress"),
            )
            return None

    def complete(self, scope: str, key: str, record: IdempotencyRecord) -> None:
        with self._lock:
            self._data[(scope, key)] = (self._clock() + self._ttl, record)

    def abandon(self, scope: str, key: str) -> None:
        with self._lock:
            self._data.pop((scope, key), None)


class RedisIdempotencyStore:
    def __init__(
        self, client: redis.Redis, *, ttl: timedelta = DEFAULT_TTL, prefix: str = "sos:idem:"
    ) -> None:
        self._client = client
        self._ttl = int(ttl.total_seconds())
        self._prefix = prefix

    def _k(self, scope: str, key: str) -> str:
        return f"{self._prefix}{scope}:{key}"

    def begin(self, scope: str, key: str, request_sha256: str) -> IdempotencyRecord | None:
        try:
            claimed = self._client.set(
                self._k(scope, key),
                IdempotencyRecord(request_sha256, "in_progress").to_json(),
                nx=True,
                ex=self._ttl,
            )
            if claimed:
                return None
            raw = self._client.get(self._k(scope, key))
        except redis.exceptions.RedisError:
            raise ServiceUnavailable() from None
        if raw is None:  # expired between SET and GET: try once more
            return self.begin(scope, key, request_sha256)
        return IdempotencyRecord.from_json(raw)  # type: ignore[arg-type]

    def complete(self, scope: str, key: str, record: IdempotencyRecord) -> None:
        try:
            self._client.set(self._k(scope, key), record.to_json(), ex=self._ttl)
        except redis.exceptions.RedisError:
            raise ServiceUnavailable() from None

    def abandon(self, scope: str, key: str) -> None:
        try:
            self._client.delete(self._k(scope, key))
        except redis.exceptions.RedisError:
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
