"""Weekly management brief (FA-05; FR-BRF-001..FR-BRF-015; lead decision D85).

What the owner and the principal (holders of ``brief.read``) see once a week: school-level
**counts** that existing services already return, so the numbers agree with the screens.

- **Sources** (each through the owning module's ``service.py``, CLAUDE.md §4): readiness per
  profile and the portal windows closing soon (``dq``), open findings (``dq.summary``),
  corrections waiting (``changes``), certificates issued in the week (``certificates``), fee
  totals when fees are on (``fees``) and AI answers against the bundle (``knowledge``). They run
  under a read-only, whole-school system context (never used to write), like the portal
  reminders (``dq.portal_tasks.system_context``).
- **Counts only** (invariants 5 and 14): no names, no student IDs, no child-level values and no
  early-warning (``insights``) data at all. Student counts in 1..``small_cell_min``-1 are hidden
  (FR-BRF-009, :mod:`app.brief.suppression`). No LLM writes any text: the page and the notice
  are fixed templates.
- **Per-reader sections**: a section is shown only when the reader also holds the permission of
  the screen behind it (as the home page does), so ``brief.read`` never widens what a person
  can see.
- **Weekly job** (:func:`build_week`, Monday mornings): stores the week that ended the day
  before (Monday-Sunday, IST) once per school (``UNIQUE (tenant_id, week_start)``), notifies the
  holders of ``brief.read`` who did not turn the notice off, queues the e-mail for those who
  turned it on (opt-in) through the notifications outbox, records ``brief.weekly_built`` in the
  same transaction and deletes stored weeks older than ``keep_weeks``.
- **Preferences** (:func:`set_preferences`): each member's own ``in_app`` / ``email`` choice,
  audited ``brief.preferences_changed``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import Scopes, UserContext
from app.brief import repository as repo
from app.brief.config import BriefConfig, load_config
from app.brief.schemas import (
    AskOut,
    BriefHistoryItemOut,
    BriefHistoryOut,
    BriefPreferencesIn,
    BriefPreferencesOut,
    DeadlineOut,
    FeesOut,
    FindingsOut,
    ReadinessLineOut,
    WeeklyBriefOut,
)
from app.brief.subjects import erase_for_subject, subject_report
from app.brief.suppression import suppress_group, suppress_one
from app.certificates import service as certificates
from app.changes import service as changes
from app.core import purge as purging
from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, PreconditionFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.dq import service as dq
from app.fees import service as fees
from app.knowledge import service as knowledge
from app.notifications import service as notifications
from app.tenancy import service as tenancy

log = get_logger(__name__)

READ: Final = "brief.read"
TEMPLATE: Final = "brief.weekly_ready"
EMAIL_TEMPLATE: Final = "brief.weekly"
BUILT_EVENT: Final = "brief.weekly_built"
PREFS_EVENT: Final = "brief.preferences_changed"
IST: Final = ZoneInfo("Asia/Kolkata")
SYSTEM_ID: Final = uuid.UUID(int=0)
COUNTS_VERSION: Final = 1

# The permission of the screen behind each section (UX parity with the home page).
SECTION_PERMISSIONS: Final[Mapping[str, tuple[str, ...]]] = {
    "readiness": ("dq.readiness.read",),
    "deadlines": ("dq.readiness.read",),
    "findings": ("dq.findings.read",),
    "changes": ("student.identity_change.approve", "student.identity_change.request"),
    "certificates": ("certificate.read",),
    "fees": ("finance.read",),
    "ask": ("tenant.billing.read",),
}
# What the read-only system context may read to take the counts (never used to write).
_SOURCE_PERMISSIONS: Final = frozenset(
    {"dq.findings.read", "dq.readiness.read", "student.read_basic", "finance.read"}
)


def system_context(tenant_id: uuid.UUID) -> UserContext:
    """Whole-school read context for the counts of a brief (never used to write)."""
    return UserContext(
        user_id=SYSTEM_ID,
        tenant_id=tenant_id,
        membership_id=SYSTEM_ID,
        roles=frozenset({"system"}),
        permissions=_SOURCE_PERMISSIONS,
        scopes=Scopes(school=True),
        mfa=False,
        auth_time=None,
    )


def today_ist(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.UTC)).astimezone(IST).date()


def week_start_of(day: dt.date) -> dt.date:
    """The Monday of ``day``'s week."""
    return day - dt.timedelta(days=day.weekday())


def _ist_midnight(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(0, 0), tzinfo=IST)


# --- taking the counts --------------------------------------------------------------------------


