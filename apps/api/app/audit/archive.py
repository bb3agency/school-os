"""Signed daily audit archive to S3 (FR-AUD-004, SEC-007, docs/05 §13).

For each tenant and UTC day the job writes

    s3://<bucket>/t/<tenant_id>/yyyy/mm/dd/audit-<yyyy-mm-dd>.jsonl.gz       (events)
    s3://<bucket>/t/<tenant_id>/yyyy/mm/dd/audit-<yyyy-mm-dd>.jsonl.gz.sig   (signed manifest)

Each JSONL line is the RFC 8785 canonical JSON of one event (the hashed fields plus
``prev_hash``/``hash`` in hex), in ``seq`` order; gzip uses ``mtime=0`` so the bytes are
deterministic. The ``.sig`` file is canonical JSON ``{"manifest": {...}, "signature": b64}``;
the manifest pins the object's SHA-256, the seq range and the first ``prev_hash``/last ``hash``
so consecutive days link into one verifiable chain outside the database. With Object Lock
(COMPLIANCE) even an administrator cannot rewrite history after the fact.

Idempotent: an existing object with the same SHA-256 is left alone; a different one raises
``ArchiveConflictError`` (it should never happen and is alerted on).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from botocore.exceptions import ClientError
from sqlalchemy import Engine

from app.audit import repository
from app.audit.hashing import canonical_bytes, chain_hash, tenant_event_dict
from app.audit.schemas import JsonValue
from app.audit.signing import Signer, verify_signature
from app.core.db import tenant_session

logger = logging.getLogger("app.audit.archive")

ARCHIVE_FORMAT = "schoolos.audit.archive.v1"
# docs/05 §13: audit archive kept 3 years under Object Lock.
DEFAULT_RETENTION_DAYS = 3 * 365 + 1


class ArchiveConflictError(RuntimeError):
    """An archive object already exists with different content."""


class ArchiveChainError(RuntimeError):
    """An event in the day's batch does not match its own hash; refusing to archive it."""


class ArchiveIncompleteError(RuntimeError):
    """At least one tenant failed; the task is retried (exports are idempotent)."""


@dataclass(frozen=True)
class ArchiveResult:
    tenant_id: uuid.UUID
    day: date
    key: str | None
    sig_key: str | None
    event_count: int
    sha256: str | None
    status: Literal["written", "skipped", "empty"]


def object_key(tenant_id: uuid.UUID, day: date) -> str:
    return f"t/{tenant_id}/{day:%Y/%m/%d}/audit-{day.isoformat()}.jsonl.gz"


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=UTC)
    return start, start + timedelta(days=1)


def build_archive(rows: list[dict[str, Any]]) -> bytes:
    """Deterministic gzip JSONL of the given events (already in ``seq`` order)."""
    lines = []
    for row in rows:
        record: dict[str, JsonValue] = {
            **tenant_event_dict(row),
            "prev_hash": bytes(row["prev_hash"]).hex(),
            "hash": bytes(row["hash"]).hex(),
        }
        lines.append(canonical_bytes(record))
    payload = b"\n".join(lines) + b"\n"
    return gzip.compress(payload, compresslevel=9, mtime=0)


