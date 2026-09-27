"""Pydantic IO models for notifications (docs/09 §4 Notifications)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    """One notification, rendered in the reader's language (``Accept-Language``: en or te)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    template_key: str
    language: Literal["en", "te"]
    title: str
    body: str
    params: dict[str, Any]
    resource_type: str | None
    resource_id: uuid.UUID | None
    created_at: dt.datetime
    read_at: dt.datetime | None


class UnreadCountOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    count: int


class MarkedReadOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    updated: int
