"""Register-photo extraction public API (US-402; FR-IMP-020..024; PRV-015, PRV-016; SEC-013).

Flow (docs/04 §7.2)::

    create_batch()      register_scan documents (JPG/PNG, scanned clean) -> batch + pages
                        (queued) -> audit -> outbox "extraction.batch.created"
    worker              process_batch(): per page, read the image -> provider.extract()
                        -> sanitize.clean_page() (Aadhaar masked, page flagged) -> items
                        (pending_review) + counters, one transaction per page
    reviewer            list/get items (page image beside each row, low-confidence fields
                        flagged) -> confirm_item() / reject_item()
    confirm_item()      values recorded through app.students.service with source
                        admission_register and the page as evidence -> audit -> outbox
                        "extraction.confirmed" {batch_id, item_id, student_id}

Rules:

- **Nothing becomes a record without a person (FR-IMP-020, US-402 AC2).** Workers only write
  ``sis.extraction_*``; ``sis.students``/``sis.attribute_values`` are written only by
  :func:`confirm_item`, with the values the reviewer sent (``import.commit``).
- **Aadhaar (FR-IMP-022, PRV-015/016, invariant 4).** Provider output is masked in memory before
  the first write or log line; the page text is never stored. A page whose text held a full
  number is ``aadhaar_detected`` and its original image is never kept (ADR-0007):
  :func:`app.extraction.imaging.redact_page` blacks the number out and checks the copy, which
  ``documents.replace_with_redacted`` stores as the page's new version (``image_redacted``; the
  page shows and cites the copy). When that is not possible the original is discarded
  (``documents.discard_version``) and the page is ``image_withheld``: no image, and its rows
  cannot be confirmed (``evidence_unavailable``).
- **Evidence (FR-IMP-023).** Every confirmed value carries ``evidence_document_id`` = the page.
- **Identity fields (BR-01, ADR-0010).** ``app.students.service`` records a first
  admission-register identity value only as ``unverified`` (verifying it needs a change
  request), so identity values from a confirmed page are unverified/provisional; non-identity
  values are recorded ``verified`` by the confirming person.
- **Pluggable providers (FR-IMP-024):** :mod:`app.extraction.providers`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, encode_cursor
from app.core.config import Settings, get_settings
from app.core.db import tenant_session
from app.core.errors import Conflict, DomainError, Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.documents import service as documents
from app.extraction import imaging, sanitize
from app.extraction import repository as repo
from app.extraction.models import ExtractionBatch, ExtractionItem, ExtractionPage
from app.extraction.providers import (
    ExtractionFailed,
    ExtractionProvider,
    ExtractionUnavailable,
    PageExtraction,
    ProviderNotConfigured,
    ProviderRefused,
    build_provider,
    provider_kind,
)
from app.extraction.schemas import (
    BatchCreate,
    BatchDetail,
    BatchOut,
    FieldOut,
    ItemConfirm,
    ItemDetail,
    ItemOut,
    ItemReject,
    PageImage,
    PageOut,
)
from app.extraction.settings import extraction_config
from app.notifications import service as notifications
from app.ops import service as ops
from app.students import service as students
from app.students.schemas import SearchFilters, StudentCreate, StudentSummary, ValueIn

log = get_logger(__name__)

RUN: Final = "import.run"
COMMIT: Final = "import.commit"
SOURCE: Final = "admission_register"

BATCH_EVENT: Final = "extraction.batch.created"
CONFIRMED_EVENT: Final = "extraction.confirmed"
PROCESS_TASK: Final = "extraction.process_batch"
ops.register_outbox_route(BATCH_EVENT, PROCESS_TASK)

READY_TEMPLATE: Final = "extraction.batch.ready"
FAILED_TEMPLATE: Final = "extraction.batch.failed"

PDF_MIME: Final = "application/pdf"
TERMINAL: Final = ("review", "completed", "failed")
# A masked Aadhaar-like number as shown to reviewers; never accepted back as a value.
MASKED_RE: Final = re.compile(r"X{4}[ \xa0-]?X{4}[ \xa0-]?\d{4}", re.IGNORECASE)

PDF_DETAIL: Final = (
    "Register photos must be JPG or PNG images for now. Photograph each page, or save each "
    "PDF page as an image, and upload those."
)


# --- plumbing -------------------------------------------------------------------------------


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    summary: Mapping[str, Any],
    *,
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=dict(summary),
        actor_type="system" if system else "user",
        request_id=None if system else _request_id(),
    )


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _batch_not_found() -> NotFound:
    return NotFound("Extraction batch not found")


def _item_not_found() -> NotFound:
    return NotFound("Extraction item not found")


def _notify(
    session: Session, batch: ExtractionBatch, template_key: str, params: Mapping[str, Any]
) -> None:
    """Tell the person who started the batch (FR-NOT-001). Both templates ship in
    app/notifications/templates.yaml (tests/notifications/test_templates.py checks every key
    the code sends). Still best effort: a template error must not lose the extraction result."""
    try:
        notifications.notify(
            session,
            tenant_id=batch.tenant_id,
            recipients=[batch.created_by_membership],
            template_key=template_key,
            params=params,
            resource_id=batch.id,
            dedupe_key=f"{template_key}:{batch.id}",
        )
    except ValueError:
        log.warning(
            "extraction.notify.skipped",
            error_code="template_unavailable",
            resource_type="extraction_batch",
            resource_id=batch.id,
        )


# --- outputs --------------------------------------------------------------------------------


def _batch_out(batch: ExtractionBatch) -> BatchOut:
    return BatchOut.model_validate(batch)


def _page_out(page: ExtractionPage) -> PageOut:
    return PageOut.model_validate(page)


def _fields_out(fields: Mapping[str, Any]) -> dict[str, FieldOut]:
    return {
        key: FieldOut(
            value=str(cell.get("value", "")),
            confidence=cell.get("confidence"),
            bbox=cell.get("bbox"),
            masked=bool(cell.get("masked", False)),
            low_confidence=bool(cell.get("low_confidence", False)),
        )
        for key, cell in fields.items()
    }


def _item_out(item: ExtractionItem) -> ItemOut:
    fields = _fields_out(item.fields)
    return ItemOut(
        id=item.id,
        batch_id=item.batch_id,
        page_id=item.page_id,
        document_id=item.document_id,
        page_no=item.page_no,
        row_index=item.row_index,
        status=item.status,
        fields=fields,
        low_confidence=item.low_confidence,
        low_confidence_fields=[k for k, f in fields.items() if f.low_confidence],
        masked=item.masked,
        reviewed_by=item.reviewed_by,
        reviewed_at=item.reviewed_at,
        reject_reason=item.reject_reason,
        student_id=item.student_id,
        value_ids=list(item.value_ids or []),
        corrected_fields=list(item.corrected_fields or []),
        created_student=item.created_student,
        version=item.version,
    )


# --- batches --------------------------------------------------------------------------------


def create_batch(session: Session, ctx: UserContext, data: BatchCreate) -> BatchOut:
    """Queue extraction for register-page photos (permission ``import.run``; FR-IMP-020).

    Each id must be a ``register_scan`` document you can see, scanned clean, JPG or PNG (PDF is
    refused until page rendering is available) and not already extracted. Audit:
    ``extraction.batch.created``; outbox ``extraction.batch.created`` -> worker (queue ocr).
    """
    cfg = extraction_config()
    ids = list(data.document_ids)
    errors: list[dict[str, str]] = []
    if len(ids) > cfg.documents_per_batch:
        raise ValidationFailed([_error("document_ids", "too_many")])
    if len(set(ids)) != len(ids):
        raise ValidationFailed([_error("document_ids", "duplicate")])
    stored: list[documents.StoredObject] = []
    pdf = False
    for i, document_id in enumerate(ids):
        field = f"document_ids.{i}"
        if not documents.is_visible(session, ctx, document_id):
            errors.append(_error(field, "not_found"))
            continue
        try:
            obj = documents.document_object(session, document_id)
        except NotFound:
            errors.append(_error(field, "not_found"))
            continue
        except Conflict:
            errors.append(_error(field, "document_not_ready"))
            continue
        if obj.purpose != "register_scan":
            errors.append(_error(field, "not_register_scan"))
        elif obj.mime_type == PDF_MIME:
            pdf = True
            errors.append(_error(field, "pdf_not_supported"))
        elif obj.mime_type not in cfg.accepted_mime_types:
            errors.append(_error(field, "unsupported_file_type"))
        else:
            stored.append(obj)
    busy = repo.documents_in_open_batches(session, [o.document_id for o in stored])
    errors.extend(
        _error(f"document_ids.{ids.index(o.document_id)}", "already_extracted")
        for o in stored
        if o.document_id in busy
    )
    if errors:
        raise ValidationFailed(errors, detail=PDF_DETAIL if pdf else None)
    kind = provider_kind(get_settings()).value
    batch_id = new_id()
    with _db_errors():
        batch = repo.insert_batch(
            session,
            {
                "id": batch_id,
                "tenant_id": repo.current_tenant_id(session),
                "provider": kind,
                "created_by": ctx.user_id,
                "created_by_membership": ctx.membership_id,
                "page_count": len(stored),
            },
        )
        repo.insert_pages(
            session,
            [
                {
                    "id": new_id(),
                    "tenant_id": batch.tenant_id,
                    "batch_id": batch_id,
                    "document_id": obj.document_id,
                    "document_version_no": obj.version_no,
                    "page_no": 1,
                    "seq": seq,
                }
                for seq, obj in enumerate(stored, start=1)
            ],
        )
    _audit(
        session,
        "extraction.batch.created",
        "extraction_batch",
        batch_id,
        {
            "document_ids": [o.document_id for o in stored],
            "page_count": len(stored),
            "provider": kind,
        },
    )
    ops.enqueue_event(session, BATCH_EVENT, {"batch_id": batch_id})
    log.info("extraction.batch.created", resource_type="extraction_batch", resource_id=batch_id)
    return _batch_out(batch)


def list_batches(
    session: Session, ctx: UserContext, *, limit: int, before_id: uuid.UUID | None
) -> Page[BatchOut]:
    """Newest first (permission ``import.run``)."""
    rows = repo.list_batches(session, before_id=before_id, limit=limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    cursor = encode_cursor({"k": str(rows[-1].id)}) if more and rows else None
    return Page[BatchOut](data=[_batch_out(b) for b in rows], next_cursor=cursor)


def get_batch(session: Session, ctx: UserContext, batch_id: uuid.UUID) -> BatchDetail:
    """A batch with progress per page (US-402 AC4)."""
    batch = repo.get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found()
    pages = [_page_out(p) for p in repo.pages_of(session, batch_id)]
    return BatchDetail(**_batch_out(batch).model_dump(), pages=pages)


# --- worker ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PageRef:
    id: uuid.UUID
    document_id: uuid.UUID
    version_no: int
    page_no: int


def _start(tenant_id: uuid.UUID, batch_id: uuid.UUID) -> tuple[str, list[_PageRef]]:
    with tenant_session(tenant_id) as s:
        batch = repo.get_batch(s, batch_id, for_update=True)
        if batch is None:
            return "missing", []
        if batch.status in TERMINAL:
            return batch.status, []
        if batch.status == "queued":
            repo.update_batch(s, batch_id, {"status": "processing"})
        pages = [
            _PageRef(p.id, p.document_id, p.document_version_no, p.page_no)
            for p in repo.pages_of(s, batch_id)
            if p.status == "queued"
        ]
    return "processing", pages


def _read_page(tenant_id: uuid.UUID, page: _PageRef) -> bytes | None:
    with tenant_session(tenant_id) as s:
        try:
            obj = documents.document_object(s, page.document_id, page.version_no)
            return documents.read_document_object(s, obj)
        except (NotFound, Conflict):
            return None


def _page_failed(tenant_id: uuid.UUID, batch_id: uuid.UUID, page_id: uuid.UUID, code: str) -> None:
    with tenant_session(tenant_id) as s:
        batch = repo.get_batch(s, batch_id, for_update=True)
        done = repo.finish_page(
            s, page_id, {"status": "failed", "error_code": code, "processed_at": func.now()}
        )
        if batch is None or done is None:
            return
        repo.update_batch(s, batch_id, {"pages_failed": batch.pages_failed + 1})
    log.warning(
        "extraction.page.failed",
        error_code=code,
        resource_type="extraction_page",
        resource_id=page_id,
    )


@dataclass(frozen=True, slots=True)
class _Redaction:
    """Outcome of :func:`_redact` for a flagged page: the copy, or why there is none."""

    image: imaging.RedactedImage | None
    cause: str | None


def _redact(
    image: bytes, result: PageExtraction, provider: ExtractionProvider, hints: tuple[str, ...]
) -> _Redaction:
    try:
        return _Redaction(imaging.redact_page(image, result, provider, language_hints=hints), None)
    except imaging.Unredactable as exc:
        return _Redaction(None, exc.code)


def _replace_with_redacted(s: Session, page: _PageRef, copy: imaging.RedactedImage) -> int | None:
    """Store the redacted copy as the page's new version; None if it cannot be used."""
    try:
        return documents.replace_with_redacted(
            s, page.document_id, page.version_no, copy.data, copy.mime_type, regions=copy.regions
        )
    except Conflict as exc:
        if exc.code == "storage_unavailable":
            # Transient: the transaction rolls back and the task retries the page.
            raise ExtractionUnavailable("storage_unavailable") from exc
        return None
    except DomainError:
        return None


