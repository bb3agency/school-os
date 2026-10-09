"""Admin public API: the school's full data export and its retention settings (US-1201;
FR-ADM-001, FR-ADM-002; BR-08; invariants 3, 4, 5, 7). Other modules call only these functions.

Full export flow (docs/04 §6, §8.2)::

    POST /admin/tenant-export       request_export()   owner (tenant.export_all, school-wide) +
                                                       step-up; one live export per school;
                                                       job run + row + audit
                                                       ``admin.export.requested`` + outbox event
    worker admin.tenant_export      run_export()       (queue "exports") re-resolves the
                                                       requester's CURRENT permissions, collects
                                                       every module's record tables through their
                                                       service.py, streams one zip (records as
                                                       CSV + JSON, documents, audit CSV, manifest,
                                                       README) straight into the private bucket
                                                       (multipart, SSE-KMS; never local disk),
                                                       then ONE transaction: row ready, job done,
                                                       audit ``admin.export.completed``,
                                                       notification to the requester
    GET  /admin/tenant-export/{id}/download-url      presigned GET <= 5 min while the archive is
                                                       valid (24 h); step-up; audit
                                                       ``admin.export.downloaded``
    beat admin.purge_tenant_exports  purge_expired()   archive deleted 24 h after it was ready

Rules:

- **Who (docs/07 §6.2).** ``tenant.export_all`` (owner only by default) reaching the whole
  school; every holder sees and downloads every full export of the school (the export is the
  school's, not a person's). Other schools' ids are 404.
- **Restricted values (docs/07 §8, docs/08 §5, ADR-0021).** C3 values are masked (``••••``)
  unless the requester explicitly asks (``include_sensitive``) and holds school-wide
  ``student.read_sensitive``; the audit event lists the restricted fields included, never
  values. The Aadhaar-as-printed fields are never exported; ``aadhaar_last4`` only as
  ``XXXX XXXX 1234``; any Aadhaar-like number in any value is masked by the writer.
- **Suspended schools (BR-08, docs/16 §5.5).** The export routes are on the suspended-school
  allowlist for the owner and principal; the worker runs for suspended and offboarding schools.
- **Audit (invariant 7)** in the same transaction, IDs/codes/counts only. The audit CSV inside
  the archive is made by ``app.audit.export`` (which records ``audit.exported``).

Retention settings (FR-ADM-002): per data category within the bounds of ``retention.yaml``;
``GET``/``PUT /admin/retention`` (``tenant.settings.manage``; changes need step-up and
``If-Match``); audit ``admin.retention.updated``. The retention jobs of other modules read the
setting through :mod:`app.core.retention`, where this module registers its provider on import.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.academics import service as academics
from app.admin import archive
from app.admin import repository as repo
from app.admin.config import RetentionCategory, load_config, load_retention
from app.admin.models import RetentionSetting, TenantExport
from app.admin.schemas import (
    MemberOut,
    RetentionCategoryOut,
    RetentionOut,
    RetentionUpdate,
    TenantExportCounts,
    TenantExportCreate,
    TenantExportDownloadOut,
    TenantExportOut,
)
from app.audit import export as audit_export
from app.audit import service as audit
from app.audit.viewer import AuditFilters
from app.authz.catalog import BREAKGLASS_ROLE
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.authz.resolver import build_snapshot
from app.certificates import service as certificates
from app.changes import service as changes
from app.circulars import service as circulars
from app.core import purge as purging
from app.core import retention
from app.core.config import get_settings
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    PreconditionFailed,
    StepUpRequired,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.documents import service as documents
from app.documents.service import ObjectStore, ObjectWriter
from app.dq import service as dq
from app.identity import service as identity
from app.identity.principal import STEP_UP_MAX_AGE
from app.imports import service as imports
from app.insights import service as insights
from app.knowledge import service as knowledge
from app.notifications import service as notifications
from app.ops import service as ops
from app.students import service as students
from app.tally import service as tally
from app.tenancy import service as tenancy

log = get_logger(__name__)

EXPORT_ALL: Final = "tenant.export_all"
SENSITIVE: Final = "student.read_sensitive"
SETTINGS: Final = "tenant.settings.manage"

EXPORT_EVENT: Final = "admin.tenant_export.requested"
EXPORT_TASK: Final = "admin.tenant_export"
PURGE_TASK: Final = "admin.purge_tenant_exports"
ops.register_outbox_route(EXPORT_EVENT, EXPORT_TASK)

READY_TEMPLATE: Final = "admin.tenant_export.ready"
FAILED_TEMPLATE: Final = "admin.tenant_export.failed"
RESOURCE: Final = "tenant_export"
IST: Final = ZoneInfo("Asia/Kolkata")
LIVE: Final = repo.LIVE_STATUSES


# --- plumbing -------------------------------------------------------------------------------------


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
    *,
    resource_type: str = RESOURCE,
    actor_type: str = "user",
    actor_id: uuid.UUID | None = None,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if actor_type == "system" else "user",
        actor_id=actor_id,
        request_id=_request_id(),
    )


def _school_wide(ctx: UserContext, permission: str) -> bool:
    return ctx.has(permission) and ctx.scope_for(permission).school_wide


def _require_step_up(ctx: UserContext) -> None:
    """SEC-005: MFA-backed sign-in within 5 minutes (428 ``step_up_required``)."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def _need_export_all(ctx: UserContext) -> None:
    if not _school_wide(ctx, EXPORT_ALL):
        raise Forbidden("Only the school's owner can export all of the school's data.")


