"""Data-quality routes (docs/09 Data quality; US-501..US-506, FR-DQ-002, FR-DQ-020,
FR-DQ-040..FR-DQ-046).

Every route declares its permission with ``require()``; the service limits findings to the
caller's students (class teachers: their sections this year) and answers 404 outside them.
Values in findings are masked; C3 values are never returned in clear by the findings routes.
The readiness student view and slips (ADR-0040) show C3 values only to holders of
``student.read_sensitive`` for the student, and audit it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import StringConstraints

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag, if_match_version
from app.authz.http import OptionalIfMatch as HttpOptionalIfMatch
from app.dq import calendar_service as calendar
from app.dq import portal_tasks
from app.dq import readiness_service as readiness
from app.dq import service as dq
from app.dq.schemas import (
    EVENT_KEY_PATTERN,
    PROFILE_PATTERN,
    CalendarAssigneeOut,
    CalendarConfirmIn,
    CalendarEventIn,
    CalendarEventOut,
    CalendarOut,
    FindingFilters,
    FindingOut,
    FindingStatus,
    PortalTaskLinkIn,
    ProfileOut,
    ReadinessOverviewOut,
    ReadinessRunIn,
    ReadinessStatus,
    ReadinessStudentDetailOut,
    ReadinessStudentOut,
    ReadinessSummaryOut,
    ResolveIn,
    RuleId,
    RuleOut,
    RunCreate,
    RunOut,
    SeverityName,
    SummaryOut,
    WaiveIn,
)
from app.dq.slip import STYLE_CSP as SLIP_CSP

router = APIRouter(prefix="/api/v1/dq", tags=["data-quality"])

Reader = Annotated[UserContext, Depends(require(dq.READ))]
Resolver = Annotated[UserContext, Depends(require(dq.RESOLVE))]
# dq.findings.waive is a step-up permission (docs/07 §6.2 ✓ᴿ): every waiver needs a fresh MFA
# sign-in at the route; the service also insists on it for blockers (docs/09).
Waiver = Annotated[UserContext, Depends(require(dq.WAIVE, step_up=True))]


def optional_if_match(request: Request) -> int | None:
    """``If-Match`` when sent (lost-update guard); the finding's ETag is its version."""
    if request.headers.get("if-match") is None:
        return None
    return if_match_version(request)


OptionalIfMatch = Annotated[int | None, Depends(optional_if_match)]


