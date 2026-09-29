"""Offboarding after the two-person approval: export gate, deletion job, crypto-shredding,
certificate of deletion (FR-PLT-005, US-1303 AC3; docs/16 §5.5; ADR-0029).

One ``platform.offboarding_runs`` row per school, created in the approval transaction::

    awaiting_export --(operator confirms the export)--> scheduled
    shared:    scheduled --(inventory)--> deleting --(purge, verify, destroy keys)--> keys_destroyed
    dedicated: scheduled --(operator confirms KMS key deletion + host teardown)--> keys_destroyed
    keys_destroyed --(certificate on the pdf queue)--> completed  (school status ``deleted``)

- The school side runs through ``app.tenancy.service`` (lifecycle "offboard", ADR-0020): the
  control plane never opens a ``tenant_session`` and never sees a row, only counts and codes.
- Every step is idempotent. A runner holds the run's lease (``offboarding.lease_seconds``); a
  crashed runner's lease expires and the next beat run resumes from the recorded state. A step
  that raises records ``failed_step`` and an error code (never a message), releases the lease and
  is retried by the next beat run; keys are never destroyed while verification finds data.
- Deadline: ``deadline_at`` = approval + 30 days. ``platform.offboarding.due_soon`` (warning) and
  ``platform.offboarding.overdue`` (error, alert; audit ``tenant.deletion_overdue`` once) are
  logged by :func:`check_deadlines` (docs/16 §17).
- The certificate (content hash + PDF under the control-plane prefix) is stored in the same
  platform transaction that sets the school ``deleted``; its school-chain copy is queued.
- A year after completion the retained school audit chain is deleted (:func:`purge_expired_audit`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import RowMapping, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.db import platform_session
from app.core.errors import Conflict, DomainError, NotFound
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.pdf import PdfRenderer, RenderError, get_renderer
from app.platform import models as m
from app.platform import repository as repo
from app.platform import tenant_audit
from app.platform.common import SYSTEM, Actor, audit_platform, db_errors, must, now
from app.platform.deletion_certificate import (
    OffboardingConfig,
    build_content,
    content_sha256,
    offboarding_config,
    pdf_filename,
    render_html,
)
from app.platform.invoice_storage import InvoiceStore, get_invoice_store
from app.platform.schemas import (
    CertificateDownloadOut,
    DeletionCertificateOut,
    OffboardingOut,
)
from app.tenancy import service as tenancy

log = get_logger(__name__)

R = m.offboarding_runs
C = m.deletion_certificates


@dataclass(frozen=True, slots=True)
class SweepResult:
    advanced: int
    failed: int
    skipped: int


# --- run lifecycle ------------------------------------------------------------------------------


def create_run(s: Session, dep: RowMapping, approved_at: dt.datetime) -> None:
    """In the approval transaction: the run starts ``awaiting_export`` with its deadline."""
    cfg = offboarding_config()
    repo.insert_row(
        s,
        R,
        {
            "id": new_id(),
            "tenant_id": dep["tenant_id"],
            "tier": dep["mode"],
            "state": "awaiting_export",
            "approved_at": approved_at,
            "deadline_at": approved_at + dt.timedelta(days=cfg.deletion_deadline_days),
            "attempts": 0,
        },
    )


def _run(s: Session, tenant_id: uuid.UUID, *, for_update: bool = False) -> RowMapping | None:
    return repo.get_by(s, R, R.c.tenant_id == tenant_id, for_update=for_update)


def _certificate(s: Session, tenant_id: uuid.UUID) -> RowMapping | None:
    return repo.get_by(s, C, C.c.tenant_id == tenant_id)


def view(
    run: RowMapping | None, cert: RowMapping | None, *, at: dt.datetime
) -> OffboardingOut | None:
    if run is None:
        return None
    cfg = offboarding_config()
    open_ = run["state"] != "completed"
    leased = run["lease_expires_at"] is not None and run["lease_expires_at"] > at
    return OffboardingOut.model_validate(
        {
            **dict(run),
            "overdue": open_ and at > run["deadline_at"],
            "due_soon": open_ and at > run["deadline_at"] - dt.timedelta(days=cfg.due_soon_days),
            "in_progress": leased,
            "certificate": DeletionCertificateOut.model_validate(dict(cert)) if cert else None,
        }
    )


def get_offboarding(tenant_id: uuid.UUID) -> OffboardingOut:
    with platform_session() as s:
        if repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id) is None:
            raise NotFound("School not found")
        run = _run(s, tenant_id)
        if run is None:
            raise Conflict("This school is not being offboarded.", code="not_offboarding")
        out = view(run, _certificate(s, tenant_id), at=now())
    return must(out)


def confirm_export(
    actor: Actor, tenant_id: uuid.UUID, basis: str, reference: str
) -> OffboardingOut:
    """The export gate (docs/16 §5.5, runbook R8): deletion never starts before this."""
    with platform_session() as s, db_errors():
        run = _run(s, tenant_id, for_update=True)
        if run is None:
            raise NotFound("School not found")
        if run["state"] != "awaiting_export":
            raise Conflict("The export was already confirmed.", code="export_already_confirmed")
        repo.update_row(
            s,
            R,
            run["id"],
            {
                "state": "scheduled",
                "export_basis": basis,
                "export_reference": reference,
                "export_confirmed_by": actor.operator_id,
                "export_confirmed_at": now(),
                "updated_at": now(),
            },
        )
        summary = {"basis": basis, "tier": run["tier"]}
        audit_platform(
            s, actor, "tenant.export_confirmed", "tenant", tenant_id, summary, tenant_id=tenant_id
        )
        if run["tier"] == "shared":
            tenant_audit.enqueue(s, tenant_id, actor, "tenant.export_confirmed", summary)
    if run["tier"] == "shared":
        tenant_audit.deliver_now(tenant_id)
    return get_offboarding(tenant_id)


def confirm_teardown(
    actor: Actor, tenant_id: uuid.UUID, kms_reference: str, host_reference: str
) -> OffboardingOut:
    """Dedicated tier: operators destroyed the host and scheduled its KMS key for deletion
    (Terraform; no AWS call from the app). Crypto-shreds the host's data and backups."""
    with platform_session() as s, db_errors():
        run = _run(s, tenant_id, for_update=True)
        if run is None:
            raise NotFound("School not found")
        if run["tier"] != "dedicated":
            raise Conflict("Only a dedicated host is torn down.", code="not_dedicated")
        if run["state"] != "scheduled":
            raise Conflict(
                "Confirm the export first; the teardown can be confirmed once.",
                code="invalid_state",
            )
        at = now()
        repo.update_row(
            s,
            R,
            run["id"],
            {
                "state": "keys_destroyed",
                "kms_deletion_reference": kms_reference,
                "host_teardown_reference": host_reference,
                "teardown_confirmed_by": actor.operator_id,
                "teardown_confirmed_at": at,
                "deletion_started_at": at,
                "data_deleted_at": at,
                "keys_destroyed_at": at,
                "updated_at": at,
            },
        )
        audit_platform(
            s, actor, "tenant.teardown_confirmed", "tenant", tenant_id, {}, tenant_id=tenant_id
        )
    return get_offboarding(tenant_id)