def _store_page(
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    page: _PageRef,
    clean: sanitize.CleanPage,
    redaction: _Redaction | None,
) -> None:
    """Items + page outcome + counters (+ the PRV-016 image outcome) in one transaction (a
    retry never duplicates)."""
    with tenant_session(tenant_id) as s:
        batch = repo.get_batch(s, batch_id, for_update=True)
        if batch is None:
            return
        current = repo.get_page(s, page.id)
        if current is None or current.status != "queued":
            return  # finished by an earlier delivery (the batch lock serialises workers)
        detected = clean.aadhaar_detected
        copy = redaction.image if detected and redaction is not None else None
        redacted_no = _replace_with_redacted(s, page, copy) if copy is not None else None
        withheld = detected and redacted_no is None
        done = repo.finish_page(
            s,
            page.id,
            {
                "status": "done",
                "aadhaar_detected": detected,
                "image_withheld": withheld,
                "image_redacted": redacted_no is not None,
                "document_version_no": redacted_no or page.version_no,
                "row_count": len(clean.rows),
                "low_confidence_count": clean.low_confidence_rows,
                "dropped_field_count": clean.dropped_fields,
                "processed_at": func.now(),
            },
        )
        if done is None:  # pragma: no cover - checked above under the batch lock
            return
        repo.insert_items(
            s,
            [
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "batch_id": batch_id,
                    "page_id": page.id,
                    "document_id": page.document_id,
                    "page_no": page.page_no,
                    "row_index": i,
                    "fields": row.fields,
                    "low_confidence": row.low_confidence,
                    "masked": row.masked,
                }
                for i, row in enumerate(clean.rows)
            ],
        )
        n = len(clean.rows)
        repo.update_batch(
            s,
            batch_id,
            {
                "pages_done": batch.pages_done + 1,
                "pages_withheld": batch.pages_withheld + int(withheld),
                "items_total": batch.items_total + n,
                "items_pending": batch.items_pending + n,
                "items_low_confidence": batch.items_low_confidence + clean.low_confidence_rows,
            },
        )
        if not detected:
            return
        # PRV-016: the page image showed a full Aadhaar number; its original is not kept.
        if copy is not None and redacted_no is not None:
            _audit(
                s,
                "extraction.page.image_redacted",
                "extraction_page",
                page.id,
                {
                    "batch_id": batch_id,
                    "document_id": page.document_id,
                    "version_no": page.version_no,
                    "redacted_version_no": redacted_no,
                    "regions": copy.regions,
                },
                system=True,
            )
        else:
            documents.discard_version(s, page.document_id, page.version_no, "aadhaar_unredactable")
            cause = redaction.cause if redaction is not None else None
            _audit(
                s,
                "extraction.page.image_withheld",
                "extraction_page",
                page.id,
                {
                    "batch_id": batch_id,
                    "document_id": page.document_id,
                    "reason": "sensitive_number_detected",
                    "cause": cause or "not_replaceable",
                },
                system=True,
            )
    log.warning(
        "extraction.page.sensitive_number_detected",
        resource_type="document",
        resource_id=page.document_id,
        outcome="redacted" if redacted_no is not None else "discarded",
    )


