"""SchoolOS's own incident records (R3-1B; ADR-0050 decision 1; FR-DPDP-030, FR-DPDP-031;
US-2801; docs/16 §5.20).

The control plane keeps one record per suspected security incident: T0 (``opened_at``, when it
was noticed), severity, status, a summary of **categories and counts only** (refused when it
holds a run of 6 or more digits, an e-mail address or more than 500 characters; the DB CHECK
repeats the rule), the CERT-In flag and report time (clock T0 + 6 h) and the lead operator. Per
affected school, ``platform.incident_schools`` holds data-category codes, an approximate count of
people and the time window; never a name, number or record (invariant 1, ADR-0013).

Two different operators holding ``platform.incidents.manage`` confirm the list of affected
schools (SEC-029 rules of :mod:`app.platform.two_person`: 409 ``same_operator``,
``request_expired``, ``requester_not_authorised``, ``approver_not_eligible``); adding a school
after the first confirmation clears it, and the list is frozen after the second. Only then can
schools see the notice: each school's worker pulls it through :mod:`app.platform.service`
(:func:`notices_for_school`, then :func:`mark_notice_pulled`, which records when the school was
notified; clock confirmation + 24 h). A dedicated host has no pull: the operator records the
phone or e-mail notice with ``notified_tenant_ids``. The control plane never writes tenant
tables (ADR-0020). Every change is audited in the platform chain in the same transaction.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.core.db import platform_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.platform import models as m
from app.platform import repository as repo
from app.platform import two_person
from app.platform.common import (
    Actor,
    audit_platform,
    clamp_limit,
    db_errors,
    must,
    now,
    parse_cursor,
)

MANAGE: Final = "platform.incidents.manage"
CERT_IN_HOURS: Final = 6
SCHOOL_NOTICE_HOURS: Final = 24
_DIGIT_RUN: Final = re.compile(r"\d{6,}")
_EMAIL: Final = re.compile(r"@")

Severity = Literal["P1", "P2", "P3", "P4"]
IncidentStatus = Literal["open", "contained", "resolved", "closed"]
Category = Literal[
    "identity",
    "contact",
    "aadhaar_partial",
    "academic",
    "attendance",
    "financial",
    "documents",
    "insights",
    "staff",
    "other",
]


def check_summary(value: str) -> str:
    """Categories and counts only (FR-DPDP-030): no 6+ digit runs, no e-mail addresses."""
    text = " ".join(value.split())
    if _DIGIT_RUN.search(text) or _EMAIL.search(text):
        raise ValueError(
            "Write categories and counts only: no phone, Aadhaar or other long numbers and no "
            "e-mail addresses."
        )
    return text


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class IncidentIn(_In):
    """Open an incident record. ``summary``: what kind of data and roughly how much (no names,
    numbers, e-mail addresses or record text)."""

    opened_at: dt.datetime = Field(description="T0: when SchoolOS noticed the incident.")
    severity: Severity
    summary: str = Field(min_length=10, max_length=500)
    cert_in_required: bool = False
    lead_operator_id: uuid.UUID | None = Field(
        default=None, description="Defaults to the operator opening the record."
    )

    @field_validator("summary")
    @classmethod
    def _clean(cls, value: str) -> str:
        return check_summary(value)


class IncidentPatch(_In):
    severity: Severity | None = None
    status: IncidentStatus | None = None
    summary: str | None = Field(default=None, min_length=10, max_length=500)
    cert_in_required: bool | None = None
    cert_in_reported_at: dt.datetime | None = None
    lead_operator_id: uuid.UUID | None = None
    notified_tenant_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=500,
        description="Schools told by phone or e-mail (dedicated hosts have no pull): records "
        "when the school was notified.",
    )

    @field_validator("summary")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return None if value is None else check_summary(value)


class IncidentSchoolIn(_In):
    tenant_id: uuid.UUID
    categories: list[Category] = Field(min_length=1, max_length=10)
    approx_people: int = Field(ge=0, le=10_000_000)
    window_from: dt.datetime | None = None
    window_to: dt.datetime | None = None

    @model_validator(mode="after")
    def _window(self) -> IncidentSchoolIn:
        if self.window_from and self.window_to and self.window_from > self.window_to:
            raise ValueError("window_from must not be after window_to")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("categories must not repeat")
        return self


class IncidentSchoolOut(_Out):
    tenant_id: uuid.UUID
    school_name: str | None
    tenant_code: str | None
    mode: str | None
    categories: list[str]
    approx_people: int
    window_from: dt.datetime | None
    window_to: dt.datetime | None
    school_notified_at: dt.datetime | None
    pulled_at: dt.datetime | None
    notice_due_at: dt.datetime | None = Field(
        description="Confirmation + 24 h while the school has not been notified."
    )


class IncidentOut(_Out):
    id: uuid.UUID
    opened_at: dt.datetime
    severity: Severity
    status: IncidentStatus
    summary: str
    cert_in_required: bool
    cert_in_reported_at: dt.datetime | None
    cert_in_due_at: dt.datetime | None = Field(
        description="T0 + 6 h while CERT-In is required and not yet reported."
    )
    lead_operator_id: uuid.UUID
    created_by: uuid.UUID
    confirmed_by: uuid.UUID | None
    first_confirmed_at: dt.datetime | None
    second_confirmed_by: uuid.UUID | None
    confirmed_at: dt.datetime | None
    confirm_by: dt.datetime | None = Field(
        description="When the first confirmation expires without a second one."
    )
    schools_count: int
    schools_notified: int
    created_at: dt.datetime | None
    version: int


class IncidentDetailOut(IncidentOut):
    schools: list[IncidentSchoolOut]


class SchoolIncidentNotice(BaseModel):
    """What a school's worker pulls (codes and counts only)."""

    model_config = ConfigDict(frozen=True)

    incident_id: uuid.UUID
    severity: str
    summary: str
    opened_at: dt.datetime
    confirmed_at: dt.datetime
    categories: list[str]
    approx_people: int
    window_from: dt.datetime | None
    window_to: dt.datetime | None


