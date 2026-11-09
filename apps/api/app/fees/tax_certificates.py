"""Annual tuition fee certificate for income tax (FM-06; FR-FEE-046..060; lead decision D84;
internal: other modules use ``app.fees.service``).

Issued on request only, for one student or for a class or section (never automatically). The
amount is the tuition allocations (heads whose "counts as tuition for tax" setting is on) of the
student's issued, not cancelled, receipts dated in the financial year (April-March, IST), less
the refunds of those lines; the certificate lists each receipt's number and date. Issuing freezes
the printed content with its SHA-256, takes the next serial of the school's ``FTC`` series from
the locked counter row (gap-free), audits ``fee.tax_certificate.issued`` in the same transaction
and queues the A4 PDF on queue ``pdf``.

One issued certificate per student and year: issuing again with the same receipts and amounts
returns it unchanged; with different ones the earlier certificate is superseded (it keeps its
serial and prints "SUPERSEDED") and a new one is issued. The legal reference and the "not tax
advice" wording come from ``tax_certificate.yaml`` (``verified: false``; invariant 13).

Parents read the latest issued certificate through their receipt or dues link from 1 January of
the year's last quarter (ADR-0046/0047): a read, never an issue.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.pdf import PdfRenderer, get_renderer
from app.documents import service as documents
from app.fees import _common as c
from app.fees import repository as repo
from app.fees import tax_repository as trepo
from app.fees import tax_templates
from app.fees.money import ZERO, group_indian, in_words, total
from app.fees.tax_config import fy_bounds, load_config, parent_link_year
from app.fees.tax_models import TaxCertificate
from app.fees.tax_schemas import (
    IssueOutcome,
    TaxCertificateBulkIssue,
    TaxCertificateBulkOut,
    TaxCertificateDownloadOut,
    TaxCertificateIssue,
    TaxCertificateListItem,
    TaxCertificateOut,
    TaxCertificateReceiptOut,
)
from app.identity import service as identity
from app.ops import service as ops
from app.students import service as students
from app.students.schemas import StudentSummary
from app.tenancy import service as tenancy

log = get_logger(__name__)

RENDER_EVENT: Final = "fee.tax_certificate.issued"
RENDER_TASK: Final = "fees.render_tax_certificate"
DOCUMENT_PURPOSE: Final = "fee_receipt"  # generated fee documents share the receipts' purpose
_FILENAME_RE: Final = re.compile(r"[^A-Za-z0-9._-]+")

ops.register_outbox_route(RENDER_EVENT, RENDER_TASK)


class NoTuitionPaid(Conflict):
    code = "no_tuition_paid"

    def __init__(self) -> None:
        super().__init__(
            "No tuition fee was paid by this student in that financial year, so there is "
            "nothing to certify. Check the year, or which fee heads count as tuition."
        )


class TooManyReceipts(Conflict):
    code = "too_many_receipts"

    def __init__(self) -> None:
        super().__init__(
            "This student has more tuition receipts in the year than one certificate lists. "
            "Ask SchoolOS support."
        )


class CertificatePdfNotReady(Conflict):
    code = "pdf_not_ready"

    def __init__(self) -> None:
        super().__init__("The PDF is still being made. Try again in a moment.")


# --- the basis ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Line:
    receipt_id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    paid: Decimal
    refunded: Decimal


@dataclass(frozen=True, slots=True)
class _Basis:
    lines: tuple[_Line, ...]
    paid: Decimal
    refunded: Decimal

    @property
    def amount(self) -> Decimal:
        return self.paid - self.refunded

    def digest(self, student_id: uuid.UUID, financial_year: str, heads: Sequence[str]) -> bytes:
        """What a re-issue compares (not the as-of date): the receipts, amounts and heads."""
        data = {
            "student_id": str(student_id),
            "financial_year": financial_year,
            "heads": list(heads),
            "lines": [
                [str(x.receipt_id), f"{x.paid:.2f}", f"{x.refunded:.2f}"] for x in self.lines
            ],
        }
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).digest()


def _year(session: Session, financial_year: str) -> tuple[dt.date, dt.date, dt.date]:
    """``(start, end, as_of)`` of a year that has started and is within the kept years (422
    ``financial_year`` otherwise). ``as_of`` is today while the year is in progress."""
    start_month = c.settings().numbering.financial_year_start_month
    bounds = fy_bounds(financial_year, start_month)
    if bounds is None:
        raise ValidationFailed([c.error("financial_year", "invalid")])
    start, end = bounds
    today = c.today(session)
    if start > today:
        raise ValidationFailed([c.error("financial_year", "not_started")])
    current_first_year = int(c.fy(today)[:4])
    if start.year < current_first_year - load_config().limits.max_years_back:
        raise ValidationFailed([c.error("financial_year", "too_old")])
    return start, end, min(today, end)


def _bases(
    session: Session,
    student_ids: Collection[uuid.UUID],
    head_ids: Collection[uuid.UUID],
    start: dt.date,
    as_of: dt.date,
) -> dict[uuid.UUID, _Basis]:
    found = trepo.tuition_lines(session, student_ids, head_ids, start, as_of)
    refunds = trepo.tuition_refunds(
        session, [x.receipt_id for lines in found.values() for x in lines], head_ids
    )
    out: dict[uuid.UUID, _Basis] = {}
    for student_id, lines in found.items():
        items = tuple(
            _Line(
                x.receipt_id,
                x.receipt_number,
                x.receipt_date,
                x.amount,
                min(refunds.get(x.receipt_id, ZERO), x.amount),
            )
            for x in lines
            if x.amount > 0
        )
        basis = _Basis(items, total(x.paid for x in items), total(x.refunded for x in items))
        if basis.amount > 0:
            out[student_id] = basis
    return out


# --- issuing --------------------------------------------------------------------------------------


def _school(session: Session) -> dict[str, str]:
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    return {"name": tenant.name, "address": head.address_en, "affiliation": head.affiliation}


def _content(
    session: Session,
    *,
    school: dict[str, str],
    serial_number: str,
    financial_year: str,
    start: dt.date,
    end: dt.date,
    as_of: dt.date,
    student: StudentSummary,
    heads: Sequence[str],
    basis: _Basis,
) -> dict[str, Any]:
    cfg = load_config()
    return {
        "school": school,
        "title": cfg.print.title,
        "serial_number": serial_number,
        "financial_year": financial_year,
        "period_from": start.isoformat(),
        "period_to": end.isoformat(),
        "as_of": as_of.isoformat(),
        "in_progress": as_of < end,
        "issued_on": c.today(session).isoformat(),
        "student": {
            "id": str(student.id),
            "name": student.display_name or "—",
            "admission_no": student.admission_no or "—",
            "class_section": student.class_section or "—",
        },
        "heads": list(heads),
        "receipts": [
            {
                "receipt_number": x.receipt_number,
                "receipt_date": x.receipt_date.isoformat(),
                "amount": f"{x.paid:.2f}",
                "amount_text": group_indian(x.paid),
                "refunded": f"{x.refunded:.2f}",
                "refunded_text": group_indian(x.refunded),
            }
            for x in basis.lines
        ],
        "paid": f"{basis.paid:.2f}",
        "paid_text": group_indian(basis.paid),
        "refunded": f"{basis.refunded:.2f}",
        "refunded_text": group_indian(basis.refunded),
        "amount": f"{basis.amount:.2f}",
        "amount_text": group_indian(basis.amount),
        "amount_in_words": in_words(basis.amount),
        "legal": {
            "section_reference": cfg.legal.section_reference,
            "certifies": cfg.legal.certifies,
            "disclaimer": cfg.legal.disclaimer,
            "verified": cfg.legal.verified,
        },
        "signatory_label": cfg.print.signatory_label,
        "template_version": cfg.print.template_version,
    }


def _digest(content: dict[str, Any]) -> bytes:
    data = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).digest()


def _issue(
    session: Session,
    ctx: UserContext,
    *,
    financial_year: str,
    start: dt.date,
    end: dt.date,
    as_of: dt.date,
    student: StudentSummary,
    heads: Sequence[str],
    basis: _Basis,
    existing: TaxCertificate | None,
    school: dict[str, str],
    bulk: bool,
) -> tuple[TaxCertificate, IssueOutcome]:
    """One certificate in the caller's transaction (FR-FEE-049..052)."""
    basis_sha = basis.digest(student.id, financial_year, heads)
    if existing is not None and bytes(existing.basis_sha256) == basis_sha:
        return existing, "unchanged"
    if len(basis.lines) > load_config().limits.max_receipts:
        raise TooManyReceipts()
    numbering = load_config().numbering
    # Last: the counter row stays locked until commit (gap-free, as receipts; FR-FEE-051).
    number = repo.allocate_number(session, numbering.series, financial_year)
    serial_number = numbering.render(financial_year, number)
    content = _content(
        session,
        school=school,
        serial_number=serial_number,
        financial_year=financial_year,
        start=start,
        end=end,
        as_of=as_of,
        student=student,
        heads=heads,
        basis=basis,
    )
    certificate_id = new_id()
    with c.db_errors():
        if existing is not None:
            # The partial unique index allows one issued row: supersede first (deferred FK).
            trepo.update_certificate(
                session, existing.id, {"status": "superseded", "superseded_by": certificate_id}
            )
        row = trepo.insert_certificate(
            session,
            id=certificate_id,
            tenant_id=repo.current_tenant_id(session),
            student_id=student.id,
            financial_year=financial_year,
            serial_no=number,
            serial_number=serial_number,
            as_of=as_of,
            amount=basis.amount,
            receipt_count=len(basis.lines),
            content=content,
            content_sha256=_digest(content),
            basis_sha256=basis_sha,
            issued_by=ctx.membership_id,
            issued_at=c.now(session),
        )
    summary: dict[str, Any] = {
        "serial_number": serial_number,
        "financial_year": financial_year,
        "student_id": student.id,
        "amount": c.money_text(basis.amount),
        "receipts": len(basis.lines),
        "legal_verified": load_config().legal.verified,
    }
    if existing is not None:
        summary["supersedes"] = existing.id
    if bulk:
        summary["bulk"] = True
    c.record(
        session,
        action="fee.tax_certificate.issued",
        resource_type="fee_tax_certificate",
        resource_id=row.id,
        summary=summary,
    )
    ops.enqueue_event(
        session, RENDER_EVENT, {"certificate_id": str(row.id), "user_id": str(ctx.user_id)}
    )
    log.info("fee.tax_certificate.issued", resource_type="fee_tax_certificate", resource_id=row.id)
    return row, "reissued" if existing is not None else "issued"


