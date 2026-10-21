"""CBSE Appendix IX disclosure public API (R3-1C; ADR-0051; US-2901..US-2904;
FR-DISC-001..012; SEC-050, PRV-037, NFR-PRV-006). Other modules call only this module.

- **CBSE only (FR-DISC-001).** Every staff operation answers 404 ``module_hidden`` unless the
  school's boards include CBSE.
- **Draft (FR-DISC-004).** One per school (``If-Match``; version 0 before the first save):
  typed inputs checked against the format (:mod:`app.disclosure.values`) and documents the
  editor can see (``documents.is_visible``). Derived values are shown as they are now.
- **Publish (FR-DISC-005, FR-DISC-008).** ``disclosure.publish`` (step-up) with
  ``confirm_no_student_data``: required items filled, every chosen document publishable now
  (a clean PDF, not C3); the content is frozen with its SHA-256 and the document versions in one
  transaction, the first publish issues the school's ``school_disclosure`` handle, and the HTML
  and PDF files are queued (outbox, queue ``pdf``). Audited ``disclosure.published``.
- **Live page (FR-DISC-009).** The latest snapshot, unless it was withdrawn (then the uniform
  404). Withdrawing (FR-DISC-007) applies to the live snapshot only; publishing again makes a
  new live snapshot.
- **Link (FR-DISC-008).** "Change the link" revokes the handle and issues a new one; the raw
  key is kept on the draft so staff can copy the link again (it is meant to be published).
- **Files (FR-DISC-011).** Rendered by the worker under the school's ``exports/`` prefix (the
  bucket expires them after 7 days; a later download makes them again).
- **No student data (FR-DISC-006).** The format has no student item; derived values are counts
  and school-level facts from ``tenancy``, ``identity`` and ``fees`` services; result counts below
  10 are "fewer than 10".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any, Final

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core import pdf
from app.core import purge as purging
from app.core.config import get_settings
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.disclosure import derived as derive
from app.disclosure import formats, templates, values
from app.disclosure import repository as repo
from app.disclosure.config import rules
from app.disclosure.models import DisclosureDraft, DisclosureSnapshot
from app.disclosure.schemas import (
    DerivedValueOut,
    DisclosureDraftIn,
    DisclosureDraftOut,
    DisclosureFormatOut,
    DraftDocumentOut,
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
from app.documents import service as documents
from app.identity import service as identity
from app.ops import service as ops
from app.public import service as public
from app.tenancy import service as tenancy

log = get_logger(__name__)

EDIT: Final = "disclosure.edit"
PUBLISH: Final = "disclosure.publish"
HANDLE_KIND: Final = "school_disclosure"
TARGET_TYPE: Final = "school_disclosure"
RENDER_EVENT: Final = "disclosure.render_requested"
RENDER_TASK: Final = "disclosure.render_files"
FILE_MIME: Final[dict[str, str]] = {"html": "text/html; charset=utf-8", "pdf": "application/pdf"}
_FILE_NAMES: Final[dict[str, str]] = {"html": "disclosure.html", "pdf": "disclosure.pdf"}
_DAY: Final = dt.timedelta(days=1)

ops.register_outbox_route(RENDER_EVENT, RENDER_TASK)


class ModuleHidden(NotFound):
    code = "module_hidden"

    def __init__(self) -> None:
        super().__init__("The CBSE disclosure is only for schools whose boards include CBSE.")


class ConfirmationRequired(ValidationFailed):
    code = "confirmation_required"


class Incomplete(ValidationFailed):
    code = "incomplete"


# --- helpers ------------------------------------------------------------------------------------


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    resource_id: uuid.UUID | None,
    summary: dict[str, Any],
    *,
    resource_type: str = "disclosure_snapshot",
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=None if system else _request_id(),
    )


def _require_cbse(session: Session) -> None:
    if not derive.is_cbse(session):
        raise ModuleHidden()


def _now(session: Session) -> dt.datetime:
    return repo.now(session)


def _ist_today(session: Session) -> dt.date:
    return _now(session).astimezone(templates.IST).date()


def _names(session: Session, user_ids: list[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    found = identity.members_for_users(session, [u for u in user_ids if u is not None])
    return {user_id: name for user_id, (_m, name) in found.items()}


def link_url(key: str) -> str:
    """The public page: ``{SOS_PUBLIC_BASE_URL}/d/{key}`` (a path while the base is not set)."""
    base = (get_settings().public_base_url or "").rstrip("/")
    return f"{base}/d/{key}"


def _content_digest(content: dict[str, Any]) -> bytes:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


# --- format -------------------------------------------------------------------------------------


def format_out(fmt: formats.DisclosureFormat) -> DisclosureFormatOut:
    return DisclosureFormatOut.model_validate(
        {
            "key": fmt.key,
            "version": fmt.version,
            "title": fmt.title,
            "verified": fmt.verified,
            "sources": [s.model_dump() for s in fmt.sources],
            "sections": [
                {
                    "key": s.key,
                    "title": s.title,
                    "items": [
                        {
                            **i.model_dump(exclude={"source", "documents"}),
                            "takes_documents": i.takes_documents,
                        }
                        for i in s.items
                    ],
                }
                for s in fmt.sections
            ],
        }
    )


def get_format(session: Session, ctx: UserContext) -> DisclosureFormatOut:
    """The format schools fill (sections, items, fills, ``verified``, sources; FR-DISC-001)."""
    _require_cbse(session)
    return format_out(formats.current())


# --- draft --------------------------------------------------------------------------------------


def _from_schoolos(session: Session, fmt: formats.DisclosureFormat) -> dict[str, DerivedValueOut]:
    _name, school = derive.school_values(session)
    found = derive.derived_values(session)
    out: dict[str, DerivedValueOut] = {}
    for item in fmt.items():
        if item.fill == "school":
            out[item.key] = DerivedValueOut(value=school.get(item.source or ""), table=None)
        elif item.fill == "derived":
            d = found.get(item.source or "", values.Derived())
            out[item.key] = DerivedValueOut.model_validate(
                {"value": d.value, "table": d.table.as_dict() if d.table else None}
            )
    return out


def _draft_documents(
    session: Session, ctx: UserContext, chosen: dict[str, list[str]]
) -> dict[str, list[DraftDocumentOut]]:
    out: dict[str, list[DraftDocumentOut]] = {}
    for key, ids in chosen.items():
        items: list[DraftDocumentOut] = []
        for raw in ids:
            doc_id = uuid.UUID(str(raw))
            title: str | None = None
            found = None
            if documents.is_visible(session, ctx, doc_id):
                title = documents.get_document(session, ctx, doc_id).title
                found = documents.publishable_version(session, doc_id)
            items.append(DraftDocumentOut(id=doc_id, title=title, publishable=found is not None))
        out[key] = items
    return out


def _draft_out(
    session: Session, ctx: UserContext, draft: DisclosureDraft | None
) -> DisclosureDraftOut:
    fmt = formats.current()
    inputs: dict[str, Any] = dict(draft.inputs) if draft else {}
    chosen: dict[str, list[str]] = dict(draft.documents) if draft else {}
    names = _names(session, [draft.updated_by] if draft else [])
    return DisclosureDraftOut(
        format_key=fmt.key,
        format_version=fmt.version,
        inputs=inputs,
        documents=_draft_documents(session, ctx, chosen),
        from_schoolos=_from_schoolos(session, fmt),
        missing=values.missing_required(fmt, inputs),
        version=draft.version if draft else 0,
        updated_at=draft.updated_at if draft else None,
        updated_by_name=names.get(draft.updated_by) if draft and draft.updated_by else None,
    )


def get_draft(session: Session, ctx: UserContext) -> DisclosureDraftOut:
    """The draft with derived values as of now (``disclosure.edit``; FR-DISC-004)."""
    _require_cbse(session)
    return _draft_out(session, ctx, repo.get_draft(session))


def _check_documents(
    session: Session, ctx: UserContext, fmt: formats.DisclosureFormat, body: DisclosureDraftIn
) -> tuple[dict[str, list[str]], values.Problems]:
    problems = values.check_document_keys(fmt, body.documents)
    out: dict[str, list[str]] = {}
    for key, ids in body.documents.items():
        if not ids:
            continue
        for doc_id in ids:
            if not documents.is_visible(session, ctx, doc_id):
                problems.add(f"documents.{key}", "document_not_found")
            elif documents.publishable_version(session, doc_id) is None:
                problems.add(f"documents.{key}", "document_not_publishable")
        out[key] = [str(i) for i in ids]
    return out, problems


def put_draft(
    session: Session, ctx: UserContext, body: DisclosureDraftIn, *, expected_version: int
) -> DisclosureDraftOut:
    """Replace the draft (``disclosure.edit``, ``If-Match``: ``W/"0"`` for the first save).
    422 per item (``inputs.<key>`` / ``documents.<key>``); 412 when someone saved in between.
    Audited ``disclosure.draft_saved`` (item keys only)."""
    _require_cbse(session)
    fmt = formats.current()
    inputs, problems = values.check_inputs(fmt, body.inputs)
    chosen, doc_problems = _check_documents(session, ctx, fmt, body)
    errors = problems.errors + doc_problems.errors
    if errors:
        raise ValidationFailed(errors)
    draft = repo.get_draft(session, lock=True)
    current = draft.version if draft else 0
    if expected_version != current:
        raise PreconditionFailed()
    if draft is None:
        draft = repo.insert_draft(
            session,
            id=new_id(),
            format_key=fmt.key,
            format_version=fmt.version,
            inputs=inputs,
            documents=chosen,
            updated_by=ctx.user_id,
        )
    else:
        draft = repo.update_draft(
            session,
            draft.id,
            {
                "format_key": fmt.key,
                "format_version": fmt.version,
                "inputs": inputs,
                "documents": chosen,
                "updated_by": ctx.user_id,
                "version": draft.version + 1,
            },
        )
    _audit(
        session,
        "disclosure.draft_saved",
        draft.id,
        {"inputs": sorted(inputs), "documents": sum(len(v) for v in chosen.values())},
        resource_type="disclosure_draft",
    )
    return _draft_out(session, ctx, draft)


# --- publish ------------------------------------------------------------------------------------


def _views_total(row: DisclosureSnapshot) -> int:
    return sum(int(v) for v in (row.views_by_day or {}).values())


def _snapshot_out(
    session: Session, row: DisclosureSnapshot, live_id: uuid.UUID | None
) -> SnapshotOut:
    names = _names(session, [row.published_by, row.withdrawn_by])
    return SnapshotOut(
        id=row.id,
        format_key=row.format_key,
        format_version=row.format_version,
        published_at=row.published_at,
        published_by_name=names.get(row.published_by),
        withdrawn_at=row.withdrawn_at,
        withdrawn_by_name=names.get(row.withdrawn_by) if row.withdrawn_by else None,
        withdraw_reason=row.withdraw_reason,
        live=row.id == live_id,
        documents=sum(
            len(i.get("documents") or [])
            for s in row.content.get("sections") or []
            for i in s.get("items") or []
        ),
        views_total=_views_total(row),
        pdf_status=row.pdf_status,
        html_status=row.html_status,
    )


def _live(session: Session, *, lock: bool = False) -> DisclosureSnapshot | None:
    latest = repo.latest_snapshot(session, lock=lock)
    return latest if latest is not None and latest.withdrawn_at is None else None


def _ensure_link(session: Session, ctx: UserContext, draft: DisclosureDraft) -> DisclosureDraft:
    """The school's live handle (the first publish issues it; FR-DISC-008)."""
    if draft.link_handle_id is not None:
        return draft
    return _issue_link(session, ctx, draft)


