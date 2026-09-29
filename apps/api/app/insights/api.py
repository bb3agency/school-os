"""Student timeline and early-warning routes (docs/09 Student insights; M5; US-1704..US-1709;
FR-EW-*).

Every route declares its guard. Restricted (C3) and purpose-limited (08 §4): the service also
needs ``student.read_sensitive`` in the same scope and answers 404 for any student, flag or note
outside the caller's scope. Reads of notes, flags and timelines are audited. There is no export
or download route for insights (FR-EW-016): only the school's full data export carries them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag
from app.insights import service
from app.insights.schemas import (
    ActionIn,
    AssignIn,
    CloseIn,
    DueFilter,
    ErasedOut,
    EraseIn,
    FlagStatus,
    FlagView,
    IndicatorName,
    InsightFlagDetail,
    InsightFlagOut,
    InsightSummaryOut,
    ManualFlagIn,
    NoteIn,
    NoteOut,
    OwnerOut,
    SettingsIn,
    SettingsOut,
    TimelineOut,
)

router = APIRouter(prefix="/api/v1", tags=["student insights"])

Reader = Annotated[UserContext, Depends(require(service.READ))]
NoteWriter = Annotated[UserContext, Depends(require(service.NOTE))]
Actor = Annotated[UserContext, Depends(require(service.ACT))]
ManagerRead = Annotated[UserContext, Depends(require(service.MANAGE, scope="school"))]
Manager = Annotated[UserContext, Depends(require(service.MANAGE, scope="school", step_up=True))]


def _flag_etag(response: Response, out: InsightFlagDetail) -> InsightFlagDetail:
    response.headers["ETag"] = etag(out.version)
    return out


# --- flags ----------------------------------------------------------------------------------------


@router.get("/insights/flags", response_model=Page[InsightFlagOut])
def list_flags(
    ctx: Reader,
    db: TenantDB,
    *,
    view: Annotated[FlagView, Query(description="mine (default) or all in your scope")] = "mine",
    status: FlagStatus | None = None,
    indicator: IndicatorName | None = None,
    section_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    due: DueFilter | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[InsightFlagOut]:
    """Early-warning flags (``insights.read``): ``mine`` = flags you own; ``all`` = every flag
    of students in your scope (class teachers: their sections). Without ``status``: open and in
    progress. Soonest due first. Audited."""
    return service.list_flags(
        db,
        ctx,
        view=view,
        status=status,
        indicator=indicator,
        section_id=section_id,
        student_id=student_id,
        due=due,
        limit=limit,
        cursor=cursor,
    )


@router.get("/insights/summary", response_model=InsightSummaryOut)
def summary(
    ctx: Reader,
    db: TenantDB,
    since: Annotated[dt.date | None, Query(description="Default: 30 days ago")] = None,
) -> InsightSummaryOut:
    """Counts only for your scope in this school: flags raised, first acted on by their due
    date (the M5 exit metric), late, not yet, overdue and open (``insights.read``)."""
    return service.summary(db, ctx, since)


@router.get("/insights/flags/{flag_id}", response_model=InsightFlagDetail)
def get_flag(
    ctx: Reader, db: TenantDB, flag_id: uuid.UUID, response: Response
) -> InsightFlagDetail:
    """One flag with the numbers that raised it and its action log (``insights.read``; 404
    outside your scope). Audited."""
    return _flag_etag(response, service.get_flag(db, ctx, flag_id))


@router.post("/students/{student_id}/flags", response_model=InsightFlagDetail, status_code=201)
def raise_flag(
    ctx: Actor, db: TenantDB, student_id: uuid.UUID, body: ManualFlagIn, idem: IdempotencyDep
) -> Response:
    """Raise a concern for a student in your scope (``insights.act``); owned by the class
    teacher when they may act, else by you. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.raise_flag(db, ctx, student_id, body),
        headers=lambda out: {
            "Location": f"/api/v1/insights/flags/{out.id}",
            "ETag": etag(out.version),
        },
    )


@router.post("/insights/flags/{flag_id}/actions", response_model=InsightFlagDetail, status_code=201)
def add_action(
    ctx: Actor, db: TenantDB, flag_id: uuid.UUID, body: ActionIn, response: Response
) -> InsightFlagDetail:
    """Record what was done about a flag (``insights.act``): talked with the student, called or
    met a parent, home visit, remedial support, referral, other; optional note. The first
    action marks the flag actioned. 409 ``flag_closed``."""
    return _flag_etag(response, service.add_action(db, ctx, flag_id, body))