def _heads(session: Session) -> tuple[list[uuid.UUID], list[str]]:
    heads = trepo.tuition_heads(session)
    return [h.id for h in heads], [h.name for h in heads]


def issue(session: Session, ctx: UserContext, data: TaxCertificateIssue) -> TaxCertificateOut:
    """Issue (or return unchanged, or re-issue) the certificate of one student (``fee.collect``;
    FR-FEE-049). 404 for a student outside the caller's reach; 409 ``no_tuition_paid``,
    ``too_many_receipts``; 422 ``financial_year``. Audit ``fee.tax_certificate.issued``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    start, end, as_of = _year(session, data.financial_year)
    student = students.summaries(session, ctx, [data.student_id]).get(data.student_id)
    if student is None:
        raise NotFound("Student not found")
    existing = trepo.issued_certificates(session, [student.id], data.financial_year, lock=True).get(
        student.id
    )
    head_ids, head_names = _heads(session)
    basis = _bases(session, [student.id], head_ids, start, as_of).get(student.id)
    if basis is None:
        raise NoTuitionPaid()
    row, outcome = _issue(
        session,
        ctx,
        financial_year=data.financial_year,
        start=start,
        end=end,
        as_of=as_of,
        student=student,
        heads=head_names,
        basis=basis,
        existing=existing,
        school=_school(session),
        bulk=False,
    )
    return certificate_out(session, row, outcome=outcome)


def issue_bulk(
    session: Session, ctx: UserContext, data: TaxCertificateBulkIssue
) -> TaxCertificateBulkOut:
    """Issue the certificates of a class or section of the current year in one transaction
    (``fee.collect``; FR-FEE-050). Students without tuition paid are skipped; at most
    ``max_bulk_students`` (409 ``too_many_students``). Each certificate is audited, and the
    request once (``fee.tax_certificate.bulk_issued``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    start, end, as_of = _year(session, data.financial_year)
    ids = students.list_students_in_scope(
        session,
        ctx,
        section_ids=[data.section_id] if data.section_id else None,
        class_ids=[data.class_id] if data.class_id else None,
    )
    if len(ids) > load_config().limits.max_bulk_students:
        raise Conflict(
            "This class has more students than one request issues. Issue it section by section.",
            code="too_many_students",
        )
    people = students.summaries(session, ctx, ids)
    head_ids, head_names = _heads(session)
    bases = _bases(session, list(people), head_ids, start, as_of)
    existing = trepo.issued_certificates(session, list(bases), data.financial_year, lock=True)
    school = _school(session)
    counts: dict[str, int] = {"issued": 0, "reissued": 0, "unchanged": 0}
    out_ids: list[uuid.UUID] = []
    ordered = sorted(
        bases, key=lambda s: (people[s].class_section or "", people[s].display_name or "")
    )
    for student_id in ordered:
        row, outcome = _issue(
            session,
            ctx,
            financial_year=data.financial_year,
            start=start,
            end=end,
            as_of=as_of,
            student=people[student_id],
            heads=head_names,
            basis=bases[student_id],
            existing=existing.get(student_id),
            school=school,
            bulk=True,
        )
        counts[outcome] += 1
        out_ids.append(row.id)
    skipped = len(people) - len(bases)
    c.record(
        session,
        action="fee.tax_certificate.bulk_issued",
        resource_type="fee_tax_certificate",
        resource_id=None,
        summary={
            "financial_year": data.financial_year,
            "class_id": data.class_id,
            "section_id": data.section_id,
            "students": len(people),
            "skipped_no_tuition": skipped,
            **counts,
        },
    )
    return TaxCertificateBulkOut(
        financial_year=data.financial_year,
        students=len(people),
        skipped_no_tuition=skipped,
        certificate_ids=out_ids,
        **counts,
    )


# --- reads ----------------------------------------------------------------------------------------


def _load(session: Session, certificate_id: uuid.UUID, *, lock: bool = False) -> TaxCertificate:
    row = trepo.get_certificate(session, certificate_id, lock=lock)
    if row is None:
        raise NotFound("Certificate not found")
    return row


def certificate_out(
    session: Session, row: TaxCertificate, *, outcome: IssueOutcome | None = None
) -> TaxCertificateOut:
    content = row.content
    student = content.get("student") or {}
    names = identity.member_display_names(session, [row.issued_by])
    return TaxCertificateOut(
        id=row.id,
        serial_number=row.serial_number,
        financial_year=row.financial_year,
        as_of=row.as_of,
        in_progress=bool(content.get("in_progress")),
        student_id=row.student_id,
        student_name=str(student.get("name", "")),
        admission_no=str(student.get("admission_no", "")),
        class_section=str(student.get("class_section", "")),
        paid=Decimal(str(content.get("paid", row.amount))),
        refunded=Decimal(str(content.get("refunded", "0.00"))),
        amount=row.amount,
        amount_in_words=str(content.get("amount_in_words", "")),
        receipts=[
            TaxCertificateReceiptOut(
                receipt_number=str(r["receipt_number"]),
                receipt_date=dt.date.fromisoformat(str(r["receipt_date"])),
                amount=Decimal(str(r["amount"])),
                refunded=Decimal(str(r["refunded"])),
            )
            for r in content.get("receipts") or []
        ],
        head_names=[str(h) for h in content.get("heads") or []],
        status=row.status,
        superseded_by=row.superseded_by,
        issued_by=row.issued_by,
        issued_by_name=names.get(row.issued_by),
        issued_at=row.issued_at,
        pdf_status=row.pdf_status,
        content_sha256=bytes(row.content_sha256).hex(),
        legal_verified=bool((content.get("legal") or {}).get("verified", False)),
        outcome=outcome,
    )


def get(session: Session, ctx: UserContext, certificate_id: uuid.UUID) -> TaxCertificateOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    return certificate_out(session, _load(session, certificate_id))


def _after(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    data = decode_cursor(cursor)
    if data is None:
        return None
    try:
        return dt.datetime.fromisoformat(str(data["t"])), uuid.UUID(str(data["i"]))
    except (KeyError, ValueError) as exc:
        raise ValidationFailed([c.error("cursor", "invalid")]) from exc


def list_certificates(
    session: Session,
    ctx: UserContext,
    *,
    financial_year: str | None,
    student_id: uuid.UUID | None,
    class_id: uuid.UUID | None,
    section_id: uuid.UUID | None,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> Page[TaxCertificateListItem]:
    """Certificates newest first (``fee.collect`` or ``finance.read``); filters are codes and
    ids only (FR-FEE-055)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    wanted: list[uuid.UUID] | None = None
    if class_id is not None or section_id is not None:
        wanted = students.list_students_in_scope(
            session,
            ctx,
            section_ids=[section_id] if section_id else None,
            class_ids=[class_id] if class_id else None,
        )
    if student_id is not None:
        wanted = [student_id] if wanted is None or student_id in wanted else []
    rows = trepo.list_certificates(
        session,
        financial_year=financial_year,
        student_ids=wanted,
        status=status,
        after=_after(cursor),
        limit=limit + 1,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    items = [
        TaxCertificateListItem(
            id=r.id,
            serial_number=r.serial_number,
            financial_year=r.financial_year,
            as_of=r.as_of,
            student_id=r.student_id,
            student_name=str((r.content.get("student") or {}).get("name", "")),
            class_section=str((r.content.get("student") or {}).get("class_section", "")),
            amount=r.amount,
            receipt_count=r.receipt_count,
            status=r.status,
            issued_at=r.issued_at,
            pdf_status=r.pdf_status,
        )
        for r in rows
    ]
    last = rows[-1] if rows else None
    next_cursor = (
        encode_cursor({"t": last.issued_at.isoformat(), "i": str(last.id)})
        if more and last
        else None
    )
    return Page[TaxCertificateListItem](data=items, next_cursor=next_cursor)


def _superseding_serial(session: Session, row: TaxCertificate) -> str | None:
    if row.superseded_by is None:
        return None
    newer = trepo.get_certificate(session, row.superseded_by)
    return newer.serial_number if newer is not None else "—"


def print_page(session: Session, ctx: UserContext, certificate_id: uuid.UUID) -> str:
    """The print view (A4, no scripts); a superseded certificate is marked. Audit
    ``fee.tax_certificate.print_viewed``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    row = _load(session, certificate_id)
    page = tax_templates.render_certificate(
        row.content, superseded_by=_superseding_serial(session, row)
    )
    c.record(
        session,
        action="fee.tax_certificate.print_viewed",
        resource_type="fee_tax_certificate",
        resource_id=row.id,
        summary={"serial_number": row.serial_number, "status": row.status},
    )
    return page


def _filename(row: TaxCertificate) -> str:
    return _FILENAME_RE.sub("-", f"tuition-certificate-{row.serial_number}") + ".pdf"


def download_url(
    session: Session, ctx: UserContext, certificate_id: uuid.UUID
) -> TaxCertificateDownloadOut:
    """A presigned PDF download (at most 5 minutes) once stored; 409 ``pdf_not_ready`` before.
    A superseded certificate's PDF stays downloadable (it is a record of what was issued; the
    print view marks it). Audit ``fee.tax_certificate.downloaded``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    row = _load(session, certificate_id)
    if row.document_id is None:
        raise CertificatePdfNotReady()
    filename = _filename(row)
    url, expires_at = documents.generated_download_url(
        session, row.document_id, filename=filename, ttl_s=c.settings().receipt.download_url_ttl_s
    )
    c.record(
        session,
        action="fee.tax_certificate.downloaded",
        resource_type="fee_tax_certificate",
        resource_id=row.id,
        summary={"serial_number": row.serial_number, "document_id": row.document_id},
    )
    return TaxCertificateDownloadOut(url=url, expires_at=expires_at, filename=filename)


def retry_render(
    session: Session, ctx: UserContext, certificate_id: uuid.UUID
) -> TaxCertificateOut:
    """Queue the PDF again after it failed (``fee.collect``); a no-op otherwise."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    row = _load(session, certificate_id, lock=True)
    if row.pdf_status == "failed":
        with c.db_errors():
            row = trepo.update_certificate(
                session, row.id, {"pdf_status": "queued", "pdf_error": None}
            )
        ops.enqueue_event(
            session, RENDER_EVENT, {"certificate_id": str(row.id), "user_id": str(ctx.user_id)}
        )
        c.record(
            session,
            action="fee.tax_certificate.render_requested",
            resource_type="fee_tax_certificate",
            resource_id=row.id,
            summary={"serial_number": row.serial_number},
        )
    return certificate_out(session, row)


# --- PDF (worker, queue "pdf") --------------------------------------------------------------------


def render_pdf(
    tenant_id: uuid.UUID,
    certificate_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    renderer: PdfRenderer | None = None,
) -> str:
    """Worker: print the frozen content to PDF and store it as a generated ``fee_receipt``
    document (the receipts' ACL roles and retention). Idempotent; errors propagate for a
    retry."""
    with tenant_session(tenant_id, user_id) as session:
        row = trepo.get_certificate(session, certificate_id)
        if row is None:
            return "missing"
        if row.document_id is not None:
            return row.pdf_status
        page = tax_templates.render_certificate(row.content, for_pdf=True)
        title = f"Tuition fee certificate {row.serial_number}"
        issued_on = row.issued_at.date()
    pdf = (renderer or get_renderer()).render(page)
    with tenant_session(tenant_id, user_id) as session:
        row = trepo.get_certificate(session, certificate_id, lock=True)
        if row is None:
            return "missing"
        if row.document_id is not None:
            return row.pdf_status
        document_id = documents.store_generated_document(
            session,
            purpose=DOCUMENT_PURPOSE,
            title=title[:200],
            content=pdf,
            created_by=user_id,
            acl_roles=c.settings().receipt.document_acl_roles,
            issued_on=issued_on,
            language="en",
        )
        with c.db_errors():
            row = trepo.update_certificate(
                session,
                row.id,
                {"document_id": document_id, "pdf_status": "ready", "pdf_error": None},
            )
        c.record(
            session,
            action="fee.tax_certificate.pdf_stored",
            resource_type="fee_tax_certificate",
            resource_id=row.id,
            summary={"document_id": document_id, "size_bytes": len(pdf)},
            system=True,
        )
    log.info(
        "fee.tax_certificate.pdf_stored", resource_type="fee_tax_certificate", resource_id=row.id
    )
    return "ready"


def mark_pdf_failed(tenant_id: uuid.UUID, certificate_id: uuid.UUID, code: str) -> None:
    with tenant_session(tenant_id) as session:
        row = trepo.get_certificate(session, certificate_id, lock=True)
        if row is None or row.document_id is not None:
            return
        trepo.update_certificate(session, row.id, {"pdf_status": "failed", "pdf_error": code})
        c.record(
            session,
            action="fee.tax_certificate.pdf_failed",
            resource_type="fee_tax_certificate",
            resource_id=row.id,
            summary={"error_code": code},
            system=True,
        )


# --- parent link (ADR-0046/0047; FR-FEE-057..059) -------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParentTaxCertificate:
    """The "Tax certificate FY ..." button of a parent receipt or dues page: the year it names
    and whether the school has issued that certificate (else the page says "ask the office")."""

    financial_year: str
    issued: bool


def _parent_row(
    session: Session, student_id: uuid.UUID
) -> tuple[str | None, TaxCertificate | None]:
    """The button's year (``None`` outside the window) and its issued certificate, if any."""
    today = c.today(session)
    year = parent_link_year(today, c.settings().numbering.financial_year_start_month)
    if year is None:
        return None, None
    row = trepo.issued_certificates(session, [student_id], year, lock=False).get(student_id)
    return year, row


def parent_tax_certificate(session: Session, student_id: uuid.UUID) -> ParentTaxCertificate | None:
    """``None`` while fees are off or outside the button's window (the page shows no button).
    The caller reached the student through a live parent link and checked the guardian."""
    if not c.fees_on(session, repo.current_tenant_id(session)):
        return None
    year, row = _parent_row(session, student_id)
    if year is None:
        return None
    return ParentTaxCertificate(financial_year=year, issued=row is not None)


PDF_PREPARING: Final = "preparing"


def parent_tax_certificate_pdf(
    session: Session, student_id: uuid.UUID
) -> tuple[str, dt.datetime] | str | None:
    """The presigned PDF of the issued certificate of the button's year, ``"preparing"`` while
    it is being made, else ``None``. Audit ``fee.tax_certificate.downloaded`` (system, ``via:
    parent_link``)."""
    if not c.fees_on(session, repo.current_tenant_id(session)):
        return None
    _, row = _parent_row(session, student_id)
    if row is None:
        return None
    if row.document_id is None:
        return PDF_PREPARING if row.pdf_status == "queued" else None
    try:
        url, expires_at = documents.generated_download_url(
            session,
            row.document_id,
            filename=_filename(row),
            ttl_s=c.settings().receipt.download_url_ttl_s,
        )
    except (Conflict, NotFound):  # not scanned yet: the page says "being prepared"
        return PDF_PREPARING
    c.record(
        session,
        action="fee.tax_certificate.downloaded",
        resource_type="fee_tax_certificate",
        resource_id=row.id,
        summary={"document_id": row.document_id, "via": "parent_link"},
        system=True,
    )
    return url, expires_at


__all__ = [
    "PDF_PREPARING",
    "RENDER_EVENT",
    "RENDER_TASK",
    "CertificatePdfNotReady",
    "NoTuitionPaid",
    "ParentTaxCertificate",
    "TooManyReceipts",
    "certificate_out",
    "download_url",
    "get",
    "issue",
    "issue_bulk",
    "list_certificates",
    "mark_pdf_failed",
    "parent_tax_certificate",
    "parent_tax_certificate_pdf",
    "print_page",
    "render_pdf",
    "retry_render",
]