def gather(session: Session, *, week_start: dt.date, as_of: dt.date) -> dict[str, Any]:
    """The school-level counts of the week starting ``week_start``, taken on ``as_of`` (the
    stored JSON, schema version :data:`COUNTS_VERSION`). Only counts, codes, labels of boards
    and portals, dates and rupee totals."""
    cfg = load_config()
    tenant_id = repo.current_tenant(session)
    if tenant_id is None:
        raise RuntimeError("gather() must run in a tenant_session")
    ctx = system_context(tenant_id)
    last = min(week_start + dt.timedelta(days=6), as_of)
    start, end = _ist_midnight(week_start), _ist_midnight(last + dt.timedelta(days=1))
    summary = dq.summary(session, ctx)
    counts: dict[str, Any] = {
        "v": COUNTS_VERSION,
        "as_of": as_of.isoformat(),
        "readiness": [
            {
                "profile_key": r.profile_key,
                "label_en": r.label_en,
                "students": r.students,
                "ready": r.ready,
                "needs_parent": r.needs_parent,
                "needs_school": r.needs_school,
                "blocked": r.blocked,
            }
            for r in dq.readiness_totals(session, ctx, limit=cfg.max_profiles)
        ],
        "findings": {
            "blockers": summary.blockers,
            "warnings": summary.warnings,
            "students_with_blockers": summary.students_with_blockers,
        },
        "changes_waiting": changes.pending_count(session),
        "certificates_issued": certificates.issued_count(session, start, end),
        "deadlines": [
            {
                "event_key": w.event_key,
                "portal": w.portal,
                "label_en": w.label_en,
                "closes_on": w.closes_on.isoformat(),
                "days_left": w.days_left,
                "confirmed": w.confirmed,
            }
            for w in dq.windows_closing(session, as_of, cfg.deadline_days)
        ],
        "fees": None,
        "ask": None,
    }
    if fees.enabled(session, tenant_id):
        receipts, collected = fees.collected_between(session, week_start, last)
        owing = fees.ask_school_fees(session, ctx)
        counts["fees"] = {
            "receipts": receipts,
            "collected": str(collected),
            "balance": str(owing.balance),
            "overdue": str(owing.overdue),
            "students_owing": owing.students_owing,
        }
    answers, included = knowledge.billable_answers_this_month(session, end)
    counts["ask"] = {"answers": answers, "included": included}
    return counts


# --- rendering for a reader -------------------------------------------------------------------


def _may(ctx: UserContext, section: str) -> bool:
    return any(ctx.has(p) for p in SECTION_PERMISSIONS[section])


def _readiness(
    rows: Sequence[Mapping[str, Any]], before: Sequence[Mapping[str, Any]] | None, minimum: int
) -> list[ReadinessLineOut]:
    previous = {r["profile_key"]: r for r in before or ()}
    out = []
    for r in rows:
        total = suppress_one(int(r["students"]), minimum)
        parts = suppress_group(
            {k: int(r[k]) for k in ("ready", "needs_parent", "needs_school", "blocked")}, minimum
        )
        if total is None:
            parts = dict.fromkeys(parts)
        change = None
        old = previous.get(r["profile_key"])
        if old is not None and parts["ready"] is not None:
            old_ready = suppress_group(
                {k: int(old[k]) for k in ("ready", "needs_parent", "needs_school", "blocked")},
                minimum,
            )["ready"]
            if old_ready is not None and suppress_one(int(old["students"]), minimum) is not None:
                change = parts["ready"] - old_ready
        out.append(
            ReadinessLineOut(
                profile_key=r["profile_key"],
                label=r["label_en"],
                students=total,
                ready=parts["ready"],
                needs_parent=parts["needs_parent"],
                needs_school=parts["needs_school"],
                blocked=parts["blocked"],
                ready_change=change,
            )
        )
    return out