# --- the deletion job (shared tier) --------------------------------------------------------------


def _claim(tenant_id: uuid.UUID, lease: uuid.UUID, cfg: OffboardingConfig) -> RowMapping | None:
    with platform_session() as s:
        return (
            s.execute(
                update(R)
                .where(
                    R.c.tenant_id == tenant_id,
                    R.c.tier == "shared",
                    R.c.state.in_(("scheduled", "deleting")),
                    or_(R.c.lease_expires_at.is_(None), R.c.lease_expires_at < func.now()),
                )
                .values(
                    lease_id=lease,
                    lease_expires_at=func.now() + dt.timedelta(seconds=cfg.lease_seconds),
                    attempts=R.c.attempts + 1,
                    updated_at=func.now(),
                )
                .returning(R)
            )
            .mappings()
            .first()
        )


class LeaseLost(RuntimeError):
    """Another runner took over the run (our lease expired); stop without changing it."""


def _fenced(
    run_id: uuid.UUID,
    lease: uuid.UUID,
    values: dict[str, Any],
    audit: Callable[[Session], None] | None = None,
) -> RowMapping:
    """Change the run only while ``lease`` still holds it, plus its audit event, atomically."""
    with platform_session() as s:
        row = (
            s.execute(
                update(R)
                .where(R.c.id == run_id, R.c.lease_id == lease)
                .values(**values, updated_at=func.now())
                .returning(R)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LeaseLost("lease_lost")
        if audit is not None:
            audit(s)
    return row


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, DomainError) and getattr(exc, "code", None):
        return str(exc.code)[:64]
    return "unexpected_error"


def advance(tenant_id: uuid.UUID) -> str:
    """Run the remaining shared-tier steps of one school under its lease; return the state the
    run was left in (``skipped`` if another runner holds it or nothing is due)."""
    cfg = offboarding_config()
    lease = new_id()
    run = _claim(tenant_id, lease, cfg)
    if run is None:
        return "skipped"
    step = "inventory"
    try:
        if run["state"] == "scheduled":
            inv = tenancy.tenant_data_inventory(tenant_id)
            run = _fenced(
                run["id"],
                lease,
                {
                    "state": "deleting",
                    "inventory": inv.rows,
                    "objects_before": inv.objects,
                    "deletion_started_at": now(),
                },
                lambda s: audit_platform(
                    s,
                    SYSTEM,
                    "tenant.deletion_started",
                    "tenant",
                    tenant_id,
                    {"rows": sum(inv.rows.values()), "objects": inv.objects},
                    tenant_id=tenant_id,
                ),
            )
        step = "purge"
        purged = tenancy.purge_tenant(tenant_id)
        first = run["deleted"] is None
        if first or any(purged.rows.values()) or purged.objects_deleted:
            run = _fenced(
                run["id"],
                lease,
                {
                    "deleted": purged.rows if first else run["deleted"],
                    "objects_deleted": (run["objects_deleted"] or 0) + purged.objects_deleted,
                    "profiles_cleared": (run["profiles_cleared"] or 0) + purged.profiles_cleared,
                },
            )
        step = "verify"
        remaining = tenancy.verify_tenant_purged(tenant_id)
        if remaining.rows or remaining.objects:
            _fenced(run["id"], lease, {"remaining": {**remaining.rows, "files": remaining.objects}})
            raise Conflict("School data remains after the purge.", code="data_remaining")
        run = _fenced(
            run["id"],
            lease,
            {"remaining": {}, "data_deleted_at": run["data_deleted_at"] or now()},
            None
            if run["data_deleted_at"]
            else lambda s: audit_platform(
                s,
                SYSTEM,
                "tenant.data_deleted",
                "tenant",
                tenant_id,
                {"rows": sum((run["inventory"] or {}).values()), "objects": run["objects_before"]},
                tenant_id=tenant_id,
            ),
        )
        step = "keys"
        keys = tenancy.destroy_tenant_keys(tenant_id)
        _fenced(
            run["id"],
            lease,
            {
                "state": "keys_destroyed",
                "keys_destroyed": (run["keys_destroyed"] or 0) + keys.count,
                "keys_destroyed_at": now(),
                "failed_step": None,
                "last_error": None,
                "lease_id": None,
                "lease_expires_at": None,
            },
            lambda s: audit_platform(
                s,
                SYSTEM,
                "tenant.keys_destroyed",
                "tenant",
                tenant_id,
                {"key_versions": keys.key_versions},
                tenant_id=tenant_id,
            ),
        )
    except LeaseLost:
        log.warning("platform.offboarding.lease_lost", tenant_id=str(tenant_id), step=step)
        return "lease_lost"
    except Exception as exc:  # recorded as a code; the next beat run retries (fail closed)
        _mark_failed(run, lease, step, exc)
        raise
    log.info("platform.offboarding.keys_destroyed", tenant_id=str(tenant_id))
    return "keys_destroyed"


def _mark_failed(run: RowMapping, lease: uuid.UUID, step: str, exc: BaseException) -> None:
    code = _error_code(exc)
    tenant_id = run["tenant_id"]
    log.error(
        "platform.offboarding.step_failed",
        tenant_id=str(tenant_id),
        step=step,
        error_code=code,
        error_type=type(exc).__name__,
    )
    try:
        _fenced(
            run["id"],
            lease,
            {"failed_step": step, "last_error": code, "lease_id": None, "lease_expires_at": None},
            lambda s: audit_platform(
                s,
                SYSTEM,
                "tenant.deletion_failed",
                "tenant",
                tenant_id,
                {"step": step, "error_code": code, "attempt": run["attempts"]},
                tenant_id=tenant_id,
            ),
        )
    except Exception:  # the lease still expires; the run stays resumable
        log.error("platform.offboarding.failure_not_recorded", tenant_id=str(tenant_id))


def _due(states: tuple[str, ...], *, tier: str | None = None) -> list[uuid.UUID]:
    with platform_session() as s:
        stmt = select(R.c.tenant_id).where(R.c.state.in_(states)).order_by(R.c.approved_at)
        if tier is not None:
            stmt = stmt.where(R.c.tier == tier)
        return list(s.execute(stmt).scalars())


def process_due() -> SweepResult:
    """Beat (queue ``maintenance``): advance every scheduled or unfinished shared-tier run."""
    advanced = failed = skipped = 0
    for tenant_id in _due(("scheduled", "deleting"), tier="shared"):
        try:
            outcome = advance(tenant_id)
        except Exception:
            failed += 1
            continue
        if outcome == "keys_destroyed":
            advanced += 1
        else:
            skipped += 1
    return SweepResult(advanced=advanced, failed=failed, skipped=skipped)


# --- certificate of deletion ---------------------------------------------------------------------


def issue_certificate(
    tenant_id: uuid.UUID,
    *,
    renderer: PdfRenderer | None = None,
    store: InvoiceStore | None = None,
) -> str:
    """Render, store and record the certificate of a ``keys_destroyed`` run; the same platform
    transaction sets the school ``deleted``. Idempotent (``exists`` for a second attempt)."""
    cfg = offboarding_config()
    with platform_session() as s:
        run = _run(s, tenant_id)
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id)
        if run is None or dep is None:
            return "not_found"
        if _certificate(s, tenant_id) is not None:
            return "exists"
        if run["state"] != "keys_destroyed":
            return "not_ready"
    cert_id = new_id()
    issued_at = now()
    content = build_content(
        dict(run), dict(dep), certificate_id=cert_id, issued_at=issued_at, cfg=cfg
    )
    digest = content_sha256(content)
    data = (renderer or get_renderer()).render(render_html(content, digest, cfg.certificate))
    if not data.startswith(b"%PDF-"):
        raise RenderError("pdf_render_failed")
    target = store or get_invoice_store()
    key = f"{cfg.certificate.object_prefix}{tenant_id}/{cert_id}.pdf"
    target.put(key, data)
    shared = run["tier"] == "shared"
    try:
        with platform_session() as s, db_errors():
            locked = _run(s, tenant_id, for_update=True)
            if locked is None or locked["state"] != "keys_destroyed":
                target.delete(key)
                return "not_ready"
            repo.insert_row(
                s,
                C,
                {
                    "id": cert_id,
                    "tenant_id": tenant_id,
                    "template_version": cfg.certificate.template_version,
                    "content": content,
                    "content_sha256": digest,
                    "object_key": key,
                    "pdf_sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "issued_at": issued_at,
                },
            )
            repo.update_row(
                s,
                R,
                locked["id"],
                {
                    "state": "completed",
                    "completed_at": issued_at,
                    "audit_delete_after": (
                        issued_at + dt.timedelta(days=cfg.audit_chain_retention_days)
                        if shared
                        else None
                    ),
                    "failed_step": None,
                    "last_error": None,
                    "updated_at": issued_at,
                },
            )
            _mark_deleted(s, dep, str(cert_id), shared=shared)
            audit_platform(
                s,
                SYSTEM,
                "tenant.deletion_certified",
                "tenant",
                tenant_id,
                {"certificate_id": str(cert_id)},
                tenant_id=tenant_id,
            )
    except Exception:
        target.delete(key)
        raise
    if shared:
        tenant_audit.deliver_now(tenant_id)
    log.info("platform.offboarding.certified", tenant_id=str(tenant_id))
    return "issued"


