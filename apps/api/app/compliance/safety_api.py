"""Safety check log and policy library routes (docs/09 "Safety checks and policies"; FC-09,
FC-10; FR-COMP-046..FR-COMP-070).

Office side: reads declare ``require(compliance.read)``, writes ``require(compliance.manage)``,
school-wide. Staff side (``/compliance/my-policies``): the implicit ``session.authenticated``;
the service answers 404 for a policy not asked of the caller. Writes to an existing row take
``If-Match``; creating one accepts ``Idempotency-Key``. Another school's row is 404 (RLS).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.authz.catalog import AUTHENTICATED
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep, IfMatch, etag
from app.compliance import checks, policies
from app.compliance.safety_schemas import (
    CheckCreate,
    ChecksLogOut,
    ChecksOverviewOut,
    ComplianceCheckOut,
    CompliancePolicyOut,
    MyPoliciesOut,
    MyPolicyOut,
    PoliciesOut,
    PolicyAcknowledge,
    PolicyAckRequestOut,
    PolicyCreate,
    PolicyDetailOut,
    PolicyFileOut,
    PolicyReview,
    PolicyUpdate,
)

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])

Reader = Annotated[UserContext, Depends(require("compliance.read", scope="school"))]
Manager = Annotated[UserContext, Depends(require("compliance.manage", scope="school"))]
Member = Annotated[UserContext, Depends(require(AUTHENTICATED))]


def _etag[M: (ComplianceCheckOut, CompliancePolicyOut, PolicyDetailOut)](
    response: Response, item: M
) -> M:
    response.headers["ETag"] = etag(item.version)
    return item


# --- safety drills and periodic checks (FC-09) --------------------------------------------------


@router.get("/checks", response_model=ChecksOverviewOut)
def get_checks(ctx: Reader, db: TenantDB) -> ChecksOverviewOut:
    """Every drill and periodic check that applies to the school with its next due date
    (overdue, never done, due soon, ok), counts, the latest entries and the catalogue
    (``verified: false``). Permission ``compliance.read``."""
    return checks.overview(db, ctx)


@router.get("/checks/entries", response_model=ChecksLogOut)
def list_check_entries(
    ctx: Reader,
    db: TenantDB,
    kind: Annotated[str | None, Query(max_length=63, pattern=r"^[a-z][a-z0-9_]{1,62}$")] = None,
) -> ChecksLogOut:
    """The log, newest first (at most 200), optionally of one kind. Permission
    ``compliance.read``."""
    return checks.entries(db, ctx, kind=kind)


@router.post("/checks/entries", response_model=ComplianceCheckOut, status_code=201)
def record_check(ctx: Manager, db: TenantDB, body: CheckCreate, idem: IdempotencyDep) -> Response:
    """Log a drill or check (counts and a short note; never student names). Permission
    ``compliance.manage``. Errors (422): ``unknown_kind``, ``label_required``,
    ``label_not_allowed``, ``done_in_future``, ``drill_fields_not_allowed``,
    ``document_not_found``, ``leader_not_active``, ``aadhaar_full_number_rejected``. Accepts
    ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: checks.record(db, ctx, body),
        headers=lambda item: {"ETag": etag(item.version)},
    )


@router.post("/checks/entries/{check_id}:remove", response_model=ComplianceCheckOut)
def remove_check(
    ctx: Manager, db: TenantDB, check_id: uuid.UUID, version: IfMatch, response: Response
) -> ComplianceCheckOut:
    """Remove an entry logged by mistake (kept as removed; permission ``compliance.manage``,
    ``If-Match``). Errors: 409 ``check_removed``, 412 stale ``If-Match``."""
    return _etag(response, checks.remove(db, ctx, check_id, expected_version=version))


# --- policy library (FC-10) ---------------------------------------------------------------------


@router.get("/policies", response_model=PoliciesOut)
def list_policies(ctx: Reader, db: TenantDB) -> PoliciesOut:
    """The school's policies with their review state and how many of the asked staff have
    acknowledged the current version. Permission ``compliance.read``."""
    return policies.library(db, ctx)


