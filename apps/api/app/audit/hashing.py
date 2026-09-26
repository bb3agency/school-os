"""Canonical event hashing (FR-AUD-003, SEC-007).

``hash = sha256(prev_hash || rfc8785(event_dict))`` where ``event_dict`` holds every stored
column except the hashes, UUIDs as lowercase strings, ``occurred_at`` as RFC 3339 UTC with
microseconds and a ``Z`` suffix, and ``ip_hash`` as lowercase hex (or null). The genesis
``prev_hash`` is 32 zero bytes. RFC 8785 (JSON Canonicalization Scheme) makes the byte
encoding independent of key order and whitespace, so any verifier can recompute it.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import rfc8785

from app.audit.schemas import ZERO_HASH, JsonValue

__all__ = [
    "ZERO_HASH",
    "canonical_bytes",
    "chain_hash",
    "format_timestamp",
    "platform_event_dict",
    "tenant_event_dict",
]


def format_timestamp(value: datetime) -> str:
    """RFC 3339 UTC with microseconds, e.g. ``2026-09-26T04:05:06.000123Z``."""
    if value.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uuid(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value).lower()


def _hex(value: bytes | None) -> str | None:
    return None if value is None else bytes(value).hex()


def tenant_event_dict(row: Mapping[Any, Any]) -> dict[str, JsonValue]:
    """The hashed view of a tenant event (``row`` = stored columns)."""
    return {
        "id": _uuid(row["id"]),
        "tenant_id": _uuid(row["tenant_id"]),
        "seq": int(row["seq"]),
        "occurred_at": format_timestamp(row["occurred_at"]),
        "actor_type": row["actor_type"],
        "actor_id": _uuid(row["actor_id"]),
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": _uuid(row["resource_id"]),
        "summary": row["summary"],
        "request_id": row["request_id"],
        "ip_hash": _hex(row["ip_hash"]),
    }


def platform_event_dict(row: Mapping[Any, Any]) -> dict[str, JsonValue]:
    """The hashed view of a control-plane event."""
    return {
        "id": _uuid(row["id"]),
        "seq": int(row["seq"]),
        "occurred_at": format_timestamp(row["occurred_at"]),
        "actor_type": row["actor_type"],
        "actor_id": _uuid(row["actor_id"]),
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": _uuid(row["resource_id"]),
        "subject_tenant_id": _uuid(row["subject_tenant_id"]),
        "summary": row["summary"],
        "request_id": row["request_id"],
        "ip_hash": _hex(row["ip_hash"]),
    }


def canonical_bytes(value: Mapping[str, JsonValue]) -> bytes:
    """RFC 8785 canonical JSON encoding."""
    encoded: bytes = rfc8785.dumps(dict(value))
    return encoded


def chain_hash(prev_hash: bytes, event: Mapping[str, JsonValue]) -> bytes:
    if len(prev_hash) != 32:
        raise ValueError("prev_hash must be 32 bytes")
    return hashlib.sha256(bytes(prev_hash) + canonical_bytes(event)).digest()