def _mark_deleted(s: Session, dep: RowMapping, certificate_ref: str, *, shared: bool) -> None:
    tenant_id = dep["tenant_id"]
    if shared:
        tenancy.set_tenant_status(s, tenant_id, "deleted")
    repo.update_row(
        s,
        m.deployments,
        dep["id"],
        {
            "tenant_status": "deleted",
            "tenant_status_reason": "offboarding",
            "deletion_certificate_ref": certificate_ref,
        },
    )
    summary = {"from": "offboarding", "to": "deleted", "tier": dep["mode"]}
    audit_platform(s, SYSTEM, "tenant.deleted", "tenant", tenant_id, summary, tenant_id=tenant_id)
    if shared:
        tenant_audit.enqueue(
            s, tenant_id, SYSTEM, "tenant.deleted", {"from": "offboarding", "to": "deleted"}
        )


def certify_pending(
    *, renderer: PdfRenderer | None = None, store: InvoiceStore | None = None
) -> SweepResult:
    """Beat (queue ``pdf``): issue the certificates of every ``keys_destroyed`` run."""
    advanced = failed = skipped = 0
    for tenant_id in _due(("keys_destroyed",)):
        try:
            outcome = issue_certificate(tenant_id, renderer=renderer, store=store)
        except Exception as exc:
            failed += 1
            _record_certificate_failure(tenant_id, exc)
            continue
        if outcome == "issued":
            advanced += 1
        else:
            skipped += 1
    return SweepResult(advanced=advanced, failed=failed, skipped=skipped)