@router.post("/runs", response_model=RunOut, status_code=202)
def start_run(ctx: Resolver, db: TenantDB, body: RunCreate, idem: IdempotencyDep) -> Response:
    """Check sections, classes, students or an import batch, optionally for an export profile
    such as ``cisce-registration-2026`` (permission ``dq.findings.resolve``: read-only holders
    such as auditors and class teachers read findings but do not start runs; value changes
    re-check students automatically). Small scopes are
    checked at once (status ``completed``); bigger ones are queued (status ``queued``) and you
    are notified when they finish. A section or class outside your scope answers like an unknown
    one (422 ``not_found``). Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: dq.request_run(db, ctx, body),
        status_code=202,
        headers=lambda r: {"Location": f"/api/v1/dq/runs/{r.id}"},
    )


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(ctx: Reader, db: TenantDB, run_id: uuid.UUID) -> RunOut:
    """A check run with its counts (permission ``dq.findings.read``; class teachers see their
    own runs)."""
    return dq.get_run(db, ctx, run_id)


@router.get("/findings", response_model=Page[FindingOut])
def list_findings(
    *,
    ctx: Reader,
    db: TenantDB,
    severity: Annotated[list[SeverityName] | None, Query()] = None,
    rule_id: Annotated[list[RuleId] | None, Query()] = None,
    status: Annotated[list[FindingStatus] | None, Query()] = None,
    section_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    profile_key: Annotated[str | None, Query(pattern=PROFILE_PATTERN)] = None,
    attribute_key: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[FindingOut]:
    """Findings, most severe first, with masked values, English explanations (``te`` empty while
    Telugu is hidden, ADR-0036) and correction routes (permission ``dq.findings.read``).
    ``status`` defaults to unresolved (``open``, ``reopened``)."""
    filters = FindingFilters(
        severity=severity,
        rule_id=rule_id,
        status=status,
        section_id=section_id,
        student_id=student_id,
        profile_key=profile_key,
        attribute_key=attribute_key,
    )
    return dq.list_findings(db, ctx, filters, limit=limit, cursor=cursor)


@router.get("/findings/{finding_id}", response_model=FindingOut)
def get_finding(ctx: Reader, db: TenantDB, finding_id: uuid.UUID, response: Response) -> FindingOut:
    """One finding (permission ``dq.findings.read``); ``ETag`` is its version."""
    out = dq.get_finding(db, ctx, finding_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/findings/{finding_id}/resolve", response_model=FindingOut)
def resolve_finding(
    *,
    ctx: Resolver,
    db: TenantDB,
    finding_id: uuid.UUID,
    body: ResolveIn,
    version: OptionalIfMatch,
    response: Response,
) -> FindingOut:
    """Resolve with a note or a linked change request (permission ``dq.findings.resolve``).
    A blocker also needs ``dq.findings.waive`` (else 403 ``blocker_needs_waive``) and a fresh
    MFA sign-in (else 428 ``step_up_required``), like waiving it. If the conflict is still
    there, the next check reopens it. Optional ``If-Match``."""
    out = dq.resolve_finding(db, ctx, finding_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/findings/{finding_id}/waive", response_model=FindingOut)
def waive_finding(
    *,
    ctx: Waiver,
    db: TenantDB,
    finding_id: uuid.UUID,
    body: WaiveIn,
    version: OptionalIfMatch,
    response: Response,
) -> FindingOut:
    """Accept a finding with a reason (permission ``dq.findings.waive`` with a fresh MFA
    sign-in, else 428 ``step_up_required``). Optional ``If-Match``."""
    out = dq.waive_finding(db, ctx, finding_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/rules", response_model=list[RuleOut])
def list_rules(ctx: Reader) -> list[RuleOut]:
    """The rule catalog DQ-001..DQ-012 with English texts (``te`` empty while Telugu is
    hidden, ADR-0036; permission ``dq.findings.read``)."""
    return dq.rules_catalog()


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(
    ctx: Reader,
    db: TenantDB,
    include_all: Annotated[
        bool,
        Query(alias="all", description="Also list profiles of boards the school did not declare."),
    ] = False,
) -> list[ProfileOut]:
    """Export pre-check profiles, e.g. UDISE+, CBSE registration and CISCE (permission
    ``dq.findings.read``). Board profiles are listed for the school's declared boards only
    (FR-TEN-020; every profile while none is declared); each says which of its classes follow
    its board here, whether its format is verified, and its public sources (ADR-0041)."""
    return dq.school_profiles(db, include_all=include_all)


@router.get("/summary", response_model=SummaryOut)
def get_summary(
    *,
    ctx: Reader,
    db: TenantDB,
    profile_key: Annotated[str | None, Query(pattern=PROFILE_PATTERN)] = None,
    section_ids: Annotated[list[uuid.UUID] | None, Query(max_length=100)] = None,
) -> SummaryOut:
    """Unresolved findings by severity and rule for the pre-check screen: blockers apart from
    warnings (permission ``dq.findings.read``)."""
    return dq.summary(db, ctx, profile_key=profile_key, section_ids=section_ids)


# --- board and portal readiness (US-504..US-506, FR-DQ-040..FR-DQ-046, ADR-0040) ---------------

ReadinessReader = Annotated[UserContext, Depends(require(readiness.READ))]
ReadinessManager = Annotated[UserContext, Depends(require(readiness.MANAGE))]
ProfileKey = Annotated[
    str,
    Path(
        pattern=PROFILE_PATTERN,
        max_length=64,
        description="Readiness profile, e.g. bseap-ssc-2027, apaar",
    ),
]

_SLIP_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": SLIP_CSP,
    "X-Content-Type-Options": "nosniff",
}


ProfileKeyValue = Annotated[str, StringConstraints(pattern=PROFILE_PATTERN, max_length=64)]


@router.get("/readiness", response_model=ReadinessOverviewOut)
def readiness_overview(
    *,
    ctx: ReadinessReader,
    db: TenantDB,
    profile_keys: Annotated[
        list[ProfileKeyValue] | None,
        Query(
            max_length=readiness.MAX_OVERVIEW_PROFILES,
            description="Readiness profiles (repeat the parameter); omitted: every readiness "
            "profile listed for this school.",
        ),
    ] = None,
    section_ids: Annotated[list[uuid.UUID] | None, Query(max_length=100)] = None,
) -> ReadinessOverviewOut:
    """Readiness of several boards and portals in one call: for each profile (at most 5, in
    the order given) the same totals and per-section counts as ``GET
    /dq/readiness/{profile_key}`` (permission ``dq.readiness.read``; class teachers see their
    sections). Without ``profile_keys``, every readiness profile listed for the school. An
    unknown profile, or one without a readiness check: 404."""
    return readiness.overview(db, ctx, profile_keys, section_ids=section_ids)


@router.get("/readiness/{profile_key}", response_model=ReadinessSummaryOut)
def readiness_summary(
    *,
    ctx: ReadinessReader,
    db: TenantDB,
    profile_key: ProfileKey,
    section_ids: Annotated[list[uuid.UUID] | None, Query(max_length=100)] = None,
) -> ReadinessSummaryOut:
    """Board or portal readiness by section ("142 of 160 ready"): ready, waiting for the
    parent (Aadhaar), waiting for the school (UDISE+ or the register) and blocked (needs a
    decision), computed from the current records (permission ``dq.readiness.read``; class
    teachers see their sections). Unknown profile: 404."""
    return readiness.summary(db, ctx, profile_key, section_ids=section_ids)


@router.get("/readiness/{profile_key}/students", response_model=list[ReadinessStudentOut])
def readiness_students(
    *,
    ctx: ReadinessReader,
    db: TenantDB,
    profile_key: ProfileKey,
    section_id: uuid.UUID,
    status: Annotated[list[ReadinessStatus] | None, Query()] = None,
) -> list[ReadinessStudentOut]:
    """The students of one section with their readiness and who must act, worst first (no
    values; permission ``dq.readiness.read``). A section outside your scope: 404."""
    return readiness.section_students(db, ctx, profile_key, section_id, status=status)


@router.get(
    "/readiness/{profile_key}/students/{student_id}", response_model=ReadinessStudentDetailOut
)
def readiness_student(
    ctx: ReadinessReader, db: TenantDB, profile_key: ProfileKey, student_id: uuid.UUID
) -> ReadinessStudentDetailOut:
    """One student: the value every record holds, each exact difference (character by
    character when you may see both values) and who must fix it (permission
    ``dq.readiness.read``). Aadhaar-as-printed values appear only with
    ``student.read_sensitive`` for the student, and that view is audited."""
    return readiness.student_detail(db, ctx, profile_key, student_id)


@router.post("/readiness/{profile_key}/runs", response_model=RunOut, status_code=202)
def start_readiness_run(
    ctx: ReadinessManager,
    db: TenantDB,
    profile_key: ProfileKey,
    body: ReadinessRunIn,
    idem: IdempotencyDep,
) -> Response:
    """Check sections, classes or students for the profile and store each difference as a
    DQ-031 finding, so it can be resolved, waived or linked to a change request (permission
    ``dq.readiness.manage``). An empty scope means the profile's classes. Small scopes are
    checked at once (``completed``), bigger ones are queued. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: readiness.request_run(db, ctx, profile_key, body),
        status_code=202,
        headers=lambda r: {"Location": f"/api/v1/dq/runs/{r.id}"},
    )


