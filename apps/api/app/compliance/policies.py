"""School policy library with yearly review and staff acknowledgement (FC-10;
FR-COMP-056..FR-COMP-070; docs/05 §16.10).

A policy is a document in Documents (purpose ``policy``, a clean PDF) with a next review date
and the roles whose members must acknowledge it (child protection, anti-bullying, safety,
privacy, code of conduct). Signed-in staff only: no paper acknowledgement, no staff without a
login (that needs the staff register, FC-04, a new data class).

- **Library (FR-COMP-056..FR-COMP-059).** ``compliance.manage`` adds, corrects and retires
  policies; ``compliance.read`` sees the list with review state and acknowledgement counts.
- **Review (FR-COMP-060..FR-COMP-062).** "Mark reviewed" sets today as the last review and the
  next review date (default ``review_months`` later, ``policies.yaml``). Daily in-app reminders
  to the holders of ``compliance.manage`` before the date and while overdue (the certificate
  register's rules: :func:`app.compliance.config.due_reminder`).
- **Acknowledgement (FR-COMP-063..FR-COMP-067).** Each member whose roles are asked sees the
  policy under "Policies to read" (implicit ``session.authenticated``; another member's or a
  policy not asked of them: 404), opens the exact clean version and confirms "I have read this
  version". Acknowledgements are append-only, one per member and version; a new document
  version needs a new acknowledgement. The detail shows who has and has not acknowledged.
- **Audit (FR-COMP-068).** ``compliance.policy.*`` in the same transaction: ids, role keys,
  dates and counts, never the title.
- **Privacy (FR-COMP-069).** Acknowledgements are staff personal data (record class
  ``policy_acknowledgements``): in the member's access report and the full export.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.compliance import repository as cert_repo
from app.compliance import safety_config as cfg
from app.compliance import safety_repository as repo
from app.compliance.checks import today
from app.compliance.config import due_reminder
from app.compliance.safety_models import CompliancePolicy as Row
from app.compliance.safety_schemas import (
    CompliancePolicyOut,
    MyPoliciesOut,
    MyPolicyOut,
    PoliciesOut,
    PolicyAcknowledge,
    PolicyAckRequestOut,
    PolicyCountsOut,
    PolicyCreate,
    PolicyDetailOut,
    PolicyFileOut,
    PolicyMemberOut,
    PolicyReview,
    PolicyRoleOut,
    PolicyUpdate,
)
from app.compliance.schemas import DocumentRefOut
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.redaction import contains_full_aadhaar
from app.core.report_format import fmt_date
from app.core.subjects import ReportSection, Subject
from app.documents import service as documents
from app.identity import service as identity
from app.identity.schemas import StaffMemberOut
from app.notifications import service as notifications

MANAGE: Final = "compliance.manage"
RESOURCE: Final = "compliance_policy"
ACK_TEMPLATE: Final = "compliance.policy.acknowledge"
REVIEW_TEMPLATE: Final = "compliance.policy.review_due"
CLASS_KEY: Final = "policy_acknowledgements"
POLICY_PURPOSE: Final = "policy"
FILE_TTL_S: Final = 300
MANAGERS = notifications.PermissionSelector(MANAGE)

log = get_logger(__name__)


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    row: Row,
    extra: Mapping[str, Any] | None = None,
    *,
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=RESOURCE,
        resource_id=row.id,
        summary={
            "document_id": str(row.document_id) if row.document_id else None,
            "review_due": row.review_due.isoformat(),
            "ack_roles": sorted(row.ack_roles),
            **dict(extra or {}),
        },
        actor_type="system" if system else "user",
        request_id=None if system else _request_id(),
    )


def _not_found() -> NotFound:
    return NotFound("Policy not found")


def _role_names(session: Session) -> dict[str, str]:
    """Role key -> English name of the school's assignable roles (no break-glass role)."""
    return {r.key: r.name_en for r in identity.list_roles(session)}


def _targets(members: Sequence[StaffMemberOut], roles: Iterable[str]) -> list[StaffMemberOut]:
    wanted = set(roles)
    return [m for m in members if wanted.intersection(m.roles)]


def _current_version(session: Session, row: Row) -> uuid.UUID | None:
    if row.document_id is None:
        return None
    found = documents.publishable_version(session, row.document_id)
    return found.version_id if found is not None else None


def _review_due_default(day: dt.date) -> dt.date:
    return cfg.add_months(day, cfg.policies().review_months)