# --- shaping ----------------------------------------------------------------------------------


def _school_counts(s: Any, incident_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    if not incident_ids:
        return {}
    t = m.incident_schools
    stmt = (
        select(t.c.incident_id, func.count(), func.count(t.c.school_notified_at))
        .where(t.c.incident_id.in_(incident_ids))
        .group_by(t.c.incident_id)
    )
    return {i: (int(a), int(b)) for i, a, b in s.execute(stmt)}


def _out(row: Any, counts: tuple[int, int]) -> dict[str, Any]:
    data = dict(row)
    data["cert_in_due_at"] = (
        data["opened_at"] + dt.timedelta(hours=CERT_IN_HOURS)
        if data["cert_in_required"] and data["cert_in_reported_at"] is None
        else None
    )
    waiting = data["confirmed_by"] is not None and data["confirmed_at"] is None
    data["confirm_by"] = two_person.expires_at(data["first_confirmed_at"]) if waiting else None
    data["schools_count"], data["schools_notified"] = counts
    return data


def _schools(s: Any, incident_id: uuid.UUID, confirmed_at: dt.datetime | None) -> list[Any]:
    t, d = m.incident_schools, m.deployments
    stmt = (
        select(t, d.c.school_name, d.c.tenant_code, d.c.mode)
        .select_from(t.outerjoin(d, d.c.tenant_id == t.c.tenant_id))
        .where(t.c.incident_id == incident_id)
        .order_by(d.c.school_name, t.c.tenant_id)
    )
    out = []
    for row in s.execute(stmt).mappings():
        data = dict(row)
        data["notice_due_at"] = (
            confirmed_at + dt.timedelta(hours=SCHOOL_NOTICE_HOURS)
            if confirmed_at is not None and data["school_notified_at"] is None
            else None
        )
        out.append(IncidentSchoolOut.model_validate(data))
    return out


def _detail(s: Any, row: Any) -> IncidentDetailOut:
    counts = _school_counts(s, [row["id"]]).get(row["id"], (0, 0))
    data = _out(row, counts)
    data["schools"] = _schools(s, row["id"], row["confirmed_at"])
    return IncidentDetailOut.model_validate(data)


def _locked(s: Any, incident_id: uuid.UUID) -> Any:
    row = repo.get(s, m.incidents, incident_id, for_update=True)
    if row is None:
        raise NotFound("Incident not found")
    return row


def _operator_ok(s: Any, operator_id: uuid.UUID) -> None:
    op = repo.get(s, m.operators, operator_id)
    if op is None or op["status"] != "active":
        raise ValidationFailed(
            [{"field": "lead_operator_id", "code": "not_found", "message_key": "errors.not_found"}]
        )


def _not_future(value: dt.datetime, field: str) -> None:
    if value > now() + dt.timedelta(minutes=5):
        raise ValidationFailed(
            [{"field": field, "code": "date_in_future", "message_key": "errors.date_in_future"}]
        )


# --- operator actions -------------------------------------------------------------------------


def list_incidents(
    *, limit: int = 50, cursor: str | None = None
) -> tuple[list[IncidentOut], str | None]:
    """Newest first, keyset-paged on the UUIDv7 id."""
    limit = clamp_limit(limit)
    with platform_session() as s:
        rows = repo.list_rows(s, m.incidents, limit=limit, cursor=parse_cursor(cursor))
        counts = _school_counts(s, [r["id"] for r in rows[:limit]])
        items = [
            IncidentOut.model_validate(_out(r, counts.get(r["id"], (0, 0)))) for r in rows[:limit]
        ]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get_incident(incident_id: uuid.UUID) -> IncidentDetailOut:
    with platform_session() as s:
        row = repo.get(s, m.incidents, incident_id)
        if row is None:
            raise NotFound("Incident not found")
        return _detail(s, row)


def create_incident(actor: Actor, data: IncidentIn) -> IncidentDetailOut:
    """Open an incident record (FR-DPDP-030). Audit ``incident.opened`` (severity, CERT-In flag;
    never the summary)."""
    _not_future(data.opened_at, "opened_at")
    operator_id = must(actor.operator_id)
    lead = data.lead_operator_id or operator_id
    with platform_session() as s, db_errors():
        _operator_ok(s, lead)
        row = repo.insert_row(
            s,
            m.incidents,
            {
                "id": new_id(),
                "opened_at": data.opened_at,
                "severity": data.severity,
                "status": "open",
                "summary": data.summary,
                "cert_in_required": data.cert_in_required,
                "lead_operator_id": lead,
                "created_by": operator_id,
            },
        )
        audit_platform(
            s,
            actor,
            "incident.opened",
            "incident",
            row["id"],
            {"severity": data.severity, "cert_in_required": data.cert_in_required},
        )
        return _detail(s, row)


def update_incident(
    actor: Actor, incident_id: uuid.UUID, data: IncidentPatch, *, expected_version: int
) -> IncidentDetailOut:
    """Change status, severity, summary, CERT-In fields or the lead, and record schools told by
    phone or e-mail (``If-Match``). Audit ``incident.updated`` (field names and codes)."""
    with platform_session() as s, db_errors():
        row = _locked(s, incident_id)
        if row["version"] != expected_version:
            raise PreconditionFailed("Someone else changed this incident. Reload it.")
        values: dict[str, Any] = {}
        for field in ("severity", "status", "summary", "cert_in_required", "lead_operator_id"):
            value = getattr(data, field)
            if value is not None and value != row[field]:
                values[field] = value
        if "lead_operator_id" in values:
            _operator_ok(s, values["lead_operator_id"])
        if data.cert_in_reported_at is not None:
            _not_future(data.cert_in_reported_at, "cert_in_reported_at")
            if not values.get("cert_in_required", row["cert_in_required"]):
                raise Conflict(
                    "Mark the incident as reportable to CERT-In first.", code="cert_in_not_required"
                )
            values["cert_in_reported_at"] = data.cert_in_reported_at
        notified = 0
        if data.notified_tenant_ids:
            if row["confirmed_at"] is None:
                raise Conflict(
                    "Two operators must confirm the affected schools before any school is told.",
                    code="not_confirmed",
                )
            t = m.incident_schools
            for tenant_id in dict.fromkeys(data.notified_tenant_ids):
                school = repo.get_by(
                    s, t, t.c.incident_id == incident_id, t.c.tenant_id == tenant_id
                )
                if school is None:
                    raise ValidationFailed(
                        [
                            {
                                "field": "notified_tenant_ids",
                                "code": "not_found",
                                "message_key": "errors.not_found",
                            }
                        ]
                    )
                if school["school_notified_at"] is None:
                    repo.update_row(
                        s, t, school["id"], {"school_notified_at": now()}, bump_version=False
                    )
                    notified += 1
        if values or notified:
            values["updated_at"] = now()
            row = repo.update_row(s, m.incidents, incident_id, values)
            audit_platform(
                s,
                actor,
                "incident.updated",
                "incident",
                incident_id,
                {
                    "fields": sorted(k for k in values if k != "updated_at"),
                    "status": row["status"],
                    "severity": row["severity"],
                    "schools_notified": notified,
                },
            )
        return _detail(s, row)


def add_school(actor: Actor, incident_id: uuid.UUID, data: IncidentSchoolIn) -> IncidentDetailOut:
    """Mark a school as affected (categories, approximate count, window; FR-DPDP-030). Not after
    the second confirmation (409 ``incident_confirmed``); after the first one, it clears that
    confirmation so both operators confirm the new list. Audit ``incident.school_added``."""
    with platform_session() as s, db_errors():
        row = _locked(s, incident_id)
        if row["confirmed_at"] is not None:
            raise Conflict(
                "Two operators already confirmed the affected schools. Open a follow-up "
                "incident for another school.",
                code="incident_confirmed",
            )
        if repo.get_by(s, m.deployments, m.deployments.c.tenant_id == data.tenant_id) is None:
            raise NotFound("School not found")
        t = m.incident_schools
        existing = repo.get_by(
            s, t, t.c.incident_id == incident_id, t.c.tenant_id == data.tenant_id, for_update=True
        )
        values = {
            "categories": list(data.categories),
            "approx_people": data.approx_people,
            "window_from": data.window_from,
            "window_to": data.window_to,
        }
        if existing is None:
            repo.insert_row(
                s,
                t,
                {"id": new_id(), "incident_id": incident_id, "tenant_id": data.tenant_id} | values,
            )
        else:
            repo.update_row(s, t, existing["id"], values, bump_version=False)
        reset: dict[str, Any] = {"updated_at": now()}
        if row["confirmed_by"] is not None:
            reset |= {"confirmed_by": None, "first_confirmed_at": None}
        row = repo.update_row(s, m.incidents, incident_id, reset)
        audit_platform(
            s,
            actor,
            "incident.school_added",
            "incident",
            incident_id,
            {
                "categories": sorted(data.categories),
                "approx_people": data.approx_people,
                "confirmation_cleared": "confirmed_by" in reset,
            },
            tenant_id=data.tenant_id,
        )
        return _detail(s, row)


def confirm(actor: Actor, incident_id: uuid.UUID) -> IncidentDetailOut:
    """First call records confirmer 1; a second, different operator completes the confirmation
    (FR-DPDP-031). 409 ``no_schools``, ``same_operator``, ``incident_confirmed`` and the
    two-person checks of :mod:`app.platform.two_person`. Audit ``incident.confirmed``."""
    with platform_session() as s, db_errors():
        row = _locked(s, incident_id)
        if row["confirmed_at"] is not None:
            raise Conflict("The affected schools are already confirmed.", code="incident_confirmed")
        t = m.incident_schools
        count = s.execute(
            select(func.count()).select_from(t).where(t.c.incident_id == incident_id)
        ).scalar_one()
        if not count:
            raise Conflict("Add the affected schools before confirming.", code="no_schools")
        if row["confirmed_by"] is None:
            values: dict[str, Any] = {
                "confirmed_by": actor.operator_id,
                "first_confirmed_at": now(),
            }
            step = 1
        elif row["confirmed_by"] == actor.operator_id:
            raise Conflict(
                "A different operator must give the second confirmation.", code="same_operator"
            )
        else:
            two_person.check_second_step(
                s,
                first_operator_id=row["confirmed_by"],
                approver_id=actor.operator_id,
                permission=MANAGE,
                requested_at=row["first_confirmed_at"],
            )
            values = {"second_confirmed_by": actor.operator_id, "confirmed_at": now()}
            step = 2
        values["updated_at"] = now()
        row = repo.update_row(s, m.incidents, incident_id, values)
        audit_platform(
            s,
            actor,
            "incident.confirmed",
            "incident",
            incident_id,
            {"confirmation": step, "schools": int(count)},
        )
        return _detail(s, row)


# --- the school-side pull (through app.platform.service) ---------------------------------------


def notices_for_school(tenant_id: uuid.UUID) -> list[SchoolIncidentNotice]:
    """Confirmed notices this school's worker has not stored yet (codes and counts only)."""
    i, t = m.incidents, m.incident_schools
    stmt = (
        select(
            i.c.id.label("incident_id"),
            i.c.severity,
            i.c.summary,
            i.c.opened_at,
            i.c.confirmed_at,
            t.c.categories,
            t.c.approx_people,
            t.c.window_from,
            t.c.window_to,
        )
        .join(i, i.c.id == t.c.incident_id)
        .where(t.c.tenant_id == tenant_id, t.c.pulled_at.is_(None), i.c.confirmed_at.is_not(None))
        .order_by(i.c.confirmed_at, i.c.id)
        .limit(50)
    )
    with platform_session() as s:
        return [SchoolIncidentNotice.model_validate(dict(r)) for r in s.execute(stmt).mappings()]


def mark_notice_pulled(tenant_id: uuid.UUID, incident_id: uuid.UUID) -> bool:
    """The school stored the notice: record when (and, if not yet, that the school was notified
    then). Idempotent; False when nothing changed or the incident is not this school's. Audit
    ``incident.school_notified`` in the platform chain (ids only)."""
    t = m.incident_schools
    with platform_session() as s, db_errors():
        row = repo.get_by(
            s, t, t.c.incident_id == incident_id, t.c.tenant_id == tenant_id, for_update=True
        )
        if row is None or row["pulled_at"] is not None:
            return False
        at = now()
        values: dict[str, Any] = {"pulled_at": at}
        if row["school_notified_at"] is None:
            values["school_notified_at"] = at
        repo.update_row(s, t, row["id"], values, bump_version=False)
        audit_platform(
            s,
            Actor(None),
            "incident.school_notified",
            "incident",
            incident_id,
            {"source": "school_pull"},
            tenant_id=tenant_id,
        )
        return True


__all__ = [
    "IncidentDetailOut",
    "IncidentIn",
    "IncidentOut",
    "IncidentPatch",
    "IncidentSchoolIn",
    "SchoolIncidentNotice",
    "add_school",
    "check_summary",
    "confirm",
    "create_incident",
    "get_incident",
    "list_incidents",
    "mark_notice_pulled",
    "notices_for_school",
    "update_incident",
]
