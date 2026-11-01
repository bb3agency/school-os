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

A ``closed`` incident is locked (D48): PATCH, adding a school and confirming answer 409
``incident_closed``. Reopening is a two-person action: one operator requests it with a reason
(the summary's rule: categories and counts only), a different operator confirms it with the same
SEC-029 checks as the incident's own confirmation; the request can be withdrawn. The confirmation
sets the status back to ``open``. Audit ``incident.reopen_requested``, ``incident.reopened`` and
``incident.reopen_withdrawn`` (never the reason text).

Closing is gated (D49 part 1): PATCH ``status: closed`` answers 409
``incident_cert_in_unreported`` while CERT-In is required and its report time is not recorded,
and 409 ``incident_school_not_notified`` while a school of a confirmed incident has not been
notified (pulled, or recorded by hand). The same request may record what is missing.
``close_blocked_by`` on every incident lists the codes a close would meet now.

A confirmed reopen counts up ``reopen_count`` (D49 part 3). Each affected school whose
``reopen_pulled`` is behind it gets the notice again on its next pull (``reopen_seq``); the
school side records the reopen on its breach (``app.privacy.breach_sync``) and reports it with
:func:`mark_reopen_pulled` (audit ``incident.school_reopen_pulled``). A dedicated host has no
pull: the operator tells it by phone or e-mail and records that with :func:`record_reopen_told`
(audit ``incident.school_reopen_told``), which counts as the school's pull.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any, Final, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import func, or_, select

from app.core.db import platform_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.redaction import contains_full_aadhaar
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
CLOSE_CERT_IN: Final = "incident_cert_in_unreported"
CLOSE_SCHOOLS: Final = "incident_school_not_notified"
SCHOOL_NOTICE_HOURS: Final = 24
_DIGIT_RUN: Final = re.compile(r"\d{6,}")
_EMAIL: Final = re.compile(r"@")

Severity = Literal["P1", "P2", "P3", "P4"]
IncidentStatus = Literal["open", "contained", "resolved", "closed"]
CloseBlocker = Literal["incident_cert_in_unreported", "incident_school_not_notified"]
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
    if _DIGIT_RUN.search(text) or _EMAIL.search(text) or contains_full_aadhaar(text):
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

    opened_at: AwareDatetime = Field(description="T0: when SchoolOS noticed the incident.")
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
    cert_in_reported_at: AwareDatetime | None = None
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


class IncidentReopenIn(_In):
    """Ask to reopen a closed incident (D48). ``reason``: why, in categories and counts only (no
    names, numbers, e-mail addresses or record text)."""

    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason")
    @classmethod
    def _clean(cls, value: str) -> str:
        return check_summary(value)


class IncidentReopenToldIn(_In):
    """A dedicated-host school was told of reopen ``reopen_seq`` by phone or e-mail (it has no
    pull; D49). ``note``: how it was told, in the summary's rule (no names, long numbers,
    e-mail addresses or Aadhaar numbers)."""

    tenant_id: uuid.UUID
    reopen_seq: int = Field(ge=1, description="The incident's reopen count the school was told of.")
    note: str = Field(min_length=10, max_length=200)

    @field_validator("note")
    @classmethod
    def _clean(cls, value: str) -> str:
        return check_summary(value)


class IncidentSchoolIn(_In):
    tenant_id: uuid.UUID
    categories: list[Category] = Field(min_length=1, max_length=10)
    approx_people: int = Field(ge=0, le=10_000_000)
    window_from: AwareDatetime | None = None
    window_to: AwareDatetime | None = None

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
    reopen_pulled: int = Field(
        description="The incident's reopen count this school last stored (D49)."
    )
    reopen_pulled_at: dt.datetime | None


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
    reopen_requested_by: uuid.UUID | None = Field(
        default=None, description="The operator asking to reopen this closed incident (D48)."
    )
    reopen_requested_at: dt.datetime | None = None
    reopen_reason: str | None = None
    reopen_confirm_by: dt.datetime | None = Field(
        default=None, description="When the reopen request expires without a confirmation."
    )
    reopen_count: int = Field(
        description="How many times two operators reopened this incident (D49)."
    )
    last_reopened_at: dt.datetime | None
    close_blocked_by: list[CloseBlocker] = Field(
        description="Why closing would be refused now (D49): CERT-In not reported, or a school "
        "of the confirmed incident not notified. Empty when it can be closed.",
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
    reopen_seq: int = Field(
        default=0, description="The incident's reopen count; 0 when it was never reopened (D49)."
    )
    reopened_at: dt.datetime | None = None


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
    data["reopen_confirm_by"] = two_person.expires_at(data.get("reopen_requested_at"))
    data["schools_count"], data["schools_notified"] = counts
    data["close_blocked_by"] = _close_blockers(
        cert_in_required=data["cert_in_required"],
        cert_in_reported_at=data["cert_in_reported_at"],
        confirmed_at=data["confirmed_at"],
        unnotified=counts[0] - counts[1],
    )
    return data


def _close_blockers(
    *,
    cert_in_required: bool,
    cert_in_reported_at: dt.datetime | None,
    confirmed_at: dt.datetime | None,
    unnotified: int,
) -> list[str]:
    """D49 part 1: what a close would meet. Schools count only once two operators confirmed
    them (an unconfirmed list was never sent to anyone)."""
    out = []
    if cert_in_required and cert_in_reported_at is None:
        out.append(CLOSE_CERT_IN)
    if confirmed_at is not None and unnotified > 0:
        out.append(CLOSE_SCHOOLS)
    return out


def _check_close(s: Any, incident_id: uuid.UUID, row: Any, values: dict[str, Any]) -> None:
    """Refuse closing while CERT-In or a school is still owed a notice (D49 part 1)."""
    counts = _school_counts(s, [incident_id]).get(incident_id, (0, 0))
    blockers = _close_blockers(
        cert_in_required=values.get("cert_in_required", row["cert_in_required"]),
        cert_in_reported_at=values.get("cert_in_reported_at", row["cert_in_reported_at"]),
        confirmed_at=row["confirmed_at"],
        unnotified=counts[0] - counts[1],
    )
    if CLOSE_CERT_IN in blockers:
        raise Conflict(
            "CERT-In has not been told yet. Record when SchoolOS reported the incident to "
            "CERT-In, or mark it as not reportable, before closing.",
            code=CLOSE_CERT_IN,
        )
    if CLOSE_SCHOOLS in blockers:
        raise Conflict(
            "An affected school has not been notified yet. Wait for the school's register to "
            "pull the notice, or record the schools told by phone or e-mail, before closing.",
            code=CLOSE_SCHOOLS,
        )


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


def _not_closed(row: Any) -> None:
    """D48: a closed incident is locked; it changes only through a two-operator reopen."""
    if row["status"] == "closed":
        raise Conflict(
            "This incident is closed and can no longer be changed. Ask to reopen it; a second "
            "operator must confirm.",
            code="incident_closed",
        )


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


def _record_notified(s: Any, incident_id: uuid.UUID, row: Any, tenant_ids: list[uuid.UUID]) -> int:
    """Schools told by phone or e-mail (dedicated hosts): how many were newly notified."""
    if not tenant_ids:
        return 0
    if row["confirmed_at"] is None:
        raise Conflict(
            "Two operators must confirm the affected schools before any school is told.",
            code="not_confirmed",
        )
    t = m.incident_schools
    notified = 0
    for tenant_id in dict.fromkeys(tenant_ids):
        school = repo.get_by(s, t, t.c.incident_id == incident_id, t.c.tenant_id == tenant_id)
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
            repo.update_row(s, t, school["id"], {"school_notified_at": now()}, bump_version=False)
            notified += 1
    return notified


def update_incident(
    actor: Actor, incident_id: uuid.UUID, data: IncidentPatch, *, expected_version: int
) -> IncidentDetailOut:
    """Change status, severity, summary, CERT-In fields or the lead, and record schools told by
    phone or e-mail (``If-Match``). Audit ``incident.updated`` (field names and codes)."""
    with platform_session() as s, db_errors():
        row = _locked(s, incident_id)
        if row["version"] != expected_version:
            raise PreconditionFailed("Someone else changed this incident. Reload it.")
        _not_closed(row)
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
        notified = _record_notified(s, incident_id, row, data.notified_tenant_ids)
        if values.get("status") == "closed":
            _check_close(s, incident_id, row, values)
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
        _not_closed(row)
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
        _not_closed(row)
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


# --- reopening a closed incident (D48) ---------------------------------------------------------

_NO_REQUEST: Final[dict[str, Any]] = {
    "reopen_requested_by": None,
    "reopen_requested_at": None,
    "reopen_reason": None,
}


def request_reopen(
    actor: Actor, incident_id: uuid.UUID, data: IncidentReopenIn
) -> IncidentDetailOut:
    """First step of a reopen (D48): records who asked, when and why. The incident stays
    ``closed`` until a different operator confirms. 409 ``incident_not_closed`` or
    ``reopen_requested`` (one request at a time; withdraw it first). Audit
    ``incident.reopen_requested`` (never the reason text)."""
    with platform_session() as s, db_errors():
        row = _locked(s, incident_id)
        if row["status"] != "closed":
            raise Conflict("Only a closed incident can be reopened.", code="incident_not_closed")
        if row["reopen_requested_by"] is not None:
            raise Conflict(
                "A reopen request is already waiting. Confirm or withdraw it.",
                code="reopen_requested",
            )
        row = repo.update_row(
            s,
            m.incidents,
            incident_id,
            {
                "reopen_requested_by": must(actor.operator_id),
                "reopen_requested_at": now(),
                "reopen_reason": data.reason,
                "updated_at": now(),
            },
        )
        audit_platform(
            s,
            actor,
            "incident.reopen_requested",
            "incident",
            incident_id,
            {"reason_length": len(data.reason)},
        )
        return _detail(s, row)


def _pending(row: Any) -> uuid.UUID:
    requested_by: uuid.UUID | None = row["reopen_requested_by"]
    if row["status"] != "closed" or requested_by is None:
        raise Conflict("There is no reopen request waiting.", code="no_reopen_request")
    return requested_by


def confirm_reopen(actor: Actor, incident_id: uuid.UUID) -> IncidentDetailOut:
    """Second step (D48): a different operator holding ``platform.incidents.manage`` confirms;
    the status goes back to ``open``. 409 ``no_reopen_request``, ``same_operator`` and the
    two-person checks of :mod:`app.platform.two_person`. Audit ``incident.reopened``."""
    with platform_session() as s, db_errors():
        row = _locked(s, incident_id)
        requested_by = _pending(row)
        if requested_by == actor.operator_id:
            raise Conflict("A different operator must confirm the reopen.", code="same_operator")
        two_person.check_second_step(
            s,
            first_operator_id=requested_by,
            approver_id=actor.operator_id,
            permission=MANAGE,
            requested_at=row["reopen_requested_at"],
        )
        at = now()
        reopen_count = int(row["reopen_count"]) + 1
        row = repo.update_row(
            s,
            m.incidents,
            incident_id,
            {
                "status": "open",
                "reopen_count": reopen_count,
                "last_reopened_at": at,
                "updated_at": at,
            }
            | _NO_REQUEST,
        )
        audit_platform(
            s,
            actor,
            "incident.reopened",
            "incident",
            incident_id,
            {"requested_by": str(requested_by), "status": "open", "reopen": reopen_count},
        )
        return _detail(s, row)


def withdraw_reopen(actor: Actor, incident_id: uuid.UUID) -> IncidentDetailOut:
    """Withdraw a waiting reopen request (also an expired one); the incident stays closed.
    409 ``no_reopen_request``. Audit ``incident.reopen_withdrawn``."""
    with platform_session() as s, db_errors():
        row = _locked(s, incident_id)
        requested_by = _pending(row)
        row = repo.update_row(s, m.incidents, incident_id, {"updated_at": now()} | _NO_REQUEST)
        audit_platform(
            s,
            actor,
            "incident.reopen_withdrawn",
            "incident",
            incident_id,
            {"requested_by": str(requested_by)},
        )
        return _detail(s, row)


def record_reopen_told(
    actor: Actor, incident_id: uuid.UUID, data: IncidentReopenToldIn
) -> IncidentDetailOut:
    """A dedicated-host school was told of a reopen by phone or e-mail (D49 open item): records
    ``reopen_pulled = reopen_count`` and ``reopen_pulled_at`` for that school, as a pull would.
    Refused for a shared-tier school (409 ``school_pulls``; its register pulls the reopen), when
    the school has nothing pending (409 ``reopen_not_pending``) and when the incident's reopen
    count is no longer ``reopen_seq`` (409 ``reopen_changed``; reload). Audit
    ``incident.school_reopen_told`` (the reopen count and the note, which follows the summary
    rule)."""
    t, d = m.incident_schools, m.deployments
    with platform_session() as s, db_errors():
        row = _locked(s, incident_id)
        school = repo.get_by(
            s, t, t.c.incident_id == incident_id, t.c.tenant_id == data.tenant_id, for_update=True
        )
        if school is None:
            raise NotFound("School not found")
        deployment = repo.get_by(s, d, d.c.tenant_id == data.tenant_id)
        if deployment is None or deployment["mode"] != "dedicated":
            raise Conflict(
                "This school's register pulls the reopened incident by itself. Only a school on "
                "a dedicated host is recorded as told by hand.",
                code="school_pulls",
            )
        reopen_count = int(row["reopen_count"])
        if int(school["reopen_pulled"]) >= reopen_count:
            raise Conflict(
                "This school has no reopen waiting to be told.", code="reopen_not_pending"
            )
        if reopen_count != data.reopen_seq:
            raise Conflict(
                "The incident's reopen count changed. Reload the incident and record it again.",
                code="reopen_changed",
            )
        repo.update_row(
            s,
            t,
            school["id"],
            {"reopen_pulled": reopen_count, "reopen_pulled_at": now()},
            bump_version=False,
        )
        audit_platform(
            s,
            actor,
            "incident.school_reopen_told",
            "incident",
            incident_id,
            {"source": "operator", "reopen": reopen_count, "note": data.note},
            tenant_id=data.tenant_id,
        )
        return _detail(s, row)


# --- the school-side pull (through app.platform.service) ---------------------------------------


def notices_for_school(tenant_id: uuid.UUID) -> list[SchoolIncidentNotice]:
    """Confirmed notices this school's worker has not stored yet (codes and counts only): new
    ones, and ones reopened since the school last stored them (``reopen_seq``; D49)."""
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
            i.c.reopen_count.label("reopen_seq"),
            i.c.last_reopened_at.label("reopened_at"),
        )
        .join(i, i.c.id == t.c.incident_id)
        .where(
            t.c.tenant_id == tenant_id,
            i.c.confirmed_at.is_not(None),
            or_(t.c.pulled_at.is_(None), t.c.reopen_pulled < i.c.reopen_count),
        )
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


def mark_reopen_pulled(tenant_id: uuid.UUID, incident_id: uuid.UUID, reopen_seq: int) -> bool:
    """The school recorded reopen ``reopen_seq`` of the incident on its breach (D49 part 3).
    Idempotent; False when nothing changed, the incident is not this school's or the count is
    not one the incident reached. Audit ``incident.school_reopen_pulled`` (ids and the count)."""
    t = m.incident_schools
    with platform_session() as s, db_errors():
        incident = repo.get(s, m.incidents, incident_id)
        row = repo.get_by(
            s, t, t.c.incident_id == incident_id, t.c.tenant_id == tenant_id, for_update=True
        )
        if (
            incident is None
            or row is None
            or reopen_seq < 1
            or reopen_seq > incident["reopen_count"]
            or row["reopen_pulled"] >= reopen_seq
        ):
            return False
        repo.update_row(
            s,
            t,
            row["id"],
            {"reopen_pulled": reopen_seq, "reopen_pulled_at": now()},
            bump_version=False,
        )
        audit_platform(
            s,
            Actor(None),
            "incident.school_reopen_pulled",
            "incident",
            incident_id,
            {"source": "school_pull", "reopen": reopen_seq},
            tenant_id=tenant_id,
        )
        return True


__all__ = [
    "IncidentDetailOut",
    "IncidentIn",
    "IncidentOut",
    "IncidentPatch",
    "IncidentReopenIn",
    "IncidentReopenToldIn",
    "IncidentSchoolIn",
    "SchoolIncidentNotice",
    "add_school",
    "check_summary",
    "confirm",
    "confirm_reopen",
    "create_incident",
    "get_incident",
    "list_incidents",
    "mark_notice_pulled",
    "mark_reopen_pulled",
    "notices_for_school",
    "record_reopen_told",
    "request_reopen",
    "update_incident",
    "withdraw_reopen",
]