def render(
    ctx: UserContext,
    counts: Mapping[str, Any],
    *,
    week_start: dt.date,
    row: RowMapping | None,
    previous: RowMapping | None,
    cfg: BriefConfig | None = None,
) -> WeeklyBriefOut:
    """The brief as ``ctx`` may see it: sections behind permissions, small cells hidden."""
    config = cfg or load_config()
    minimum = config.small_cell_min
    findings = counts["findings"]
    fee = counts.get("fees")
    ask = counts.get("ask")
    return WeeklyBriefOut(
        id=row["id"] if row is not None else None,
        stored=row is not None,
        week_start=week_start,
        week_end=week_start + dt.timedelta(days=6),
        as_of=dt.date.fromisoformat(counts["as_of"]),
        built_at=row["built_at"] if row is not None else None,
        previous_week_start=previous["week_start"] if previous is not None else None,
        small_cell_min=minimum,
        readiness=(
            _readiness(
                counts["readiness"],
                previous["counts"].get("readiness") if previous is not None else None,
                minimum,
            )
            if _may(ctx, "readiness")
            else None
        ),
        findings=(
            FindingsOut(
                blockers=int(findings["blockers"]),
                warnings=int(findings["warnings"]),
                students_with_blockers=suppress_one(
                    int(findings["students_with_blockers"]), minimum
                ),
            )
            if _may(ctx, "findings")
            else None
        ),
        changes_waiting=int(counts["changes_waiting"]) if _may(ctx, "changes") else None,
        certificates_issued=(
            int(counts["certificates_issued"]) if _may(ctx, "certificates") else None
        ),
        deadlines=(
            [
                DeadlineOut(
                    event_key=d["event_key"],
                    portal=d["portal"],
                    label=d["label_en"],
                    closes_on=dt.date.fromisoformat(d["closes_on"]),
                    days_left=int(d["days_left"]),
                    confirmed=bool(d["confirmed"]),
                )
                for d in counts["deadlines"]
            ]
            if _may(ctx, "deadlines")
            else None
        ),
        deadline_days=config.deadline_days,
        fees=(
            FeesOut(
                receipts=int(fee["receipts"]),
                collected=Decimal(fee["collected"]),
                balance=Decimal(fee["balance"]),
                overdue=Decimal(fee["overdue"]),
                students_owing=suppress_one(int(fee["students_owing"]), minimum),
            )
            if fee is not None and _may(ctx, "fees")
            else None
        ),
        ask=(
            AskOut(answers=int(ask["answers"]), included=ask["included"])
            if ask is not None and _may(ctx, "ask")
            else None
        ),
    )


def _guard(ctx: UserContext) -> None:
    if not ctx.has(READ):
        raise Forbidden()


def latest(session: Session, ctx: UserContext, *, today: dt.date | None = None) -> WeeklyBriefOut:
    """``GET /brief/weekly``: the newest stored week; before the first Monday job, a live
    preview of the current week so far (not stored, ``stored: false``; FR-BRF-013)."""
    _guard(ctx)
    row = repo.latest(session)
    if row is not None:
        before = repo.previous(session, row["week_start"])
        return render(ctx, row["counts"], week_start=row["week_start"], row=row, previous=before)
    day = today or today_ist()
    start = week_start_of(day)
    log.info("brief.preview")
    return render(
        ctx, gather(session, week_start=start, as_of=day), week_start=start, row=None, previous=None
    )


def get(session: Session, ctx: UserContext, brief_id: uuid.UUID) -> WeeklyBriefOut:
    """``GET /brief/weekly/{brief_id}``: one stored week (another school's is 404)."""
    _guard(ctx)
    row = repo.get_brief(session, brief_id)
    if row is None:
        raise NotFound()
    before = repo.previous(session, row["week_start"])
    return render(ctx, row["counts"], week_start=row["week_start"], row=row, previous=before)


def history(session: Session, ctx: UserContext, limit: int | None = None) -> BriefHistoryOut:
    """``GET /brief/weekly/history``: the stored weeks, newest first."""
    _guard(ctx)
    cap = load_config().history_limit
    rows = repo.history(session, min(limit or cap, cap))
    return BriefHistoryOut(
        items=[
            BriefHistoryItemOut(
                id=r["id"],
                week_start=r["week_start"],
                week_end=r["week_start"] + dt.timedelta(days=6),
                built_at=r["built_at"],
            )
            for r in rows
        ]
    )


# --- preferences (FR-BRF-011, FR-BRF-012) -------------------------------------------------------


def _prefs_out(row: RowMapping | None) -> BriefPreferencesOut:
    delivery = load_config().delivery
    return BriefPreferencesOut(
        in_app=bool(row["in_app"]) if row is not None else delivery.in_app_default,
        email=bool(row["email"]) if row is not None else delivery.email_default,
        email_available=get_settings().email_enabled,
        version=int(row["version"]) if row is not None else 0,
    )


def get_preferences(session: Session, ctx: UserContext) -> BriefPreferencesOut:
    """The caller's own choice (defaults until they save one)."""
    _guard(ctx)
    return _prefs_out(repo.get_preferences(session, ctx.membership_id))


def _stale() -> PreconditionFailed:
    return PreconditionFailed(
        "Someone else changed this in the meantime. Reload it and try again.",
        code="version_mismatch",
    )