def _out(
    session: Session,
    ctx: UserContext,
    row: Row,
    day: dt.date,
    *,
    members: Sequence[StaffMemberOut],
    acks: Mapping[tuple[uuid.UUID, uuid.UUID, uuid.UUID], dt.datetime],
    names: Mapping[str, str],
) -> CompliancePolicyOut:
    version = _current_version(session, row)
    targets = _targets(members, row.ack_roles) if row.status == repo.ACTIVE else []
    done = (
        sum(1 for m in targets if (row.id, version, m.membership_id) in acks)
        if version is not None
        else 0
    )
    document = None
    if row.document_id is not None:
        title = (
            documents.get_document(session, ctx, row.document_id).title
            if documents.is_visible(session, ctx, row.document_id)
            else None
        )
        document = DocumentRefOut(id=row.document_id, title=title)
    rules = cfg.policies()
    return CompliancePolicyOut(
        id=row.id,
        title=row.title,
        document=document,
        current_version_id=version,
        ack_roles=[PolicyRoleOut(role=k, name=names.get(k, k)) for k in row.ack_roles],
        review_due=row.review_due,
        review_state=cfg.review_state(row.review_due, day, due_soon_days=rules.due_soon_days),
        days_left=(row.review_due - day).days,
        last_reviewed_on=row.last_reviewed_on,
        status="retired" if row.status == "retired" else "active",
        targeted=len(targets),
        acknowledged=done,
        ack_requested_at=row.ack_requested_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )


def _one_out(session: Session, ctx: UserContext, row: Row) -> CompliancePolicyOut:
    return _out(
        session,
        ctx,
        row,
        today(session),
        members=identity.active_members(session),
        acks=repo.acks_for(session, [row.id]),
        names=_role_names(session),
    )


# --- reads --------------------------------------------------------------------------------------


def library(session: Session, ctx: UserContext) -> PoliciesOut:
    """Every policy with its review state and acknowledgement counts (``compliance.read``;
    FR-COMP-058). Active ones first, the nearest review first."""
    rules = cfg.policies()
    day = today(session)
    rows = repo.policies(session)
    members = identity.active_members(session)
    acks = repo.acks_for(session, [r.id for r in rows])
    names = _role_names(session)
    items = [_out(session, ctx, r, day, members=members, acks=acks, names=names) for r in rows]
    active = [i for i in items if i.status == "active"]
    return PoliciesOut(
        items=items,
        counts=PolicyCountsOut(
            overdue=sum(1 for i in active if i.review_state == "overdue"),
            due_soon=sum(1 for i in active if i.review_state == "due_soon"),
            pending_acks=sum(i.targeted - i.acknowledged for i in active),
        ),
        roles=[PolicyRoleOut(role=k, name=v) for k, v in names.items()],
        review_months=rules.review_months,
        verified=rules.verified,
        today=day,
    )


def _load(session: Session, policy_id: uuid.UUID, *, lock: bool = False) -> Row:
    row = repo.get_policy(session, policy_id, lock=lock)
    if row is None:
        raise _not_found()
    return row


def get_policy(session: Session, ctx: UserContext, policy_id: uuid.UUID) -> PolicyDetailOut:
    """One policy with who has and has not acknowledged the current version
    (``compliance.read``; FR-COMP-066). Not yet acknowledged first. Another school's: 404."""
    row = _load(session, policy_id)
    members = identity.active_members(session)
    acks = repo.acks_for(session, [row.id])
    base = _out(
        session,
        ctx,
        row,
        today(session),
        members=members,
        acks=acks,
        names=_role_names(session),
    )
    version = base.current_version_id
    earlier = {m for (_p, v, m) in acks if v != version}
    people = [
        PolicyMemberOut(
            membership_id=m.membership_id,
            display_name=m.display_name,
            roles=sorted(set(m.roles) & set(row.ack_roles)),
            acknowledged_at=acks.get((row.id, version, m.membership_id)) if version else None,
            acknowledged_earlier=m.membership_id in earlier,
        )
        for m in (_targets(members, row.ack_roles) if row.status == repo.ACTIVE else [])
    ]
    people.sort(key=lambda p: (p.acknowledged_at is not None, p.display_name.casefold()))
    return PolicyDetailOut(**base.model_dump(), members=people)


# --- writes -------------------------------------------------------------------------------------