def fail_batch(tenant_id: uuid.UUID, batch_id: uuid.UUID, code: str) -> None:
    """Mark the batch and its unprocessed pages failed (operator-facing ``code``)."""
    with tenant_session(tenant_id) as s:
        batch = repo.get_batch(s, batch_id, for_update=True)
        if batch is None or batch.status in ("completed", "failed"):
            return
        failed = repo.fail_queued_pages(s, batch_id, code)
        batch = repo.update_batch(
            s,
            batch_id,
            {
                "status": "failed",
                "error_code": code,
                "pages_failed": batch.pages_failed + failed,
                "processed_at": func.now(),
            },
        )
        _audit(
            s,
            "extraction.batch.failed",
            "extraction_batch",
            batch_id,
            {"error_code": code, "pages_failed": batch.pages_failed},
            system=True,
        )
        _notify(s, batch, FAILED_TEMPLATE, {"batch_id": batch_id})
    log.error(
        "extraction.batch.failed",
        error_code=code,
        resource_type="extraction_batch",
        resource_id=batch_id,
    )


def _finish(tenant_id: uuid.UUID, batch_id: uuid.UUID) -> str:
    with tenant_session(tenant_id) as s:
        batch = repo.get_batch(s, batch_id, for_update=True)
        if batch is None:
            return "missing"
        if batch.status != "processing":
            return batch.status
        if batch.pages_done + batch.pages_failed < batch.page_count:  # pragma: no cover
            return "processing"
        values: dict[str, Any] = {"processed_at": func.now()}
        if batch.pages_done == 0:
            values.update(status="failed", error_code="all_pages_failed")
        elif batch.items_pending == 0:
            values.update(status="completed", completed_at=func.now())
        else:
            values["status"] = "review"
        batch = repo.update_batch(s, batch_id, values)
        _audit(
            s,
            "extraction.batch.processed",
            "extraction_batch",
            batch_id,
            {
                "status": batch.status,
                "pages_done": batch.pages_done,
                "pages_failed": batch.pages_failed,
                "pages_withheld": batch.pages_withheld,
                "items_total": batch.items_total,
                "items_low_confidence": batch.items_low_confidence,
            },
            system=True,
        )
        if batch.status == "failed":
            _notify(s, batch, FAILED_TEMPLATE, {"batch_id": batch_id})
        else:
            _notify(
                s,
                batch,
                READY_TEMPLATE,
                {
                    "batch_id": batch_id,
                    "rows": batch.items_total,
                    "low_confidence_rows": batch.items_low_confidence,
                },
            )
        status = batch.status
    log.info(
        "extraction.batch.processed",
        resource_type="extraction_batch",
        resource_id=batch_id,
        count=batch.items_total,
        outcome=status,
    )
    return status