def _record_certificate_failure(tenant_id: uuid.UUID, exc: BaseException) -> None:
    code = _error_code(exc)
    log.error(
        "platform.offboarding.step_failed",
        tenant_id=str(tenant_id),
        step="certificate",
        error_code=code,
        error_type=type(exc).__name__,
    )
    with platform_session() as s:
        s.execute(
            update(R)
            .where(R.c.tenant_id == tenant_id, R.c.state == "keys_destroyed")
            .values(failed_step="certificate", last_error=code, updated_at=func.now())
        )


def download_url(
    actor: Actor, tenant_id: uuid.UUID, *, store: InvoiceStore | None = None
) -> CertificateDownloadOut:
    """A presigned GET (<= 5 minutes, attachment) for the certificate; audited."""
    cfg = offboarding_config().certificate
    with platform_session() as s:
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id)
        if dep is None:
            raise NotFound("School not found")
        cert = _certificate(s, tenant_id)
        if cert is None:
            raise Conflict(
                "The certificate of deletion has not been issued yet.",
                code="certificate_pending",
            )
        filename = pdf_filename(str(dep["tenant_code"]))
        url, expires_at = (store or get_invoice_store()).presigned_get(
            str(cert["object_key"]), filename=filename, expires_s=cfg.download_url_ttl_s
        )
        audit_platform(
            s,
            actor,
            "tenant.deletion_certificate_downloaded",
            "tenant",
            tenant_id,
            {"certificate_id": str(cert["id"])},
            tenant_id=tenant_id,
        )
    return CertificateDownloadOut(
        url=url,
        expires_at=expires_at,
        filename=filename,
        size_bytes=int(cert["size_bytes"]),
        sha256=str(cert["pdf_sha256"]),
    )