def _not_found() -> NotFound:
    return NotFound("Export not found")


def _invalid(field: str, code: str) -> ValidationFailed:
    return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.admin.{code}"}])


def _after(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    value = decode_cursor(cursor)
    if value is None:
        return None
    try:
        created = dt.datetime.fromisoformat(str(value["t"]))
        last = uuid.UUID(str(value["i"]))
    except (KeyError, ValueError) as exc:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        ) from exc
    if created.tzinfo is None:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        )
    return created, last


def _never_exported() -> tuple[str, ...]:
    return load_config().tenant_export.never_exported


# --- full export: requests and reads --------------------------------------------------------------


def _counts(row: TenantExport) -> TenantExportCounts | None:
    if not row.counts:
        return None
    return TenantExportCounts.model_validate(row.counts)


def _download_problem(ctx: UserContext, row: TenantExport) -> Forbidden | None:
    if not _school_wide(ctx, EXPORT_ALL):
        return Forbidden("Only the school's owner can download the school's data export.")
    if row.include_sensitive and not _school_wide(ctx, SENSITIVE):
        return Forbidden(
            "This export has restricted details you are not allowed to see.",
            code="sensitive_not_allowed",
        )
    return None


def _outs(
    session: Session, ctx: UserContext, rows: Sequence[TenantExport]
) -> list[TenantExportOut]:
    names = identity.member_display_names(session, {r.requested_by_membership for r in rows})
    now = repo.now(session) if rows else dt.datetime.now(dt.UTC)
    return [
        TenantExportOut(
            id=row.id,
            status=row.status,
            include_sensitive=row.include_sensitive,
            error_code=row.error_code,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            expires_at=row.expires_at,
            size_bytes=row.size_bytes if row.files_deleted_at is None else None,
            counts=_counts(row),
            requested_by=MemberOut(
                membership_id=row.requested_by_membership,
                display_name=names.get(row.requested_by_membership),
            ),
            own=row.requested_by_membership == ctx.membership_id,
            can_download=(
                row.status == "ready"
                and row.files_deleted_at is None
                and row.expires_at is not None
                and row.expires_at > now
                and _download_problem(ctx, row) is None
            ),
        )
        for row in rows
    ]