def process_batch(
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    *,
    provider: ExtractionProvider | None = None,
    settings: Settings | None = None,
) -> str:
    """Worker (queue ``ocr``): extract every queued page of the batch; returns its status.

    Idempotent: finished pages are skipped, so a retry after a transient provider error
    (``ExtractionUnavailable``, re-raised for the task to retry) continues where it stopped.
    A missing or refused provider fails the batch with an operator-facing code and re-raises.
    """
    settings = settings or get_settings()
    cfg = extraction_config()
    state, pages = _start(tenant_id, batch_id)
    if state != "processing":
        return state
    try:
        chosen = provider or build_provider(settings)
    except ProviderRefused as exc:
        fail_batch(tenant_id, batch_id, exc.code)
        raise
    for page in pages:
        image = _read_page(tenant_id, page)
        if image is None:
            _page_failed(tenant_id, batch_id, page.id, "document_unavailable")
            continue
        try:
            result = chosen.extract(image, language_hints=cfg.language_hints)
            # Masking happens here, in memory, before the first write (FR-IMP-022, PRV-015).
            clean = sanitize.clean_page(
                result, cfg, threshold=settings.extraction_low_confidence_threshold
            )
            # PRV-016: black the number out and read the copy again (same provider).
            redaction = (
                _redact(image, result, chosen, cfg.language_hints)
                if clean.aadhaar_detected
                else None
            )
        except (ProviderNotConfigured, ProviderRefused) as exc:
            fail_batch(tenant_id, batch_id, exc.code)
            raise
        except ExtractionFailed as exc:
            _page_failed(tenant_id, batch_id, page.id, exc.code)
            continue
        del result, image
        _store_page(tenant_id, batch_id, page, clean, redaction)
    return _finish(tenant_id, batch_id)