def _check(
    session: Session,
    ctx: UserContext,
    *,
    title: str,
    document_id: uuid.UUID,
    review_due: dt.date | None,
    ack_roles: Sequence[str],
) -> None:
    errors: list[dict[str, str]] = []
    if contains_full_aadhaar(title):
        errors.append(_error("title", "aadhaar_full_number_rejected"))
    ready = documents.is_visible(session, ctx, document_id)
    if ready:
        doc = documents.get_document(session, ctx, document_id)
        ready = (
            doc.purpose == POLICY_PURPOSE
            and documents.publishable_version(session, document_id) is not None
        )
    if not ready:
        errors.append(_error("document_id", "policy_document_not_ready"))
    if review_due is not None and review_due < today(session):
        errors.append(_error("review_due", "review_in_past"))
    known = _role_names(session)
    if any(r not in known for r in ack_roles):
        errors.append(_error("ack_roles", "unknown_role"))
    if len(set(ack_roles)) > cfg.policies().max_roles:
        errors.append(_error("ack_roles", "too_many_roles"))
    if errors:
        raise ValidationFailed(errors)


def _ask(session: Session, row: Row) -> int:
    """Notify the asked members who have not acknowledged the current version."""
    version = _current_version(session, row)
    if version is None or row.status != repo.ACTIVE:
        return 0
    acks = repo.acks_for(session, [row.id])
    pending = [
        m.membership_id
        for m in _targets(identity.active_members(session), row.ack_roles)
        if (row.id, version, m.membership_id) not in acks
    ]
    if not pending:
        return 0
    notified = notifications.notify(
        session,
        tenant_id=row.tenant_id,
        recipients=pending,
        template_key=ACK_TEMPLATE,
        params={},
        resource_id=row.id,
        dedupe_key=f"{ACK_TEMPLATE}:{row.id}:{version}:{today(session).isoformat()}",
    )
    repo.set_policy_values(session, row.id, {"ack_requested_at": cert_repo.now(session)})
    return notified


def create(session: Session, ctx: UserContext, data: PolicyCreate) -> CompliancePolicyOut:
    """Add a policy (``compliance.manage``; FR-COMP-056). Errors (422):
    ``policy_document_not_ready`` (not a clean PDF of purpose policy you can see),
    ``review_in_past``, ``unknown_role``, ``too_many_roles``, ``aadhaar_full_number_rejected``.
    With ``notify`` the asked members get an in-app notice."""
    roles = sorted(set(data.ack_roles))
    _check(
        session,
        ctx,
        title=data.title,
        document_id=data.document_id,
        review_due=data.review_due,
        ack_roles=roles,
    )
    row = repo.insert_policy(
        session,
        id=new_id(),
        tenant_id=cert_repo.current_tenant_id(session),
        title=data.title,
        document_id=data.document_id,
        ack_roles=roles,
        review_due=data.review_due or _review_due_default(today(session)),
        created_by=ctx.user_id,
    )
    notified = _ask(session, row) if data.notify else 0
    _audit(session, "compliance.policy.created", row, {"notified": notified})
    session.expire(row)
    return _one_out(session, ctx, _load(session, row.id))


def _guard_version(row: Row, expected_version: int) -> None:
    if row.version != expected_version:
        raise PreconditionFailed("The policy was changed by someone else. Reload it.")


def _write(session: Session, row: Row, values: Mapping[str, Any], expected_version: int) -> Row:
    updated = repo.update_policy(session, row, values, expected_version=expected_version)
    if updated is None:
        raise PreconditionFailed("The policy was changed by someone else. Reload it.")
    return updated


def update(
    session: Session,
    ctx: UserContext,
    policy_id: uuid.UUID,
    data: PolicyUpdate,
    *,
    expected_version: int,
) -> CompliancePolicyOut:
    """Correct or retire a policy (``compliance.manage``, ``If-Match``; FR-COMP-057,
    FR-COMP-059). A new review date restarts its reminders. A retired policy can be made
    active again. Errors: 412 stale ``If-Match``, 422 as :func:`create`."""
    row = _load(session, policy_id, lock=True)
    _guard_version(row, expected_version)
    roles = sorted(set(data.ack_roles))
    _check(
        session,
        ctx,
        title=data.title,
        document_id=data.document_id,
        review_due=data.review_due if data.review_due != row.review_due else None,
        ack_roles=roles,
    )
    values: dict[str, Any] = {
        "title": data.title,
        "document_id": data.document_id,
        "ack_roles": roles,
        "review_due": data.review_due,
        "status": data.status,
    }
    changed = sorted(k for k, v in values.items() if getattr(row, k) != v)
    if "review_due" in changed:
        values.update(last_reminded_lead=None, last_reminded_on=None)
    if "status" in changed:
        retiring = data.status == "retired"
        values.update(
            retired_at=cert_repo.now(session) if retiring else None,
            retired_by=ctx.user_id if retiring else None,
        )
    updated = _write(session, row, values, expected_version)
    _audit(session, "compliance.policy.updated", updated, {"fields": changed})
    return _one_out(session, ctx, updated)


