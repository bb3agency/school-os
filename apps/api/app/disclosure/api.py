"""CBSE Appendix IX disclosure routes (docs/09 "Disclosure"; ADR-0051; FR-DISC-001..012).

Staff routes: ``disclosure.edit`` reads and edits the draft, sees the published history and
downloads the files; ``disclosure.publish`` (step-up MFA within 5 minutes) publishes, withdraws
and changes the link. School-wide only (``scope="school"``). Non-CBSE schools get 404
``module_hidden``.

Public routes (``public_router``, pinned in ``app/public/public_routes.yaml``; ADR-0051 decision
4): ``GET /public/disclosure/{key}`` and ``GET /public/disclosure/{key}/documents/{n}``, called by
the web app's ``(public)/d/[key]`` page with its service token and no user token, guarded by
``require_public_handle("school_disclosure")`` (per-IP failed-lookup budget, resolver, per-handle
budget). Unknown, revoked, withdrawn and suspended-school keys get the uniform 404. Responses
carry ``Cache-Control: private, no-store``, ``X-Robots-Tag: noindex`` and set no cookie.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from starlette.concurrency import run_in_threadpool

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep, IfMatch, etag
from app.disclosure import service
from app.disclosure.schemas import (
    DisclosureDraftIn,
    DisclosureDraftOut,
    DisclosureFormatOut,
    FileFormat,
    FileLinkOut,
    LinkOut,
    PublicDisclosureOut,
    PublicDocumentLinkOut,
    PublishIn,
    SnapshotDetailOut,
    SnapshotOut,
    WithdrawIn,
)
from app.public.guards import PublicCaller, PublicNotFound, require_public_handle

router = APIRouter(prefix="/api/v1", tags=["disclosure"])

Editor = Annotated[UserContext, Depends(require(service.EDIT, scope="school"))]
Publisher = Annotated[UserContext, Depends(require(service.PUBLISH, scope="school", step_up=True))]


def _draft_etag(response: Response, out: DisclosureDraftOut) -> DisclosureDraftOut:
    response.headers["ETag"] = etag(out.version)
    response.headers["Cache-Control"] = "no-store"
    return out


@router.get("/disclosure/format", response_model=DisclosureFormatOut)
def get_disclosure_format(ctx: Editor, db: TenantDB) -> DisclosureFormatOut:
    """The Appendix IX format: sections and items in CBSE's order, how each is filled, its
    sources and ``verified`` (false until a CBSE school confirms it; D3). ``disclosure.edit``;
    404 ``module_hidden`` for schools without CBSE (FR-DISC-001)."""
    return service.get_format(db, ctx)


@router.get("/disclosure/draft", response_model=DisclosureDraftOut)
def get_disclosure_draft(ctx: Editor, db: TenantDB, response: Response) -> DisclosureDraftOut:
    """The draft (typed values, chosen documents) with the values SchoolOS fills as they are now
    and the required items still empty; ``ETag`` for ``If-Match`` (``W/"0"`` before the first
    save). ``disclosure.edit`` (FR-DISC-003, FR-DISC-004)."""
    return _draft_etag(response, service.get_draft(db, ctx))


@router.put("/disclosure/draft", response_model=DisclosureDraftOut)
def put_disclosure_draft(
    ctx: Editor, db: TenantDB, body: DisclosureDraftIn, expected: IfMatch, response: Response
) -> DisclosureDraftOut:
    """Save the whole draft (``disclosure.edit``, ``If-Match``). 422 per item: Aadhaar-like
    numbers anywhere, phone-like numbers outside the two contact items, markup, https-only
    links, documents you cannot see or that cannot be published (a clean PDF of a circular,
    policy or other school document); 412 when someone else saved in between (FR-DISC-004)."""
    return _draft_etag(response, service.put_draft(db, ctx, body, expected_version=expected))


@router.post(
    "/disclosure/publish",
    response_model=SnapshotOut,
    status_code=201,
    responses={422: {"description": "confirmation_required, incomplete or a document problem"}},
)
def publish_disclosure(
    ctx: Publisher, db: TenantDB, body: PublishIn, idem: IdempotencyDep
) -> Response:
    """Publish the draft as a frozen snapshot that replaces the page (``disclosure.publish``,
    step-up; ``Idempotency-Key``). Needs ``confirm_no_student_data: true``; the first publish
    issues the page's link. The HTML and PDF files follow from the worker (FR-DISC-005,
    FR-DISC-008)."""
    return idem.run(db, body, lambda: service.publish(db, ctx, body))


@router.get("/disclosure/snapshots", response_model=list[SnapshotOut])
def list_disclosure_snapshots(ctx: Editor, db: TenantDB) -> list[SnapshotOut]:
    """Published history, newest first; ``live`` marks what the page shows now
    (``disclosure.edit``)."""
    return service.list_snapshots(db, ctx)


@router.get("/disclosure/snapshots/{snapshot_id}", response_model=SnapshotDetailOut)
def get_disclosure_snapshot(ctx: Editor, db: TenantDB, snapshot_id: uuid.UUID) -> SnapshotDetailOut:
    """One publication exactly as it was shown, with its views per day (``disclosure.edit``)."""
    return service.get_snapshot(db, ctx, snapshot_id)


@router.post("/disclosure/snapshots/{snapshot_id}/withdraw", response_model=SnapshotOut)
def withdraw_disclosure_snapshot(
    ctx: Publisher, db: TenantDB, snapshot_id: uuid.UUID, body: WithdrawIn
) -> SnapshotOut:
    """Withdraw the live page with a reason (10-500 characters; ``disclosure.publish``,
    step-up): the link then shows "not found" until someone publishes again. 409
    ``snapshot_not_live`` for an older snapshot (FR-DISC-007)."""
    return service.withdraw(db, ctx, snapshot_id, body)


@router.get("/disclosure/link", response_model=LinkOut)
def get_disclosure_link(ctx: Editor, db: TenantDB) -> LinkOut:
    """The page's public link (``{SOS_PUBLIC_BASE_URL}/d/{key}``), null before the first
    publish (``disclosure.edit``)."""
    return service.get_link(db, ctx)


@router.post("/disclosure/link/rotate", response_model=LinkOut)
def rotate_disclosure_link(ctx: Publisher, db: TenantDB) -> LinkOut:
    """ "Change the link": the old link stops working at once and a new one is issued
    (``disclosure.publish``, step-up; 409 ``not_published`` before the first publish;
    FR-DISC-008)."""
    return service.rotate_link(db, ctx)


@router.get("/disclosure/snapshots/{snapshot_id}/download-url", response_model=FileLinkOut)
def get_disclosure_download_url(
    ctx: Editor,
    db: TenantDB,
    snapshot_id: uuid.UUID,
    file_format: Annotated[FileFormat, Query(alias="format")],
) -> FileLinkOut:
    """A link (at most 5 minutes) to the published version as one HTML file for the school's
    website or as an A4 PDF (``disclosure.edit``; audited). 409 ``files_not_ready`` /
    ``files_failed`` (FR-DISC-011)."""
    return service.download_url(db, ctx, snapshot_id, file_format)


# --- public (pinned in app/public/public_routes.yaml) -----------------------------------------

public_router = APIRouter(prefix="/api/v1", tags=["public"])

ByKey = Annotated[
    PublicCaller, Depends(require_public_handle("school_disclosure", key="path", param="key"))
]

_PUBLIC_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"description": "Unknown, changed, withdrawn or unpublished link (always the same body)."},
    429: {"description": "Too many requests or failed lookups from this address."},
}


def _public_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"


@public_router.get(
    "/public/disclosure/{key}", response_model=PublicDisclosureOut, responses=_PUBLIC_RESPONSES
)
async def get_public_disclosure(
    key: Annotated[str, Path(max_length=64)], caller: ByKey, response: Response
) -> PublicDisclosureOut:
    """The school's published Appendix IX page (ADR-0051; FR-DISC-009, FR-DISC-010): exactly
    the fields of the live snapshot (school name, publication date, format version, sections
    and items with a value, a table, document numbers and titles, or a link). Counted per day,
    no visitor data. Called by the web app with its service token and no user token."""
    del key  # read by the guard
    _public_headers(response)
    out = await run_in_threadpool(service.public_page, caller.tenant_id, caller.handle_id)
    if out is None:
        raise PublicNotFound()
    return out


@public_router.get(
    "/public/disclosure/{key}/documents/{n}",
    response_model=PublicDocumentLinkOut,
    responses=_PUBLIC_RESPONSES,
)
async def get_public_disclosure_document(
    key: Annotated[str, Path(max_length=64)],
    n: Annotated[int, Path(ge=1, le=200)],
    caller: ByKey,
    response: Response,
) -> PublicDocumentLinkOut:
    """A link (at most 5 minutes) to document ``n`` of the live page, only the version it
    published; the web app answers 303 to it (FR-DISC-009)."""
    del key
    _public_headers(response)
    out = await run_in_threadpool(service.public_document, caller.tenant_id, caller.handle_id, n)
    if out is None:
        raise PublicNotFound()
    return out


__all__ = ["public_router", "router"]