def _issue_link(session: Session, ctx: UserContext, draft: DisclosureDraft) -> DisclosureDraft:
    issued = public.issue_handle(
        session,
        kind=HANDLE_KIND,
        target_type=TARGET_TYPE,
        target_id=draft.id,
        created_by=ctx.user_id,
    )
    if issued.key is None:  # pragma: no cover - the public service generates disclosure keys
        raise RuntimeError("no key issued")
    return repo.update_draft(
        session,
        draft.id,
        {
            "link_handle_id": issued.handle_id,
            "link_key": issued.key,
            "link_created_at": _now(session),
        },
    )


def publish(session: Session, ctx: UserContext, body: PublishIn) -> SnapshotOut:
    """Freeze the draft into a new live snapshot (``disclosure.publish``, step-up;
    FR-DISC-005). 422 ``confirmation_required`` / ``incomplete`` (the required items) /
    ``validation_error`` (a document that cannot be published now)."""
    _require_cbse(session)
    if not body.confirm_no_student_data:
        raise ConfirmationRequired(
            [
                {
                    "field": "confirm_no_student_data",
                    "code": "confirmation_required",
                    "message_key": "errors.confirmation_required",
                }
            ],
            "Confirm that the documents contain no student's personal data.",
        )
    fmt = formats.current()
    draft = repo.get_draft(session, lock=True)
    inputs: dict[str, Any] = dict(draft.inputs) if draft else {}
    missing = values.missing_required(fmt, inputs)
    if draft is None or missing:
        raise Incomplete(
            [
                {"field": f"inputs.{k}", "code": "required", "message_key": "errors.required"}
                for k in missing
            ]
            or [{"field": "draft", "code": "required", "message_key": "errors.required"}],
            "Fill in the required items before publishing.",
        )
    # The draft is re-checked against today's rules and documents (a document may have been
    # archived or quarantined since it was chosen).
    clean, problems = values.check_inputs(fmt, inputs)
    published: list[values.PublishedDocument] = []
    n = 0
    for item in fmt.items():
        for raw in (draft.documents or {}).get(item.key, []):
            doc_id = uuid.UUID(str(raw))
            found = documents.publishable_version(session, doc_id)
            if found is None or not documents.is_visible(session, ctx, doc_id):
                problems.add(f"documents.{item.key}", "document_not_publishable")
                continue
            n += 1
            published.append(
                values.PublishedDocument(
                    n=n,
                    item_key=item.key,
                    document_id=found.document_id,
                    version_id=found.version_id,
                    title=values.clean_text(found.title)[:300] or "Document",
                )
            )
    if problems.errors:
        raise ValidationFailed(problems.errors)
    school_name, school = derive.school_values(session)
    content = values.build_content(
        fmt,
        school_name=school_name,
        inputs=clean,
        derived=derive.derived_values(session),
        school=school,
        documents=published,
    )
    draft = _ensure_link(session, ctx, draft)
    snapshot = repo.insert_snapshot(
        session,
        id=new_id(),
        format_key=fmt.key,
        format_version=fmt.version,
        content=content,
        content_sha256=_content_digest(content),
        published_by=ctx.user_id,
    )
    repo.insert_snapshot_documents(
        session,
        [
            {
                "id": new_id(),
                "snapshot_id": snapshot.id,
                "n": d.n,
                "item_key": d.item_key,
                "document_id": d.document_id,
                "version_id": d.version_id,
                "title": d.title,
            }
            for d in published
        ],
    )
    ops.enqueue_event(session, RENDER_EVENT, {"snapshot_id": str(snapshot.id)})
    _audit(
        session,
        "disclosure.published",
        snapshot.id,
        {
            "format_key": fmt.key,
            "format_version": fmt.version,
            "documents": len(published),
            "handle_id": draft.link_handle_id,
        },
    )
    return _snapshot_out(session, snapshot, snapshot.id)