def review(
    session: Session,
    ctx: UserContext,
    policy_id: uuid.UUID,
    data: PolicyReview,
    *,
    expected_version: int,
) -> CompliancePolicyOut:
    """Mark the policy reviewed today (``compliance.manage``, ``If-Match``; FR-COMP-060).
    Errors: 409 ``policy_retired``, 412 stale ``If-Match``, 422 ``review_in_past``."""
    row = _load(session, policy_id, lock=True)
    _guard_version(row, expected_version)
    if row.status != repo.ACTIVE:
        raise Conflict("A retired policy is not reviewed.", code="policy_retired")
    day = today(session)
    if data.next_review_due is not None and data.next_review_due <= day:
        raise ValidationFailed([_error("next_review_due", "review_in_past")])
    updated = _write(
        session,
        row,
        {
            "last_reviewed_on": day,
            "review_due": data.next_review_due or _review_due_default(day),
            "last_reminded_lead": None,
            "last_reminded_on": None,
        },
        expected_version,
    )
    _audit(session, "compliance.policy.reviewed", updated, {"reviewed_on": day.isoformat()})
    return _one_out(session, ctx, updated)


def request_acknowledgement(
    session: Session, ctx: UserContext, policy_id: uuid.UUID
) -> PolicyAckRequestOut:
    """Ask again: notify the asked members who have not acknowledged the current version
    (``compliance.manage``; FR-COMP-064). At most one notice per member and day. Errors: 409
    ``policy_retired``, 409 ``policy_document_not_ready``."""
    row = _load(session, policy_id, lock=True)
    if row.status != repo.ACTIVE:
        raise Conflict("A retired policy is not sent to staff.", code="policy_retired")
    if _current_version(session, row) is None:
        raise Conflict(
            "The policy document has no clean PDF version yet.", code="policy_document_not_ready"
        )
    notified = _ask(session, row)
    _audit(session, "compliance.policy.ack_requested", row, {"notified": notified})
    return PolicyAckRequestOut(notified=notified)


# --- the member's own list (implicit session.authenticated) -------------------------------------


def _mine(session: Session, ctx: UserContext, policy_id: uuid.UUID) -> Row:
    """A policy asked of the caller, or 404 (another school's, retired, or not asked of them)."""
    row = repo.get_policy(session, policy_id)
    if row is None or row.status != repo.ACTIVE or not set(row.ack_roles) & set(ctx.roles):
        raise _not_found()
    return row


def my_policies(session: Session, ctx: UserContext) -> MyPoliciesOut:
    """The policies the caller is asked to acknowledge, not yet acknowledged first
    (FR-COMP-063)."""
    rows = repo.policies_for_roles(session, sorted(ctx.roles))
    acks = repo.acks_for(session, [r.id for r in rows])
    items = []
    for r in rows:
        version = _current_version(session, r)
        when = acks.get((r.id, version, ctx.membership_id)) if version else None
        items.append(
            MyPolicyOut(
                id=r.id,
                title=r.title,
                current_version_id=version,
                acknowledged_at=when,
                needs_ack=version is not None and when is None,
            )
        )
    items.sort(key=lambda i: (not i.needs_ack, i.title.casefold()))
    return MyPoliciesOut(items=items, pending=sum(1 for i in items if i.needs_ack))


