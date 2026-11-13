"""Daily overdue TC request reminder (FO-09 follow-up; FR-CERT-045). *(Lead decision D87, PO to
confirm.)*

Once a day (beat ``certificates.tc_overdue_reminders``, 07:15 IST) every active member holding
``certificate.approve`` (the principal) or ``certificate.issue`` (the office) gets one in-app
notification, ``tc_request.overdue``, saying how many TC requests they can see are overdue
(``tc_requests.overdue_after_days``, FR-CERT-036). Count only: no student names, no request IDs.

- **Per recipient's reach.** The count is the one the recipient's own TC requests list shows
  (``overdue_count``): a clerk scoped to some sections is told about those sections only. A member
  whose count is 0 gets nothing; break-glass support memberships are never told.
- **At most once per day per recipient** (dedupe key ``tc_request.overdue:<IST day>``): a re-run,
  a retry or a member holding both permissions still gets one notification.
- **Audit.** One ``tc_request.overdue_notified`` system event per school and day when at least
  one notification was created, in the same transaction: the day and the number of recipients.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.authz.resolver import build_snapshot
from app.certificates import repository as cert_repo
from app.certificates import service as certificates
from app.certificates import tc_requests
from app.certificates import tc_requests_repository as repo
from app.core.db import tenant_session
from app.core.logging import get_logger
from app.identity import service as identity
from app.identity.schemas import MembershipAccess, RoleAccess, RoleOut, UserOut
from app.notifications import service as notifications

TEMPLATE: Final = "tc_request.overdue"
AUDIT_ACTION: Final = "tc_request.overdue_notified"
RECIPIENT_PERMISSIONS: Final = (certificates.APPROVE, certificates.ISSUE)
_USER_PAGE: Final = 200

log = get_logger(__name__)


def _member_context(
    tenant_id: uuid.UUID, user: UserOut, roles: Mapping[str, RoleOut]
) -> UserContext:
    """A member's current permissions and scopes (what their next request would get), used only
    to count what their TC requests list would show."""
    access = MembershipAccess(
        roles=tuple(
            RoleAccess(
                key=key,
                is_system=roles[key].is_system,
                permissions=frozenset(roles[key].permissions),
            )
            for key in user.roles
            if key in roles
        ),
        scopes=tuple((s.type, s.ref) for s in user.scopes),
        mfa_required=False,
    )
    snap = build_snapshot(access)
    return UserContext(
        user_id=user.id,
        tenant_id=tenant_id,
        membership_id=user.membership_id,
        roles=snap.roles,
        permissions=snap.permissions,
        scopes=snap.scopes,
        mfa=False,
        auth_time=None,
        scoped_permissions=snap.scoped_permissions,
    )


def _recipients(session: Session) -> list[UserOut]:
    """Active, unexpired members holding either permission, without break-glass memberships."""
    allowed: set[uuid.UUID] = set()
    for permission in RECIPIENT_PERMISSIONS:
        allowed.update(identity.active_holders(session, permission))
    if not allowed:
        return []
    out: list[UserOut] = []
    after: uuid.UUID | None = None
    while True:
        users, after = identity.list_users(session, limit=_USER_PAGE, after=after)
        out.extend(u for u in users if u.membership_id in allowed)
        if after is None:
            return out


def notify_overdue(session: Session, *, day: dt.date | None = None) -> int:
    """One school, inside its ``tenant_session``: notify each recipient of their overdue count.
    Returns how many notifications were created (0 on a re-run the same day)."""
    on = day or certificates._today(session)
    before = on - dt.timedelta(days=tc_requests.settings().overdue_after_days)
    tenant_id = cert_repo.current_tenant_id(session)
    roles = {r.key: r for r in identity.list_roles(session)}
    dedupe = f"{TEMPLATE}:{on.isoformat()}"
    days = tc_requests.settings().overdue_after_days
    created = 0
    for user in _recipients(session):
        ctx = _member_context(tenant_id, user, roles)
        reach = certificates._reach(session, ctx, RECIPIENT_PERMISSIONS)
        count = repo.count_overdue(session, student_ids=reach, received_before=before)
        if count == 0:
            continue
        created += notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=[user.membership_id],
            template_key=TEMPLATE,
            params={"count": count, "days": days},
            dedupe_key=dedupe,
        )
    if created:
        certificates._audit(
            session,
            action=AUDIT_ACTION,
            resource_type="tc_request",
            resource_id=None,
            summary={"day": on.isoformat(), "notified": created},
            system=True,
        )
    log.info("certificates.tc_overdue_notified", count=created)
    return created


def send_reminders(tenant_id: uuid.UUID, *, day: dt.date | None = None) -> int:
    """One school's reminder in its own transaction (the beat job's unit of work)."""
    with tenant_session(tenant_id) as session:
        return notify_overdue(session, day=day)


__all__ = ["AUDIT_ACTION", "TEMPLATE", "notify_overdue", "send_reminders"]
