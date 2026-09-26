"""Identifier helpers: UUIDv7 (time-ordered) for every entity (05 §1)."""

from __future__ import annotations

import uuid

import uuid_utils


def new_id() -> uuid.UUID:
    """Return a new UUIDv7 as a standard-library ``uuid.UUID``."""
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)