# --- deadline and retention ----------------------------------------------------------------------


def check_deadlines(at: dt.datetime | None = None) -> dict[str, int]:
    """Log ``platform.offboarding.due_soon`` / ``.overdue`` (alerts, docs/16 §17) once per run;
    the overdue one is also audited (``tenant.deletion_overdue``)."""
    cfg = offboarding_config()
    at = at or now()
    due_soon = overdue = 0
    with platform_session() as s:
        runs = list(
            s.execute(
                select(R).where(R.c.state != "completed").with_for_update(skip_locked=True)
            ).mappings()
        )
        for run in runs:
            tenant_id = run["tenant_id"]
            if at > run["deadline_at"] and run["overdue_alerted_at"] is None:
                overdue += 1
                log.error(
                    "platform.offboarding.overdue",
                    tenant_id=str(tenant_id),
                    state=run["state"],
                    alert="offboarding_overdue",
                )
                repo.update_row(s, R, run["id"], {"overdue_alerted_at": at})
                audit_platform(
                    s,
                    SYSTEM,
                    "tenant.deletion_overdue",
                    "tenant",
                    tenant_id,
                    {"state": run["state"]},
                    tenant_id=tenant_id,
                )
            elif (
                at > run["deadline_at"] - dt.timedelta(days=cfg.due_soon_days)
                and run["due_soon_alerted_at"] is None
                and at <= run["deadline_at"]
            ):
                due_soon += 1
                log.warning(
                    "platform.offboarding.due_soon",
                    tenant_id=str(tenant_id),
                    state=run["state"],
                    alert="offboarding_due_soon",
                )
                repo.update_row(s, R, run["id"], {"due_soon_alerted_at": at})
    return {"due_soon": due_soon, "overdue": overdue}


