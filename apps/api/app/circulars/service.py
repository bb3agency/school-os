"""Circulars, tasks and parent notices: public API (M4; US-1601..US-1606; FR-CIR-*, FR-TASK-*,
FR-NOTICE-*).

**Circulars.** A circular is a document of type ``circular`` (``documents``). When its current
version is indexed, ``knowledge`` calls :func:`on_version_indexed` in the index transaction and a
``circulars.read.requested`` outbox event queues the worker job :func:`run_reading` (queue
``ingest``). The job reads the version's passages, asks ``knowledge.service.read_circular`` (the
gateway, outside any transaction) and stores what the checks kept: metadata, EN/TE summary with
citations, and deadline **suggestions**. Nothing else happens until a holder of
``circular.review`` confirms a suggestion into a task (or dismisses it) through the normal
endpoints (invariant 9). One reading per document version (idempotent); every failure ends in
``needs_review`` with a code, and "Try again" is limited (``config.yaml``). Readings and
suggestions are visible only to people who can see the document (``documents`` visibility, 404).

**Tasks.** Owners see their tasks (``task.read``), ``task.read_all`` holders every task,
``task.manage`` holders create, reassign, edit and cancel. A daily job (:func:`send_reminders`)
notifies owners before the due date and when overdue, in-app only (email reminders wait for
general staff email templates; ``notifications`` sends only invitations today).

**Notices.** ``notice.draft`` holders start a notice from a C1 circular (its passages and the
dates confirmed as tasks), from their own text (refused with phone numbers, emails or Aadhaar-like
numbers) or blank. An AI notice starts ``drafting`` and the worker job :func:`run_notice_draft`
(outbox ``circulars.notice.draft_requested``, queue ``ingest``) drafts it through
``knowledge.service.draft_notice`` with no transaction open, ending in ``draft`` or
``draft_failed`` with a code ("Try again" or write it by hand); the request never waits for the
model. The AI draft is marked as such and editable; ``notice.approve`` holders approve; the
worker renders an A4 PDF and a PNG (queue ``pdf``); downloads are presigned (<= 5 minutes) and
audited. No student data is ever read here.

Every change is audited in its own transaction with IDs, counts and codes only; logs carry IDs and
codes only (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import Scopes, UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.authz.resolver import build_snapshot
from app.circulars import repository as repo
from app.circulars.config import rules
from app.circulars.models import CircularReading, CircularSuggestion, ParentNotice, Task
from app.circulars.rendering import notice_html
from app.circulars.schemas import (
    AssigneeOut,
    CircularDetail,
    CircularOut,
    CitationOut,
    DueWindow,
    MemberOut,
    NoticeCreate,
    NoticeDownloadOut,
    NoticeFileFormat,
    NoticeOut,
    NoticeUpdate,
    ReadingOut,
    SuggestionConfirmIn,
    SuggestionOut,
    TaskCreate,
    TaskOut,
    TaskStatus,
    TaskStatusIn,
    TaskUpdate,
    TaskView,
)
from app.core import pdf
from app.core import purge as purging
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.languages import telugu_enabled, telugu_text
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.documents.schemas import DocumentDetail, DocumentOut
from app.identity import service as identity
from app.identity.schemas import MembershipAccess, RoleAccess, RoleOut, UserOut
from app.knowledge import service as knowledge
from app.notifications import service as notifications
from app.ops import service as ops
from app.tenancy import service as tenancy

log = get_logger(__name__)

READ: Final = "document.read"
REVIEW: Final = "circular.review"
TASK_READ: Final = "task.read"
TASK_ALL: Final = "task.read_all"
TASK_MANAGE: Final = "task.manage"
NOTICE_DRAFT: Final = "notice.draft"
NOTICE_APPROVE: Final = "notice.approve"

CIRCULAR: Final = "circular"
READ_EVENT: Final = "circulars.read.requested"
READ_TASK: Final = "circulars.read_version"
RENDER_EVENT: Final = "circulars.notice.render_requested"
RENDER_TASK: Final = "circulars.render_notice"
DRAFT_EVENT: Final = "circulars.notice.draft_requested"
DRAFT_TASK: Final = "circulars.draft_notice"
SOURCE_UNAVAILABLE: Final = "source_unavailable"
DRAFT_FAILED: Final = "ai_unavailable"
DOCUMENT_GONE: Final = "document_gone"
ACTIVE: Final = ("open", "in_progress")
IST: Final = ZoneInfo("Asia/Kolkata")
SYSTEM_ID: Final = uuid.UUID(int=0)
NOTICE_MIME: Final[dict[str, str]] = {"pdf": "application/pdf", "png": "image/png"}
DOWNLOAD_TTL_S: Final = 300
_USER_PAGE: Final = 200

ops.register_outbox_route(READ_EVENT, READ_TASK)
ops.register_outbox_route(RENDER_EVENT, RENDER_TASK)
ops.register_outbox_route(DRAFT_EVENT, DRAFT_TASK)


# --- helpers --------------------------------------------------------------------------------------


def today_ist(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.UTC)).astimezone(IST).date()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


AADHAAR_CODE: Final = "aadhaar_full_number_rejected"


def _refuse_aadhaar(values: Mapping[str, str | None]) -> None:
    """Typed text is never stored with a full Aadhaar number (invariant 4): 422 per field,
    like reasons, notes and cell edits in the other modules."""
    fields = sorted(k for k, v in values.items() if v and contains_full_aadhaar(v))
    if fields:
        raise ValidationFailed(
            [
                {"field": f, "code": AADHAAR_CODE, "message_key": "errors.aadhaar_last4_only"}
                for f in fields
            ],
            detail="Don't enter Aadhaar numbers. Enter only the last 4 digits.",
        )


def _audit(
    session: Session,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    summary: Mapping[str, Any],
    *,
    system: bool = False,
    actor_id: uuid.UUID | None = None,
) -> None:
    """``actor_id``: work a worker finishes for the person who asked (their event, no request)."""
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        actor_id=actor_id,
        request_id=None if system or actor_id is not None else _request_id(),
    )


def _check_version(current: int, expected: int) -> None:
    if current != expected:
        raise PreconditionFailed(
            "Someone else changed this in the meantime. Reload it and try again.",
            code="version_mismatch",
        )


def system_context(tenant_id: uuid.UUID) -> UserContext:
    """Whole-school document read context for the worker (only ever used to read)."""
    return UserContext(
        user_id=SYSTEM_ID,
        tenant_id=tenant_id,
        membership_id=SYSTEM_ID,
        roles=frozenset({"system"}),
        permissions=frozenset({READ, "document.manage_acl"}),
        scopes=Scopes(school=True),
        mfa=False,
        auth_time=None,
    )


def _circular(session: Session, ctx: UserContext, document_id: uuid.UUID) -> DocumentDetail:
    """A circular the caller may see (404 for anything else, like documents do)."""
    doc = documents.get_document(session, ctx, document_id)
    if doc.doc_type != CIRCULAR:
        raise NotFound("Circular not found")
    return doc


def _citation(value: Mapping[str, Any]) -> CitationOut:
    return CitationOut(
        source=str(value["source"]),
        passage=int(value.get("passage", 1)),
        page=value.get("page"),
        quote=str(value.get("quote", "")),
    )


def _member(names: Mapping[uuid.UUID, str], membership_id: uuid.UUID | None) -> MemberOut | None:
    if membership_id is None:
        return None
    return MemberOut(membership_id=membership_id, display_name=names.get(membership_id))


def _user_members(
    session: Session, user_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, MemberOut]:
    wanted = {u for u in user_ids if u is not None}
    found = identity.members_for_users(session, wanted) if wanted else {}
    return {
        user: MemberOut(membership_id=membership, display_name=name)
        for user, (membership, name) in found.items()
    }


def _member_context(
    tenant_id: uuid.UUID, user: UserOut, roles: Mapping[str, RoleOut]
) -> UserContext:
    """A member's current permissions and scopes, read in the caller's transaction (what their
    next request would get), for checks made on their behalf: who may be told about a circular,
    and what a notice drafted in the worker may read."""
    access = MembershipAccess(
        roles=tuple(
            RoleAccess(
                key=key,
                is_system=roles[key].is_system,
                permissions=frozenset(roles[key].permissions),
            )
            for key in user.roles
            if key in roles
        ),
        scopes=tuple((s.type, s.ref) for s in user.scopes),
        mfa_required=False,
    )
    snap = build_snapshot(access)
    return UserContext(
        user_id=user.id,
        tenant_id=tenant_id,
        membership_id=user.membership_id,
        roles=snap.roles,
        permissions=snap.permissions,
        scopes=snap.scopes,
        mfa=False,
        auth_time=None,
        scoped_permissions=snap.scoped_permissions,
    )


def _active(user: UserOut, now: dt.datetime) -> bool:
    return user.status == "active" and (user.expires_at is None or user.expires_at > now)


def _reviewers_who_see(
    session: Session, tenant_id: uuid.UUID, document_id: uuid.UUID
) -> list[uuid.UUID]:
    """Active ``circular.review`` holders whose document visibility reaches the circular (its
    ACL, their role, sections and classes; FR-CIR-006): a notification names the circular, so
    nobody outside a restrictive ACL is told about it."""
    roles = {r.key: r for r in identity.list_roles(session)}
    now = _now()
    out: list[uuid.UUID] = []
    after: uuid.UUID | None = None
    while True:
        users, after = identity.list_users(session, limit=_USER_PAGE, after=after)
        for user in users:
            if not _active(user, now):
                continue
            member = _member_context(tenant_id, user, roles)
            if member.has(REVIEW) and documents.is_visible(session, member, document_id):
                out.append(user.membership_id)
        if after is None:
            return out


# --- circulars: the reading -----------------------------------------------------------------------


def _queue_reading(
    session: Session, document_id: uuid.UUID, version_id: uuid.UUID, version_no: int
) -> uuid.UUID | None:
    reading_id = repo.insert_reading(
        session,
        {
            "id": new_id(),
            "document_id": document_id,
            "version_id": version_id,
            "version_no": version_no,
            "status": "queued",
        },
    )
    if reading_id is not None:
        ops.enqueue_event(session, READ_EVENT, {"reading_id": reading_id})
    return reading_id


def on_version_indexed(
    session: Session, document_id: uuid.UUID, version_id: uuid.UUID, doc_type: str
) -> None:
    """``knowledge.service.INDEXED_HOOKS``: queue the reading of a circular's current version
    in the index transaction (FR-CIR-001). Idempotent: a version is read once."""
    if doc_type != CIRCULAR:
        return
    tenant_id = repo.current_tenant_id(session)
    doc = documents.get_document(session, system_context(tenant_id), document_id)
    version = next((v for v in doc.versions if v.id == version_id), None)
    if version is None:
        return
    if _queue_reading(session, document_id, version_id, version.version_no) is not None:
        log.info(
            "circulars.reading.queued",
            tenant_id=tenant_id,
            resource_type="document",
            resource_id=document_id,
        )


def _reading_ctx(
    session: Session, tenant_id: uuid.UUID, row: CircularReading
) -> tuple[knowledge.CircularContext, tuple[knowledge.Passage, ...]] | None:
    try:
        doc = documents.get_document(session, system_context(tenant_id), row.document_id)
    except NotFound:
        return None
    if not any(v.id == row.version_id for v in doc.versions):
        return None
    passages = knowledge.circular_passages(session, row.document_id, row.version_id, row.version_no)
    context = knowledge.CircularContext(title=doc.title, issuer=doc.issuer, issued_on=doc.issued_on)
    return context, passages


def run_reading(tenant_id: uuid.UUID, reading_id: uuid.UUID) -> str:
    """Worker (outbox ``circulars.read.requested``): read one circular version (FR-CIR-002).

    Three short transactions: claim and read the passages; call the model with no transaction
    open; store the result (or ``needs_review`` with the code) with its audit event. Returns an
    outcome code for the task result.
    """
    with tenant_session(tenant_id) as session:
        row = repo.lock_reading(session, reading_id)
        if row is None or row.status not in ("queued", "running"):
            return "skipped"
        found = _reading_ctx(session, tenant_id, row)
        repo.update_reading(
            session,
            reading_id,
            {"status": "running", "started_at": _now(), "attempts": row.attempts + 1},
        )
    outcome: knowledge.ReadingOutcome | None = None
    code: str | None = DOCUMENT_GONE
    if found is not None:
        context, passages = found
        try:
            outcome = knowledge.read_circular(tenant_id, context, passages)
            code = None
        except knowledge.AiUnavailable as exc:
            code = exc.code
    with tenant_session(tenant_id) as session:
        row = repo.lock_reading(session, reading_id)
        if row is None or row.status != "running":
            return "skipped"
        if outcome is not None:
            _store_reading(session, tenant_id, row, outcome)
            return "ready"
        failed = code or DOCUMENT_GONE
        _needs_review(session, tenant_id, row, failed)
        return failed


def abandon_reading(tenant_id: uuid.UUID, reading_id: uuid.UUID, code: str) -> None:
    """The worker gave up (retries used): the reading needs manual review with ``code``."""
    with tenant_session(tenant_id) as session:
        row = repo.lock_reading(session, reading_id)
        if row is not None and row.status in ("queued", "running"):
            _needs_review(session, tenant_id, row, code)


def _citation_json(citation: knowledge.PassageCitation) -> dict[str, Any]:
    return {
        "source": citation.source,
        "passage": citation.passage,
        "page": citation.page,
        "quote": citation.quote,
    }


def _store_reading(
    session: Session,
    tenant_id: uuid.UUID,
    row: CircularReading,
    outcome: knowledge.ReadingOutcome,
) -> None:
    result = outcome.reading
    repo.update_reading(
        session,
        row.id,
        {
            "status": "ready",
            "error_code": None,
            "issuer": result.issuer,
            "reference_no": result.reference_no,
            "issued_on": result.issued_on,
            "subject": result.subject,
            "summary_en": result.summary_en,
            "summary_te": result.summary_te,
            "summary_sources": [_citation_json(c) for c in result.summary_sources],
            "model": outcome.model,
            "prompt": outcome.prompt,
            "suggestions_dropped": result.dropped,
            "passages_sent": result.passages_sent,
            "passages_total": result.passages_total,
            "completed_at": _now(),
        },
    )
    repo.insert_suggestions(
        session,
        [
            {
                "id": new_id(),
                "reading_id": row.id,
                "position": i,
                "title": s.title,
                "details": s.details,
                "due_on": s.due_on,
                "citation": _citation_json(s.citation),
            }
            for i, s in enumerate(result.deadlines, start=1)
        ],
    )
    summary = {
        "reading_id": row.id,
        "version_no": row.version_no,
        "suggestions": len(result.deadlines),
        "dropped": result.dropped,
        "passages": result.passages_sent,
        "prompt_ref": outcome.prompt,
    }
    _audit(session, "circular.read_completed", "document", row.document_id, summary, system=True)
    notifications.notify(
        session,
        tenant_id=tenant_id,
        recipients=_reviewers_who_see(session, tenant_id, row.document_id),
        template_key="circular.read_ready",
        params={"document_id": str(row.document_id), "suggestions": len(result.deadlines)},
        resource_id=row.document_id,
        dedupe_key=f"circular.read:{row.id}",
    )
    log.info(
        "circulars.reading.ready",
        tenant_id=tenant_id,
        resource_type="document",
        resource_id=row.document_id,
        count=len(result.deadlines),
    )


def _needs_review(session: Session, tenant_id: uuid.UUID, row: CircularReading, code: str) -> None:
    repo.update_reading(
        session, row.id, {"status": "needs_review", "error_code": code, "completed_at": _now()}
    )
    summary = {"reading_id": row.id, "version_no": row.version_no, "code": code}
    _audit(session, "circular.read_failed", "document", row.document_id, summary, system=True)
    notifications.notify(
        session,
        tenant_id=tenant_id,
        recipients=_reviewers_who_see(session, tenant_id, row.document_id),
        template_key="circular.needs_review",
        params={"document_id": str(row.document_id), "code": code},
        resource_id=row.document_id,
        dedupe_key=f"circular.review:{row.id}:{row.attempts}",
    )
    log.warning(
        "circulars.reading.needs_review",
        tenant_id=tenant_id,
        resource_type="document",
        resource_id=row.document_id,
        error_code=code,
    )


# --- circulars: reads and review ------------------------------------------------------------------


def _can_retry(row: CircularReading) -> bool:
    return row.status == "needs_review" and row.attempts < rules().reading.max_attempts


def _suggestion_out(s: CircularSuggestion) -> SuggestionOut:
    return SuggestionOut(
        id=s.id,
        position=s.position,
        title=s.title,
        details=s.details,
        due_on=s.due_on,
        citation=_citation(s.citation),
        status=s.status,
        task_id=s.task_id,
        decided_at=s.decided_at,
        version=s.version,
    )


def _reading_out(session: Session, row: CircularReading) -> ReadingOut:
    suggestions = repo.suggestions_of(session, [row.id])
    reviewer = _user_members(session, [row.reviewed_by]).get(row.reviewed_by)  # type: ignore[arg-type]
    return ReadingOut(
        id=row.id,
        version_no=row.version_no,
        status=row.status,
        error_code=row.error_code,
        issuer=row.issuer,
        reference_no=row.reference_no,
        issued_on=row.issued_on,
        subject=row.subject,
        summary_en=row.summary_en,
        summary_te=telugu_text(row.summary_te),  # null while Telugu is hidden (ADR-0036)
        summary_sources=[_citation(c) for c in row.summary_sources],
        suggestions=[_suggestion_out(s) for s in suggestions],
        suggestions_dropped=row.suggestions_dropped,
        passages_sent=row.passages_sent,
        passages_total=row.passages_total,
        attempts=row.attempts,
        can_retry=_can_retry(row),
        reviewed_at=row.reviewed_at,
        reviewed_by=reviewer,
        completed_at=row.completed_at,
        version=row.version,
    )


def _current(
    doc: DocumentOut, readings: Mapping[uuid.UUID, CircularReading]
) -> CircularReading | None:
    row = readings.get(doc.id)
    current = doc.current_version
    if row is None or current is None or row.version_id != current.id:
        return None
    return row


def _circular_out(
    doc: DocumentOut,
    row: CircularReading | None,
    open_counts: Mapping[uuid.UUID, int],
    task_counts: Mapping[uuid.UUID, int],
) -> dict[str, Any]:
    return {
        "document_id": doc.id,
        "title": doc.title,
        "issuer": doc.issuer,
        "issued_on": doc.issued_on,
        "current_version_no": doc.current_version.version_no if doc.current_version else None,
        "reading_status": row.status if row else "not_read",
        "reading_error": row.error_code if row else None,
        "reviewed": bool(row and row.reviewed_at),
        "open_suggestions": open_counts.get(row.id, 0) if row else 0,
        "tasks": task_counts.get(doc.id, 0),
        "created_at": doc.created_at,
    }


def list_circulars(
    session: Session, ctx: UserContext, *, limit: int, cursor: str | None
) -> Page[CircularOut]:
    """Circulars the caller may see, newest first, with their reading status (``document.read``;
    documents visibility applies, FR-CIR-006)."""
    after = decode_cursor(cursor)
    before_id: uuid.UUID | None = None
    if after is not None:
        try:
            before_id = uuid.UUID(str(after.get("b")))
        except ValueError:
            raise ValidationFailed([_error("cursor", "invalid")]) from None
    docs, next_before = documents.list_documents(
        session, ctx, limit=limit, before_id=before_id, doc_type=CIRCULAR
    )
    ids = [d.id for d in docs]
    readings = repo.latest_readings(session, ids)
    current = {d.id: _current(d, readings) for d in docs}
    open_counts = repo.open_suggestion_counts(
        session, [r.id for r in current.values() if r is not None]
    )
    tasks = repo.task_counts(session, ids)
    data = [CircularOut(**_circular_out(d, current[d.id], open_counts, tasks)) for d in docs]
    return Page[CircularOut](
        data=data, next_cursor=encode_cursor({"b": str(next_before)}) if next_before else None
    )


def get_circular(session: Session, ctx: UserContext, document_id: uuid.UUID) -> CircularDetail:
    """One circular with the reading of its current version (``document.read``; 404 when the
    caller cannot see the document)."""
    doc = _circular(session, ctx, document_id)
    row = _current(doc, repo.latest_readings(session, [doc.id]))
    open_counts = repo.open_suggestion_counts(session, [row.id] if row else [])
    tasks = repo.task_counts(session, [doc.id])
    return CircularDetail(
        **_circular_out(doc, row, open_counts, tasks),
        sensitivity=doc.sensitivity,
        reading=_reading_out(session, row) if row else None,
    )


def request_reading(
    session: Session,
    ctx: UserContext,
    document_id: uuid.UUID,
    *,
    scope: MutableMapping[str, Any] | None = None,
) -> CircularDetail:
    """Read (or read again) the current version of a circular (``circular.review``, FR-CIR-001).

    409 ``document_not_ready`` (not scanned yet), ``reading_in_progress``, ``reading_done`` (a
    version is read once) or ``reading_attempts_used``."""
    if not ctx.has(REVIEW):
        raise Forbidden()
    doc = _circular(session, ctx, document_id)
    version = doc.current_version
    if version is None or version.status != "ready":
        raise Conflict(
            "The file is still being checked. Try again shortly.", code="document_not_ready"
        )
    row = repo.reading_for_version(session, version.id)
    if row is None:
        knowledge.admit_ai_request(ctx, scope=scope)  # SEC-020 / R-20
        reading_id = _queue_reading(session, doc.id, version.id, version.version_no)
        if reading_id is None:
            raise Conflict("This circular is already being read.", code="reading_in_progress")
    elif row.status in ("queued", "running"):
        raise Conflict("This circular is already being read.", code="reading_in_progress")
    elif row.status == "ready":
        raise Conflict("This version of the circular has been read.", code="reading_done")
    elif not _can_retry(row):
        raise Conflict(
            "This circular was tried several times. Add its tasks by hand.",
            code="reading_attempts_used",
        )
    else:
        knowledge.admit_ai_request(ctx, scope=scope)  # SEC-020 / R-20
        reading_id = row.id
        repo.update_reading(
            session,
            reading_id,
            {
                "status": "queued",
                "error_code": None,
                "completed_at": None,
                "reviewed_at": None,
                "reviewed_by": None,
                "requested_by": ctx.user_id,
            },
        )
        ops.enqueue_event(session, READ_EVENT, {"reading_id": reading_id})
    _audit(
        session,
        "circular.read_requested",
        "document",
        doc.id,
        {"reading_id": reading_id, "version_no": version.version_no},
    )
    return get_circular(session, ctx, document_id)


def mark_reviewed(
    session: Session, ctx: UserContext, document_id: uuid.UUID, version: int
) -> CircularDetail:
    """Mark the current reading reviewed (``circular.review``; ``If-Match`` = reading version).
    409 ``reading_not_done`` or ``suggestions_open`` (decide every suggestion first)."""
    if not ctx.has(REVIEW):
        raise Forbidden()
    doc = _circular(session, ctx, document_id)
    current = _current(doc, repo.latest_readings(session, [doc.id]))
    if current is None or current.status not in ("ready", "needs_review"):
        raise Conflict("This circular has not been read yet.", code="reading_not_done")
    row = repo.lock_reading(session, current.id)
    if row is None:
        raise NotFound("Circular not found")
    _check_version(row.version, version)
    if repo.open_suggestion_counts(session, [row.id]).get(row.id, 0):
        raise Conflict(
            "Confirm or dismiss every suggested deadline first.", code="suggestions_open"
        )
    if row.reviewed_at is None:
        repo.update_reading(session, row.id, {"reviewed_at": _now(), "reviewed_by": ctx.user_id})
        _audit(session, "circular.reviewed", "document", doc.id, {"reading_id": row.id})
    return get_circular(session, ctx, document_id)


def _active_owner(session: Session, membership_id: uuid.UUID, field: str) -> str:
    members = {m.membership_id: m.display_name for m in identity.active_members(session)}
    if membership_id not in members:
        raise ValidationFailed([_error(field, "owner_not_active")])
    return members[membership_id]


def _locked_suggestion(
    session: Session, ctx: UserContext, suggestion_id: uuid.UUID, version: int
) -> tuple[CircularSuggestion, CircularReading]:
    if not ctx.has(REVIEW):
        raise Forbidden()
    pair = repo.lock_suggestion(session, suggestion_id)
    if pair is None:
        raise NotFound("Suggestion not found")
    suggestion, reading = pair
    try:
        _circular(session, ctx, reading.document_id)
    except NotFound:
        raise NotFound("Suggestion not found") from None
    _check_version(suggestion.version, version)
    if suggestion.status != "suggested":
        raise Conflict("This suggestion was already decided.", code="suggestion_decided")
    return suggestion, reading


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def confirm_suggestion(
    session: Session,
    ctx: UserContext,
    suggestion_id: uuid.UUID,
    data: SuggestionConfirmIn,
    version: int,
) -> TaskOut:
    """Turn a suggested deadline into a task (``circular.review``, FR-CIR-004): a person decides;
    the task keeps the circular's citation. Audited ``circular.suggestion_confirmed`` and
    ``task.created``; the owner is notified."""
    suggestion, reading = _locked_suggestion(session, ctx, suggestion_id, version)
    _refuse_aadhaar({"title": data.title, "details": data.details})
    _active_owner(session, data.owner_membership_id, "owner_membership_id")
    limits = rules().tasks
    edited = sorted(
        name for name in ("title", "details", "due_on") if getattr(data, name) is not None
    )
    task = repo.insert_task(
        session,
        {
            "id": new_id(),
            "title": _cut(data.title or suggestion.title, limits.max_title_chars),
            "details": data.details if data.details is not None else suggestion.details,
            "owner_membership_id": data.owner_membership_id,
            "due_on": data.due_on or suggestion.due_on,
            "status": "open",
            "source": "circular",
            "document_id": reading.document_id,
            "citation": suggestion.citation,
            "created_by": ctx.user_id,
            "created_by_membership": ctx.membership_id,
        },
    )
    repo.update_suggestion(
        session,
        suggestion.id,
        {
            "status": "confirmed",
            "task_id": task.id,
            "decided_by": ctx.user_id,
            "decided_at": _now(),
        },
    )
    _audit(
        session,
        "circular.suggestion_confirmed",
        "document",
        reading.document_id,
        {"suggestion_id": suggestion.id, "task_id": task.id, "edited": edited},
    )
    _task_created(session, ctx, task)
    return _task_out(session, ctx, task)


def dismiss_suggestion(
    session: Session, ctx: UserContext, suggestion_id: uuid.UUID, version: int
) -> SuggestionOut:
    """Dismiss a suggested deadline: no task (``circular.review``; audited)."""
    suggestion, reading = _locked_suggestion(session, ctx, suggestion_id, version)
    repo.update_suggestion(
        session,
        suggestion.id,
        {"status": "dismissed", "decided_by": ctx.user_id, "decided_at": _now()},
    )
    _audit(
        session,
        "circular.suggestion_dismissed",
        "document",
        reading.document_id,
        {"suggestion_id": suggestion.id},
    )
    fresh = repo.lock_suggestion(session, suggestion.id)
    if fresh is None:
        raise NotFound("Suggestion not found")
    session.refresh(fresh[0])
    return _suggestion_out(fresh[0])


# --- tasks ----------------------------------------------------------------------------------------


def _task_out(
    session: Session,
    ctx: UserContext,
    task: Task,
    *,
    names: Mapping[uuid.UUID, str] | None = None,
    visible_docs: set[uuid.UUID] | None = None,
) -> TaskOut:
    if names is None:
        names = identity.member_display_names(
            session, [task.owner_membership_id, task.created_by_membership]
        )
    shows_source = task.citation is not None and task.document_id is not None
    if shows_source and visible_docs is None:
        shows_source = documents.is_visible(session, ctx, task.document_id)  # type: ignore[arg-type]
    elif shows_source and visible_docs is not None:
        shows_source = task.document_id in visible_docs
    owner = MemberOut(
        membership_id=task.owner_membership_id,
        display_name=names.get(task.owner_membership_id),
    )
    return TaskOut(
        id=task.id,
        title=task.title,
        details=task.details,
        owner=owner,
        due_on=task.due_on,
        status=task.status,
        overdue=task.status in ACTIVE and task.due_on < today_ist(),
        source=task.source,
        document_id=task.document_id,
        citation=_citation(task.citation) if shows_source and task.citation else None,
        created_by=_member(names, task.created_by_membership),
        created_at=task.created_at,
        completed_at=task.completed_at,
        cancelled_at=task.cancelled_at,
        version=task.version,
    )


def _task_created(session: Session, ctx: UserContext, task: Task) -> None:
    _audit(
        session,
        "task.created",
        "task",
        task.id,
        {
            "source": task.source,
            "document_id": task.document_id,
            "owner_membership_id": task.owner_membership_id,
        },
    )
    _notify_owner(session, ctx, task)


def _notify_owner(session: Session, ctx: UserContext, task: Task) -> None:
    if task.owner_membership_id == ctx.membership_id:
        return
    notifications.notify(
        session,
        tenant_id=ctx.tenant_id,
        recipients=[task.owner_membership_id],
        template_key="task.assigned",
        params={"task_id": str(task.id)},
        resource_id=task.id,
        dedupe_key=f"task.assigned:{task.id}:{task.owner_membership_id}:{task.version}",
    )


def _due_bounds(due: DueWindow | None, today: dt.date) -> tuple[dt.date | None, dt.date | None]:
    if due == "overdue":
        return today, None
    if due == "week":
        return today + dt.timedelta(days=7), today
    if due == "later":
        return None, today + dt.timedelta(days=7)
    return None, None


def list_tasks(
    session: Session,
    ctx: UserContext,
    *,
    view: TaskView,
    status: TaskStatus | None,
    due: DueWindow | None,
    owner: uuid.UUID | None,
    document_id: uuid.UUID | None,
    limit: int,
    cursor: str | None,
) -> Page[TaskOut]:
    """``mine`` (default): tasks you own (``task.read``); ``all``: every task of the school
    (``task.read_all``, else 403). Without ``status``: open and in-progress tasks. Soonest due
    first; ``due`` = ``overdue`` / ``week`` (next 7 days) / ``later``."""
    if view == "all" and not ctx.has(TASK_ALL):
        raise Forbidden("You can see only your own tasks.", code="tasks_not_all")
    who = ctx.membership_id if view == "mine" else owner
    after = decode_cursor(cursor)
    position: tuple[dt.date, uuid.UUID] | None = None
    if after is not None:
        try:
            position = (dt.date.fromisoformat(str(after["d"])), uuid.UUID(str(after["i"])))
        except (KeyError, ValueError):
            raise ValidationFailed([_error("cursor", "invalid")]) from None
    before, since = _due_bounds(due, today_ist())
    rows = repo.list_tasks(
        session,
        owner=who,
        statuses=[status] if status else ACTIVE,
        document_id=document_id,
        due_before=before,
        due_from=since,
        after=position,
        limit=limit + 1,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    names = identity.member_display_names(
        session, {m for t in rows for m in (t.owner_membership_id, t.created_by_membership)}
    )
    docs = {t.document_id for t in rows if t.document_id is not None and t.citation is not None}
    visible = {d for d in docs if documents.is_visible(session, ctx, d)}
    data = [_task_out(session, ctx, t, names=names, visible_docs=visible) for t in rows]
    next_cursor = (
        encode_cursor({"d": rows[-1].due_on.isoformat(), "i": str(rows[-1].id)}) if more else None
    )
    return Page[TaskOut](data=data, next_cursor=next_cursor)


def _visible_task(
    session: Session, ctx: UserContext, task_id: uuid.UUID, *, lock: bool = False
) -> Task:
    task = repo.get_task(session, task_id, lock=lock)
    if task is None or not (
        task.owner_membership_id == ctx.membership_id or ctx.has(TASK_ALL) or ctx.has(TASK_MANAGE)
    ):
        raise NotFound("Task not found")
    return task


def get_task(session: Session, ctx: UserContext, task_id: uuid.UUID) -> TaskOut:
    """One task: yours, or any with ``task.read_all`` / ``task.manage`` (404 otherwise)."""
    return _task_out(session, ctx, _visible_task(session, ctx, task_id))


def _linked_document(session: Session, ctx: UserContext, document_id: uuid.UUID) -> uuid.UUID:
    try:
        return _circular(session, ctx, document_id).id
    except NotFound:
        raise ValidationFailed([_error("document_id", "not_found")]) from None


def create_task(session: Session, ctx: UserContext, data: TaskCreate) -> TaskOut:
    """Add a task by hand (``task.manage``), optionally linked to a circular you can see."""
    if not ctx.has(TASK_MANAGE):
        raise Forbidden()
    _refuse_aadhaar({"title": data.title, "details": data.details})
    _active_owner(session, data.owner_membership_id, "owner_membership_id")
    document_id = _linked_document(session, ctx, data.document_id) if data.document_id else None
    task = repo.insert_task(
        session,
        {
            "id": new_id(),
            "title": data.title,
            "details": data.details or None,
            "owner_membership_id": data.owner_membership_id,
            "due_on": data.due_on,
            "status": "open",
            "source": "manual",
            "document_id": document_id,
            "created_by": ctx.user_id,
            "created_by_membership": ctx.membership_id,
        },
    )
    _task_created(session, ctx, task)
    return _task_out(session, ctx, task)


def update_task(
    session: Session, ctx: UserContext, task_id: uuid.UUID, data: TaskUpdate, version: int
) -> TaskOut:
    """Change title, details, due date or owner (``task.manage``; ``If-Match``). Done or
    cancelled tasks cannot change (409 ``task_closed``). A new owner is notified."""
    if not ctx.has(TASK_MANAGE):
        raise Forbidden()
    _refuse_aadhaar({"title": data.title, "details": data.details})
    task = _visible_task(session, ctx, task_id, lock=True)
    _check_version(task.version, version)
    if task.status not in ACTIVE:
        raise Conflict("This task is closed. Reopen it first.", code="task_closed")
    values: dict[str, Any] = {}
    for name in ("title", "details", "due_on", "owner_membership_id"):
        value = getattr(data, name)
        if value is not None and value != getattr(task, name):
            values[name] = value if value != "" else None
    if "owner_membership_id" in values:
        _active_owner(session, values["owner_membership_id"], "owner_membership_id")
    if "title" in values and not values["title"]:
        raise ValidationFailed([_error("title", "required")])
    if not values:
        return _task_out(session, ctx, task)
    task = repo.update_task(session, task.id, values)
    _audit(session, "task.updated", "task", task.id, {"fields": sorted(values)})
    if "owner_membership_id" in values:
        _audit(
            session,
            "task.assigned",
            "task",
            task.id,
            {"owner_membership_id": task.owner_membership_id},
        )
        _notify_owner(session, ctx, task)
    return _task_out(session, ctx, task)


_OWNER_MOVES: Final = {
    "open": {"in_progress", "done"},
    "in_progress": {"open", "done"},
    "done": {"open"},
    "cancelled": set[str](),
}


def set_task_status(
    session: Session, ctx: UserContext, task_id: uuid.UUID, data: TaskStatusIn, version: int
) -> TaskOut:
    """Move a task (``task.read`` for your own; ``task.manage`` for any): ``open``,
    ``in_progress``, ``done`` (records who and when), back to ``open``; ``cancelled`` needs
    ``task.manage``. 409 ``task_status_not_allowed`` for other moves."""
    task = _visible_task(session, ctx, task_id, lock=True)
    manager = ctx.has(TASK_MANAGE)
    if task.owner_membership_id != ctx.membership_id and not manager:
        raise Forbidden("Only the task's owner can change its status.", code="not_task_owner")
    _check_version(task.version, version)
    target = data.status
    allowed = _OWNER_MOVES[task.status] | (
        {"cancelled"} if manager and task.status in ACTIVE else set()
    )
    if target == task.status:
        return _task_out(session, ctx, task)
    if target not in allowed:
        code = "task_cancel_not_allowed" if target == "cancelled" and not manager else None
        raise Conflict(
            "This change of status is not possible.", code=code or "task_status_not_allowed"
        )
    values: dict[str, Any] = {"status": target}
    if target == "done":
        values |= {"completed_at": _now(), "completed_by": ctx.user_id}
    elif task.status == "done":
        values |= {"completed_at": None, "completed_by": None}
    if target == "cancelled":
        values["cancelled_at"] = _now()
    task = repo.update_task(session, task.id, values)
    action = {"done": "task.completed", "cancelled": "task.cancelled"}.get(
        target, "task.status_changed"
    )
    _audit(session, action, "task", task.id, {"status": target})
    return _task_out(session, ctx, task)


def assignees(session: Session, ctx: UserContext) -> list[AssigneeOut]:
    """Active staff a task can be given to (a school-wide ``task.manage`` or
    ``circular.review``, the grants every route that assigns a task requires)."""
    if not any(ctx.has(p) and ctx.scope_for(p).school_wide for p in (TASK_MANAGE, REVIEW)):
        raise Forbidden()
    members = identity.active_members(session)
    return sorted(
        (
            AssigneeOut(membership_id=m.membership_id, display_name=m.display_name, roles=m.roles)
            for m in members
        ),
        key=lambda a: (a.display_name.casefold(), str(a.membership_id)),
    )


def send_reminders(session: Session, *, today: dt.date | None = None) -> int:
    """Daily (FR-TASK-007): one ``task.due_soon`` reminder per open task due within the
    configured days, one ``task.overdue`` once it is late; dedupe keys make reruns harmless."""
    day = today or today_ist()
    tenant_id = repo.current_tenant_id(session)
    horizon = day + dt.timedelta(days=rules().reminders.days_before_due)
    sent = 0
    for task in repo.tasks_to_remind(session, until=horizon):
        if task.due_on < day:
            key, template = f"task.overdue:{task.id}:{task.due_on}", "task.overdue"
            params: dict[str, Any] = {"task_id": str(task.id)}
        else:
            key, template = f"task.due_soon:{task.id}:{task.due_on}", "task.due_soon"
            params = {"task_id": str(task.id), "days": (task.due_on - day).days}
        sent += notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=[task.owner_membership_id],
            template_key=template,
            params=params,
            resource_id=task.id,
            dedupe_key=key,
        )
    return sent


# --- parent notices -------------------------------------------------------------------------------


def _files_available(notice: ParentNotice, now: dt.datetime | None = None) -> bool:
    if notice.render_status != "ready" or notice.rendered_at is None:
        return False
    keep = dt.timedelta(days=rules().notices.render_keep_days)
    return (now or _now()) - notice.rendered_at < keep


def _notice_out(session: Session, notice: ParentNotice) -> NoticeOut:
    people = _user_members(session, [notice.created_by, notice.approved_by])
    return NoticeOut(
        id=notice.id,
        source=notice.source,
        document_id=notice.document_id,
        status=notice.status,
        ai_drafted=notice.ai_drafted,
        draft_error=notice.draft_error,
        title_en=notice.title_en,
        body_en=notice.body_en,
        # Kept in the database; empty while Telugu is hidden (ADR-0036).
        title_te=telugu_text(notice.title_te) or "",
        body_te=telugu_text(notice.body_te) or "",
        created_by=people.get(notice.created_by),
        approved_by=people.get(notice.approved_by) if notice.approved_by else None,
        approved_at=notice.approved_at,
        render_status=notice.render_status,
        render_error=notice.render_error,
        files_available=_files_available(notice),
        created_at=notice.created_at,
        updated_at=notice.updated_at,
        version=notice.version,
    )


def _personal_fields(values: Mapping[str, str | None]) -> list[str]:
    return sorted(k for k, v in values.items() if v and knowledge.has_personal_numbers(v))


def _refuse_personal(values: Mapping[str, str | None]) -> None:
    fields = _personal_fields(values)
    if fields:
        raise ValidationFailed([_error(f, "notice_personal_data") for f in fields])


def _checked_source(session: Session, ctx: UserContext, data: NoticeCreate) -> uuid.UUID | None:
    """What a notice may be drafted from, checked with the caller's access (FR-NOTICE-001,
    FR-NOTICE-002): the circular's id (one they can see, C1 only), or None for staff text (no
    phone numbers, emails or Aadhaar-like numbers) and blank notices. 422 otherwise."""
    if data.source == "blank":
        return None
    if data.source == "staff_text":
        if not data.text:
            raise ValidationFailed([_error("text", "required")])
        _refuse_personal({"text": data.text})
        return None
    if data.document_id is None:
        raise ValidationFailed([_error("document_id", "required")])
    try:
        doc = _circular(session, ctx, data.document_id)
    except NotFound:
        raise ValidationFailed([_error("document_id", "not_found")]) from None
    if doc.sensitivity != "C1":
        raise ValidationFailed([_error("document_id", "notice_source_personal")])
    return doc.id


class _NoDraft(Exception):
    """The worker cannot draft this notice; ``code`` says why (stored as ``draft_error``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _notice_source(
    session: Session, ctx: UserContext, notice: ParentNotice
) -> knowledge.NoticeSource:
    """The text the AI drafts from, read with the requester's CURRENT access (invariant 8: a
    circular they can no longer see, or one no longer C1, is not sent): the circular's indexed
    passages and the dates confirmed from it as tasks, or the staff text. Never student data."""
    if notice.source == "staff_text":
        text = notice.source_text or ""
        if not text or knowledge.has_personal_numbers(text):
            raise _NoDraft(SOURCE_UNAVAILABLE)
        return knowledge.NoticeSource(kind="staff_text", staff_text=text)
    if notice.document_id is None:  # the circular was deleted
        raise _NoDraft(SOURCE_UNAVAILABLE)
    try:
        doc = _circular(session, ctx, notice.document_id)
    except NotFound:
        raise _NoDraft(SOURCE_UNAVAILABLE) from None
    if doc.sensitivity != "C1":
        raise _NoDraft("notice_source_personal")
    version = doc.current_version
    passages: tuple[knowledge.Passage, ...] = ()
    if version is not None:
        passages = knowledge.circular_passages(session, doc.id, version.id, version.version_no)
    if not passages:
        raise _NoDraft(knowledge.CIRCULAR_NO_TEXT)
    confirmed = repo.list_tasks(
        session,
        owner=None,
        statuses=("open", "in_progress", "done"),
        document_id=doc.id,
        due_before=None,
        due_from=None,
        after=None,
        limit=50,
    )
    deadlines = tuple(
        knowledge.ConfirmedDeadline(due_on=t.due_on, title=t.title)
        for t in confirmed
        if t.source == "circular"
    )
    return knowledge.NoticeSource(
        kind="circular", title=doc.title, passages=passages, deadlines=deadlines
    )


def _queue_draft(session: Session, notice_id: uuid.UUID) -> None:
    ops.enqueue_event(session, DRAFT_EVENT, {"notice_id": notice_id})


def create_notice(
    session: Session,
    ctx: UserContext,
    data: NoticeCreate,
    *,
    scope: MutableMapping[str, Any] | None = None,
) -> NoticeOut:
    """Start a parent notice (``notice.draft``; FR-NOTICE-001..003). From a circular (C1 only,
    else 422 ``notice_source_personal``) or staff text (422 ``notice_personal_data`` with phone
    numbers, emails or Aadhaar-like numbers) the notice starts ``drafting`` and the worker
    drafts both languages (:func:`run_notice_draft`, queue ``ingest``): the request never waits
    for the model. A ``blank`` notice starts as a ``draft``. Audited ``notice.draft_requested``
    (``notice.drafted`` when the draft is done; at once for a blank notice)."""
    if not ctx.has(NOTICE_DRAFT):
        raise Forbidden()
    document_id = _checked_source(session, ctx, data)
    by_ai = data.source != "blank"
    if by_ai:  # SEC-020 / R-20: the same per-user AI admission as Ask (429 ai_rate_limited)
        knowledge.admit_ai_request(ctx, scope=scope)
    notice = repo.insert_notice(
        session,
        {
            "id": new_id(),
            "source": data.source,
            "document_id": document_id,
            "status": "drafting" if by_ai else "draft",
            "source_text": data.text if data.source == "staff_text" else None,
            "ai_drafted": False,
            "draft_error": None,
            "created_by": ctx.user_id,
        },
    )
    summary: dict[str, Any] = {"source": data.source, "document_id": document_id}
    if by_ai:
        _queue_draft(session, notice.id)
        _audit(session, "notice.draft_requested", "parent_notice", notice.id, summary)
    else:
        summary |= {"ai_drafted": False, "draft_error": None}
        _audit(session, "notice.drafted", "parent_notice", notice.id, summary)
    return _notice_out(session, notice)


def _requester(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> UserContext | None:
    """The person who asked for the draft, with their current roles and scopes (None when they
    left the school or were suspended)."""
    try:
        user = identity.get_user(session, user_id)
    except NotFound:
        return None
    if not _active(user, _now()):
        return None
    roles = {r.key: r for r in identity.list_roles(session)}
    return _member_context(tenant_id, user, roles)


def run_notice_draft(tenant_id: uuid.UUID, notice_id: uuid.UUID) -> str:
    """Worker (outbox ``circulars.notice.draft_requested``, queue ``ingest``): draft one notice
    in English and Telugu (FR-NOTICE-003).

    Like the circular reading, three short transactions: read the source with the requester's
    current access; call the model with no transaction open (it may outlast
    ``idle_in_transaction_session_timeout``); store the draft (``draft``) or the reason
    (``draft_failed`` with ``draft_error``) with its audit event ``notice.drafted``. Idempotent:
    only a ``drafting`` notice is drafted. Returns ``draft``, the failure code or ``skipped``.
    """
    source: knowledge.NoticeSource | None = None
    code: str | None = None
    with tenant_session(tenant_id) as session:
        notice = repo.get_notice(session, notice_id, lock=True)
        if notice is None or notice.status != "drafting":
            return "skipped"
        requester = _requester(session, tenant_id, notice.created_by)
        if requester is None or not requester.has(NOTICE_DRAFT):
            code = SOURCE_UNAVAILABLE
        else:
            try:
                source = _notice_source(session, requester, notice)
            except _NoDraft as exc:
                code = exc.code
    draft: knowledge.NoticeDraft | None = None
    if source is not None:
        try:
            draft = knowledge.draft_notice(tenant_id, source)
        except knowledge.AiUnavailable as exc:
            code = exc.code
    with tenant_session(tenant_id) as session:
        notice = repo.get_notice(session, notice_id, lock=True)
        if notice is None or notice.status != "drafting":
            return "skipped"
        return _finish_draft(session, tenant_id, notice, draft, code)


def _finish_draft(
    session: Session,
    tenant_id: uuid.UUID,
    notice: ParentNotice,
    draft: knowledge.NoticeDraft | None,
    code: str | None,
) -> str:
    values: dict[str, Any]
    if draft is not None:
        values = {
            "status": "draft",
            "ai_drafted": True,
            "draft_error": None,
            "source_text": None,
            "title_en": draft.title_en,
            "body_en": draft.body_en,
            "title_te": draft.title_te,
            "body_te": draft.body_te,
        }
    else:
        values = {"status": "draft_failed", "draft_error": code or DRAFT_FAILED}
    notice = repo.update_notice(session, notice.id, values)
    _audit(
        session,
        "notice.drafted",
        "parent_notice",
        notice.id,
        {
            "source": notice.source,
            "document_id": notice.document_id,
            "ai_drafted": notice.ai_drafted,
            "draft_error": notice.draft_error,
        },
        actor_id=notice.created_by,
    )
    log.info(
        "circulars.notice.drafted",
        tenant_id=tenant_id,
        resource_type="parent_notice",
        resource_id=notice.id,
        error_code=notice.draft_error,
    )
    return notice.draft_error or "draft"


def abandon_draft(tenant_id: uuid.UUID, notice_id: uuid.UUID, code: str) -> None:
    """The worker gave up (retries used): the notice shows ``draft_failed`` with ``code``."""
    with tenant_session(tenant_id) as session:
        notice = repo.get_notice(session, notice_id, lock=True)
        if notice is not None and notice.status == "drafting":
            _finish_draft(session, tenant_id, notice, None, code)


def retry_notice_draft(
    session: Session,
    ctx: UserContext,
    notice_id: uuid.UUID,
    version: int,
    *,
    scope: MutableMapping[str, Any] | None = None,
) -> NoticeOut:
    """Ask the AI again after it could not draft the notice (``notice.draft``; ``If-Match``):
    ``draft_failed -> drafting``, with the source checked again with the caller's access (422
    like :func:`create_notice`). 409 ``notice_not_draft_failed`` in any other state. Audited
    ``notice.draft_requested``."""
    if not ctx.has(NOTICE_DRAFT):
        raise Forbidden()
    notice = _visible_notice(session, ctx, notice_id, lock=True)
    _check_version(notice.version, version)
    if notice.status != "draft_failed":
        raise Conflict(
            "Only a notice the AI could not draft can be tried again.",
            code="notice_not_draft_failed",
        )
    if notice.source == "circular" and notice.document_id is None:  # the circular was deleted
        raise ValidationFailed([_error("document_id", "not_found")])
    _checked_source(
        session,
        ctx,
        NoticeCreate(
            source=notice.source,
            document_id=notice.document_id,
            text=notice.source_text,
        ),
    )
    knowledge.admit_ai_request(ctx, scope=scope)  # SEC-020 / R-20
    notice = repo.update_notice(session, notice.id, {"status": "drafting", "draft_error": None})
    _queue_draft(session, notice.id)
    _audit(
        session,
        "notice.draft_requested",
        "parent_notice",
        notice.id,
        {"source": notice.source, "document_id": notice.document_id, "again": True},
    )
    return _notice_out(session, notice)


def _notice(session: Session, notice_id: uuid.UUID, *, lock: bool = False) -> ParentNotice:
    notice = repo.get_notice(session, notice_id, lock=lock)
    if notice is None:
        raise NotFound("Notice not found")
    return notice


def _notice_visible(session: Session, ctx: UserContext, notice: ParentNotice) -> bool:
    """A-16 (with DL-08): a notice not yet approved that was drafted from a circular is shown
    only to its drafter and to people who may read that circular (its AI text comes from it).
    Approved notices are written for parents, so every drafter sees them."""
    if notice.status == "approved" or notice.source != "circular":
        return True
    if notice.created_by == ctx.user_id or notice.document_id is None:
        return True
    try:
        _circular(session, ctx, notice.document_id)
    except NotFound:
        return False
    return True


def _visible_notice(
    session: Session, ctx: UserContext, notice_id: uuid.UUID, *, lock: bool = False
) -> ParentNotice:
    notice = _notice(session, notice_id, lock=lock)
    if not _notice_visible(session, ctx, notice):
        raise NotFound("Notice not found")
    return notice


def list_notices(
    session: Session, ctx: UserContext, *, status: str | None, limit: int, cursor: str | None
) -> Page[NoticeOut]:
    """Parent notices, newest first (``notice.draft``)."""
    if not ctx.has(NOTICE_DRAFT):
        raise Forbidden()
    after = decode_cursor(cursor)
    position: tuple[dt.datetime, uuid.UUID] | None = None
    if after is not None:
        try:
            position = (dt.datetime.fromisoformat(str(after["t"])), uuid.UUID(str(after["i"])))
        except (KeyError, ValueError):
            raise ValidationFailed([_error("cursor", "invalid")]) from None
    rows = repo.list_notices(session, status=status, after=position, limit=limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
        if more
        else None
    )
    shown = [n for n in rows if _notice_visible(session, ctx, n)]
    return Page[NoticeOut](data=[_notice_out(session, n) for n in shown], next_cursor=next_cursor)


def get_notice(session: Session, ctx: UserContext, notice_id: uuid.UUID) -> NoticeOut:
    if not ctx.has(NOTICE_DRAFT):
        raise Forbidden()
    return _notice_out(session, _visible_notice(session, ctx, notice_id))


def update_notice(
    session: Session, ctx: UserContext, notice_id: uuid.UUID, data: NoticeUpdate, version: int
) -> NoticeOut:
    """Edit a draft (``notice.draft``; ``If-Match``). 409 ``notice_approved`` once approved."""
    if not ctx.has(NOTICE_DRAFT):
        raise Forbidden()
    notice = _visible_notice(session, ctx, notice_id, lock=True)
    _check_version(notice.version, version)
    _refuse_drafting(notice)
    if notice.status == "approved":
        raise Conflict(
            "An approved notice cannot be changed. Start a new one.", code="notice_approved"
        )
    values: dict[str, Any] = {
        k: v for k, v in data.model_dump(exclude_none=True).items() if v != getattr(notice, k)
    }
    _refuse_personal(values)
    if not values:
        return _notice_out(session, notice)
    fields = sorted(values)
    if notice.status == "draft_failed":  # written by hand now: a draft like any other
        values |= {"status": "draft", "source_text": None}
    notice = repo.update_notice(session, notice.id, values)
    _audit(session, "notice.edited", "parent_notice", notice.id, {"fields": fields})
    return _notice_out(session, notice)


def _refuse_drafting(notice: ParentNotice) -> None:
    if notice.status == "drafting":
        raise Conflict(
            "The AI is still drafting this notice. Try again in a moment.",
            code="notice_drafting",
        )


def _queue_render(session: Session, notice_id: uuid.UUID) -> None:
    ops.enqueue_event(session, RENDER_EVENT, {"notice_id": notice_id})


def approve_notice(
    session: Session, ctx: UserContext, notice_id: uuid.UUID, version: int
) -> NoticeOut:
    """Approve a draft (``notice.approve``; ``If-Match``; FR-NOTICE-004/005): every title and
    body filled (422 ``notice_incomplete``; the Telugu ones only while Telugu is shown,
    ADR-0036), no phone numbers, emails or Aadhaar-like numbers
    (422 ``notice_personal_data``). The PDF and image are rendered next (queue ``pdf``)."""
    if not ctx.has(NOTICE_APPROVE):
        raise Forbidden()
    notice = _visible_notice(session, ctx, notice_id, lock=True)
    _check_version(notice.version, version)
    _refuse_drafting(notice)
    if notice.status == "approved":
        raise Conflict("This notice is already approved.", code="notice_approved")
    texts = {
        "title_en": notice.title_en,
        "body_en": notice.body_en,
        "title_te": notice.title_te,
        "body_te": notice.body_te,
    }
    # English first (ADR-0036): the Telugu title and body are needed only while Telugu is shown.
    needed = texts if telugu_enabled() else {"title_en": notice.title_en, "body_en": notice.body_en}
    empty = sorted(k for k, v in needed.items() if not v.strip())
    if empty:
        raise ValidationFailed([_error(f, "notice_incomplete") for f in empty])
    _refuse_personal(texts)
    values: dict[str, Any] = {
        "status": "approved",
        "approved_by": ctx.user_id,
        "approved_at": _now(),
        "render_status": "queued",
        "source_text": None,
    }
    # The database still requires both languages on an approved notice
    # (parent_notices_approved_complete). While Telugu is hidden the Telugu title and body take
    # the English text, so no migration is needed; it is never shown as Telugu (the page skips a
    # Telugu section that repeats the English one, and the API hides it). Any Telugu text already
    # there is replaced too: the approver could not see it (the API hides it), so it was never
    # reviewed and must not reach parents when Telugu is switched on (audit 2026-10-06 R-16).
    if not telugu_enabled():
        values["title_te"] = notice.title_en
        values["body_te"] = notice.body_en
    notice = repo.update_notice(session, notice.id, values)
    _queue_render(session, notice.id)
    _audit(
        session, "notice.approved", "parent_notice", notice.id, {"ai_drafted": notice.ai_drafted}
    )
    return _notice_out(session, notice)


def request_render(
    session: Session, ctx: UserContext, notice_id: uuid.UUID, version: int
) -> NoticeOut:
    """Render the approved notice's files again (``notice.draft``; ``If-Match``)."""
    if not ctx.has(NOTICE_DRAFT):
        raise Forbidden()
    notice = _visible_notice(session, ctx, notice_id, lock=True)
    _check_version(notice.version, version)
    if notice.status != "approved":
        raise Conflict("Approve the notice first.", code="notice_not_approved")
    if notice.render_status == "queued":
        raise Conflict("The files are being made. Try again shortly.", code="render_in_progress")
    notice = repo.update_notice(
        session,
        notice.id,
        {
            "render_status": "queued",
            "render_error": None,
            "pdf_key": None,
            "png_key": None,
            "rendered_at": None,
        },
    )
    _queue_render(session, notice.id)
    _audit(session, "notice.render_requested", "parent_notice", notice.id, {})
    return _notice_out(session, notice)


def download_url(
    session: Session, ctx: UserContext, notice_id: uuid.UUID, file_format: NoticeFileFormat
) -> NoticeDownloadOut:
    """A link to the A4 PDF or the PNG, valid at most 5 minutes (``notice.draft``; audited
    ``notice.downloaded``). 409 ``notice_files_not_ready`` or ``notice_files_expired`` (render
    again)."""
    if not ctx.has(NOTICE_DRAFT):
        raise Forbidden()
    notice = _visible_notice(session, ctx, notice_id)
    key = notice.pdf_key if file_format == "pdf" else notice.png_key
    if notice.render_status != "ready" or key is None:
        raise Conflict("The files are not ready yet.", code="notice_files_not_ready")
    if not _files_available(notice):
        raise Conflict("The files were removed. Make them again.", code="notice_files_expired")
    filename = f"parent-notice-{str(notice.id)[:8]}.{file_format}"
    url, expires_at = documents.export_download_url(
        session,
        notice.id,
        key,
        content_type=NOTICE_MIME[file_format],
        filename=filename,
        ttl_s=DOWNLOAD_TTL_S,
    )
    _audit(session, "notice.downloaded", "parent_notice", notice.id, {"format": file_format})
    return NoticeDownloadOut(
        url=url, expires_at=expires_at, filename=filename, mime_type=NOTICE_MIME[file_format]
    )


def render_notice(tenant_id: uuid.UUID, notice_id: uuid.UUID) -> str:
    """Worker (outbox ``circulars.notice.render_requested``, queue ``pdf``): the A4 PDF and the
    PNG of an approved notice, stored under the school's ``exports/`` prefix."""
    with tenant_session(tenant_id) as session:
        notice = repo.get_notice(session, notice_id)
        if notice is None or notice.status != "approved" or notice.render_status != "queued":
            return "skipped"
        school = tenancy.get_tenant(session).name
        approved = (notice.approved_at or _now()).astimezone(IST).date()
        page = notice_html(
            school_name=school,
            approved_on=approved,
            title_en=notice.title_en,
            body_en=notice.body_en,
            title_te=notice.title_te,
            body_te=notice.body_te,
        )
    try:
        pdf_bytes = pdf.get_renderer().render(page)
        png_bytes = pdf.get_image_renderer().render_png(
            page, width_px=rules().notices.image_width_px
        )
    except pdf.RenderError:
        with tenant_session(tenant_id) as session:
            repo.update_notice(
                session, notice_id, {"render_status": "failed", "render_error": "render_failed"}
            )
        log.warning("circulars.notice.render_failed", tenant_id=tenant_id, resource_id=notice_id)
        return "failed"
    with tenant_session(tenant_id) as session:
        current = repo.get_notice(session, notice_id, lock=True)
        if current is None or current.render_status != "queued":
            return "skipped"
        pdf_key = documents.store_export_file(
            session, notice_id, "notice.pdf", pdf_bytes, NOTICE_MIME["pdf"]
        )
        png_key = documents.store_export_file(
            session, notice_id, "notice.png", png_bytes, NOTICE_MIME["png"]
        )
        repo.update_notice(
            session,
            notice_id,
            {
                "render_status": "ready",
                "render_error": None,
                "pdf_key": pdf_key,
                "png_key": png_key,
                "rendered_at": _now(),
            },
        )
        _audit(
            session,
            "notice.rendered",
            "parent_notice",
            notice_id,
            {"pdf_bytes": len(pdf_bytes), "png_bytes": len(png_bytes)},
            system=True,
        )
    return "ready"


def abandon_render(tenant_id: uuid.UUID, notice_id: uuid.UUID, code: str) -> None:
    """The worker gave up rendering: the notice shows the failure (render again later)."""
    with tenant_session(tenant_id) as session:
        notice = repo.get_notice(session, notice_id, lock=True)
        if notice is not None and notice.render_status == "queued":
            repo.update_notice(
                session, notice_id, {"render_status": "failed", "render_error": code}
            )


# --- registration ---------------------------------------------------------------------------------

if on_version_indexed not in knowledge.INDEXED_HOOKS:
    knowledge.INDEXED_HOOKS.append(on_version_indexed)


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: circular readings and suggestions, tasks and parent notices of the current
    school for its full data export (``app.admin``; the caller checked ``tenant.export_all``
    and audits the export). No student data lives in these tables."""
    return repo.export_tables(session)


# Offboarding purge (FR-PLT-005, ADR-0029): children before parents.
_PURGE = purging.PurgeTables(
    deleted=(
        "kb.circular_suggestions",
        "kb.circular_readings",
        "ops.parent_notices",
        "ops.tasks",
    ),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="circulars", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "DRAFT_EVENT",
    "DRAFT_TASK",
    "NOTICE_APPROVE",
    "NOTICE_DRAFT",
    "READ",
    "READ_EVENT",
    "READ_TASK",
    "RENDER_EVENT",
    "RENDER_TASK",
    "REVIEW",
    "TASK_ALL",
    "TASK_MANAGE",
    "TASK_READ",
    "abandon_draft",
    "abandon_reading",
    "abandon_render",
    "approve_notice",
    "assignees",
    "confirm_suggestion",
    "create_notice",
    "create_task",
    "dismiss_suggestion",
    "download_url",
    "export_records",
    "get_circular",
    "get_notice",
    "get_task",
    "list_circulars",
    "list_notices",
    "list_tasks",
    "mark_reviewed",
    "on_version_indexed",
    "purge_tenant_data",
    "render_notice",
    "request_reading",
    "request_render",
    "retry_notice_draft",
    "run_notice_draft",
    "run_reading",
    "send_reminders",
    "set_task_status",
    "tenant_data_counts",
    "today_ist",
    "update_notice",
    "update_task",
]
