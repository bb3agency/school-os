"""Pydantic IO models for the school-side break-glass routes (docs/09 §4 Break-glass)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

GrantStatus = Literal["requested", "approved", "active", "expired", "revoked", "denied"]


class GrantOut(BaseModel):
    """A support-access request and, once decided, its grant.

    ``status``: requested (waiting for the school) · approved (emergency access confirmed by two
    SchoolOS staff, not yet usable) · active (support can read until ``expires_at``) · expired ·
    revoked (ended by the school) · denied. ``operator_display_name`` is the SchoolOS employee
    who asked; ``membership_id`` is their temporary access in this school.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    platform_request_id: uuid.UUID | None
    status: GrantStatus
    emergency: bool
    reason_code: str
    reason: str
    scope: dict[str, Any]
    duration_minutes: int | None
    operator_display_name: str | None
    requested_at: dt.datetime | None
    starts_at: dt.datetime | None
    expires_at: dt.datetime | None
    decided_at: dt.datetime | None
    revoked_at: dt.datetime | None
    approved_by_membership: uuid.UUID | None
    denied_by_membership: uuid.UUID | None
    revoked_by_membership: uuid.UUID | None
    membership_id: uuid.UUID | None
    created_at: dt.datetime