def set_preferences(
    session: Session, ctx: UserContext, data: BriefPreferencesIn, expected_version: int | None
) -> BriefPreferencesOut:
    """Save the caller's own choice; ``If-Match`` (when sent) must match ``version`` (0 before
    the first save). Audited ``brief.preferences_changed`` in the same transaction."""
    _guard(ctx)
    current = repo.get_preferences(session, ctx.membership_id)
    version = int(current["version"]) if current is not None else 0
    if expected_version is not None and expected_version != version:
        raise _stale()
    if current is None:
        row = repo.insert_preferences(
            session,
            tenant_id=ctx.tenant_id,
            membership_id=ctx.membership_id,
            in_app=data.in_app,
            email=data.email,
        )
    else:
        updated = repo.update_preferences(
            session, ctx.membership_id, version=version, in_app=data.in_app, email=data.email
        )
        if updated is None:
            raise _stale()
        row = updated
    audit.record(
        session,
        action=PREFS_EVENT,
        resource_type="membership",
        resource_id=ctx.membership_id,
        summary={"in_app": data.in_app, "digest_message": data.email},
        request_id=ctx.request_id,
    )
    return _prefs_out(row)


# --- the weekly job (FR-BRF-010) ----------------------------------------------------------------


def _recipients(session: Session) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """(in-app recipients, e-mail recipients) among the holders of ``brief.read``. A member who
    turned the e-mail on also gets the in-app notice (the e-mail is sent with it)."""
    delivery = load_config().delivery
    prefs = repo.preferences_of(session)
    in_app: list[uuid.UUID] = []
    email: list[uuid.UUID] = []
    for member in notifications.holders(session, READ):
        pref = prefs.get(member)
        wants_app = bool(pref["in_app"]) if pref is not None else delivery.in_app_default
        wants_email = bool(pref["email"]) if pref is not None else delivery.email_default
        if wants_email:
            email.append(member)
        elif wants_app:
            in_app.append(member)
    return in_app, email


def build_week(session: Session, *, today: dt.date | None = None) -> int:
    """One school's brief of the week that ended before ``today``'s week (inside its
    ``tenant_session``). Stores it once, notifies, audits; returns how many notices were
    created (0 when the week is stored already)."""
    day = today or today_ist()
    week_start = week_start_of(day) - dt.timedelta(days=7)
    tenant_id = repo.current_tenant(session)
    if tenant_id is None:
        raise RuntimeError("build_week() must run in a tenant_session")
    cfg = load_config()
    repo.delete_older_than(session, week_start - dt.timedelta(weeks=cfg.keep_weeks))
    if repo.brief_of_week(session, week_start) is not None:
        return 0
    counts = gather(session, week_start=week_start, as_of=day)
    row = repo.insert_brief(
        session, brief_id=new_id(), tenant_id=tenant_id, week_start=week_start, counts=counts
    )
    if row is None:  # a concurrent run stored the week first
        return 0
    # No counts in the notice or e-mail: each reader's page shows only the sections their own
    # permissions allow, and a notice must not say more than the page (FR-BRF-010).
    params: dict[str, object] = {}
    dedupe = f"weekly_brief:{week_start.isoformat()}"
    in_app, email = _recipients(session)
    notified = 0
    if email:
        notified += notifications.notify_and_email(
            session,
            tenant_id=tenant_id,
            recipients=email,
            template_key=TEMPLATE,
            params=params,
            dedupe_key=dedupe,
            email_template=EMAIL_TEMPLATE,
            resource_id=row["id"],
        )
    if in_app:
        notified += notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=in_app,
            template_key=TEMPLATE,
            params=params,
            resource_id=row["id"],
            dedupe_key=dedupe,
        )
    repo.set_notified(session, row["id"], notified)
    audit.record(
        session,
        action=BUILT_EVENT,
        resource_type="weekly_brief",
        resource_id=row["id"],
        summary={
            "week_start": week_start.isoformat(),
            "notified": notified,
            "queued_messages": len(email) if get_settings().email_enabled else 0,
        },
        actor_type="system",
    )
    log.info("brief.weekly_built", count=notified)
    return notified


# --- offboarding (FR-PLT-005) -------------------------------------------------------------------

_PURGE = purging.PurgeTables(deleted=("ops.brief_preferences", "ops.weekly_briefs"))


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="brief", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "BUILT_EVENT",
    "EMAIL_TEMPLATE",
    "PREFS_EVENT",
    "READ",
    "SECTION_PERMISSIONS",
    "TEMPLATE",
    "build_week",
    "erase_for_subject",
    "gather",
    "get",
    "get_preferences",
    "history",
    "latest",
    "purge_tenant_data",
    "render",
    "set_preferences",
    "subject_report",
    "system_context",
    "tenant_data_counts",
    "today_ist",
    "week_start_of",
]