# --- review queue ---------------------------------------------------------------------------


def list_items(
    session: Session,
    ctx: UserContext,
    *,
    batch_id: uuid.UUID | None,
    status: str | None,
    limit: int,
    after_id: uuid.UUID | None,
) -> Page[ItemOut]:
    """The verification queue in page order (permission ``import.run``; FR-IMP-020)."""
    rows = repo.list_items(
        session, batch_id=batch_id, status=status, after_id=after_id, limit=limit + 1
    )
    more = len(rows) > limit
    rows = rows[:limit]
    cursor = encode_cursor({"k": str(rows[-1].id)}) if more and rows else None
    return Page[ItemOut](data=[_item_out(i) for i in rows], next_cursor=cursor)


def _page_image(
    session: Session, ctx: UserContext, item: ExtractionItem, page: ExtractionPage
) -> tuple[PageImage | None, str | None]:
    if page.image_withheld:
        return None, "withheld_sensitive_number"
    try:
        link = documents.get_download_url(session, ctx, item.document_id, page.document_version_no)
    except (NotFound, Forbidden):
        return None, "not_visible"
    except Conflict:
        return None, "not_ready"
    return PageImage(url=link.url, expires_at=link.expires_at, mime_type=link.mime_type), None


def _possible_matches(
    session: Session, ctx: UserContext, item: ExtractionItem
) -> list[StudentSummary]:
    cell = item.fields.get("admission_no") or {}
    value = cell.get("value")
    if item.status != "pending_review" or not isinstance(value, str) or cell.get("masked"):
        return []
    try:
        found = students.search(session, ctx, SearchFilters(admission_no=value), limit=5)
    except ValidationFailed:
        return []
    return list(found.data)