@router.get(
    "/readiness/{profile_key}/slips",
    response_class=HTMLResponse,
    responses={
        200: {"content": {"text/html": {}}, "description": "Print-ready A4 slips"},
        204: {"description": "With check=true: the slips can be printed now (nothing audited)"},
    },
)
def readiness_slips(
    *,
    ctx: ReadinessReader,
    db: TenantDB,
    profile_key: ProfileKey,
    student_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    include_ready: bool = True,
    include_sensitive: bool = False,
    check: Annotated[
        bool,
        Query(
            description="true: only check that the slips can be printed now (permission, "
            "reach, step-up for include_sensitive) and answer 204 without the page; the print "
            "view itself is audited once."
        ),
    ] = False,
) -> Response:
    """Parent verification slips (one A4 page per student) for one student or one section:
    the value each record holds, the differences and who must fix them, and a signature line
    for the parent (permission ``dq.readiness.read``). Aadhaar-as-printed values are masked
    unless ``include_sensitive=true``, which needs ``student.read_sensitive`` for every student
    (403 ``sensitive_not_allowed``) and a recent sign-in with MFA (428 ``step_up_required``),
    like the PDF print; never an Aadhaar number. Printing is audited, naming the students whose
    restricted values were shown; with ``check=true`` 204 only, not audited."""
    page = readiness.slips(
        db,
        ctx,
        profile_key,
        student_id=student_id,
        section_id=section_id,
        include_ready=include_ready,
        include_sensitive=include_sensitive,
        check=check,
    )
    if page is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    name = f"verification-slips-{profile_key}.html"
    return HTMLResponse(
        page, headers={**_SLIP_HEADERS, "Content-Disposition": f'inline; filename="{name}"'}
    )


# --- portal calendar (US-3101..US-3104, FR-RDY-001..010, ADR-0053) -----------------------------