# --- snapshots ----------------------------------------------------------------------------------


def list_snapshots(session: Session, ctx: UserContext) -> list[SnapshotOut]:
    """Published history, newest first (``disclosure.edit``)."""
    _require_cbse(session)
    live = _live(session)
    live_id = live.id if live else None
    return [
        _snapshot_out(session, row, live_id)
        for row in repo.list_snapshots(session, rules().history_limit)
    ]


def _load_snapshot(session: Session, snapshot_id: uuid.UUID, *, lock: bool = False) -> Any:
    row = repo.get_snapshot(session, snapshot_id, lock=lock)
    if row is None:
        raise NotFound("Snapshot not found")
    return row


def _public_out(row: DisclosureSnapshot) -> PublicDisclosureOut:
    content = row.content
    return PublicDisclosureOut.model_validate(
        {
            "school_name": content.get("school_name") or "",
            "title": content.get("title") or "",
            "published_at": row.published_at,
            "format_version": row.format_version,
            "sections": [
                {
                    "key": s.get("key"),
                    "title": s.get("title"),
                    "items": [
                        {
                            "key": i.get("key"),
                            "label": i.get("label"),
                            "value": i.get("value"),
                            "table": i.get("table"),
                            "documents": [
                                {"n": d.get("n"), "title": d.get("title")}
                                for d in i.get("documents") or []
                            ],
                            "link": i.get("link"),
                        }
                        for i in s.get("items") or []
                    ],
                }
                for s in content.get("sections") or []
            ],
        }
    )


