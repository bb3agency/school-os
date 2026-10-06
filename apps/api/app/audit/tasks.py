"""Celery tasks for the audit module (FR-AUD-004, SEC-007).

Scheduled by beat (apps/worker/sos_worker/celery_app.py):
- ``audit.archive_daily``      20:30 UTC (02:00 IST): signed export of the previous UTC day.
- ``audit.verify_all_chains``  20:45 UTC: verify every tenant chain (+ the platform chain on the
  shared deployment) and check partition runway. Each school's result is stored
  (``audit.chain_verifications``) and served by ``GET /audit/verify`` (R-19).

Outbox consumer: ``audit.verify_chain`` runs an on-demand verification queued by
``POST /audit/verify`` (from the stored checkpoint, or the whole chain when asked).

Both are idempotent: the archive is keyed by (tenant, day) and skips identical objects;
verification is read-only. They carry no personal data (dates and IDs only).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
from celery import shared_task

from app.audit import verification
from app.audit.archive import (
    ArchiveIncompleteError,
    export_all,
)
from app.audit.signing import KmsSigner, LocalDevSigner, Signer, SignerRefused
from app.audit.verify_all import (
    check_partition_runway,
    list_tenant_ids,
    verify_all,
    verify_platform,
)
from app.core.config import DeploymentMode, KeyWrapperKind, Settings, get_settings


def _tenant_ids() -> list[Any]:
    return list(list_tenant_ids())


def build_signer(settings: Settings) -> Signer:
    """KMS in staging/production; the local-dev Ed25519 key elsewhere."""
    if settings.key_wrapper is KeyWrapperKind.KMS:
        arn = settings.audit_signing_key_arn
        if not arn:
            raise SignerRefused("SOS_AUDIT_SIGNING_KEY_ARN is not configured")
        return KmsSigner(arn, region=settings.aws_region)
    return LocalDevSigner(settings)


def build_s3_client(settings: Settings) -> Any:
    return boto3.client(
        "s3", endpoint_url=settings.s3_endpoint_url, region_name=settings.aws_region
    )


def idempotency_key(task: str, day: date) -> str:
    return f"{task}:{day.isoformat()}"


@shared_task(
    name="audit.archive_daily",
    acks_late=True,
    autoretry_for=(ArchiveIncompleteError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def archive_daily(day: str | None = None) -> dict[str, Any]:
    """Archive one UTC day (default: yesterday) for every school that can hold a chain."""
    settings = get_settings()
    target = date.fromisoformat(day) if day else datetime.now(UTC).date() - timedelta(days=1)
    results = export_all(
        _tenant_ids(),
        target,
        s3=build_s3_client(settings),
        signer=build_signer(settings),
        bucket=settings.s3_bucket_audit,
        object_lock_retention_days=(
            settings.audit_archive_retention_days if settings.is_production_like else None
        ),
        statement_timeout_ms=settings.worker_statement_timeout_ms,
    )
    return {
        "idempotency_key": idempotency_key("audit.archive_daily", target),
        "tenants": len(results),
        "written": sum(1 for r in results if r.status == "written"),
        "events": sum(r.event_count for r in results),
    }


@shared_task(name="audit.verify_all_chains", acks_late=True)
def verify_all_chains() -> dict[str, Any]:
    """Verify every chain; broken chains are logged as P1 (``audit.chain.broken``)."""
    settings = get_settings()
    results = verify_all(_tenant_ids(), statement_timeout_ms=settings.worker_statement_timeout_ms)
    platform_ok: bool | None = None
    if settings.deployment_mode is DeploymentMode.SHARED:
        platform_ok = verify_platform(statement_timeout_ms=settings.worker_statement_timeout_ms).ok
    bound = check_partition_runway()
    return {
        "idempotency_key": idempotency_key("audit.verify_all_chains", datetime.now(UTC).date()),
        "tenants": len(results),
        "broken": sorted(str(t) for t, r in results.items() if not r.ok),
        "platform_ok": platform_ok,
        "partitions_until": bound.isoformat() if bound else None,
    }


@shared_task(name=verification.VERIFY_TASK, acks_late=True, ignore_result=True)
def verify_chain(tenant_id: str, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One queued on-demand verification (R-19). IDs, counts and codes only."""
    del event_id, payload  # the stored request says full or incremental
    out = verification.run_requested(uuid.UUID(tenant_id))
    return {"ok": out.ok, "checked": out.checked, "mode": out.mode}
