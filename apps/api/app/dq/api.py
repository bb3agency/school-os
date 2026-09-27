"""Data-quality routes (docs/09 Data quality; US-501, US-502, FR-DQ-002, FR-DQ-020).

Every route declares its permission with ``require()``; the service limits findings to the
caller's students (class teachers: their sections this year) and answers 404 outside them.
Values in findings are masked; C3 values are never returned in clear here.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, Limit, Page, etag, if_match_version
from app.dq import service as dq
from app.dq.schemas import (
    PROFILE_PATTERN,
    FindingFilters,
    FindingOut,
    FindingStatus,
    ProfileOut,
    ResolveIn,
    RuleId,
    RuleOut,
    RunCreate,
    RunOut,
    SeverityName,
    SummaryOut,
    WaiveIn,
)

router = APIRouter(prefix="/api/v1/dq", tags=["data-quality"])

Reader = Annotated[UserContext, Depends(require(dq.READ))]
Resolver = Annotated[UserContext, Depends(require(dq.RESOLVE))]
Waiver = Annotated[UserContext, Depends(require(dq.WAIVE))]


def optional_if_match(request: Request) -> int | None:
    """``If-Match`` when sent (lost-update guard); the finding's ETag is its version."""
    if request.headers.get("if-match") is None:
        return None
    return if_match_version(request)


OptionalIfMatch = Annotated[int | None, Depends(optional_if_match)]


@router.post("/runs", response_model=RunOut, status_code=202)
def start_run(ctx: Reader, db: TenantDB, body: RunCreate, idem: IdempotencyDep) -> Response:
    """Check sections, classes, students or an import batch, optionally for an export profile
    such as ``cisce-registration-2026`` (permission ``dq.findings.read``). Small scopes are
    checked at once (status ``completed``); bigger ones are queued (status ``queued``) and you
    are notified when they finish. Accepts ``Idempotency-Key``."""
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
    """Findings, most severe first, with masked values, English/Telugu explanations and
    correction routes (permission ``dq.findings.read``). ``status`` defaults to unresolved
    (``open``, ``reopened``)."""
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
    If the conflict is still there, the next check reopens it. Optional ``If-Match``."""
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
    """Accept a finding with a reason (permission ``dq.findings.waive``; blockers also need a
    fresh MFA sign-in, 428 ``step_up_required``). Optional ``If-Match``."""
    out = dq.waive_finding(db, ctx, finding_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/rules", response_model=list[RuleOut])
def list_rules(ctx: Reader) -> list[RuleOut]:
    """The rule catalog DQ-001..DQ-012 with English/Telugu texts (permission
    ``dq.findings.read``)."""
    return dq.rules_catalog()


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(ctx: Reader) -> list[ProfileOut]:
    """Export pre-check profiles, e.g. CISCE registration and UDISE+ (permission
    ``dq.findings.read``)."""
    return dq.profiles_catalog()


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
