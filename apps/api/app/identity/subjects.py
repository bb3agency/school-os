"""Staff accounts in a data principal's access report (R3-1A; FR-DPDP-009, PRV-031;
ADR-0048 decision 6).

A staff member (subject kind ``member``, ``core.memberships.id``) sees their account in this
school: name, email, status, when they were added and last signed in, their roles and their
access scopes. Never the sign-in identity (issuer, subject), phone ciphertext or anything about
other members. Not an eraser: the account is kept (deactivated) for the audit trail
(``staff_accounts`` in ``app/privacy/record_classes.yaml``).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.core.report_format import EMPTY, cap_rows, fmt_date, fmt_datetime, fmt_text, humanise
from app.core.subjects import ReportSection, Subject
from app.identity import repository as repo
from app.identity.models import Membership, MembershipScope
from app.tenancy import service as tenancy

CLASS_KEY = "staff_accounts"


def _email_hidden(session: Session, membership: Membership) -> bool:
    """Same rule as the staff list (audit DL-09): the email of an account shared with another
    school is shown only while this school's membership is accepted (active or suspended)."""
    shared = (repo.user_membership_count(session, membership.user_id) or 0) > 1
    return shared and membership.status not in ("active", "suspended")


def _class_label(session: Session, class_id: uuid.UUID) -> str:
    try:
        return tenancy.get_class(session, class_id).display_en
    except NotFound:
        return "a class no longer in use"


def _scope_label(session: Session, scope: MembershipScope) -> str:
    if scope.scope_type == "school" or scope.scope_ref is None:
        return "Whole school"
    if scope.scope_type == "class":
        return f"Class: {_class_label(session, scope.scope_ref)}"
    if scope.scope_type == "section":
        try:
            section = tenancy.get_section(session, scope.scope_ref)
        except NotFound:
            return "Section: a section no longer in use"
        return f"Section: {_class_label(session, section.class_id)} {section.name}"
    return humanise(scope.scope_type)


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The member's account, roles and scopes ([] for other subject kinds or an unknown id)."""
    if subject.kind != "member":
        return []
    membership = repo.get_membership(session, subject.id)
    if membership is None:
        return []
    user = repo.get_user(session, membership.user_id)
    if user is None:  # pragma: no cover - RLS shows users with a membership here
        return []
    hidden = _email_hidden(session, membership)
    name = (membership.invited_display_name or user.display_name) if hidden else user.display_name
    account = ReportSection(
        class_key=CLASS_KEY,
        title="Staff account",
        columns=("Detail", "Value"),
        rows=(
            ("Name", fmt_text(name)),
            (
                "Email",
                "Not shown until the invitation is accepted" if hidden else fmt_text(user.email),
            ),
            ("Status", humanise(membership.status)),
            ("Added to the school on", fmt_date(membership.created_at)),
            ("Access ends on", fmt_date(membership.expires_at)),
            ("Last signed in", EMPTY if hidden else fmt_datetime(user.last_login_at)),
        ),
    )
    grants = repo.membership_role_grants(session, membership.id)
    role_rows, role_note = cap_rows([(fmt_text(r.name_en), fmt_date(at)) for r, at in grants])
    roles = ReportSection(
        class_key=CLASS_KEY,
        title="Roles",
        columns=("Role", "Given on"),
        rows=tuple(role_rows),
        note=role_note if grants else "No roles.",
    )
    scopes = sorted(
        repo.list_membership_scopes(session, membership.id),
        key=lambda s: (s.created_at, str(s.id)),
        reverse=True,
    )
    scope_rows, scope_note = cap_rows(
        [(_scope_label(session, s), fmt_date(s.created_at)) for s in scopes]
    )
    access = ReportSection(
        class_key=CLASS_KEY,
        title="Access scope",
        columns=("Can see", "Given on"),
        rows=tuple(scope_rows),
        note=scope_note if scopes else "No scope recorded.",
    )
    return [account, roles, access]


__all__ = ["subject_report"]
