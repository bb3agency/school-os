"""Bilingual announcements (FR-PLT-026; docs/16 §5.13, §14); English only while Telugu is
hidden (ADR-0036: the Telugu text is optional, kept, and not shown).

Delivery: ``sos_app`` cannot read ``platform.announcements``. A platform job publishes the
active announcements (no personal data) to a cache key; the school app reads the cache through
:func:`active_announcements`. Dedicated hosts receive them in the heartbeat response and write
the same cache key locally, so one reader serves both tiers.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from functools import lru_cache
from typing import Any, Protocol

import redis
from sqlalchemy import and_, select

from app.core.config import get_settings
from app.core.db import platform_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.languages import telugu_enabled
from app.core.logging import get_logger
from app.platform import models as m
from app.platform import repository as repo
from app.platform import two_person
from app.platform.common import (
    Actor,
    audit_platform,
    clamp_limit,
    db_errors,
    now,
    parse_cursor,
)
from app.platform.schemas import AnnouncementBrief, AnnouncementIn, AnnouncementOut

log = get_logger(__name__)
CACHE_KEY = "sos:announcements:active:v1"
CACHE_TTL_SECONDS = 15 * 60


class AnnouncementCache(Protocol):
    def put(self, payload: list[dict[str, Any]]) -> None: ...

    def get(self) -> list[dict[str, Any]] | None: ...


class InMemoryAnnouncementCache:
    def __init__(self) -> None:
        self._value: list[dict[str, Any]] | None = None
        self._lock = threading.Lock()

    def put(self, payload: list[dict[str, Any]]) -> None:
        with self._lock:
            self._value = list(payload)

    def get(self) -> list[dict[str, Any]] | None:
        with self._lock:
            return None if self._value is None else list(self._value)


class RedisAnnouncementCache:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def put(self, payload: list[dict[str, Any]]) -> None:
        self._client.set(CACHE_KEY, json.dumps(payload), ex=CACHE_TTL_SECONDS)

    def get(self) -> list[dict[str, Any]] | None:
        try:
            raw = self._client.get(CACHE_KEY)
        except redis.exceptions.RedisError:
            log.warning("platform.announcements.cache_unavailable", outcome="skipped")
            return None
        if raw is None:
            return None
        value: list[dict[str, Any]] = json.loads(str(raw) if not isinstance(raw, bytes) else raw)
        return value


@lru_cache(maxsize=1)
def get_cache() -> AnnouncementCache:
    settings = get_settings()
    if settings.is_production_like or settings.deployment_mode.value == "dedicated":
        client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_timeout=0.5, socket_connect_timeout=0.5
        )
        return RedisAnnouncementCache(client)
    return InMemoryAnnouncementCache()


MANAGE_PERMISSION = "platform.announcements.manage"


def _out(row: Any) -> AnnouncementOut:
    data = dict(row)
    data["approval_expires_at"] = (
        two_person.expires_at(data.get("submitted_at"))
        if data["status"] == "pending_approval"
        else None
    )
    out = AnnouncementOut.model_validate(data)
    if telugu_enabled():
        return out
    # English first (ADR-0036): the Telugu text stays stored but is not shown.
    return out.model_copy(update={"title_te": "", "body_te": ""})


def _brief(item: Any) -> AnnouncementBrief:
    """What a school sees: the Telugu text only while Telugu is shown (ADR-0036)."""
    brief = AnnouncementBrief.model_validate(item)
    if telugu_enabled():
        return brief
    return brief.model_copy(update={"title_te": "", "body_te": ""})


_TELUGU_FIELDS = (("title_te", "title_en"), ("body_te", "body_en"))


def _values(data: AnnouncementIn, row: Any = None) -> dict[str, Any]:
    """The columns to store. The database needs the Telugu text (NOT NULL, 1+ characters); while
    Telugu is hidden an empty one keeps the stored text on an update or takes the English text
    on a new announcement, and is never shown. While Telugu is shown it is required."""
    values = data.model_dump()
    missing = [te for te, _ in _TELUGU_FIELDS if not values[te].strip()]
    if missing and telugu_enabled():
        raise ValidationFailed(
            [{"field": f, "code": "missing", "message_key": "errors.missing"} for f in missing]
        )
    for te, en in _TELUGU_FIELDS:
        if te in missing:
            values[te] = row[te] if row is not None else values[en]
    return values


def _approval_values(actor: Actor, values: dict[str, Any]) -> dict[str, Any]:
    """Two-person rule for critical banners (audit 2026-10-05 hardening; owner decision
    2026-10-07): a critical announcement asked to go out (``scheduled``) waits as
    ``pending_approval`` until a second operator approves it. Any change clears an approval."""
    critical_out = values["severity"] == "critical" and values["status"] == "scheduled"
    if critical_out:
        return {
            "status": "pending_approval",
            "submitted_by": actor.operator_id,
            "submitted_at": now(),
            "approved_by": None,
            "approved_at": None,
        }
    return {"submitted_by": None, "submitted_at": None, "approved_by": None, "approved_at": None}


def has_ended(ends_at: dt.datetime, at: dt.datetime) -> bool:
    """Ended: the banner feed shows an announcement while ``starts_at <= at < ends_at``
    (:func:`_active_rows`, :func:`active_announcements`), so it has ended from ``ends_at`` on.
    Server time, UTC."""
    return ends_at <= at


def list_announcements(
    *, limit: int = 50, cursor: str | None = None
) -> tuple[list[AnnouncementOut], str | None]:
    """Newest first, keyset-paged on the UUIDv7 id (audit 2026-10-06 R-14: the list stopped at
    200 rows with no cursor, so older announcements were silently missing)."""
    limit = clamp_limit(limit)
    with platform_session() as s:
        rows = repo.list_rows(s, m.announcements, limit=limit, cursor=parse_cursor(cursor))
    items = [_out(r) for r in rows[:limit]]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get(announcement_id: uuid.UUID) -> AnnouncementOut:
    with platform_session() as s:
        row = repo.get(s, m.announcements, announcement_id)
    if row is None:
        raise NotFound("Announcement not found")
    return _out(row)


def create(actor: Actor, data: AnnouncementIn) -> AnnouncementOut:
    with platform_session() as s, db_errors():
        values = _values(data)
        values |= _approval_values(actor, values)
        row = repo.insert_row(
            s,
            m.announcements,
            {**values, "id": new_id(), "created_by": actor.operator_id},
        )
        audit_platform(
            s,
            actor,
            "announcement.created",
            "announcement",
            row["id"],
            {"audience": data.audience, "severity": data.severity, "status": row["status"]},
        )
        return _out(row)


def update(
    actor: Actor, announcement_id: uuid.UUID, data: AnnouncementIn, *, expected_version: int | None
) -> AnnouncementOut:
    with platform_session() as s, db_errors():
        row = repo.get(s, m.announcements, announcement_id, for_update=True)
        if row is None:
            raise NotFound("Announcement not found")
        if row["status"] == "cancelled":
            raise Conflict("A cancelled announcement cannot change.", code="invalid_state")
        if has_ended(row["ends_at"], now()):
            # Owner decision 2026-10-04: an ended announcement is read-only (docs/16 §5.13).
            raise Conflict("An announcement that has ended cannot change.", code="invalid_state")
        if expected_version is not None and row["version"] != expected_version:
            raise PreconditionFailed()
        values = _values(data, row)
        changed = sorted(k for k, v in values.items() if row[k] != v)
        unchanged_approved = (
            not changed and row["status"] == "scheduled" and row["approved_by"] is not None
        )
        if not unchanged_approved:
            values |= _approval_values(actor, values)
        row = repo.update_row(s, m.announcements, announcement_id, values)
        audit_platform(
            s,
            actor,
            "announcement.updated",
            "announcement",
            announcement_id,
            {"fields": changed, "status": row["status"]},
        )
        return _out(row)


def approve(actor: Actor, announcement_id: uuid.UUID) -> AnnouncementOut:
    """Second operator approves a critical announcement (``pending_approval`` -> ``scheduled``).

    409 ``invalid_state`` unless it is pending; ``same_operator`` for the operator who submitted
    it (DB CHECK too); ``request_expired``, ``requester_not_authorised`` and
    ``approver_not_eligible`` as for the other two-person flows (app.platform.two_person).
    Withdrawing is cancelling (``POST .../cancel``). Audit ``announcement.approved``."""
    with platform_session() as s, db_errors():
        row = repo.get(s, m.announcements, announcement_id, for_update=True)
        if row is None:
            raise NotFound("Announcement not found")
        if row["status"] != "pending_approval":
            raise Conflict(
                "Only an announcement waiting for approval can be approved.", code="invalid_state"
            )
        if has_ended(row["ends_at"], now()):
            raise Conflict("An announcement that has ended cannot change.", code="invalid_state")
        if row["submitted_by"] == actor.operator_id:
            raise Conflict("A different operator must approve.", code="same_operator")
        two_person.check_second_step(
            s,
            first_operator_id=row["submitted_by"],
            approver_id=actor.operator_id,
            permission=MANAGE_PERMISSION,
            requested_at=row["submitted_at"],
        )
        row = repo.update_row(
            s,
            m.announcements,
            announcement_id,
            {"status": "scheduled", "approved_by": actor.operator_id, "approved_at": now()},
        )
        audit_platform(
            s,
            actor,
            "announcement.approved",
            "announcement",
            announcement_id,
            {"severity": row["severity"], "audience": row["audience"]},
        )
        return _out(row)


def cancel(actor: Actor, announcement_id: uuid.UUID) -> AnnouncementOut:
    with platform_session() as s, db_errors():
        row = repo.get(s, m.announcements, announcement_id, for_update=True)
        if row is None:
            raise NotFound("Announcement not found")
        if row["status"] != "cancelled":
            if has_ended(row["ends_at"], now()):
                # Owner decision 2026-10-04: an ended announcement is fully read-only; a
                # cancelled one keeps answering with itself (cancelling twice is harmless).
                raise Conflict(
                    "An announcement that has ended cannot change.", code="invalid_state"
                )
            row = repo.update_row(s, m.announcements, announcement_id, {"status": "cancelled"})
            audit_platform(s, actor, "announcement.cancelled", "announcement", announcement_id, {})
        return _out(row)


def _visible_to(item: dict[str, Any], tenant_id: uuid.UUID, tier: str) -> bool:
    audience = item["audience"]
    if audience == "all":
        return True
    if audience == "tier":
        return bool(item["audience_tier"] == tier)
    return str(tenant_id) in item["audience_tenant_ids"]


def _active_rows(at: dt.datetime) -> list[dict[str, Any]]:
    with platform_session() as s:
        rows = s.execute(
            select(m.announcements).where(
                and_(
                    m.announcements.c.status == "scheduled",
                    m.announcements.c.ends_at > at,
                    # published a little ahead so the cache is ready when they start
                    m.announcements.c.starts_at <= at + dt.timedelta(minutes=5),
                )
            )
        ).mappings()
        return [
            {
                **AnnouncementBrief.model_validate(dict(r)).model_dump(mode="json"),
                "audience": r["audience"],
                "audience_tier": r["audience_tier"],
                "audience_tenant_ids": [str(t) for t in r["audience_tenant_ids"]],
            }
            for r in rows
        ]


def publish(cache: AnnouncementCache | None = None, *, at: dt.datetime | None = None) -> int:
    """Job body (every minute and after each change on the shared tier)."""
    items = _active_rows(at or now())
    (cache or get_cache()).put(items)
    return len(items)


def for_deployment(
    tenant_id: uuid.UUID, tier: str, *, at: dt.datetime | None = None
) -> list[AnnouncementBrief]:
    """Active announcements for one school, read from the database (heartbeat response)."""
    current = at or now()
    return [
        AnnouncementBrief.model_validate(item)
        for item in _active_rows(current)
        if _visible_to(item, tenant_id, tier)
        and dt.datetime.fromisoformat(item["starts_at"]) <= current
    ]


def active_announcements(
    tenant_id: uuid.UUID,
    tier: str,
    *,
    cache: AnnouncementCache | None = None,
    at: dt.datetime | None = None,
) -> list[AnnouncementBrief]:
    """School-side read (GET /api/v1/announcements): cached, filtered by audience and time."""
    current = at or now()
    items = (cache or get_cache()).get() or []
    result = []
    for item in items:
        starts = dt.datetime.fromisoformat(item["starts_at"])
        ends = dt.datetime.fromisoformat(item["ends_at"])
        if starts <= current < ends and _visible_to(item, tenant_id, tier):
            result.append(_brief(item))
    return result


def cache_from_heartbeat(
    items: list[AnnouncementBrief], cache: AnnouncementCache | None = None
) -> None:
    """Dedicated host: store the control plane's announcements (already filtered for it)."""
    (cache or get_cache()).put(
        [
            {
                **i.model_dump(mode="json"),
                "audience": "all",
                "audience_tier": None,
                "audience_tenant_ids": [],
            }
            for i in items
        ]
    )
