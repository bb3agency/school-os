"""Circulars, tasks and parent notices routes (docs/09 Circulars, tasks, notices; M4;
US-1601..US-1606; FR-CIR-*, FR-TASK-*, FR-NOTICE-*).

Every route declares its guard. AI output (readings, notice drafts) is only ever a suggestion:
tasks and approved notices exist only after a person confirms through these endpoints
(invariant 9). Circular readings follow the document's visibility (404 when you cannot see it).
Updates need ``If-Match``; creates accept ``Idempotency-Key``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag
from app.circulars import service
from app.circulars.schemas import (
    AssigneeOut,
    CircularDetail,
    CircularOut,
    DueWindow,
    NoticeApproveIn,
    NoticeCreate,
    NoticeDownloadOut,
    NoticeFileFormat,
    NoticeOut,
    NoticeRedraftIn,
    NoticeRenderIn,
    NoticeStatus,
    NoticeUpdate,
    ReviewIn,
    SuggestionConfirmIn,
    SuggestionDismissIn,
    SuggestionOut,
    TaskCreate,
    TaskOut,
    TaskStatus,
    TaskStatusIn,
    TaskUpdate,
    TaskView,
)

router = APIRouter(prefix="/api/v1", tags=["circulars"])

Reader = Annotated[UserContext, Depends(require(service.READ))]
Reviewer = Annotated[UserContext, Depends(require(service.REVIEW, scope="school"))]
TaskReader = Annotated[UserContext, Depends(require(service.TASK_READ))]
TaskManager = Annotated[UserContext, Depends(require(service.TASK_MANAGE, scope="school"))]
AssigneeReader = Annotated[UserContext, Depends(require_any(service.TASK_MANAGE, service.REVIEW))]
NoticeDrafter = Annotated[UserContext, Depends(require(service.NOTICE_DRAFT, scope="school"))]
NoticeApprover = Annotated[UserContext, Depends(require(service.NOTICE_APPROVE, scope="school"))]


def _reading_etag(response: Response, out: CircularDetail) -> CircularDetail:
    if out.reading is not None:
        response.headers["ETag"] = etag(out.reading.version)
    return out


# --- circulars ------------------------------------------------------------------------------------


@router.get("/circulars", response_model=Page[CircularOut])
def list_circulars(
    ctx: Reader, db: TenantDB, limit: Limit = 50, cursor: Cursor = None
) -> Page[CircularOut]:
    """Circulars you can see, newest first, with where their AI reading stands (``not_read``,
    ``queued``, ``running``, ``ready``, ``needs_review``), open suggestions and tasks
    (``document.read``; document visibility applies)."""
    return service.list_circulars(db, ctx, limit=limit, cursor=cursor)


@router.get("/circulars/{document_id}", response_model=CircularDetail)
def get_circular(
    ctx: Reader, db: TenantDB, document_id: uuid.UUID, response: Response
) -> CircularDetail:
    """One circular with the reading of its current version: issuer, reference, date,
    subject, English and Telugu summary with source chips, and suggested deadlines, each
    citing the sentence it comes from. Suggestions are not tasks until confirmed. The ETag is
    the reading's version (for ``/review``). 404 when you cannot see the document."""
    return _reading_etag(response, service.get_circular(db, ctx, document_id))


@router.post("/circulars/{document_id}/read", response_model=CircularDetail, status_code=202)
def request_reading(
    ctx: Reviewer, db: TenantDB, document_id: uuid.UUID, response: Response
) -> CircularDetail:
    """Read the circular's current version with AI now, or try again after "needs manual
    review" (``circular.review``). 409 ``document_not_ready``, ``reading_in_progress``,
    ``reading_done`` or ``reading_attempts_used``."""
    return _reading_etag(response, service.request_reading(db, ctx, document_id))


@router.post("/circulars/{document_id}/review", response_model=CircularDetail)
def mark_reviewed(
    ctx: Reviewer,
    db: TenantDB,
    *,
    document_id: uuid.UUID,
    body: ReviewIn,
    version: IfMatch,
    response: Response,
) -> CircularDetail:
    """Mark the circular reviewed once every suggestion is confirmed or dismissed
    (``circular.review``; ``If-Match`` = the reading's ETag). 409 ``suggestions_open``."""
    return _reading_etag(response, service.mark_reviewed(db, ctx, document_id, version))


