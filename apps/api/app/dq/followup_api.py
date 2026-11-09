"""Routes for follow-ups on fixes outside the school (FO-01) and the UDISE+ to-do list (FO-03)
(FR-UDI-021..FR-UDI-040; docs/09 Data quality).

Every route declares its permission with ``require()``: reading needs ``dq.readiness.read``,
recording and ticking ``dq.readiness.manage``; the services limit everything to the students
the caller reaches and answer 404 outside them. Codes, dates and counts only: no record value
and never an Aadhaar number.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep, IfMatch, OptionalIfMatch, etag
from app.dq import followups, movements
from app.dq.followup_config import FollowupOwner, FollowupState
from app.dq.followup_schemas import (
    ITEM_KEY_PATTERN,
    FollowupHistoryOut,
    FollowupIn,
    FollowupListOut,
    FollowupRecordedOut,
    MovementItemOut,
    MovementListOut,
    MovementMarkIn,
    MovementState,
)
from app.dq.schemas import PROFILE_PATTERN

router = APIRouter(prefix="/api/v1/dq", tags=["data-quality"])

Reader = Annotated[UserContext, Depends(require(followups.READ))]
Manager = Annotated[UserContext, Depends(require(followups.MANAGE))]
ProfileKey = Annotated[
    str,
    Path(
        pattern=PROFILE_PATTERN,
        max_length=64,
        description="Readiness profile, e.g. bseap-ssc-2027",
    ),
]
ItemKey = Annotated[
    str,
    Path(
        pattern=ITEM_KEY_PATTERN,
        max_length=70,
        description="To-do item, e.g. transfer_out:<certificate id> or admission:<student id>",
    ),
]


# --- follow-ups (FO-01) -------------------------------------------------------------------------


@router.get("/readiness/{profile_key}/follow-ups", response_model=FollowupListOut)
def list_followups(
    *,
    ctx: Reader,
    db: TenantDB,
    profile_key: ProfileKey,
    section_id: uuid.UUID | None = None,
    state: Annotated[list[FollowupState] | None, Query(max_length=4)] = None,
    fix_owner: FollowupOwner | None = None,
) -> FollowupListOut:
    """Fixes waiting outside the school, overdue first: one line per student and fix owner
    (the parent via an Aadhaar update, the MEO / MIS coordinator via UDISE+) while the
    readiness check still finds the difference, with the last step recorded, the promised date
    and the state (``not_started``, ``waiting``, ``overdue``, ``update_reported``). Overdue:
    nothing recorded for ``overdue_after_days`` days or a promised date passed (permission
    ``dq.readiness.read``; class teachers see their sections; a section outside your reach:
    404). ``totals`` count every open follow-up in reach, before the filters."""
    return followups.list_followups(
        db, ctx, profile_key, section_id=section_id, states=state, fix_owner=fix_owner
    )


@router.get("/readiness/{profile_key}/follow-ups/{student_id}", response_model=FollowupHistoryOut)
def followup_history(
    ctx: Reader, db: TenantDB, profile_key: ProfileKey, student_id: uuid.UUID
) -> FollowupHistoryOut:
    """Every step recorded for one student and profile, oldest first, and the follow-ups still
    open (permission ``dq.readiness.read``; a student outside your reach: 404)."""
    return followups.history(db, ctx, profile_key, student_id)


@router.post(
    "/readiness/{profile_key}/follow-ups", response_model=FollowupRecordedOut, status_code=201
)
def record_followup(
    ctx: Manager, db: TenantDB, profile_key: ProfileKey, body: FollowupIn, idem: IdempotencyDep
) -> Response:
    """Record one step for up to 300 students (e.g. "slips given" for a printed section):
    ``slip_given``, ``parent_contacted`` (with the channel), ``promised_by`` (with the date),
    ``update_reported``; for UDISE+ fixes ``request_sent`` and ``followed_up`` (with the
    channel). No free text. Each student must be in your reach (else 404) and still have an
    open difference for ``fix_owner`` (else 422 ``no_open_fix``). Permission
    ``dq.readiness.manage``. Accepts ``Idempotency-Key``. Audited."""
    return idem.run(db, body, lambda: followups.record(db, ctx, profile_key, body), status_code=201)


# --- UDISE+ to-do list (FO-03) ------------------------------------------------------------------


@router.get("/udise-todo", response_model=MovementListOut)
def list_udise_todo(
    ctx: Reader,
    db: TenantDB,
    state: Annotated[list[MovementState] | None, Query(max_length=3)] = None,
) -> MovementListOut:
    """What to repeat in the UDISE+ Student Module after something happened in SchoolOS: a
    transfer certificate issued (mark transferred), a child who left without one (dropbox), an
    admission (import by PEN when the record holds one, otherwise search and create), a
    promotion (progression). Computed from the records on every read; the steps are
    ``verified: false`` (permission ``dq.readiness.read``; class teachers see their students,
    progression items need a school-wide reader). Open items first, longest waiting first."""
    return movements.list_items(db, ctx, states=state)


@router.put("/udise-todo/{item_key}", response_model=MovementItemOut)
def mark_udise_todo(
    *,
    ctx: Manager,
    db: TenantDB,
    item_key: ItemKey,
    body: MovementMarkIn,
    version: OptionalIfMatch,
    response: Response,
) -> MovementItemOut:
    """Tick an item ``done`` (repeated in UDISE+) or ``not_needed`` (permission
    ``dq.readiness.manage``). The first tick needs no ``If-Match``; changing one does (400
    ``if_match_required``, 412 when stale). An item outside your reach: 404. Audited."""
    out = movements.mark(db, ctx, item_key, body.state, expected_version=version)
    if out.version is not None:
        response.headers["ETag"] = etag(out.version)
    return out


@router.delete("/udise-todo/{item_key}", status_code=204)
def reopen_udise_todo(ctx: Manager, db: TenantDB, item_key: ItemKey, version: IfMatch) -> Response:
    """Undo a tick: the item is open again (permission ``dq.readiness.manage``, ``If-Match``;
    nothing ticked: 404). Audited."""
    movements.reopen(db, ctx, item_key, expected_version=version)
    return Response(status_code=204)
