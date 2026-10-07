"""Change-request routes (docs/09 Change requests; US-601, FR-CR-001..005, SEC-014).

Every route declares its guard: ``require()`` for submit/approve/reject/cancel (approve and
reject need step-up MFA within 5 minutes, 428 ``step_up_required``) and :func:`require_any` for
the reads that either the maker or the checker may use. The service applies the student scope to
every object (404 outside scope or school), enforces maker != checker and the request state.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag
from app.changes import service
from app.changes.memo import STYLE_CSP
from app.changes.schemas import ApproveIn, ChangeRequestCreate, ChangeRequestOut, RejectIn, Status

router = APIRouter(prefix="/api/v1", tags=["change-requests"])


Requester = Annotated[UserContext, Depends(require(service.REQUEST))]
Approver = Annotated[UserContext, Depends(require(service.APPROVE, step_up=True))]
Reader = Annotated[UserContext, Depends(require_any(service.REQUEST, service.APPROVE))]


def _headers(item: ChangeRequestOut) -> dict[str, str]:
    return {"Location": f"/api/v1/change-requests/{item.id}", "ETag": etag(item.version)}


def _with_etag(response: Response, item: ChangeRequestOut) -> ChangeRequestOut:
    response.headers["ETag"] = etag(item.version)
    return item


@router.post("/change-requests", response_model=ChangeRequestOut, status_code=201)
def submit_change_request(
    ctx: Requester, db: TenantDB, body: ChangeRequestCreate, idem: IdempotencyDep
) -> Response:
    """Request a correction of an identity field (name, date of birth, gender, parents' names,
    admission number/date) with the new value, a reason (at least 10 characters) and an evidence
    document uploaded with purpose ``evidence`` (permission ``student.identity_change.request``).
    Someone else with ``student.identity_change.approve`` decides it. Accepts
    ``Idempotency-Key``. Errors: ``not_identity_attribute``, ``evidence_required`` (422),
    ``duplicate_pending_request`` (409)."""
    return idem.run(
        db,
        body,
        lambda: service.submit(db, ctx, body),
        headers=_headers,
        # Old and new values and the reason: the replay record keeps no body (audit H-01).
        refetch=lambda request_id: service.get_request(db, ctx, request_id),
    )


@router.get("/change-requests", response_model=Page[ChangeRequestOut])
def list_change_requests(
    *,
    ctx: Reader,
    db: TenantDB,
    status: Annotated[Status | None, Query()] = None,
    student_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[ChangeRequestOut]:
    """Change requests of students you can see, newest first (permission
    ``student.identity_change.request`` or ``student.identity_change.approve``). Values of
    sensitive (C3) fields are masked."""
    return service.list_requests(
        db, ctx, status=status, student_id=student_id, limit=limit, cursor=cursor
    )


@router.get("/change-requests/{change_request_id}", response_model=ChangeRequestOut)
def get_change_request(
    ctx: Reader, db: TenantDB, change_request_id: uuid.UUID, response: Response
) -> ChangeRequestOut:
    """One change request with old/new value, source, reason, evidence and decision; returns
    ``ETag`` for the decision calls (request or approve permission)."""
    return _with_etag(response, service.get_request(db, ctx, change_request_id))


@router.post("/change-requests/{change_request_id}/approve", response_model=ChangeRequestOut)
def approve_change_request(
    *,
    ctx: Approver,
    db: TenantDB,
    change_request_id: uuid.UUID,
    expected: IfMatch,
    response: Response,
    body: ApproveIn | None = None,
) -> ChangeRequestOut:
    """Approve: records the new value as verified (the old value stays in history) and closes the
    request (permission ``student.identity_change.approve``, MFA within 5 minutes, not the
    requester, ``If-Match``). Errors: ``self_approval_forbidden`` (403), ``step_up_required``
    (428), ``request_not_pending`` / ``request_expired`` / ``request_outdated`` (409)."""
    out = service.approve(
        db, ctx, change_request_id, body or ApproveIn(), expected_version=expected
    )
    return _with_etag(response, out)


@router.post("/change-requests/{change_request_id}/reject", response_model=ChangeRequestOut)
def reject_change_request(
    *,
    ctx: Approver,
    db: TenantDB,
    change_request_id: uuid.UUID,
    expected: IfMatch,
    body: RejectIn,
    response: Response,
) -> ChangeRequestOut:
    """Reject with a reason (at least 10 characters) that the requester will see (permission
    ``student.identity_change.approve``, MFA within 5 minutes, not the requester, ``If-Match``)."""
    out = service.reject(db, ctx, change_request_id, body, expected_version=expected)
    return _with_etag(response, out)


@router.post("/change-requests/{change_request_id}/cancel", response_model=ChangeRequestOut)
def cancel_change_request(
    *,
    ctx: Requester,
    db: TenantDB,
    change_request_id: uuid.UUID,
    expected: IfMatch,
    response: Response,
) -> ChangeRequestOut:
    """Withdraw your own pending request (permission ``student.identity_change.request``,
    ``If-Match``). Error ``not_requester`` (403) for someone else's request."""
    out = service.cancel(db, ctx, change_request_id, expected_version=expected)
    return _with_etag(response, out)


@router.get(
    "/change-requests/{change_request_id}/memo",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}, "description": "Print-ready A4 memo"}},
)
def change_request_memo(ctx: Reader, db: TenantDB, change_request_id: uuid.UUID) -> HTMLResponse:
    """Printable correction memo for the paper register, in English (Telugu too only while
    Telugu is shown, ADR-0036; request or approve permission). Sensitive values appear only
    for ``student.read_sensitive`` holders. The view is audited."""
    page = service.memo(db, ctx, change_request_id)
    return HTMLResponse(
        page,
        headers={
            "Content-Disposition": (
                f'inline; filename="correction-memo-{str(change_request_id)[:8]}.html"'
            ),
            "Cache-Control": "no-store",
            "Content-Security-Policy": STYLE_CSP,
            "X-Content-Type-Options": "nosniff",
        },
    )
