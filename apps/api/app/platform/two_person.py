"""Shared checks for the control plane's two-person rules (SEC-029; audit 2026-10-05 A-13, A-14).

A two-person flow has a first step (a request or first confirmation by operator A) and a second
step (an approval or second confirmation by a DIFFERENT operator B). Besides ``A != B`` (service
check and DB CHECK), the second step now also requires:

- the request is younger than ``two_person_request_ttl_hours`` (roles.yaml): 409
  ``request_expired``; the request can be withdrawn and made again;
- operator A is still active and still holds the permission: 409 ``requester_not_authorised``;
- operator B has held a role granting the permission for at least
  ``two_person_min_role_age_days`` (roles.yaml), and neither operator's qualifying role was
  granted by the other: 409 ``approver_not_eligible``. One owner can therefore not invite a
  second account they control, make it an owner and approve their own request with it.

All values come from the versioned ``app/platform/roles.yaml``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import Conflict
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import now
from app.platform.permissions import catalog


def request_ttl() -> dt.timedelta:
    return dt.timedelta(hours=catalog().two_person_request_ttl_hours)


def expires_at(requested_at: dt.datetime | None) -> dt.datetime | None:
    """When a request made at ``requested_at`` stops being approvable (None: no request)."""
    return None if requested_at is None else requested_at + request_ttl()


def is_expired(requested_at: dt.datetime, at: dt.datetime | None = None) -> bool:
    return (at or now()) >= requested_at + request_ttl()


def _qualifying(grants: list[Any], permission: str) -> list[Any]:
    roles = catalog().roles_granting(permission)
    return [g for g in grants if g["role_key"] in roles]


def check_second_step(
    session: Session,
    *,
    first_operator_id: uuid.UUID,
    approver_id: uuid.UUID | None,
    permission: str,
    requested_at: dt.datetime,
) -> None:
    """Raise ``Conflict`` unless the second step may complete now (see the module docstring).

    ``approver_id`` is the operator taking the second step (``require_platform`` has already
    checked that they are active and hold ``permission``)."""
    at = now()
    if is_expired(requested_at, at):
        raise Conflict(
            "This request has expired. Withdraw it and make a new request if it is still needed.",
            code="request_expired",
        )
    first = repo.get(session, m.operators, first_operator_id)
    first_grants = _qualifying(repo.operator_role_grants(session, first_operator_id), permission)
    if first is None or first["status"] != "active" or not first_grants:
        raise Conflict(
            "The operator who made this request is no longer active or no longer allowed to make "
            "it. Withdraw it; a current operator can make a new request.",
            code="requester_not_authorised",
        )
    if approver_id is None:
        return
    min_age = dt.timedelta(days=catalog().two_person_min_role_age_days)
    own = [
        g
        for g in _qualifying(repo.operator_role_grants(session, approver_id), permission)
        if g["granted_at"] is not None
        and g["granted_at"] <= at - min_age
        and g["granted_by"] != first_operator_id
    ]
    first_independent = [g for g in first_grants if g["granted_by"] != approver_id]
    if not own or not first_independent:
        raise Conflict(
            "You cannot be the second operator on this request: your role is too new, or one of "
            "you was given the role by the other. Ask another operator.",
            code="approver_not_eligible",
        )