def get_snapshot(session: Session, ctx: UserContext, snapshot_id: uuid.UUID) -> SnapshotDetailOut:
    """One publication with exactly what it showed (``disclosure.edit``)."""
    _require_cbse(session)
    row = _load_snapshot(session, snapshot_id)
    live = _live(session)
    base = _snapshot_out(session, row, live.id if live else None)
    return SnapshotDetailOut(
        **base.model_dump(),
        content=_public_out(row),
        views_by_day={k: int(v) for k, v in sorted((row.views_by_day or {}).items())},
    )


def withdraw(
    session: Session, ctx: UserContext, snapshot_id: uuid.UUID, body: WithdrawIn
) -> SnapshotOut:
    """Withdraw the live snapshot with a reason (``disclosure.publish``, step-up; FR-DISC-007):
    the page then answers the uniform 404 until someone publishes again. 409
    ``snapshot_not_live`` for any other snapshot. Audited ``disclosure.withdrawn``."""
    _require_cbse(session)
    reason = values.clean_text(body.reason)
    if not 10 <= len(reason) <= 500:
        raise ValidationFailed(
            [{"field": "reason", "code": "invalid", "message_key": "errors.invalid"}]
        )
    live = _live(session, lock=True)
    row = _load_snapshot(session, snapshot_id, lock=True)
    if live is None or live.id != row.id:
        raise Conflict("Only the page shown now can be withdrawn.", code="snapshot_not_live")
    row = repo.update_snapshot(
        session,
        row.id,
        {"withdrawn_at": _now(session), "withdrawn_by": ctx.user_id, "withdraw_reason": reason},
    )
    _audit(session, "disclosure.withdrawn", row.id, {"reason_length": len(reason)})
    return _snapshot_out(session, row, None)