CalendarReader = Annotated[UserContext, Depends(require(calendar.READ))]
CalendarManager = Annotated[UserContext, Depends(require(calendar.MANAGE))]
EventKey = Annotated[
    str,
    Path(
        pattern=EVENT_KEY_PATTERN,
        max_length=64,
        description="Calendar event, e.g. bseap_ssc_2027_nominal_roll, or school:<slug> for the "
        "school's own event",
    ),
]


@router.get("/calendar", response_model=CalendarOut)
def get_calendar(
    ctx: CalendarReader, db: TenantDB, academic_year_id: uuid.UUID | None = None
) -> CalendarOut:
    """The school's portal calendar for an academic year (default: the current one): UDISE+,
    board registration and nominal-roll windows, APAAR drives and the school's own events that
    concern this school (by its boards and classes), soonest closing first (permission
    ``dq.readiness.read``). Each shows the dates in force: ``confirmed`` by a person at the
    school, else the calendar file's unverified expectation ("Expected around ..."); its state
    (``expected``, ``confirmed``, ``open``, ``closed``); the owner; and the readiness of its
    linked profiles in your scope ("142 of 160 ready")."""
    return calendar.list_calendar(db, ctx, academic_year_id)


@router.get("/calendar/assignees", response_model=list[CalendarAssigneeOut])
def calendar_assignees(ctx: CalendarManager, db: TenantDB) -> list[CalendarAssigneeOut]:
    """Active staff who can own a portal window (permission ``readiness.calendar.manage``)."""
    return calendar.assignees(db)


@router.put("/calendar/{event_key}", response_model=CalendarEventOut)
def confirm_calendar_event(
    *,
    ctx: CalendarManager,
    db: TenantDB,
    event_key: EventKey,
    body: CalendarConfirmIn,
    version: HttpOptionalIfMatch,
    response: Response,
) -> CalendarEventOut:
    """Confirm an event's opening and closing dates, where they came from (a circular, the
    portal, a phone call; optionally the document) and who owns the work (permission
    ``readiness.calendar.manage``). Confirmed dates win over the expected ones. The first
    confirmation needs no ``If-Match``; changing one does (400 ``if_match_required``, 412 when
    stale). An event that does not concern the school: 404. Audited."""
    out = calendar.confirm(db, ctx, event_key, body, expected_version=version)
    if out.version is not None:
        response.headers["ETag"] = etag(out.version)
    return out


@router.post("/calendar", response_model=CalendarEventOut, status_code=201)
def add_calendar_event(
    ctx: CalendarManager, db: TenantDB, body: CalendarEventIn, idem: IdempotencyDep
) -> Response:
    """Add the school's own event: a label in the school's words (no personal data), its dates,
    the readiness profiles it concerns and an owner (permission ``readiness.calendar.manage``).
    It gets reminders like the portal windows. Accepts ``Idempotency-Key``. Audited."""
    return idem.run(
        db,
        body,
        lambda: calendar.add_event(db, ctx, body),
        status_code=201,
        headers=lambda r: {"ETag": etag(r.version)} if r.version is not None else {},
    )


@router.delete("/calendar/{event_key}", status_code=204)
def remove_calendar_event(
    *,
    ctx: CalendarManager,
    db: TenantDB,
    event_key: EventKey,
    academic_year_id: uuid.UUID,
    version: IfMatch,
) -> Response:
    """Remove the school's own event, or undo a confirmation so the expected dates show again
    (permission ``readiness.calendar.manage``; ``If-Match``). Reminders already sent stay in
    the history. Audited."""
    calendar.remove(db, ctx, event_key, academic_year_id, expected_version=version)
    return Response(status_code=204)


tasks_router = APIRouter(prefix="/api/v1/tasks", tags=["data-quality"])


@tasks_router.post("/{task_id}/portal-event", response_model=CalendarEventOut)
def link_task_to_portal_event(
    ctx: CalendarManager,
    db: TenantDB,
    task_id: uuid.UUID,
    body: PortalTaskLinkIn,
    idem: IdempotencyDep,
) -> Response:
    """Link a task confirmed from a circular's deadline to a portal calendar event: the task's
    due date becomes the event's confirmed closing date, from that circular (permission
    ``readiness.calendar.manage`` and a school-wide ``task.manage``, else 403; a task you cannot
    reach: 404; a task not from a circular: 422 ``not_circular_task``). A person's action, never
    automatic. Accepts ``Idempotency-Key``. Audited."""
    return idem.run(
        db, body, lambda: portal_tasks.link_task(db, ctx, task_id, body), status_code=200
    )