@router.post(
    "/circular-suggestions/{suggestion_id}/confirm", response_model=TaskOut, status_code=201
)
def confirm_suggestion(
    ctx: Reviewer,
    db: TenantDB,
    *,
    suggestion_id: uuid.UUID,
    body: SuggestionConfirmIn,
    version: IfMatch,
) -> TaskOut:
    """Create a task from a suggested deadline, optionally changing its title, details or due
    date, with an owner (``circular.review``; ``If-Match`` = the suggestion's version). 409
    ``suggestion_decided``; 422 ``owner_not_active``."""
    return service.confirm_suggestion(db, ctx, suggestion_id, body, version)


@router.post("/circular-suggestions/{suggestion_id}/dismiss", response_model=SuggestionOut)
def dismiss_suggestion(
    ctx: Reviewer,
    db: TenantDB,
    *,
    suggestion_id: uuid.UUID,
    body: SuggestionDismissIn,
    version: IfMatch,
) -> SuggestionOut:
    """Dismiss a suggested deadline; no task is created (``circular.review``)."""
    return service.dismiss_suggestion(db, ctx, suggestion_id, version)


# --- tasks ----------------------------------------------------------------------------------------


@router.get("/tasks", response_model=Page[TaskOut])
def list_tasks(
    ctx: TaskReader,
    db: TenantDB,
    *,
    view: Annotated[TaskView, Query(description="mine (default) or all (task.read_all)")] = "mine",
    status: TaskStatus | None = None,
    due: DueWindow | None = None,
    owner: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[TaskOut]:
    """Your tasks (``task.read``), or with ``view=all`` every task of the school
    (``task.read_all``, else 403 ``tasks_not_all``). Without ``status``: open and in progress.
    Soonest due first; ``due=overdue``, ``week`` or ``later``."""
    return service.list_tasks(
        db,
        ctx,
        view=view,
        status=status,
        due=due,
        owner=owner,
        document_id=document_id,
        limit=limit,
        cursor=cursor,
    )


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(ctx: TaskManager, db: TenantDB, body: TaskCreate, idem: IdempotencyDep) -> Response:
    """Add a task by hand with an owner and due date, optionally linked to a circular
    (``task.manage``). The owner is told in the app. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.create_task(db, ctx, body),
        headers=lambda out: {"Location": f"/api/v1/tasks/{out.id}", "ETag": etag(out.version)},
    )


@router.get("/task-assignees", response_model=list[AssigneeOut])
def list_assignees(ctx: AssigneeReader, db: TenantDB) -> list[AssigneeOut]:
    """Active staff a task can be given to (``task.manage`` or ``circular.review``)."""
    return service.assignees(db, ctx)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(ctx: TaskReader, db: TenantDB, task_id: uuid.UUID, response: Response) -> TaskOut:
    """One task: yours, or any with ``task.read_all`` / ``task.manage`` (404 otherwise). The
    circular's citation is shown only if you can see that circular."""
    out = service.get_task(db, ctx, task_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    ctx: TaskManager,
    db: TenantDB,
    *,
    task_id: uuid.UUID,
    body: TaskUpdate,
    version: IfMatch,
    response: Response,
) -> TaskOut:
    """Change a task's title, details, due date or owner (``task.manage``; ``If-Match``).
    409 ``task_closed`` for done or cancelled tasks."""
    out = service.update_task(db, ctx, task_id, body, version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/tasks/{task_id}/status", response_model=TaskOut)
def set_task_status(
    ctx: TaskReader,
    db: TenantDB,
    *,
    task_id: uuid.UUID,
    body: TaskStatusIn,
    version: IfMatch,
    response: Response,
) -> TaskOut:
    """Mark your task in progress, done or open again (``task.read``); ``task.manage`` holders
    may do this for any task and cancel it. 409 ``task_status_not_allowed``."""
    out = service.set_task_status(db, ctx, task_id, body, version)
    response.headers["ETag"] = etag(out.version)
    return out


# --- parent notices -------------------------------------------------------------------------------


@router.get("/notices", response_model=Page[NoticeOut])
def list_notices(
    ctx: NoticeDrafter,
    db: TenantDB,
    *,
    status: NoticeStatus | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[NoticeOut]:
    """Parent notices, newest first (``notice.draft``)."""
    return service.list_notices(db, ctx, status=status, limit=limit, cursor=cursor)


def _notice_headers(out: NoticeOut) -> dict[str, str]:
    return {"Location": f"/api/v1/notices/{out.id}", "ETag": etag(out.version)}


@router.post("/notices", response_model=NoticeOut, status_code=202)
def create_notice(
    ctx: NoticeDrafter, db: TenantDB, body: NoticeCreate, idem: IdempotencyDep
) -> Response:
    """Start a parent notice in English and Telugu (``notice.draft``): AI-drafted from a
    circular (only C1, else 422 ``notice_source_personal``) or from your text (422
    ``notice_personal_data`` if it holds phone numbers, emails or Aadhaar-like numbers), or
    ``blank``. 202 with ``Location``: an AI notice starts ``drafting`` and is drafted in the
    background; ask ``GET /notices/{notice_id}`` until it is ``draft`` or ``draft_failed``
    (``draft_error`` says why: try again with ``POST /notices/{notice_id}/draft`` or write it
    yourself). A ``blank`` notice starts as ``draft``. Only the circular's text is sent to the
    AI, never student records. Accepts ``Idempotency-Key`` (a retry replays the first answer
    and queues nothing)."""
    return idem.run(
        db,
        body,
        lambda: service.create_notice(db, ctx, body),
        status_code=202,
        headers=_notice_headers,
    )


@router.get("/notices/{notice_id}", response_model=NoticeOut)
def get_notice(
    ctx: NoticeDrafter, db: TenantDB, notice_id: uuid.UUID, response: Response
) -> NoticeOut:
    """One notice (``notice.draft``)."""
    out = service.get_notice(db, ctx, notice_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch("/notices/{notice_id}", response_model=NoticeOut)
def update_notice(
    ctx: NoticeDrafter,
    db: TenantDB,
    *,
    notice_id: uuid.UUID,
    body: NoticeUpdate,
    version: IfMatch,
    response: Response,
) -> NoticeOut:
    """Edit a draft notice (``notice.draft``; ``If-Match``); editing a ``draft_failed`` notice
    makes it a ``draft``. 409 ``notice_approved`` or ``notice_drafting`` (the AI is still
    drafting it)."""
    out = service.update_notice(db, ctx, notice_id, body, version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/notices/{notice_id}/draft", response_model=NoticeOut, status_code=202)
def retry_notice_draft(
    ctx: NoticeDrafter,
    db: TenantDB,
    *,
    notice_id: uuid.UUID,
    body: NoticeRedraftIn,
    version: IfMatch,
    response: Response,
) -> NoticeOut:
    """Ask the AI to draft the notice again after it could not (``notice.draft``;
    ``If-Match``): ``draft_failed`` becomes ``drafting``; the source is checked again (422 as
    for ``POST /notices``). 409 ``notice_not_draft_failed`` in any other state."""
    out = service.retry_notice_draft(db, ctx, notice_id, version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/notices/{notice_id}/approve", response_model=NoticeOut)
def approve_notice(
    ctx: NoticeApprover,
    db: TenantDB,
    *,
    notice_id: uuid.UUID,
    body: NoticeApproveIn,
    version: IfMatch,
    response: Response,
) -> NoticeOut:
    """Approve a notice (``notice.approve``; ``If-Match``): English and Telugu titles and
    bodies filled (422 ``notice_incomplete``) and no personal numbers (422
    ``notice_personal_data``); 409 ``notice_approved`` or ``notice_drafting``. The A4 PDF and
    the image are made next."""
    out = service.approve_notice(db, ctx, notice_id, version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/notices/{notice_id}/render", response_model=NoticeOut, status_code=202)
def render_notice(
    ctx: NoticeDrafter,
    db: TenantDB,
    *,
    notice_id: uuid.UUID,
    body: NoticeRenderIn,
    version: IfMatch,
    response: Response,
) -> NoticeOut:
    """Make the approved notice's A4 PDF and image again (``notice.draft``; files are kept for
    a few days). 409 ``notice_not_approved`` or ``render_in_progress``."""
    out = service.request_render(db, ctx, notice_id, version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/notices/{notice_id}/download-url", response_model=NoticeDownloadOut)
def notice_download_url(
    ctx: NoticeDrafter,
    db: TenantDB,
    *,
    notice_id: uuid.UUID,
    format: Annotated[NoticeFileFormat, Query(description="pdf (A4) or png (image)")] = "pdf",
) -> NoticeDownloadOut:
    """A download link valid at most 5 minutes (``notice.draft``; audited). 409
    ``notice_files_not_ready`` or ``notice_files_expired``."""
    return service.download_url(db, ctx, notice_id, format)