@router.post("/insights/flags/{flag_id}/close", response_model=InsightFlagDetail)
def close_flag(
    ctx: Actor,
    db: TenantDB,
    *,
    flag_id: uuid.UUID,
    body: CloseIn,
    version: IfMatch,
    response: Response,
) -> InsightFlagDetail:
    """Close a flag with a reason (``insights.act``; ``If-Match``). 409 ``flag_closed``."""
    return _flag_etag(response, service.close_flag(db, ctx, flag_id, body, version))


@router.get("/insights/flags/{flag_id}/owners", response_model=list[OwnerOut])
def flag_owners(ctx: ManagerRead, db: TenantDB, flag_id: uuid.UUID) -> list[OwnerOut]:
    """Staff who may own this flag: active and allowed to act for the student's section
    (``insights.manage``)."""
    return service.owners(db, ctx, flag_id)


@router.post("/insights/flags/{flag_id}/assign", response_model=InsightFlagDetail)
def assign_flag(
    ctx: Manager,
    db: TenantDB,
    *,
    flag_id: uuid.UUID,
    body: AssignIn,
    version: IfMatch,
    response: Response,
) -> InsightFlagDetail:
    """Give a flag another owner (``insights.manage``, recent MFA sign-in; ``If-Match``). 422
    ``owner_not_eligible``."""
    return _flag_etag(response, service.assign_flag(db, ctx, flag_id, body, version))


@router.post("/insights/flags/{flag_id}/erase", response_model=ErasedOut)
def erase_flag(ctx: Manager, db: TenantDB, flag_id: uuid.UUID, body: EraseIn) -> ErasedOut:
    """Erase a flag and its action log on a parent's request or when raised in error
    (``insights.manage``, recent MFA sign-in). The reason is recorded, never the text."""
    return service.erase_flag(db, ctx, flag_id, body)


# --- settings -------------------------------------------------------------------------------------


@router.get("/insights/settings", response_model=SettingsOut)
def get_settings(ctx: Reader, db: TenantDB, response: Response) -> SettingsOut:
    """The school's early-warning rules with thresholds and their bounds (``insights.read``).
    The ETag is the settings version (0 = defaults)."""
    out = service.get_settings(db, ctx)
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/insights/settings", response_model=SettingsOut)
def update_settings(
    ctx: Manager, db: TenantDB, *, body: SettingsIn, version: IfMatch, response: Response
) -> SettingsOut:
    """Change thresholds or switch rules off within the bounds SchoolOS sets
    (``insights.manage``, recent MFA sign-in; ``If-Match``). The AP consecutive-absence rule
    cannot be switched off (422 ``rule_required``)."""
    out = service.update_settings(db, ctx, body, version)
    response.headers["ETag"] = etag(out.version)
    return out


# --- behaviour notes ------------------------------------------------------------------------------


@router.get("/students/{student_id}/behaviour-notes", response_model=list[NoteOut])
def list_notes(ctx: Reader, db: TenantDB, student_id: uuid.UUID) -> list[NoteOut]:
    """A student's behaviour notes, newest first (``insights.read``; restricted: class teacher
    and principal only; 404 outside your scope). Audited."""
    return service.list_notes(db, ctx, student_id)


@router.post("/students/{student_id}/behaviour-notes", response_model=NoteOut, status_code=201)
def add_note(
    ctx: NoteWriter, db: TenantDB, student_id: uuid.UUID, body: NoteIn, idem: IdempotencyDep
) -> Response:
    """Write a short behaviour note (``insights.note``): positive, observation or concern, up
    to 500 characters, stored encrypted and never sent to AI. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.add_note(db, ctx, student_id, body),
        headers=lambda out: {"Location": f"/api/v1/students/{student_id}/behaviour-notes"},
    )


@router.post("/behaviour-notes/{note_id}/erase", response_model=ErasedOut)
def erase_note(ctx: Manager, db: TenantDB, note_id: uuid.UUID, body: EraseIn) -> ErasedOut:
    """Erase a behaviour note on a parent's request or when entered in error
    (``insights.manage``, recent MFA sign-in)."""
    return service.erase_note(db, ctx, note_id, body)


# --- timeline -------------------------------------------------------------------------------------


@router.get("/students/{student_id}/timeline", response_model=TimelineOut)
def timeline(ctx: Reader, db: TenantDB, student_id: uuid.UUID) -> TimelineOut:
    """The student's timeline, newest first, with the attendance, behaviour and course
    indicators (``insights.read``; 404 outside your scope). Audited."""
    return service.timeline(db, ctx, student_id)