def request_export(session: Session, ctx: UserContext, data: TenantExportCreate) -> TenantExportOut:
    """``POST /admin/tenant-export`` (FR-ADM-001): queue a full export of the school's data.

    Needs school-wide ``tenant.export_all`` and step-up (route guard, checked again here);
    ``include_sensitive`` also needs school-wide ``student.read_sensitive`` (403
    ``sensitive_not_allowed``). One export at a time per school (409
    ``tenant_export_in_progress``). Audit ``admin.export.requested`` names the restricted fields
    that will be included (names only)."""
    _need_export_all(ctx)
    if data.include_sensitive and not _school_wide(ctx, SENSITIVE):
        raise Forbidden(
            "Restricted details can be included only by staff allowed to see them for the "
            "whole school.",
            code="sensitive_not_allowed",
        )
    _require_step_up(ctx)
    if repo.live_export(session) is not None:
        raise _in_progress()
    cfg = load_config().tenant_export
    export_id = new_id()
    job = ops.start_job(
        session,
        task_name=EXPORT_TASK,
        idempotency_key=f"{EXPORT_TASK}:{export_id}",
        created_by=ctx.user_id,
    )
    try:
        with session.begin_nested():
            row = repo.insert_export(
                session,
                id=export_id,
                tenant_id=ctx.tenant_id,
                include_sensitive=data.include_sensitive,
                status="queued",
                job_id=job.id,
                requested_by=ctx.user_id,
                requested_by_membership=ctx.membership_id,
            )
    except IntegrityError as exc:  # another owner's request committed first
        if repo.is_one_live_violation(exc):
            raise _in_progress() from exc
        raise
    sensitive = (
        students.sensitive_export_fields(session, withheld=cfg.never_exported)
        if data.include_sensitive
        else []
    )
    _audit(
        session,
        "admin.export.requested",
        export_id,
        {
            "include_sensitive": data.include_sensitive,
            "sensitive_columns": sensitive,
            "layout_version": cfg.layout_version,
            "link_valid_hours": cfg.link_valid_hours,
        },
    )
    ops.enqueue_event(session, EXPORT_EVENT, {"tenant_export_id": export_id, "job_id": job.id})
    log.info("admin.export.requested", resource_type=RESOURCE, resource_id=export_id)
    return _outs(session, ctx, [row])[0]


def _in_progress() -> Conflict:
    return Conflict(
        "An export of the school's data is already being made. Wait for it to finish.",
        code="tenant_export_in_progress",
    )


def list_exports(
    session: Session, ctx: UserContext, *, limit: int = 50, cursor: str | None = None
) -> Page[TenantExportOut]:
    """Full exports of the school, newest first (school-wide ``tenant.export_all``)."""
    _need_export_all(ctx)
    rows = repo.list_exports(session, after=_after(cursor), limit=limit + 1)
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor({"t": last.created_at.isoformat(), "i": str(last.id)})
    return Page[TenantExportOut](data=_outs(session, ctx, page), next_cursor=next_cursor)


def _visible(session: Session, ctx: UserContext, export_id: uuid.UUID) -> TenantExport:
    _need_export_all(ctx)
    row = repo.get_export(session, export_id)
    if row is None:
        raise _not_found()  # also other schools' ids (RLS): existence is never revealed
    return row


def get_export(session: Session, ctx: UserContext, export_id: uuid.UUID) -> TenantExportOut:
    """One full export of the school (404 for unknown and other schools' ids)."""
    return _outs(session, ctx, [_visible(session, ctx, export_id)])[0]


def download_url(
    session: Session, ctx: UserContext, export_id: uuid.UUID
) -> TenantExportDownloadOut:
    """A presigned GET (at most 5 minutes, ``attachment``) for the archive while it is valid
    (24 hours after it was ready). Always step-up (the whole school's data). Errors: 409
    ``export_not_ready``, ``export_failed``, ``export_expired``. Audit
    ``admin.export.downloaded`` (whether it was the caller's own export)."""
    row = _visible(session, ctx, export_id)
    problem = _download_problem(ctx, row)
    if problem is not None:
        raise problem
    _require_step_up(ctx)
    now = repo.now(session)
    if (
        row.status == "expired"
        or row.files_deleted_at is not None
        or (row.expires_at is not None and row.expires_at <= now)
    ):
        raise Conflict(
            "This export was deleted 24 hours after it was ready. Make a new one.",
            code="export_expired",
        )
    if row.status == "failed":
        raise Conflict("This export could not be made. Make a new one.", code="export_failed")
    if row.status != "ready" or row.object_key is None or row.size_bytes is None:
        raise Conflict("The export is not ready yet. Try again shortly.", code="export_not_ready")
    cfg = load_config().tenant_export
    filename = f"schoolos-export-{now.astimezone(IST):%Y%m%d}-{str(row.id)[:8]}.zip"
    url, expires_at = documents.tenant_export_download_url(
        session, row.id, row.object_key, filename=filename, ttl_s=cfg.download_url_ttl_s
    )
    own = row.requested_by_membership == ctx.membership_id
    _audit(
        session,
        "admin.export.downloaded",
        row.id,
        {
            "include_sensitive": row.include_sensitive,
            "own_export": own,
            "requested_by_membership": row.requested_by_membership,
        },
    )
    log.info("admin.export.downloaded", resource_type=RESOURCE, resource_id=row.id)
    return TenantExportDownloadOut(
        url=url,
        expires_at=expires_at,
        filename=filename,
        content_type=documents.TENANT_EXPORT_MIME,
        size_bytes=row.size_bytes,
    )