def get_item(session: Session, ctx: UserContext, item_id: uuid.UUID) -> ItemDetail:
    """One row for review with a presigned link to its page image (US-402 AC1)."""
    item = repo.get_item(session, item_id)
    if item is None:
        raise _item_not_found()
    page = repo.get_page(session, item.page_id)
    if page is None:  # pragma: no cover - composite FK
        raise _item_not_found()
    image, unavailable = _page_image(session, ctx, item, page)
    return ItemDetail(
        **_item_out(item).model_dump(),
        image=image,
        image_unavailable=unavailable,
        possible_matches=_possible_matches(session, ctx, item),
    )


def _pending_item(session: Session, item_id: uuid.UUID) -> ExtractionItem:
    item = repo.get_item(session, item_id, for_update=True)
    if item is None:
        raise _item_not_found()
    if item.status != "pending_review":
        raise Conflict("Someone already checked this row.", code="item_already_reviewed")
    return item


def _count_review(session: Session, batch_id: uuid.UUID, outcome: str) -> None:
    batch = repo.get_batch(session, batch_id, for_update=True)
    if batch is None:  # pragma: no cover - composite FK
        return
    pending = batch.items_pending - 1
    values: dict[str, Any] = {
        "items_pending": pending,
        "items_confirmed": batch.items_confirmed + int(outcome == "confirmed"),
        "items_rejected": batch.items_rejected + int(outcome == "rejected"),
    }
    if pending == 0 and batch.status == "review":
        values.update(status="completed", completed_at=func.now())
    repo.update_batch(session, batch_id, values)


