"""HTTP conventions for tenant routes (docs/09 §2): cursor pagination, ETag/If-Match and
Idempotency-Key replay.

These live next to ``require()`` because every tenant route uses them together; they hold no
business logic. Idempotency records are stored in Valkey for 24 h keyed by tenant + user + route
+ key (in-process store locally and in CI), and written only after the transaction commits.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Final

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.authz.dependencies import get_user_context
from app.authz.kv import KVUnavailable, kv_store
from app.core.errors import BadRequest, Conflict, DomainError, ServiceUnavailable, ValidationFailed

MAX_LIMIT: Final = 200
DEFAULT_LIMIT: Final = 50
IDEMPOTENCY_HEADER: Final = "Idempotency-Key"
IDEMPOTENCY_TTL_S: Final = 24 * 3600
PENDING_TTL_S: Final = 60
_KEY_RE: Final = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_ETAG_RE: Final = re.compile(r'^(?:W/)?"(\d{1,9})"$')

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Page size (max 200).")]
Cursor = Annotated[str | None, Query(max_length=512, description="Opaque cursor from next_cursor.")]


class Page[ItemT](BaseModel):
    """Cursor page: ``{"data": [...], "next_cursor": "..." | null}``."""

    model_config = ConfigDict(frozen=True)

    data: list[ItemT]
    next_cursor: str | None


# --- cursors -------------------------------------------------------------------------------


def _bad_cursor() -> ValidationFailed:
    return ValidationFailed(
        [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
    )


def encode_cursor(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise _bad_cursor() from exc
    if not isinstance(value, dict):
        raise _bad_cursor()
    return value


def paginate[T](
    items: Sequence[T], *, key: Callable[[T], str], cursor: str | None, limit: int
) -> Page[T]:
    """Page an in-memory list (small collections such as classes or roles) by a stable key."""
    ordered = sorted(items, key=key)
    after = decode_cursor(cursor)
    if after is not None:
        last = after.get("k")
        if not isinstance(last, str):
            raise _bad_cursor()
        ordered = [i for i in ordered if key(i) > last]
    page = ordered[:limit]
    more = len(ordered) > limit
    return Page[T](data=page, next_cursor=encode_cursor({"k": key(page[-1])}) if more else None)


# --- ETag / If-Match -----------------------------------------------------------------------


def etag(version: int) -> str:
    return f'W/"{version}"'


def if_match_version(request: Request) -> int:
    """The version in ``If-Match`` (required on updates; mismatch -> 412 from the service)."""
    raw = request.headers.get("if-match")
    if raw is None:
        raise BadRequest(
            "Send If-Match with the ETag you last read, so changes are not overwritten.",
            code="if_match_required",
        )
    match = _ETAG_RE.match(raw.strip())
    if match is None:
        raise BadRequest('If-Match must be a single ETag such as W/"3".', code="invalid_if_match")
    return int(match.group(1))


IfMatch = Annotated[int, Depends(if_match_version)]


def optional_if_match_version(request: Request) -> int | None:
    """The version in ``If-Match`` when sent, else None. For updates whose callers do not send it
    yet (backward compatibility); when it is sent it is honoured (412 when stale)."""
    if request.headers.get("if-match") is None:
        return None
    return if_match_version(request)


OptionalIfMatch = Annotated[int | None, Depends(optional_if_match_version)]


# --- idempotency ---------------------------------------------------------------------------


class IdempotencyKeyReused(DomainError):
    status, code, title = 422, "idempotency_key_reused", "Idempotency key used with another body"


def _grants(ctx: UserContext) -> str:
    """Fingerprint of what the caller may do (roles, permissions and scopes): a stored response
    is replayed only to the same access (audit 2026-10-05 hardening "Idempotency")."""
    data = {
        "m": str(ctx.membership_id),
        "r": sorted(ctx.roles),
        "p": sorted(ctx.permissions),
        "sp": sorted(ctx.scoped_permissions),
        "school": ctx.scopes.school,
        "c": sorted(str(i) for i in ctx.scopes.class_ids),
        "s": sorted(str(i) for i in ctx.scopes.section_ids),
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _body_hash(body: BaseModel | None) -> str:
    data = body.model_dump(mode="json") if body is not None else None
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Idempotency:
    """Replays the first response for a repeated ``Idempotency-Key`` (docs/09 §2).

    Same key + same body -> original status, body and Location; same key + different body ->
    422 ``idempotency_key_reused``; first request still running -> 409 ``idempotency_in_progress``;
    the caller's roles, permissions or scopes changed since the first request -> 409
    ``idempotency_access_changed`` (the stored body is not returned and nothing is run again).
    """

    def __init__(self, request: Request, ctx: UserContext) -> None:
        raw = request.headers.get(IDEMPOTENCY_HEADER)
        if raw is not None and not _KEY_RE.match(raw):
            raise BadRequest(
                "Idempotency-Key must be 8-128 letters, digits, '-' or '_'.",
                code="invalid_idempotency_key",
            )
        self.key: str | None = None
        if raw is not None:
            scope = hashlib.sha256(f"{request.method} {request.url.path} {raw}".encode())
            self.key = f"sos:idem:{ctx.tenant_id}:{ctx.user_id}:{scope.hexdigest()}"
        self.grants = _grants(ctx)

    @staticmethod
    def _response(
        status: int, body: Any, headers: dict[str, str], replayed: bool = False
    ) -> JSONResponse:
        out = dict(headers)
        if replayed:
            out["Idempotent-Replayed"] = "true"
        return JSONResponse(body, status_code=status, headers=out)

    def _replay(self, raw: bytes, body_hash: str) -> JSONResponse:
        record = json.loads(raw)
        if record.get("hash") != body_hash:
            raise IdempotencyKeyReused(
                "This Idempotency-Key was already used for a different request."
            )
        if record.get("state") != "done":
            raise Conflict(
                "The first request with this Idempotency-Key is still running.",
                code="idempotency_in_progress",
            )
        if record.get("grants") != self.grants:
            raise Conflict(
                "Your access changed since this request was first sent. Reload the page to see "
                "the current state before trying again.",
                code="idempotency_access_changed",
            )
        return self._response(record["status"], record["body"], record["headers"], replayed=True)

    def run[M: BaseModel](
        self,
        db: Session,
        body: BaseModel | None,
        operation: Callable[[], M],
        *,
        status_code: int = 201,
        headers: Callable[[M], dict[str, str]] | None = None,
    ) -> JSONResponse:
        if self.key is None:
            result = operation()
            return self._response(
                status_code, result.model_dump(mode="json"), headers(result) if headers else {}
            )
        key, store, body_hash = self.key, kv_store(), _body_hash(body)
        try:
            existing = store.get(key)
            if existing is not None:
                return self._replay(existing, body_hash)
            pending = json.dumps({"state": "pending", "hash": body_hash}).encode()
            if not store.set(key, pending, ttl_s=PENDING_TTL_S, nx=True):
                again = store.get(key)
                if again is None:
                    raise Conflict("Please retry.", code="idempotency_in_progress")
                return self._replay(again, body_hash)
        except KVUnavailable as exc:
            raise ServiceUnavailable() from exc

        def release(_: Session) -> None:
            # On failure the pending marker simply expires after PENDING_TTL_S.
            with contextlib.suppress(KVUnavailable):
                store.delete(key)

        event.listen(db, "after_rollback", release, once=True)
        try:
            result = operation()
        except BaseException:
            release(db)
            raise
        payload = result.model_dump(mode="json")
        extra = headers(result) if headers else {}
        record = json.dumps(
            {
                "state": "done",
                "hash": body_hash,
                "grants": self.grants,
                "status": status_code,
                "body": payload,
                "headers": extra,
            }
        ).encode()

        def store_result(_: Session) -> None:
            # On failure a retry re-runs the operation and meets the unique constraints.
            with contextlib.suppress(KVUnavailable):
                store.set(key, record, ttl_s=IDEMPOTENCY_TTL_S)

        event.listen(db, "after_commit", store_result, once=True)
        return self._response(status_code, payload, extra)


def get_idempotency(
    request: Request, ctx: Annotated[UserContext, Depends(get_user_context)]
) -> Idempotency:
    return Idempotency(request, ctx)


IdempotencyDep = Annotated[Idempotency, Depends(get_idempotency)]