# --- full export: the worker ----------------------------------------------------------------------


def _worker_session(
    tenant_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> AbstractContextManager[Session]:
    return tenant_session(
        tenant_id, user_id, statement_timeout_ms=get_settings().worker_statement_timeout_ms
    )


class _Stop(Exception):
    """A permanent reason to fail the export (no retry)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class _Snapshot:
    id: uuid.UUID
    include_sensitive: bool
    job_id: uuid.UUID | None
    requested_by: uuid.UUID
    membership_id: uuid.UUID

    @classmethod
    def of(cls, row: TenantExport) -> _Snapshot:
        return cls(
            id=row.id,
            include_sensitive=row.include_sensitive,
            job_id=row.job_id,
            requested_by=row.requested_by,
            membership_id=row.requested_by_membership,
        )


@dataclass(frozen=True, slots=True)
class _Built:
    sha256: bytes
    size_bytes: int
    counts: TenantExportCounts


def _member_context(tenant_id: uuid.UUID, snap: _Snapshot) -> UserContext:
    """The requester's CURRENT permissions (a role removed after the request stops the export).
    Opens its own transaction."""
    snapshot = build_snapshot(
        identity.membership_access(tenant_id, snap.requested_by, snap.membership_id)
    )
    return UserContext(
        user_id=snap.requested_by,
        tenant_id=tenant_id,
        membership_id=snap.membership_id,
        roles=snapshot.roles,
        permissions=snapshot.permissions,
        scopes=snapshot.scopes,
        mfa=True,
        auth_time=None,
        scoped_permissions=snapshot.scoped_permissions,
    )


def _permission_problem(ctx: UserContext, snap: _Snapshot) -> str | None:
    if not _school_wide(ctx, EXPORT_ALL):
        return "permission_revoked"
    if snap.include_sensitive and not _school_wide(ctx, SENSITIVE):
        return "permission_revoked"
    return None


def _model_table(
    name: str, model: type[BaseModel], items: Sequence[BaseModel], *, drop: Sequence[str] = ()
) -> RecordTable:
    columns = tuple(c for c in model.model_fields if c not in drop)
    dumped = [item.model_dump(mode="python") for item in items]
    return RecordTable(name=name, columns=columns, rows=list(archive.rows_of(dumped, columns)))


def _school_tables(session: Session) -> tuple[list[RecordTable], dict[str, Any]]:
    school = tenancy.get_tenant(session)
    school_row = school.model_dump(mode="python")
    info = {"id": school.id, "code": school.code, "name": school.name}
    years = tenancy.list_academic_years(session, include_archived=True)
    classes = tenancy.list_classes(session, include_archived=True)
    sections = tenancy.list_sections(session, include_archived=True)
    tables = [
        RecordTable(
            name="school",
            columns=tuple(school_row),
            rows=[tuple(school_row.values())],
        ),
    ]
    if years:
        tables.append(_model_table("academic_years", type(years[0]), years, drop=("in_use",)))
    else:
        tables.append(RecordTable(name="academic_years", columns=("id",), rows=[]))
    if classes:
        tables.append(_model_table("classes", type(classes[0]), classes, drop=("in_use",)))
    else:
        tables.append(RecordTable(name="classes", columns=("id",), rows=[]))
    if sections:
        tables.append(_model_table("sections", type(sections[0]), sections, drop=("in_use",)))
    else:
        tables.append(RecordTable(name="sections", columns=("id",), rows=[]))
    return tables, info


def _staff_tables(session: Session) -> list[RecordTable]:
    users, _ = identity.list_users(session, limit=1_000_000)
    # Temporary break-glass memberships belong to SchoolOS support staff, not to the school's
    # staff: their names and emails are not school records (07 §6.4).
    users = [u for u in users if BREAKGLASS_ROLE not in u.roles]
    roles = identity.list_roles(session)
    out: list[RecordTable] = []
    if users:
        out.append(_model_table("staff", type(users[0]), users, drop=("profile_shared",)))
    else:
        out.append(RecordTable(name="staff", columns=("id",), rows=[]))
    if roles:
        out.append(_model_table("roles", type(roles[0]), roles, drop=("grantable",)))
    return out


def _collect(session: Session, snap: _Snapshot) -> tuple[list[RecordTable], dict[str, Any]]:
    """Every module's record tables, through their public services (CLAUDE.md §4)."""
    withheld = _never_exported()
    school, info = _school_tables(session)
    tables = [
        *school,
        *_staff_tables(session),
        *students.export_records(
            session, include_sensitive=snap.include_sensitive, withheld=withheld
        ),
        *changes.export_records(
            session, include_sensitive=snap.include_sensitive, withheld=withheld
        ),
        *dq.export_records(session),
        *imports.export_records(session),
        *documents.export_records(session),
        *documents.export_withheld_files(session, include_sensitive=snap.include_sensitive),
        *certificates.export_records(session),
        *circulars.export_records(session),
        *academics.export_records(session),
        *insights.export_records(session, include_sensitive=snap.include_sensitive),
        *tally.export_records(session),
        # ADR-0034: each person's Ask memory items and switch (their own work context; the
        # query log and conversations are not in the archive, docs/05 §12).
        *knowledge.export_records(session),
        repo.retention_record_table(session),
    ]
    names = [t.name for t in tables]
    if len(set(names)) != len(names):  # a programming error, caught by tests
        raise RuntimeError(f"duplicate record tables: {sorted(names)}")
    return tables, info


class _HashingWriter:
    """Passes bytes to the object writer and keeps the SHA-256 and size (zipfile target; not
    seekable, so the archive is streamed with data descriptors)."""

    def __init__(self, target: ObjectWriter) -> None:
        self._target = target
        self._digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes, /) -> int:
        self._target.write(data)
        self._digest.update(data)
        self.size += len(data)
        return len(data)

    def flush(self) -> None:
        self._target.flush()

    def close(self) -> None:
        """The zip never closes its target; the caller completes or aborts the upload."""

    def digest(self) -> bytes:
        return self._digest.digest()