def purge_expired_audit(at: dt.datetime | None = None) -> int:
    """Delete the retained school audit chains whose retention ended (ADR-0029 decision 2)."""
    at = at or now()
    with platform_session() as s:
        due = list(
            s.execute(
                select(R.c.id, R.c.tenant_id).where(
                    R.c.state == "completed",
                    R.c.tier == "shared",
                    R.c.audit_deleted_at.is_(None),
                    R.c.audit_delete_after <= at,
                )
            ).mappings()
        )
    deleted_chains = 0
    for row in due:
        tenant_id = row["tenant_id"]
        try:
            events = tenancy.purge_expired_audit_chain(tenant_id)
        except Exception as exc:
            log.error(
                "platform.offboarding.step_failed",
                tenant_id=str(tenant_id),
                step="audit",
                error_type=type(exc).__name__,
            )
            continue
        with platform_session() as s:
            repo.update_row(
                s, R, row["id"], {"audit_deleted_at": at, "audit_events_deleted": events}
            )
            audit_platform(
                s,
                SYSTEM,
                "tenant.audit_chain_deleted",
                "tenant",
                tenant_id,
                {"events": events},
                tenant_id=tenant_id,
            )
        deleted_chains += 1
    return deleted_chains


__all__ = [
    "SweepResult",
    "advance",
    "certify_pending",
    "check_deadlines",
    "confirm_export",
    "confirm_teardown",
    "create_run",
    "download_url",
    "get_offboarding",
    "issue_certificate",
    "process_due",
    "purge_expired_audit",
    "view",
]
