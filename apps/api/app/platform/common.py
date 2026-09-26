"""Shared helpers for the control-plane services: config, audit, errors, time, paging."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel
from sqlalchemy import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.db import tenant_session
from app.core.errors import BadRequest
from app.core.logging import get_logger
from app.platform import repository as repo

IST = ZoneInfo("Asia/Kolkata")
MAX_PAGE = 200
log = get_logger(__name__)


@lru_cache(maxsize=1)
def config() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.platform").joinpath("billing.yaml").read_text("utf-8")
    )
    return raw


def billing_cfg() -> dict[str, Any]:
    section: dict[str, Any] = config()["billing"]
    return section


def fleet_cfg() -> dict[str, Any]:
    section: dict[str, Any] = config()["fleet"]
    return section


def support_cfg() -> dict[str, Any]:
    section: dict[str, Any] = config()["support"]
    return section


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def today_ist(at: dt.datetime | None = None) -> dt.date:
    return (at or now()).astimezone(IST).date()


def add_months(day: dt.date, months: int) -> dt.date:
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    # clamp the day (e.g. 31 Jan + 1 month -> 28/29 Feb)
    for d in (day.day, 30, 29, 28):
        try:
            return dt.date(year, month, d)
        except ValueError:
            continue
    raise ValueError("unreachable")  # pragma: no cover


def must[T](value: T | None) -> T:
    """A row that must exist (FK, or read moments ago in the same transaction)."""
    if value is None:
        raise RuntimeError("expected row is missing")
    return value


@contextmanager
def db_errors() -> Iterator[None]:
    """Translate expected PostgreSQL errors into RFC 9457 domain errors."""
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


@dataclass(frozen=True, slots=True)
class Actor:
    """Who performs a control-plane action: an operator or the system (jobs)."""

    operator_id: uuid.UUID | None
    request_id: str | None = None

    @property
    def actor_type(self) -> str:
        return "operator" if self.operator_id else "system"


SYSTEM = Actor(None)


def audit_platform(  # noqa: PLR0917 - (session, actor, action, type, id, summary) reads naturally
    session: Session,
    actor: Actor,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
    *,
    tenant_id: uuid.UUID | None = None,
) -> None:
    audit.record_platform(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="operator" if actor.operator_id else "system",
        actor_id=actor.operator_id,
        subject_tenant_id=tenant_id,
        request_id=actor.request_id,
    )


@contextmanager
def tenant_chain(
    tenant_id: uuid.UUID,
    actor: Actor,
    action: str,
    summary: Mapping[str, Any],
    *,
    enabled: bool = True,
) -> Iterator[None]:
    """Write ``action`` into the school's own audit chain (actor_type ``platform``).

    The tenant event is written first (validation fails early) and committed only after the
    wrapped control-plane transaction commits; if that transaction fails, the tenant event is
    rolled back with it. ``enabled=False`` (dedicated schools, whose rows live on their host)
    only runs the wrapped block.
    """
    if not enabled:
        yield
        return
    with tenant_session(tenant_id) as ts:
        audit.record(
            ts,
            action=action,
            resource_type="tenant",
            resource_id=tenant_id,
            summary=summary,
            actor_type="platform",
            actor_id=actor.operator_id,
            request_id=actor.request_id,
        )
        yield


def page[M: BaseModel](
    rows: Sequence[RowMapping], limit: int, model: type[M], key: str = "id"
) -> tuple[list[M], str | None]:
    items = [model.model_validate(dict(r)) for r in rows[:limit]]
    cursor = str(rows[limit - 1][key]) if len(rows) > limit > 0 else None
    return items, cursor


def clamp_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_PAGE:
        raise BadRequest(f"limit must be between 1 and {MAX_PAGE}", code="bad_limit")
    return limit


def parse_cursor(cursor: str | None) -> uuid.UUID | None:
    if cursor is None:
        return None
    try:
        return uuid.UUID(cursor)
    except ValueError as exc:
        raise BadRequest("The cursor is not valid.", code="bad_cursor") from exc