def _is_not_found(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def _head(s3: Any, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        response: dict[str, Any] = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if _is_not_found(exc):
            return None
        raise
    return response


def _put(
    s3: Any,
    *,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
    metadata: dict[str, str],
    retain_until: datetime | None,
) -> None:
    params: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": content_type,
        "Metadata": metadata,
    }
    if retain_until is not None:
        params["ObjectLockMode"] = "COMPLIANCE"
        params["ObjectLockRetainUntilDate"] = retain_until
        params["ChecksumAlgorithm"] = "SHA256"
    s3.put_object(**params)


def export_day(
    tenant_id: uuid.UUID,
    day: date,
    *,
    s3: Any,
    signer: Signer,
    bucket: str,
    object_lock_retention_days: int | None = None,
    engine: Engine | None = None,
    statement_timeout_ms: int | None = None,
    now: datetime | None = None,
) -> ArchiveResult:
    """Archive one tenant's events for one UTC day (reads in its own ``tenant_session``)."""
    start, end = day_bounds(day)
    with tenant_session(
        tenant_id, statement_timeout_ms=statement_timeout_ms, engine=engine
    ) as session:
        rows = [dict(r) for r in repository.iter_events(session, tenant_id, start=start, end=end)]
    if not rows:
        return ArchiveResult(tenant_id, day, None, None, 0, None, "empty")

    for row in rows:
        if chain_hash(bytes(row["prev_hash"]), tenant_event_dict(row)) != bytes(row["hash"]):
            logger.error(
                "audit.chain.broken",
                extra={"tenant_id": str(tenant_id), "seq": row["seq"], "stage": "archive"},
            )
            raise ArchiveChainError(f"event seq {row['seq']} does not match its hash")

    body = build_archive(rows)
    digest = hashlib.sha256(body).hexdigest()
    key = object_key(tenant_id, day)
    sig_key = key + ".sig"
    retain_until = None
    if object_lock_retention_days:
        retain_until = (now or datetime.now(UTC)) + timedelta(days=object_lock_retention_days)

    status: Literal["written", "skipped"] = "written"
    existing = _head(s3, bucket, key)
    if existing is not None:
        if existing.get("Metadata", {}).get("sha256") != digest:
            logger.error(
                "audit.archive.conflict", extra={"tenant_id": str(tenant_id), "day": str(day)}
            )
            raise ArchiveConflictError(f"{key} exists with different content")
        status = "skipped"
    else:
        _put(
            s3,
            bucket=bucket,
            key=key,
            body=body,
            content_type="application/gzip",
            metadata={"sha256": digest, "event-count": str(len(rows))},
            retain_until=retain_until,
        )

    if _head(s3, bucket, sig_key) is None:
        manifest: dict[str, JsonValue] = {
            "format": ARCHIVE_FORMAT,
            "tenant_id": str(tenant_id),
            "day": day.isoformat(),
            "object_key": key,
            "sha256": digest,
            "event_count": len(rows),
            "first_seq": int(rows[0]["seq"]),
            "last_seq": int(rows[-1]["seq"]),
            "first_prev_hash": bytes(rows[0]["prev_hash"]).hex(),
            "last_hash": bytes(rows[-1]["hash"]).hex(),
            "algorithm": signer.algorithm,
            "key_id": signer.key_id,
        }
        signature = signer.sign(canonical_bytes(manifest))
        document = canonical_bytes(
            {"manifest": manifest, "signature": base64.b64encode(signature).decode("ascii")}
        )
        _put(
            s3,
            bucket=bucket,
            key=sig_key,
            body=document,
            content_type="application/json",
            metadata={"sha256": hashlib.sha256(document).hexdigest()},
            retain_until=retain_until,
        )
        status = "written"

    logger.info(
        "audit.archive.exported",
        extra={"tenant_id": str(tenant_id), "day": str(day), "status": status, "events": len(rows)},
    )
    return ArchiveResult(tenant_id, day, key, sig_key, len(rows), digest, status)


def export_all(
    tenant_ids: Iterable[uuid.UUID],
    day: date,
    *,
    s3: Any,
    signer: Signer,
    bucket: str,
    object_lock_retention_days: int | None = None,
    engine: Engine | None = None,
    statement_timeout_ms: int | None = None,
) -> list[ArchiveResult]:
    """Archive ``day`` for every tenant, one transaction per tenant; raise if any failed."""
    results: list[ArchiveResult] = []
    failed: list[uuid.UUID] = []
    for tenant_id in tenant_ids:
        try:
            results.append(
                export_day(
                    tenant_id,
                    day,
                    s3=s3,
                    signer=signer,
                    bucket=bucket,
                    object_lock_retention_days=object_lock_retention_days,
                    engine=engine,
                    statement_timeout_ms=statement_timeout_ms,
                )
            )
        except Exception:
            logger.exception("audit.archive.failed", extra={"tenant_id": str(tenant_id)})
            failed.append(tenant_id)
    if failed:
        raise ArchiveIncompleteError(f"audit archive failed for {len(failed)} tenant(s)")
    return results


# --- the bucket as the reference (H-04) and backfill of missed days (H-05) -----------------------

BACKFILL_DAYS = 31
"""How far back the daily job looks for days with events but no signed manifest (H-05)."""
_SIG_SUFFIX = ".jsonl.gz.sig"


@dataclass(frozen=True)
class ArchiveCheck:
    """Where the signed archive lives, for comparing a chain with it (H-04)."""

    s3: Any
    bucket: str
    signer: Signer


def _list_keys(s3: Any, bucket: str, prefix: str, *, start_after: str | None = None) -> list[str]:
    keys: list[str] = []
    params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
    if start_after is not None:
        params["StartAfter"] = start_after
    while True:
        page: dict[str, Any] = s3.list_objects_v2(**params)
        keys.extend(str(o["Key"]) for o in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        params["ContinuationToken"] = page["NextContinuationToken"]


def _sig_day(key: str) -> date | None:
    if not key.endswith(_SIG_SUFFIX):
        return None
    try:
        return date.fromisoformat(key.rsplit("/audit-", 1)[1][: -len(_SIG_SUFFIX)])
    except (IndexError, ValueError):
        return None


def _start_after(tenant_id: uuid.UUID, since: date) -> str:
    # Keys sort by day (t/<id>/yyyy/mm/dd/...): "~" sorts after every key of the day before.
    return f"t/{tenant_id}/{since - timedelta(days=1):%Y/%m/%d}/~"


def archived_days(s3: Any, bucket: str, tenant_id: uuid.UUID, *, since: date) -> set[date]:
    """Days from ``since`` on whose signed manifest is in the bucket (derived from the bucket:
    no table records what was archived)."""
    keys = _list_keys(s3, bucket, f"t/{tenant_id}/", start_after=_start_after(tenant_id, since))
    return {d for d in map(_sig_day, keys) if d is not None}


def latest_manifest(
    s3: Any, bucket: str, tenant_id: uuid.UUID, *, today: date
) -> dict[str, Any] | None:
    """The newest signed manifest document of a school (the last ``BACKFILL_DAYS`` first, then
    the whole prefix), or None when the school has none yet."""
    prefix = f"t/{tenant_id}/"
    recent = _start_after(tenant_id, today - timedelta(days=BACKFILL_DAYS))
    sigs = [k for k in _list_keys(s3, bucket, prefix, start_after=recent) if _sig_day(k)]
    if not sigs:
        sigs = [k for k in _list_keys(s3, bucket, prefix) if _sig_day(k)]
    if not sigs:
        return None
    body = s3.get_object(Bucket=bucket, Key=max(sigs))["Body"].read()
    document: dict[str, Any] = json.loads(body)
    return document


def _signature_ok(document: Mapping[str, Any], signer: Signer) -> bool:
    public_key = getattr(signer, "public_key", None)
    manifest = document.get("manifest")
    if not callable(public_key) or not isinstance(manifest, dict):
        return False
    try:
        signature = base64.b64decode(str(document.get("signature", "")), validate=True)
    except ValueError:
        return False
    return verify_signature(
        str(manifest.get("algorithm")), public_key(), canonical_bytes(manifest), signature
    )


def check_head(
    session: Any, tenant_id: uuid.UUID, document: Mapping[str, Any], signer: Signer
) -> tuple[str, int] | None:
    """Compare the chain in the database with the last signed manifest (H-04): ``(reason,
    seq)`` when the database lost or rewrote archived events, else None. A DBA who deletes the
    newest events and rewinds ``chain_heads`` leaves a consistent chain; the archive does not
    agree with it."""
    manifest = document.get("manifest")
    if (
        not isinstance(manifest, dict)
        or manifest.get("tenant_id") != str(tenant_id)
        or not _signature_ok(document, signer)
    ):
        return "archive_signature_invalid", 0
    last_seq = int(manifest["last_seq"])
    head = repository.get_head(session, tenant_id)
    if head is None or head[0] < last_seq:
        return "head_behind_archive", last_seq
    rows = repository.events_at(session, tenant_id, last_seq)
    if len(rows) != 1 or bytes(rows[0]["hash"]).hex() != manifest["last_hash"]:
        return "archive_hash_mismatch", last_seq
    return None


def export_backfill(
    tenant_ids: Iterable[uuid.UUID],
    until: date,
    *,
    s3: Any,
    signer: Signer,
    bucket: str,
    object_lock_retention_days: int | None = None,
    engine: Engine | None = None,
    statement_timeout_ms: int | None = None,
) -> list[ArchiveResult]:
    """Archive every day of the last :data:`BACKFILL_DAYS` up to ``until`` that has events but
    no signed manifest in the bucket, oldest first (H-05): a day that failed every retry is
    archived by a later run. One listing and one query per school; raise if any failed."""
    since = until - timedelta(days=BACKFILL_DAYS - 1)
    start, end = day_bounds(since)[0], day_bounds(until)[1]
    results: list[ArchiveResult] = []
    failed: list[uuid.UUID] = []
    for tenant_id in tenant_ids:
        try:
            done = archived_days(s3, bucket, tenant_id, since=since)
            with tenant_session(
                tenant_id, statement_timeout_ms=statement_timeout_ms, engine=engine
            ) as session:
                days = repository.event_days(session, tenant_id, start=start, end=end)
            for day in sorted(set(days) - done):
                results.append(
                    export_day(
                        tenant_id,
                        day,
                        s3=s3,
                        signer=signer,
                        bucket=bucket,
                        object_lock_retention_days=object_lock_retention_days,
                        engine=engine,
                        statement_timeout_ms=statement_timeout_ms,
                    )
                )
        except Exception:
            logger.exception("audit.archive.failed", extra={"tenant_id": str(tenant_id)})
            failed.append(tenant_id)
    if failed:
        raise ArchiveIncompleteError(f"audit archive failed for {len(failed)} tenant(s)")
    return results