def policy_file(session: Session, ctx: UserContext, policy_id: uuid.UUID) -> PolicyFileOut:
    """A short-lived link to the clean PDF version the caller is asked to read (FR-COMP-065):
    the member needs no Documents permission for it. Errors: 404, 409
    ``policy_document_not_ready``."""
    row = _mine(session, ctx, policy_id)
    found = documents.publishable_version(session, row.document_id) if row.document_id else None
    link = (
        documents.published_version_url(
            session,
            found.document_id,
            found.version_id,
            filename=f"policy-{row.id}.pdf",
            ttl_s=FILE_TTL_S,
        )
        if found is not None
        else None
    )
    if found is None or link is None:
        raise Conflict(
            "The policy document is not ready to read yet.", code="policy_document_not_ready"
        )
    _audit(session, "compliance.policy.opened", row, {"document_version_id": str(found.version_id)})
    return PolicyFileOut(url=link[0], expires_at=link[1], document_version_id=found.version_id)


def acknowledge(
    session: Session, ctx: UserContext, policy_id: uuid.UUID, data: PolicyAcknowledge
) -> MyPolicyOut:
    """ "I have read this version" (FR-COMP-067). Repeating it changes nothing. Errors: 404,
    409 ``policy_version_changed`` (a newer version was published since the member opened
    it: open it again)."""
    row = _mine(session, ctx, policy_id)
    version = _current_version(session, row)
    if version is None or version != data.document_version_id:
        raise Conflict(
            "The policy was updated since you opened it. Open it again and read the new version.",
            code="policy_version_changed",
        )
    added = repo.insert_ack(
        session,
        id=new_id(),
        tenant_id=row.tenant_id,
        policy_id=row.id,
        document_version_id=version,
        membership_id=ctx.membership_id,
        user_id=ctx.user_id,
    )
    if added:
        _audit(
            session,
            "compliance.policy.acknowledged",
            row,
            {"document_version_id": str(version), "membership_id": str(ctx.membership_id)},
        )
    when = repo.acks_for(session, [row.id]).get((row.id, version, ctx.membership_id))
    return MyPolicyOut(
        id=row.id,
        title=row.title,
        current_version_id=version,
        acknowledged_at=when,
        needs_ack=when is None,
    )


# --- review reminders (FR-COMP-061, FR-COMP-062) ------------------------------------------------


def send_reminders(tenant_id: uuid.UUID, *, day: dt.date | None = None) -> int:
    """Daily, one school: in-app reminders of policy reviews due soon or overdue, to the holders
    of ``compliance.manage``. Idempotent per policy and lead day. Returns how many policies
    were reminded."""
    rules = cfg.policies().reminders
    sent = 0
    with tenant_session(tenant_id) as session:
        on = day or today(session)
        for row in repo.due_policies(session):
            due = due_reminder(
                row.review_due,
                on,
                lead_days=rules.lead_days,
                expired_every_days=rules.overdue_every_days,
                last_lead=row.last_reminded_lead,
                last_on=row.last_reminded_on,
            )
            if due is None:
                continue
            notified = notifications.notify(
                session,
                tenant_id=tenant_id,
                recipients=MANAGERS,
                template_key=REVIEW_TEMPLATE,
                params={"days": due.days, "state": "overdue" if due.expired else "due"},
                resource_id=row.id,
                dedupe_key=f"{REVIEW_TEMPLATE}:{row.id}:{due.lead}:{on.isoformat()}",
            )
            repo.set_policy_values(
                session, row.id, {"last_reminded_lead": due.lead, "last_reminded_on": on}
            )
            _audit(
                session,
                "compliance.policy.reminded",
                row,
                {"lead_day": due.lead, "notified": notified},
                system=True,
            )
            sent += 1
    log.info("compliance.policy_reminders_sent", count=sent)
    return sent


# --- access report (FR-COMP-069) ----------------------------------------------------------------


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """A staff member's own acknowledgements: which policy, when ([] for other kinds)."""
    if subject.kind != "member":
        return []
    acks = repo.acks_of_member(session, subject.id)
    if not acks:
        return []
    titles = {p.id: p.title for p in repo.policies(session)}
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="School policies acknowledged",
            columns=("Policy", "Acknowledged on"),
            rows=tuple(
                (titles.get(a.policy_id, "A school policy"), fmt_date(a.acknowledged_at))
                for a in acks
            ),
        )
    ]


__all__ = [
    "ACK_TEMPLATE",
    "CLASS_KEY",
    "RESOURCE",
    "REVIEW_TEMPLATE",
    "acknowledge",
    "create",
    "get_policy",
    "library",
    "my_policies",
    "policy_file",
    "request_acknowledgement",
    "review",
    "send_reminders",
    "subject_report",
    "update",
]