# --- link ---------------------------------------------------------------------------------------


def get_link(session: Session, ctx: UserContext) -> LinkOut:
    """The page's link (``disclosure.edit``); ``url`` null before the first publish."""
    _require_cbse(session)
    draft = repo.get_draft(session)
    if draft is None or draft.link_key is None:
        return LinkOut(url=None, created_at=None)
    return LinkOut(url=link_url(draft.link_key), created_at=draft.link_created_at)


def rotate_link(session: Session, ctx: UserContext) -> LinkOut:
    """ "Change the link" (``disclosure.publish``, step-up; FR-DISC-008): the old link answers
    the uniform 404 at once. 409 ``not_published`` before the first publish. Audited
    ``disclosure.link_rotated``."""
    _require_cbse(session)
    draft = repo.get_draft(session, lock=True)
    if draft is None or draft.link_handle_id is None:
        raise Conflict("Publish the disclosure first.", code="not_published")
    old = draft.link_handle_id
    public.revoke(session, old, reason="rotated")
    draft = repo.update_draft(
        session, draft.id, {"link_handle_id": None, "link_key": None, "link_created_at": None}
    )
    draft = _issue_link(session, ctx, draft)
    _audit(
        session,
        "disclosure.link_rotated",
        draft.id,
        {"old_handle_id": old, "handle_id": draft.link_handle_id},
        resource_type="disclosure_draft",
    )
    if draft.link_key is None:  # pragma: no cover - just issued
        raise RuntimeError("no link")
    return LinkOut(url=link_url(draft.link_key), created_at=draft.link_created_at)


# --- files (HTML for the school's website, A4 PDF; FR-DISC-011) --------------------------------