@router.post("/policies", response_model=CompliancePolicyOut, status_code=201)
def create_policy(ctx: Manager, db: TenantDB, body: PolicyCreate, idem: IdempotencyDep) -> Response:
    """Add a policy: a clean PDF in Documents of purpose policy, its next review date and the
    roles whose members must acknowledge it (with ``notify``, they get an in-app notice).
    Permission ``compliance.manage``. Errors (422): ``policy_document_not_ready``,
    ``review_in_past``, ``unknown_role``, ``too_many_roles``, ``aadhaar_full_number_rejected``.
    Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: policies.create(db, ctx, body),
        headers=lambda item: {
            "Location": f"/api/v1/compliance/policies/{item.id}",
            "ETag": etag(item.version),
        },
    )


@router.get("/policies/{policy_id}", response_model=PolicyDetailOut)
def get_policy(
    ctx: Reader, db: TenantDB, policy_id: uuid.UUID, response: Response
) -> PolicyDetailOut:
    """One policy with who has and has not acknowledged the current version (permission
    ``compliance.read``). ``ETag`` for ``If-Match``."""
    return _etag(response, policies.get_policy(db, ctx, policy_id))


@router.put("/policies/{policy_id}", response_model=CompliancePolicyOut)
def update_policy(
    *,
    ctx: Manager,
    db: TenantDB,
    policy_id: uuid.UUID,
    body: PolicyUpdate,
    version: IfMatch,
    response: Response,
) -> CompliancePolicyOut:
    """Correct or retire a policy (every field; permission ``compliance.manage``,
    ``If-Match``). Errors: 412 stale ``If-Match``, 422 as when adding."""
    return _etag(response, policies.update(db, ctx, policy_id, body, expected_version=version))


@router.post("/policies/{policy_id}:review", response_model=CompliancePolicyOut)
def review_policy(
    *,
    ctx: Manager,
    db: TenantDB,
    policy_id: uuid.UUID,
    body: PolicyReview,
    version: IfMatch,
    response: Response,
) -> CompliancePolicyOut:
    """Mark the policy reviewed today and set the next review date (default in 12 months;
    permission ``compliance.manage``, ``If-Match``). Errors: 409 ``policy_retired``, 412 stale
    ``If-Match``, 422 ``review_in_past``."""
    return _etag(response, policies.review(db, ctx, policy_id, body, expected_version=version))


@router.post("/policies/{policy_id}:request-acknowledgement", response_model=PolicyAckRequestOut)
def request_policy_acknowledgement(
    ctx: Manager, db: TenantDB, policy_id: uuid.UUID
) -> PolicyAckRequestOut:
    """Send an in-app notice to the asked staff who have not acknowledged the current version
    (at most once a day each; permission ``compliance.manage``). Errors: 409
    ``policy_retired``, 409 ``policy_document_not_ready``."""
    return policies.request_acknowledgement(db, ctx, policy_id)


# --- the signed-in member's own policies (session.authenticated) --------------------------------


@router.get("/my-policies", response_model=MyPoliciesOut)
def list_my_policies(ctx: Member, db: TenantDB) -> MyPoliciesOut:
    """The school policies you are asked to read and acknowledge (any signed-in member)."""
    return policies.my_policies(db, ctx)


@router.get("/my-policies/{policy_id}/file", response_model=PolicyFileOut)
def get_my_policy_file(ctx: Member, db: TenantDB, policy_id: uuid.UUID) -> PolicyFileOut:
    """A link (5 minutes) to the version of the policy you are asked to read. Errors: 404 (not
    asked of you), 409 ``policy_document_not_ready``."""
    return policies.policy_file(db, ctx, policy_id)


@router.post("/my-policies/{policy_id}:acknowledge", response_model=MyPolicyOut)
def acknowledge_my_policy(
    ctx: Member, db: TenantDB, policy_id: uuid.UUID, body: PolicyAcknowledge
) -> MyPolicyOut:
    """Confirm "I have read this version" (repeating it changes nothing). Errors: 404 (not asked
    of you), 409 ``policy_version_changed`` (open it again)."""
    return policies.acknowledge(db, ctx, policy_id, body)