def _final_values(
    item: ExtractionItem, data: ItemConfirm, allowed: Sequence[str]
) -> tuple[list[tuple[str, str]], list[str]]:
    errors: list[dict[str, str]] = []
    values: list[tuple[str, str]] = []
    for key, raw in data.fields.items():
        if key not in allowed:
            errors.append(_error(f"fields.{key}", "unknown_field"))
            continue
        if raw is None or not raw:
            continue
        if MASKED_RE.search(raw):
            errors.append(_error(f"fields.{key}", "masked_value"))
            continue
        values.append((key, raw))
    if errors:
        raise ValidationFailed(errors)
    if not values:
        raise ValidationFailed([_error("fields", "no_values")])
    extracted = {k: str(c.get("value", "")) for k, c in item.fields.items()}
    corrected = sorted(k for k, v in values if extracted.get(k) != v)
    return values, corrected


@contextmanager
def _fields_errors(mapping: Callable[[str], str | None]) -> Iterator[None]:
    """Point student validation errors at the reviewer's field (``fields.<key>``)."""
    try:
        yield
    except ValidationFailed as exc:
        remapped = []
        for err in exc.errors:
            key = mapping(err.get("field", ""))
            remapped.append({**err, "field": f"fields.{key}"} if key else err)
        raise ValidationFailed(remapped, detail=exc.detail) from exc


def _single_field(key: str) -> Callable[[str], str | None]:
    """Errors of ``students.record_value`` name ``value``/``attribute_key``: map them to ``key``."""

    def where(field: str) -> str | None:
        return key if field in ("value", "attribute_key") else None

    return where