def _files_available(row: DisclosureSnapshot, now: dt.datetime) -> bool:
    if row.files_rendered_at is None:
        return False
    return now - row.files_rendered_at < rules().files_keep_days * _DAY


def download_url(
    session: Session, ctx: UserContext, snapshot_id: uuid.UUID, file_format: FileFormat
) -> FileLinkOut:
    """A presigned link (at most 5 minutes) to the snapshot's HTML file or A4 PDF
    (``disclosure.edit``; audited ``disclosure.downloaded``). 409 ``files_not_ready`` while they
    are being made (expired files are made again), ``files_failed`` after the worker gave up."""
    _require_cbse(session)
    row = _load_snapshot(session, snapshot_id, lock=True)
    key = row.pdf_key if file_format == "pdf" else row.html_key
    status = row.pdf_status if file_format == "pdf" else row.html_status
    now = _now(session)
    if status == "ready" and key is not None and not _files_available(row, now):
        repo.update_snapshot(
            session,
            row.id,
            {
                "pdf_status": "queued",
                "html_status": "queued",
                "pdf_key": None,
                "html_key": None,
                "files_rendered_at": None,
                "files_error": None,
            },
        )
        ops.enqueue_event(session, RENDER_EVENT, {"snapshot_id": str(row.id)})
        status, key = "queued", None
    if status == "failed":
        raise Conflict("The files could not be made. Try again later.", code="files_failed")
    if status != "ready" or key is None:
        raise Conflict("The files are being made. Try again in a minute.", code="files_not_ready")
    school = values.clean_text(str(row.content.get("school_name") or "school"))
    stem = "".join(c if c.isalnum() else "-" for c in school.lower()).strip("-")[:60] or "school"
    filename = f"{stem}-cbse-disclosure-{templates.published_on(row.published_at)}.{file_format}"
    filename = filename.replace(" ", "-")
    url, expires_at = documents.export_download_url(
        session,
        row.id,
        key,
        content_type=FILE_MIME[file_format],
        filename=filename,
        ttl_s=rules().download_url_ttl_s,
    )
    _audit(session, "disclosure.downloaded", row.id, {"format": file_format})
    return FileLinkOut(
        url=url, expires_at=expires_at, filename=filename, mime_type=FILE_MIME[file_format]
    )


def render_files(tenant_id: uuid.UUID, snapshot_id: uuid.UUID) -> str:
    """Worker (outbox ``disclosure.render_requested``, queue ``pdf``): the HTML file and the A4
    PDF of one snapshot, stored under the school's ``exports/`` prefix. Idempotent."""
    with tenant_session(tenant_id) as session:
        row = repo.get_snapshot(session, snapshot_id)
        if row is None or (row.pdf_status != "queued" and row.html_status != "queued"):
            return "skipped"
        draft = repo.get_draft(session)
        key = draft.link_key if draft is not None else None
        base = link_url(key) if key else ""
        page = templates.render_page(
            row.content, published_at=row.published_at, document_url=base + "/documents/{n}"
        )
    try:
        pdf_bytes = pdf.get_renderer().render(page)
    except pdf.RenderError:
        abandon_render(tenant_id, snapshot_id, "render_failed")
        log.warning("disclosure.render_failed", tenant_id=tenant_id, resource_id=snapshot_id)
        return "failed"
    with tenant_session(tenant_id) as session:
        row = repo.get_snapshot(session, snapshot_id, lock=True)
        if row is None or (row.pdf_status != "queued" and row.html_status != "queued"):
            return "skipped"
        html_key = documents.store_export_file(
            session, row.id, _FILE_NAMES["html"], page.encode("utf-8"), FILE_MIME["html"]
        )
        pdf_key = documents.store_export_file(
            session, row.id, _FILE_NAMES["pdf"], pdf_bytes, FILE_MIME["pdf"]
        )
        repo.update_snapshot(
            session,
            row.id,
            {
                "pdf_status": "ready",
                "html_status": "ready",
                "pdf_key": pdf_key,
                "html_key": html_key,
                "files_rendered_at": _now(session),
                "files_error": None,
            },
        )
        _audit(
            session,
            "disclosure.files_rendered",
            row.id,
            {"pdf_bytes": len(pdf_bytes), "html_bytes": len(page.encode("utf-8"))},
            system=True,
        )
    return "ready"


