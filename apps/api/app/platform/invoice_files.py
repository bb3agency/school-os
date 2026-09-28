"""Invoice PDFs: render, store and hand out (docs/16 §5.8; FR-PLT-016, FR-PLT-017).

- **Render** (worker, queue ``pdf``, beat task ``billing.render_invoice_pdfs`` every minute):
  every numbered invoice (issued, paid or void) without a stored PDF is built from its issued
  snapshot (:mod:`app.platform.invoice_pdf`), printed by the shared renderer
  (:mod:`app.core.pdf`, sandboxed Chromium per ADR-0025), stored in the control-plane
  bucket/prefix (:mod:`app.platform.invoice_storage`) and recorded in ``platform.invoice_pdfs``
  with the audit event ``invoice.pdf_rendered`` in the same platform transaction.
- **Idempotent per invoice**: the table's primary key is the invoice id. Each render writes its
  own object key; a render that loses the insert deletes its object and reports ``exists``. A
  stored PDF never changes (append-only table), so an invoice has exactly one document.
- **Download**: ``GET /platform/invoices/{id}/download-url`` (``platform.invoices.read``) returns a
  presigned GET of at most five minutes and records ``invoice.pdf_downloaded``.

No student data: the document shows the supplier, the school's billing entity, the invoice lines
and totals only. Logs carry IDs and codes only.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

from app.core.config import DEV_SUPPLIER_ADDRESS, get_settings
from app.core.db import platform_session
from app.core.errors import Conflict, NotFound
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.pdf import PdfRenderer, RenderError, get_renderer
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import SYSTEM, Actor, audit_platform
from app.platform.invoice_pdf import (
    build_document,
    pdf_config,
    pdf_filename,
    render_invoice_html,
)
from app.platform.invoice_storage import InvoiceStore, get_invoice_store, object_key
from app.platform.schemas import InvoicePdfDownloadOut

log = get_logger(__name__)

RenderOutcome = Literal["rendered", "exists", "not_numbered", "not_found"]


class SupplierDetailsMissing(RuntimeError):
    """Staging/prod still uses the placeholder supplier address: refuse to print a tax invoice."""


@dataclass(frozen=True, slots=True)
class SweepResult:
    rendered: int
    existing: int
    failed: int


def _supplier_address() -> str:
    settings = get_settings()
    if settings.is_production_like and settings.billing_supplier_address == DEV_SUPPLIER_ADDRESS:
        raise SupplierDetailsMissing("supplier_address_placeholder")
    return settings.billing_supplier_address


def render_invoice_pdf(
    invoice_id: uuid.UUID,
    *,
    renderer: PdfRenderer | None = None,
    store: InvoiceStore | None = None,
) -> RenderOutcome:
    """Render and store the PDF of one numbered invoice unless it already has one."""
    cfg = pdf_config()
    address = _supplier_address()
    with platform_session() as s:
        row = repo.get(s, m.invoices, invoice_id)
        if row is None:
            return "not_found"
        if repo.invoice_pdf(s, invoice_id) is not None:
            return "exists"
        if row["invoice_number"] is None:
            return "not_numbered"
        lines = [dict(r) for r in repo.invoice_lines(s, invoice_id)]
        invoice = dict(row)
    doc = build_document(invoice, lines, supplier_address=address, cfg=cfg)
    data = (renderer or get_renderer()).render(render_invoice_html(doc))
    if not data.startswith(b"%PDF-"):
        raise RenderError("pdf_render_failed")
    target = store or get_invoice_store()
    key = object_key(cfg.object_prefix, str(invoice["financial_year"]), invoice_id, new_id())
    target.put(key, data)
    try:
        with platform_session() as s:
            won = repo.insert_invoice_pdf(
                s,
                {
                    "invoice_id": invoice_id,
                    "template_version": cfg.template_version,
                    "object_key": key,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                },
            )
            if won:
                audit_platform(
                    s,
                    SYSTEM,
                    "invoice.pdf_rendered",
                    "invoice",
                    invoice_id,
                    {
                        "invoice_number": invoice["invoice_number"],
                        "template_version": cfg.template_version,
                        "size_bytes": len(data),
                    },
                    tenant_id=invoice["tenant_id"],
                )
    except Exception:
        target.delete(key)
        raise
    if not won:
        target.delete(key)
        return "exists"
    log.info(
        "platform.invoice_pdf.rendered",
        resource_type="invoice",
        resource_id=invoice_id,
        size_bytes=len(data),
    )
    return "rendered"


def render_pending(
    *,
    limit: int | None = None,
    renderer: PdfRenderer | None = None,
    store: InvoiceStore | None = None,
) -> SweepResult:
    """Render the PDFs of numbered invoices that have none yet (oldest first, one batch)."""
    batch = limit or pdf_config().render_batch
    with platform_session() as s:
        ids = repo.invoices_without_pdf(s, batch)
    rendered = existing = failed = 0
    for invoice_id in ids:
        try:
            outcome = render_invoice_pdf(invoice_id, renderer=renderer, store=store)
        except SupplierDetailsMissing:
            log.error("platform.invoice_pdf.supplier_address_missing")
            raise
        except Exception as exc:  # one bad invoice must not block the rest; retried next run
            failed += 1
            log.error(
                "platform.invoice_pdf.render_failed",
                resource_type="invoice",
                resource_id=invoice_id,
                error_type=type(exc).__name__,
            )
            continue
        if outcome == "rendered":
            rendered += 1
        elif outcome == "exists":
            existing += 1
    return SweepResult(rendered=rendered, existing=existing, failed=failed)


def download_url(
    actor: Actor, invoice_id: uuid.UUID, *, store: InvoiceStore | None = None
) -> InvoicePdfDownloadOut:
    """A presigned GET (<= 5 minutes, attachment) for the PDF of a numbered invoice; audited."""
    cfg = pdf_config()
    with platform_session() as s:
        row = repo.get(s, m.invoices, invoice_id)
        if row is None:
            raise NotFound("Invoice not found")
        if row["invoice_number"] is None:
            raise Conflict(
                "Draft invoices have no PDF. Issue the invoice first.", code="invoice_draft"
            )
        pdf = repo.invoice_pdf(s, invoice_id)
        if pdf is None:
            raise Conflict(
                "The invoice PDF is being prepared. Try again in a minute.",
                code="invoice_pdf_pending",
            )
        filename = pdf_filename(str(row["invoice_number"]))
        url, expires_at = (store or get_invoice_store()).presigned_get(
            str(pdf["object_key"]), filename=filename, expires_s=cfg.download_url_ttl_s
        )
        audit_platform(
            s,
            actor,
            "invoice.pdf_downloaded",
            "invoice",
            invoice_id,
            {
                "invoice_number": row["invoice_number"],
                "template_version": pdf["template_version"],
            },
            tenant_id=row["tenant_id"],
        )
    return InvoicePdfDownloadOut(
        url=url,
        expires_at=expires_at,
        filename=filename,
        template_version=str(pdf["template_version"]),
        size_bytes=int(pdf["size_bytes"]),
        sha256=str(pdf["sha256"]),
    )


__all__ = [
    "SupplierDetailsMissing",
    "SweepResult",
    "download_url",
    "render_invoice_pdf",
    "render_pending",
]