def _create_student(
    session: Session,
    ctx: UserContext,
    data: ItemConfirm,
    values: list[tuple[str, str]],
    evidence: uuid.UUID,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """New student from the identity values of the row (recorded unverified by students)."""
    if "full_name" not in {k for k, _ in values}:
        raise ValidationFailed([_error("fields.full_name", "required")])
    keys = [k for k, _ in values]
    body = [
        ValueIn(attribute_key=k, source=SOURCE, value=v, evidence_document_id=evidence)
        for k, v in values
    ]

    def where(field: str) -> str | None:
        match = re.fullmatch(r"values\.(\d+)\.(?:value|attribute_key)", field)
        return keys[int(match.group(1))] if match and int(match.group(1)) < len(keys) else None

    with _fields_errors(where):
        student = students.create_student(
            session,
            ctx,
            StudentCreate(
                values=body,
                section_id=data.section_id,
                roll_no=data.roll_no,
                status=data.student_status,
            ),
        )
    ids = [
        v.id for k in keys for v in student.values.get(k, []) if v.source == SOURCE and v.current
    ]
    return student.id, ids


def confirm_item(
    session: Session, ctx: UserContext, item_id: uuid.UUID, data: ItemConfirm
) -> ItemOut:
    """Record the reviewer's values (permission ``import.commit``; US-402 AC2, FR-IMP-023).

    Creates the student (``student_id`` omitted) or adds the values to an existing one, with
    source ``admission_register`` and the page as evidence. Identity values are recorded
    unverified (students module rule: verifying them needs a change request); other values
    are recorded verified by you. An existing different register identity value answers 403
    ``identity_change_required``. Audit: ``extraction.item.confirmed``; outbox
    ``extraction.confirmed``.
    """
    item = _pending_item(session, item_id)
    cfg = extraction_config()
    values, corrected = _final_values(item, data, cfg.fields)
    if not documents.evidence_exists(session, item.document_id):
        raise Conflict(
            "The register photo for this row is no longer available.", code="evidence_unavailable"
        )
    identity = {a.key for a in students.attribute_catalog(session) if a.is_identity}
    evidence = item.document_id
    created = data.student_id is None
    value_ids: list[uuid.UUID] = []
    later = values
    if data.student_id is None:
        # Identity values create the student (students records them unverified: provisional
        # until a change request verifies them, BR-01); the rest follow as verified values.
        student_id, value_ids = _create_student(
            session, ctx, data, [(k, v) for k, v in values if k in identity], evidence
        )
        later = [(k, v) for k, v in values if k not in identity]
    else:
        if data.section_id is not None or data.roll_no is not None:
            raise ValidationFailed([_error("section_id", "only_for_new_student")])
        student_id = data.student_id
    for key, value in later:
        verification: students.Verification = "unverified" if key in identity else "verified"
        with _fields_errors(_single_field(key)):
            recorded = students.record_value(
                session,
                ctx,
                student_id,
                key,
                SOURCE,
                value,
                evidence_document_id=evidence,
                verification=verification,
            )
        if recorded.id not in value_ids:
            value_ids.append(recorded.id)
    with _db_errors():
        updated = repo.review_item(
            session,
            item.id,
            {
                "status": "confirmed",
                "reviewed_by": ctx.user_id,
                "reviewed_at": func.now(),
                "student_id": student_id,
                "value_ids": value_ids,
                "corrected_fields": corrected,
                "created_student": created,
            },
        )
    if updated is None:  # pragma: no cover - row locked above
        raise Conflict("Someone already checked this row.", code="item_already_reviewed")
    _count_review(session, item.batch_id, "confirmed")
    _audit(
        session,
        "extraction.item.confirmed",
        "extraction_item",
        item.id,
        {
            "batch_id": item.batch_id,
            "student_id": student_id,
            "created_student": created,
            "fields": sorted(k for k, _ in values),
            "corrected_fields": corrected,
            "value_count": len(value_ids),
            "low_confidence": item.low_confidence,
        },
    )
    ops.enqueue_event(
        session,
        CONFIRMED_EVENT,
        {"batch_id": item.batch_id, "item_id": item.id, "student_id": student_id},
    )
    log.info("extraction.item.confirmed", resource_type="extraction_item", resource_id=item.id)
    return _item_out(updated)


def reject_item(
    session: Session, ctx: UserContext, item_id: uuid.UUID, data: ItemReject
) -> ItemOut:
    """Discard a row that is not a student entry (permission ``import.commit``); nothing is
    recorded. Audit: ``extraction.item.rejected``."""
    item = _pending_item(session, item_id)
    with _db_errors():
        updated = repo.review_item(
            session,
            item.id,
            {
                "status": "rejected",
                "reviewed_by": ctx.user_id,
                "reviewed_at": func.now(),
                "reject_reason": data.reason,
            },
        )
    if updated is None:  # pragma: no cover - row locked above
        raise Conflict("Someone already checked this row.", code="item_already_reviewed")
    _count_review(session, item.batch_id, "rejected")
    _audit(
        session,
        "extraction.item.rejected",
        "extraction_item",
        item.id,
        {"batch_id": item.batch_id, "reason": data.reason},
    )
    log.info("extraction.item.rejected", resource_type="extraction_item", resource_id=item.id)
    return _item_out(updated)


__all__ = [
    "BATCH_EVENT",
    "COMMIT",
    "CONFIRMED_EVENT",
    "FAILED_TEMPLATE",
    "PROCESS_TASK",
    "READY_TEMPLATE",
    "RUN",
    "confirm_item",
    "create_batch",
    "fail_batch",
    "get_batch",
    "get_item",
    "list_batches",
    "list_items",
    "process_batch",
    "reject_item",
]