def _write_archive(
    tenant_id: uuid.UUID,
    snap: _Snapshot,
    target: ObjectWriter,
    store: ObjectStore | None,
) -> _Built:
    cfg = load_config().tenant_export
    out = _HashingWriter(target)
    with _worker_session(tenant_id, snap.requested_by) as session:
        tables, school = _collect(session, snap)
        files = documents.export_files(session, include_sensitive=snap.include_sensitive)
    document_bytes = sum(f.size_bytes for f in files)
    withheld_files = next((len(t.rows) for t in tables if t.name == documents.WITHHELD_TABLE), 0)
    if document_bytes > cfg.max_document_bytes:
        raise _Stop("too_large")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr("README.txt", archive.readme(cfg.readme_en, cfg.readme_te))
        for table in tables:
            zf.writestr(f"records/{table.name}.csv", archive.table_csv(table))
            zf.writestr(f"records/{table.name}.json", archive.table_json(table))
        for f in files:
            path = archive.document_path(f.document_id, f.version_no, f.object_key)
            with zf.open(path, "w", force_zip64=f.size_bytes > 2**30) as member:
                for chunk in documents.iter_export_file(tenant_id, f, store=store):
                    member.write(chunk)
        with _worker_session(tenant_id, snap.requested_by) as session:
            try:
                plan = audit_export.start(
                    session,
                    tenant_id=tenant_id,
                    user_id=snap.requested_by,
                    filters=AuditFilters(),
                    max_rows=cfg.max_audit_rows,
                )
            except ValidationFailed as exc:
                raise _Stop("too_many_audit_events") from exc
        with zf.open("audit/audit-log.csv", "w", force_zip64=True) as member:
            for chunk in audit_export.stream_csv(plan):
                member.write(chunk)
        entries = [archive.TableEntry(t.name, len(t.rows), t.notes) for t in tables]
        zf.writestr(
            "manifest.json",
            archive.manifest(
                export_id=snap.id,
                layout_version=cfg.layout_version,
                school=school,
                generated_at=dt.datetime.now(dt.UTC),
                include_sensitive=snap.include_sensitive,
                tables=entries,
                documents=len(files),
                document_bytes=document_bytes,
                documents_withheld=withheld_files,
                audit_events=plan.rows,
                never_exported=cfg.never_exported,
            ),
        )
    counts = TenantExportCounts(
        tables={t.name: len(t.rows) for t in tables},
        documents=len(files),
        document_bytes=document_bytes,
        audit_events=plan.rows,
    )
    return _Built(sha256=out.digest(), size_bytes=out.size, counts=counts)