def abandon_render(tenant_id: uuid.UUID, snapshot_id: uuid.UUID, code: str) -> None:
    """The worker gave up: the snapshot shows the failure (a later download does not retry)."""
    with tenant_session(tenant_id) as session:
        row = repo.get_snapshot(session, snapshot_id, lock=True)
        if row is None or row.pdf_status == "ready":
            return
        repo.update_snapshot(
            session,
            row.id,
            {"pdf_status": "failed", "html_status": "failed", "files_error": code},
        )


# --- public page (no signed-in member; ADR-0051 decision 4) ------------------------------------


def _public_snapshot(session: Session, handle_id: uuid.UUID) -> DisclosureSnapshot | None:
    """The live snapshot behind a live handle that is still the school's current link."""
    handle = public.load_handle(session, handle_id, (HANDLE_KIND,))
    if handle is None or handle.target_type != TARGET_TYPE:
        return None
    draft = repo.get_draft(session)
    if draft is None or draft.id != handle.target_id or draft.link_handle_id != handle_id:
        return None
    if not derive.is_cbse(session):
        return None
    return _live(session)


def public_page(tenant_id: uuid.UUID, handle_id: uuid.UUID) -> PublicDisclosureOut | None:
    """The published page for a live ``school_disclosure`` handle (FR-DISC-009), or ``None``
    (the uniform 404) when nothing is live. Counts one view per day per snapshot (FR-DISC-010;
    no visitor data)."""
    with tenant_session(tenant_id) as session:
        row = _public_snapshot(session, handle_id)
        if row is None:
            return None
        repo.count_view(session, row.id, _ist_today(session), rules().views_keep_days)
        return _public_out(row)


def public_document(
    tenant_id: uuid.UUID, handle_id: uuid.UUID, n: int
) -> PublicDocumentLinkOut | None:
    """A presigned link (at most 5 minutes) to document ``n`` of the live snapshot, only for the
    exact version it published (FR-DISC-009); ``None`` for anything else."""
    with tenant_session(tenant_id) as session:
        row = _public_snapshot(session, handle_id)
        if row is None:
            return None
        doc = repo.snapshot_document(session, row.id, n)
        if doc is None:
            return None
        found = documents.published_version_url(
            session,
            doc.document_id,
            doc.version_id,
            filename=f"disclosure-document-{n}.pdf",
            ttl_s=rules().download_url_ttl_s,
        )
        if found is None:
            return None
        url, expires_at = found
        return PublicDocumentLinkOut(url=url, expires_at=expires_at)


# --- documents guard, export and offboarding (FR-DISC-012) -------------------------------------


def _published_document_guard(session: Session, document_id: uuid.UUID) -> str | None:
    """documents.DELETE_GUARDS: a document a disclosure published is kept (the record of what
    was published; docs/05 §16.4)."""
    return "document_published" if repo.document_is_published(session, document_id) else None


if _published_document_guard not in documents.DELETE_GUARDS:
    documents.DELETE_GUARDS.append(_published_document_guard)


def export_records(session: Session) -> list[RecordTable]:
    """The draft, every snapshot and the published document versions (full data export)."""
    return repo.export_tables(session)


_PURGE = purging.PurgeTables(
    deleted=(
        "ops.disclosure_snapshot_documents",
        "ops.disclosure_snapshots",
        "ops.disclosure_drafts",
    )
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the school's disclosure rows (offboarding only; ADR-0029). The handles go with
    ``public``, after this module."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="disclosure", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "EDIT",
    "HANDLE_KIND",
    "PUBLISH",
    "RENDER_EVENT",
    "RENDER_TASK",
    "ConfirmationRequired",
    "Incomplete",
    "ModuleHidden",
    "abandon_render",
    "download_url",
    "export_records",
    "format_out",
    "get_draft",
    "get_format",
    "get_link",
    "get_snapshot",
    "link_url",
    "list_snapshots",
    "public_document",
    "public_page",
    "publish",
    "purge_tenant_data",
    "put_draft",
    "render_files",
    "rotate_link",
    "tenant_data_counts",
    "withdraw",
]