def _fail(tenant_id: uuid.UUID, export_id: uuid.UUID, code: str) -> bool:
    """Mark the export failed, fail its job, audit and tell the requester (once)."""
    with _worker_session(tenant_id) as session:
        row = repo.get_export(session, export_id, lock=True)
        if row is None or row.status not in LIVE:
            return False
        now = repo.now(session)
        repo.update_export(
            session,
            export_id,
            {
                "status": "failed",
                "error_code": code,
                "finished_at": now,
                "started_at": row.started_at or now,
            },
        )
        if row.job_id is not None:
            ops.fail_job(session, row.job_id, code)
        _audit(
            session,
            "admin.export.failed",
            export_id,
            {"error_code": code},
            actor_id=row.requested_by,
        )
        notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=[row.requested_by_membership],
            template_key=FAILED_TEMPLATE,
            params={"tenant_export_id": export_id},
            resource_id=export_id,
            dedupe_key=f"tenant_export:{export_id}:failed",
        )
    log.warning(
        "admin.export.failed", resource_type=RESOURCE, resource_id=export_id, error_code=code
    )
    return True


def abandon(tenant_id: uuid.UUID, export_id: uuid.UUID, code: str = "worker_error") -> bool:
    """The worker gave up after its retries: the export shows ``failed``."""
    return _fail(tenant_id, export_id, code)


def run_export(
    tenant_id: uuid.UUID, export_id: uuid.UUID, *, store: ObjectStore | None = None
) -> str:
    """Worker: build and store the archive for the member who asked (idempotent: a finished
    export is left alone; a retried one starts again from nothing). Returns the final status.
    Permanent problems (permission revoked, too large) fail the export; other errors abort the
    upload and propagate for a retry."""
    with _worker_session(tenant_id) as session:
        row = repo.get_export(session, export_id, lock=True)
        if row is None:
            return "missing"
        if row.status not in LIVE:
            return row.status
        repo.update_export(
            session,
            export_id,
            {"status": "running", "started_at": row.started_at or repo.now(session)},
        )
        snap = _Snapshot.of(row)
    try:
        ctx = _member_context(tenant_id, snap)
    except DomainError:
        ctx = None
    problem = "permission_revoked" if ctx is None else _permission_problem(ctx, snap)
    if problem is not None:
        _fail(tenant_id, export_id, problem)
        return "failed"
    with _worker_session(tenant_id, snap.requested_by) as session:
        key, writer = documents.open_tenant_export(session, export_id, store=store)
    try:
        built = _write_archive(tenant_id, snap, writer, store)
    except _Stop as stop:
        writer.abort()
        _fail(tenant_id, export_id, stop.code)
        return "failed"
    except Conflict as exc:
        writer.abort()
        if exc.code == "integrity_mismatch":  # a stored document changed: never ship it
            _fail(tenant_id, export_id, "integrity_mismatch")
            return "failed"
        raise
    except BaseException:
        writer.abort()
        raise
    writer.close()
    return _complete(tenant_id, snap, key, built, store)


def _complete(
    tenant_id: uuid.UUID, snap: _Snapshot, key: str, built: _Built, store: ObjectStore | None
) -> str:
    cfg = load_config().tenant_export
    with _worker_session(tenant_id, snap.requested_by) as session:
        row = repo.get_export(session, snap.id, lock=True)
        if row is None or row.status not in LIVE:
            # Abandoned while we worked: the archive must not outlive the record.
            documents.delete_tenant_export(session, snap.id, key, store=store)
            return row.status if row is not None else "missing"
        now = repo.now(session)
        counts = built.counts.model_dump()
        repo.update_export(
            session,
            snap.id,
            {
                "status": "ready",
                "object_key": key,
                "size_bytes": built.size_bytes,
                "sha256": built.sha256,
                "counts": counts,
                "finished_at": now,
                "expires_at": now + dt.timedelta(hours=cfg.link_valid_hours),
            },
        )
        if snap.job_id is not None:
            ops.finish_job(
                session,
                snap.job_id,
                {"documents": built.counts.documents, "audit_events": built.counts.audit_events},
            )
        _audit(
            session,
            "admin.export.completed",
            snap.id,
            {
                "include_sensitive": snap.include_sensitive,
                "tables": built.counts.tables,
                "documents": built.counts.documents,
                "document_kib": -(-built.counts.document_bytes // 1024),
                "audit_events": built.counts.audit_events,
                "archive_kib": -(-built.size_bytes // 1024),
            },
            actor_id=snap.requested_by,
        )
        notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=[snap.membership_id],
            template_key=READY_TEMPLATE,
            params={"tenant_export_id": snap.id, "hours": cfg.link_valid_hours},
            resource_id=snap.id,
            dedupe_key=f"tenant_export:{snap.id}:ready",
        )
    log.info(
        "admin.export.completed",
        resource_type=RESOURCE,
        resource_id=snap.id,
        count=built.counts.documents,
    )
    return "ready"


# --- full export: retention -------------------------------------------------------------------


def purge_expired(
    tenant_id: uuid.UUID, *, now: dt.datetime | None = None, store: ObjectStore | None = None
) -> int:
    """Daily: delete this school's archives 24 hours after they were ready (and check failed
    ones); the rows stay as the record (docs/05 §13). Audited per export
    (``admin.export.expired``, system actor)."""
    cfg = load_config().tenant_export
    purged = 0
    with _worker_session(tenant_id) as session:
        at = now or repo.now(session)
        failed_before = at - dt.timedelta(hours=cfg.failed_cleanup_hours)
        for row in repo.due_for_purge(session, now=at, failed_before=failed_before):
            deleted = False
            if row.object_key is not None:
                documents.delete_tenant_export(session, row.id, row.object_key, store=store)
                deleted = True
            repo.update_export(
                session,
                row.id,
                {
                    "status": "expired" if row.status == "ready" else row.status,
                    "files_deleted_at": at,
                },
            )
            _audit(
                session,
                "admin.export.expired",
                row.id,
                {"archive_deleted": deleted, "status": row.status},
                actor_type="system",
            )
            purged += 1
    if purged:
        log.info("admin.export.purged", tenant_id=tenant_id, count=purged)
    return purged


# --- retention settings (FR-ADM-002) ----------------------------------------------------------


def _effective(rules: Mapping[str, Any], key: str, category: RetentionCategory) -> int:
    value = rules.get(key)
    if not category.configurable or not isinstance(value, int) or isinstance(value, bool):
        return category.default_days
    return category.clamp(value)


def _retention_provider(session: Session, category: str) -> int | None:
    """:mod:`app.core.retention` provider: the school's setting for ``category`` (clamped to
    the current bounds), or ``None`` for "use the job's default"."""
    spec = load_retention().categories.get(category)
    if spec is None or not spec.configurable:
        return None
    row = repo.get_retention(session)
    if row is None or category not in (row.rules or {}):
        return None
    return _effective(row.rules, category, spec)


retention.register_provider(_retention_provider)


def retention_days(session: Session, category: str) -> int:
    """The number of days the current school keeps ``category`` (setting or default)."""
    spec = load_retention().categories.get(category)
    if spec is None:
        raise KeyError(category)
    row = repo.get_retention(session)
    return _effective(row.rules if row is not None else {}, category, spec)


def _retention_out(session: Session, row: RetentionSetting | None) -> RetentionOut:
    rules: Mapping[str, Any] = row.rules if row is not None else {}
    categories = [
        RetentionCategoryOut(
            key=key,
            days=_effective(rules, key, spec),
            default_days=spec.default_days,
            min_days=spec.min_days,
            max_days=spec.max_days,
            configurable=spec.configurable,
            enforced=spec.enforced_by is not None,
            is_default=_effective(rules, key, spec) == spec.default_days,
        )
        for key, spec in load_retention().categories.items()
    ]
    updated_by = None
    if row is not None and row.updated_by is not None:
        members = identity.members_for_users(session, [row.updated_by])
        member = members.get(row.updated_by)
        if member is not None:
            updated_by = MemberOut(membership_id=member[0], display_name=member[1])
    return RetentionOut(
        categories=categories,
        version=row.version if row is not None else 0,
        updated_at=row.updated_at if row is not None else None,
        updated_by=updated_by,
    )


def get_retention(session: Session, ctx: UserContext) -> RetentionOut:
    """``GET /admin/retention``: every data category with this school's period, the default
    and the bounds (``tenant.settings.manage``)."""
    if not _school_wide(ctx, SETTINGS):
        raise Forbidden()
    return _retention_out(session, repo.get_retention(session))


def update_retention(
    session: Session, ctx: UserContext, data: RetentionUpdate, *, expected_version: int
) -> RetentionOut:
    """``PUT /admin/retention``: set the retention period of the configurable categories
    (``tenant.settings.manage``, step-up, ``If-Match`` = the version read; 412 when someone else
    changed them). A category left out goes back to its default. 422 per category:
    ``unknown_category``, ``not_configurable``, ``out_of_bounds``. Audit
    ``admin.retention.updated`` with each changed category's old and new days."""
    if not _school_wide(ctx, SETTINGS):
        raise Forbidden()
    _require_step_up(ctx)
    specs = load_retention().categories
    errors: list[dict[str, str]] = []
    for key, days in data.rules.items():
        spec = specs.get(key)
        code = None
        if spec is None:
            code = "unknown_category"
        elif not spec.configurable:
            code = "not_configurable" if days != spec.default_days else None
        elif not spec.min_days <= days <= spec.max_days:
            code = "out_of_bounds"
        if code is not None:
            errors.append(
                {"field": f"rules.{key}", "code": code, "message_key": f"errors.admin.{code}"}
            )
    if errors:
        raise ValidationFailed(errors)
    # Only differences from the default are stored; a default is "no setting".
    rules = {
        k: d
        for k, d in sorted(data.rules.items())
        if specs[k].configurable and d != specs[k].default_days
    }
    row = repo.get_retention(session, lock=True)
    before: Mapping[str, Any] = row.rules if row is not None else {}
    current_version = row.version if row is not None else 0
    if expected_version != current_version:
        raise PreconditionFailed(
            "The retention settings were changed by someone else. Reload and try again."
        )
    if row is None:
        try:
            with session.begin_nested():
                row = repo.insert_retention(
                    session,
                    id=new_id(),
                    tenant_id=ctx.tenant_id,
                    rules=rules,
                    updated_by=ctx.user_id,
                )
        except IntegrityError as exc:  # another first write committed meanwhile
            raise PreconditionFailed(
                "The retention settings were changed by someone else. Reload and try again."
            ) from exc
    else:
        updated = repo.update_retention(
            session, expected_version=expected_version, rules=rules, updated_by=ctx.user_id
        )
        if updated is None:
            raise PreconditionFailed(
                "The retention settings were changed by someone else. Reload and try again."
            )
        row = updated
    changes_list = [
        {
            "category": key,
            "from_days": _effective(before, key, spec),
            "to_days": _effective(rules, key, spec),
        }
        for key, spec in specs.items()
        if _effective(before, key, spec) != _effective(rules, key, spec)
    ]
    _audit(
        session,
        "admin.retention.updated",
        row.id,
        {"changes": changes_list, "version": row.version},
        resource_type="retention_settings",
    )
    log.info("admin.retention.updated", resource_type="retention_settings", count=len(changes_list))
    return _retention_out(session, row)


__all__ = [
    "EXPORT_ALL",
    "EXPORT_EVENT",
    "EXPORT_TASK",
    "PURGE_TASK",
    "SETTINGS",
    "abandon",
    "download_url",
    "get_export",
    "get_retention",
    "list_exports",
    "purge_expired",
    "purge_tenant_data",
    "request_export",
    "retention_days",
    "run_export",
    "tenant_data_counts",
    "update_retention",
]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Full-export records (their archives live under t/<tenant_id>/tenant-export/ and go with the
# school's files) and the school's retention settings. Registered with app.tenancy at import;
# the offboarding job counts them as sos_app and deletes them as sos_purger.
_PURGE = purging.PurgeTables(deleted=("ops.tenant_exports", "ops.retention_settings"))


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="admin", count=tenant_data_counts, purge=purge_tenant_data)
)
