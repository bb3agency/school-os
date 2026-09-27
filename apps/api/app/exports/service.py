"""Exports public API: board/portal pre-check reports and student lists (US-501 AC4, US-901;
FR-EXP-001..004; SEC-017; invariants 3, 4, 7). Other modules call only these functions.

Flow (docs/04 §6, §8.2)::

    POST /exports               request_precheck()      checks, freezes the requester's students,
    POST /exports/student-list  request_student_list()  job run + export row + audit
                                                        ``export.requested`` + outbox event
    worker exports.generate     run_export()            (queue "exports"; XLSX/CSV only) or
    worker exports.render                               (queue "pdf"; whenever a PDF is wanted):
                                                        re-resolves the requester's CURRENT
                                                        permissions and reach, re-runs the DQ
                                                        checks (pre-checks), builds the files,
                                                        stores them (private, SSE-KMS), then ONE
                                                        transaction: file rows, status ready,
                                                        job succeeded, audit ``export.completed``,
                                                        notification ``export.ready``
    GET  /exports/{id}/download-url  download_url()     presigned GET <= 5 min, audit
                                                        ``export.downloaded``
    beat exports.purge_expired  purge_expired()         files deleted 7 days after completion

Rules:

- **Reach (SEC-015, invariant 3).** Students come only from ``students.list_students_in_scope``
  with the requester's context; findings only from ``dq.service`` with the same context. An
  export is visible and downloadable only by the member who requested it (404 for anyone else,
  including other schools).
- **Sensitive data (docs/07 §8, docs/08 §5).** Restricted (C3) values are masked (``••••``)
  unless explicitly included by a holder of ``student.read_sensitive`` (pre-checks also need
  step-up). The Aadhaar-as-printed fields are never exported; ``aadhaar_last4`` is shown only as
  ``XXXX XXXX 1234``. Findings carry the masked values the DQ engine stored.
- **Cells (SEC-017, invariant 4).** Every cell passes :func:`app.exports.tables.safe_cell`.
- **Step-up (FR-EXP-004).** Student lists (bulk personal data) need MFA within 5 minutes to be
  requested (route) and downloaded (here), as do pre-checks that include sensitive values.
- **Audit (FR-EXP-003, invariant 7)** in the same transaction, IDs/codes/counts only; the
  export row keeps the exact student ids and the audit event binds them with a digest.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.authz.resolver import build_snapshot
from app.core.config import get_settings
from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, StepUpRequired, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.documents import service as documents
from app.dq import service as dq
from app.exports import repository as repo
from app.exports.config import ExportsConfig, Language, ProfileLayout, load_config
from app.exports.models import Export, ExportFile
from app.exports.pdf import PdfRenderer, get_renderer
from app.exports.report import (
    FindingLine,
    PrecheckInput,
    ReadyLine,
    build_precheck,
    render_precheck_html,
)
from app.exports.schemas import (
    ExportDownloadOut,
    ExportFileOut,
    ExportOut,
    ExportProfileOut,
    ExportScopeIn,
    PrecheckCreate,
    StudentListCreate,
)
from app.exports.tables import CSV_MIME, PDF_MIME, XLSX_MIME, Table, write_csv, write_xlsx
from app.identity import service as identity
from app.identity.principal import STEP_UP_MAX_AGE
from app.notifications import service as notifications
from app.ops import service as ops
from app.students import service as students
from app.tenancy import service as tenancy

log = get_logger(__name__)

STUDENT_READ: Final = "student.read_basic"
SENSITIVE: Final = "student.read_sensitive"
DQ_READ: Final = "dq.findings.read"
BOARD: Final = "export.board"
PORTAL: Final = "export.portal"
STUDENT_EXPORT: Final = "student.export"
EXPORT_PERMISSIONS: Final = (BOARD, PORTAL, STUDENT_EXPORT)

KIND_OF_LAYOUT: Final = {"board": "board_precheck", "portal": "portal_precheck"}
PERMISSION_OF_KIND: Final = {
    "board_precheck": BOARD,
    "portal_precheck": PORTAL,
    "student_list": STUDENT_EXPORT,
}

GENERATE_EVENT: Final = "export.requested"
RENDER_EVENT: Final = "export.render_requested"
GENERATE_TASK: Final = "exports.generate"
RENDER_TASK: Final = "exports.render"
PURGE_TASK: Final = "exports.purge_expired"
ops.register_outbox_route(GENERATE_EVENT, GENERATE_TASK)
ops.register_outbox_route(RENDER_EVENT, RENDER_TASK)

IST: Final = ZoneInfo("Asia/Kolkata")
MASK: Final = "••••"
MIME: Final = {"xlsx": XLSX_MIME, "csv": CSV_MIME, "pdf": PDF_MIME}
LIVE: Final = ("queued", "running")
FAILED_ORPHAN_AGE: Final = dt.timedelta(days=1)


# --- plumbing -------------------------------------------------------------------------------------


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    export_id: uuid.UUID,
    summary: Mapping[str, Any],
    *,
    actor_type: str = "user",
    actor_id: uuid.UUID | None = None,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type="export",
        resource_id=export_id,
        summary=summary,
        actor_type="system" if actor_type == "system" else "user",
        actor_id=actor_id,
        request_id=_request_id(),
    )


def _not_found() -> NotFound:
    return NotFound("Export not found")


def _need(ctx: UserContext, permission: str, *, code: str = "forbidden", detail: str) -> None:
    if not ctx.has(permission):
        raise Forbidden(detail, code=code)


def _require_step_up(ctx: UserContext) -> None:
    """SEC-005 / FR-EXP-004: MFA-backed sign-in within 5 minutes (428 ``step_up_required``)."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def _invalid(field: str, code: str, message_key: str | None = None) -> ValidationFailed:
    return ValidationFailed(
        [{"field": field, "code": code, "message_key": message_key or f"errors.exports.{code}"}]
    )


def student_ids_digest(student_ids: Collection[uuid.UUID]) -> str:
    """A fingerprint of the exact student set (sorted ids), recorded in the audit chain so the
    list kept on the export row can be checked against it (FR-EXP-003)."""
    joined = "\n".join(sorted(str(i) for i in student_ids)).encode()
    return str(uuid.UUID(bytes=hashlib.sha256(joined).digest()[:16]))


def member_context(
    tenant_id: uuid.UUID, user_id: uuid.UUID, membership_id: uuid.UUID
) -> UserContext:
    """The requester's CURRENT permissions and scopes, for work done on their behalf in a
    worker (a role removed after the request stops the export). Opens its own transaction."""
    snap = build_snapshot(identity.membership_access(tenant_id, user_id, membership_id))
    return UserContext(
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=snap.roles,
        permissions=snap.permissions,
        scopes=snap.scopes,
        mfa=True,
        auth_time=None,
        scoped_permissions=snap.scoped_permissions,
    )


# --- profiles -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Profile:
    key: str
    version: int
    label_en: str
    label_te: str
    required_fields: tuple[str, ...]
    layout: ProfileLayout

    @property
    def kind(self) -> str:
        return KIND_OF_LAYOUT[self.layout.kind]

    def label(self, language: Language) -> str:
        return self.label_te if language == "te" else self.label_en


def _profiles() -> dict[str, _Profile]:
    cfg = load_config()
    out: dict[str, _Profile] = {}
    for p in dq.profiles_catalog():
        layout = cfg.profiles.get(p.key)
        if layout is None:
            continue
        missing = set(p.required_fields) - set(layout.fields)
        if missing:  # configuration error, caught by tests (FR-EXP-001)
            raise RuntimeError(f"export layout {p.key} lacks required fields {sorted(missing)}")
        out[p.key] = _Profile(
            key=p.key,
            version=p.version,
            label_en=p.label_en,
            label_te=p.label_te,
            required_fields=tuple(p.required_fields),
            layout=layout,
        )
    return out


def _profile(profile_key: str) -> _Profile:
    profile = _profiles().get(profile_key)
    if profile is None:
        raise _invalid("profile_key", "unknown_profile", "errors.dq.unknown_profile")
    return profile


def list_profiles(ctx: UserContext) -> list[ExportProfileOut]:
    """Pre-check profiles with their target field order; ``allowed`` says whether the caller
    holds the profile's permission (``export.board`` or ``export.portal``)."""
    return [
        ExportProfileOut(
            key=p.key,
            kind=p.layout.kind,
            permission=PERMISSION_OF_KIND[p.kind],
            version=p.version,
            layout_version=p.layout.layout_version,
            label_en=p.label_en,
            label_te=p.label_te,
            fields=list(p.layout.fields),
            required_fields=list(p.required_fields),
            allowed=ctx.has(PERMISSION_OF_KIND[p.kind]),
        )
        for p in _profiles().values()
    ]


# --- requests -------------------------------------------------------------------------------------


def _scope_json(scope: ExportScopeIn) -> dict[str, list[str]]:
    if scope.section_ids:
        return {"section_ids": [str(s) for s in scope.section_ids]}
    if scope.class_ids:
        return {"class_ids": [str(c) for c in scope.class_ids]}
    return {}


def _check_structure(session: Session, scope: ExportScopeIn) -> None:
    """Unknown (or other schools') sections and classes are a 422, like any bad reference."""
    for i, section_id in enumerate(scope.section_ids or ()):
        try:
            tenancy.get_section(session, section_id)
        except NotFound:
            raise _invalid(f"scope.section_ids.{i}", "not_found", "errors.not_found") from None
    for i, class_id in enumerate(scope.class_ids or ()):
        try:
            tenancy.get_class(session, class_id)
        except NotFound:
            raise _invalid(f"scope.class_ids.{i}", "not_found", "errors.not_found") from None


def _students_for(session: Session, ctx: UserContext, scope: ExportScopeIn) -> list[uuid.UUID]:
    """The students of ``scope`` the caller may read (current academic year), frozen for the
    export. 422 when there are none or too many."""
    _check_structure(session, scope)
    ids = students.list_students_in_scope(
        session, ctx, section_ids=scope.section_ids, class_ids=scope.class_ids
    )
    if not ids:
        raise _invalid("scope", "no_students")
    if len(ids) > load_config().max_students:
        raise _invalid("scope", "too_many_students")
    return sorted(set(ids))


def _create(
    session: Session,
    ctx: UserContext,
    *,
    kind: str,
    formats: Sequence[str],
    language: str,
    scope: ExportScopeIn,
    student_ids: Sequence[uuid.UUID],
    layout_version: int,
    profile: _Profile | None = None,
    columns: Sequence[str] | None = None,
    include_sensitive: bool = False,
    sensitive_columns: Sequence[str] = (),
) -> ExportOut:
    export_id = new_id()
    task = RENDER_TASK if "pdf" in formats else GENERATE_TASK
    job = ops.start_job(
        session, task_name=task, idempotency_key=f"{task}:{export_id}", created_by=ctx.user_id
    )
    row = repo.insert_export(
        session,
        id=export_id,
        tenant_id=ctx.tenant_id,
        kind=kind,
        profile_key=profile.key if profile else None,
        profile_version=profile.version if profile else None,
        layout_version=layout_version,
        formats=list(formats),
        language=language,
        scope=_scope_json(scope),
        columns=list(columns) if columns is not None else None,
        include_sensitive=include_sensitive,
        student_ids=list(student_ids),
        student_count=len(student_ids),
        status="queued",
        job_id=job.id,
        requested_by=ctx.user_id,
        requested_by_membership=ctx.membership_id,
    )
    _audit(
        session,
        "export.requested",
        export_id,
        {
            "kind": kind,
            "profile_key": profile.key if profile else None,
            "profile_version": profile.version if profile else None,
            "layout_version": layout_version,
            "formats": list(formats),
            "language": language,
            "scope": _scope_json(scope),
            "columns": list(columns) if columns is not None else None,
            "include_sensitive": include_sensitive,
            "sensitive_columns": list(sensitive_columns),
            "students": len(student_ids),
            "student_ids_digest": student_ids_digest(student_ids),
        },
    )
    ops.enqueue_event(
        session,
        RENDER_EVENT if task == RENDER_TASK else GENERATE_EVENT,
        {
            "export_id": export_id,
            "job_id": job.id,
            "user_id": ctx.user_id,
            "membership_id": ctx.membership_id,
        },
    )
    log.info("exports.requested", resource_type="export", resource_id=export_id, action=kind)
    return _out(row, [])


def request_precheck(session: Session, ctx: UserContext, data: PrecheckCreate) -> ExportOut:
    """``POST /exports``: a pre-check report for a DQ profile (US-501 AC4) as XLSX (summary,
    findings blockers first, "ready to enter" sheet in the profile's field order) and/or an A4
    PDF. Needs the profile's permission (``export.board`` for board profiles, ``export.portal``
    for portal profiles), ``student.read_basic`` and ``dq.findings.read``; ``include_sensitive``
    also needs ``student.read_sensitive`` and step-up. Audit: ``export.requested``."""
    profile = _profile(data.profile_key)
    _need(
        ctx,
        PERMISSION_OF_KIND[profile.kind],
        detail="You cannot make exports for this board or portal.",
    )
    _need(ctx, STUDENT_READ, detail="You cannot see student records.")
    _need(ctx, DQ_READ, detail="You cannot see data-quality findings.")
    if data.include_sensitive:
        _need(
            ctx,
            SENSITIVE,
            code="sensitive_not_allowed",
            detail="Restricted details can be included only by staff allowed to see them.",
        )
        _require_step_up(ctx)
    ids = _students_for(session, ctx, data.scope)
    return _create(
        session,
        ctx,
        kind=profile.kind,
        formats=data.format,
        language=data.language,
        scope=data.scope,
        student_ids=ids,
        layout_version=profile.layout.layout_version,
        profile=profile,
        include_sensitive=data.include_sensitive,
    )


def _exportable_columns(session: Session, cfg: ExportsConfig) -> dict[str, str]:
    """Column key -> classification (``C1`` for structure columns)."""
    out = {
        a.key: a.classification
        for a in students.attribute_catalog(session)
        if a.key not in cfg.never_exported
    }
    out.update(dict.fromkeys(cfg.student_list.structure_columns, "C1"))
    return out


def request_student_list(session: Session, ctx: UserContext, data: StudentListCreate) -> ExportOut:
    """``POST /exports/student-list``: chosen columns for the students in scope as CSV or XLSX
    (``student.export``, step-up at the route). Restricted (C3) columns need
    ``student.read_sensitive`` (403 ``sensitive_not_allowed``); Aadhaar-as-printed fields are
    never exportable (422 ``column_not_exportable``). Audit: ``export.requested``."""
    cfg = load_config()
    _need(ctx, STUDENT_READ, detail="You cannot see student records.")
    allowed = _exportable_columns(session, cfg)
    errors = [
        {
            "field": f"columns.{i}",
            "code": "column_not_exportable",
            "message_key": "errors.exports.column_not_exportable",
        }
        for i, c in enumerate(data.columns)
        if c not in allowed
    ]
    if errors:
        raise ValidationFailed(errors)
    sensitive = [c for c in data.columns if allowed[c] == "C3"]
    if sensitive:
        _need(
            ctx,
            SENSITIVE,
            code="sensitive_not_allowed",
            detail="Restricted details can be exported only by staff allowed to see them.",
        )
    ids = _students_for(session, ctx, data.scope)
    return _create(
        session,
        ctx,
        kind="student_list",
        formats=[data.format],
        language=data.language,
        scope=data.scope,
        student_ids=ids,
        layout_version=cfg.student_list.layout_version,
        columns=data.columns,
        include_sensitive=bool(sensitive),
        sensitive_columns=sensitive,
    )


# --- reads ----------------------------------------------------------------------------------------


def _out(row: Export, files: Sequence[ExportFile]) -> ExportOut:
    return ExportOut(
        id=row.id,
        kind=row.kind,
        profile_key=row.profile_key,
        profile_version=row.profile_version,
        layout_version=row.layout_version,
        formats=list(row.formats),
        language=row.language,
        scope={k: [uuid.UUID(str(v)) for v in vs] for k, vs in (row.scope or {}).items()},
        columns=list(row.columns) if row.columns is not None else None,
        include_sensitive=row.include_sensitive,
        student_count=row.student_count,
        status=row.status,
        error_code=row.error_code,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        expires_at=row.expires_at,
        files=[
            ExportFileOut(
                format=f.format,
                content_type=f.content_type,
                size_bytes=f.size_bytes,
            )
            for f in files
            if row.files_deleted_at is None
        ],
    )


def _own(session: Session, ctx: UserContext, export_id: uuid.UUID) -> Export:
    """The caller's own export (404 for anyone else's, another school's or unknown ids)."""
    row = repo.get_export(session, export_id)
    if row is None or row.requested_by_membership != ctx.membership_id:
        raise _not_found()
    return row


def _after(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    value = decode_cursor(cursor)
    if value is None:
        return None
    try:
        created = dt.datetime.fromisoformat(str(value["t"]))
        last = uuid.UUID(str(value["i"]))
    except (KeyError, ValueError) as exc:
        raise _invalid("cursor", "invalid", "errors.invalid_cursor") from exc
    if created.tzinfo is None:
        raise _invalid("cursor", "invalid", "errors.invalid_cursor")
    return created, last


def list_exports(
    session: Session, ctx: UserContext, *, limit: int = 50, cursor: str | None = None
) -> Page[ExportOut]:
    """The caller's own exports, newest first."""
    rows = repo.list_for_membership(
        session, ctx.membership_id, after=_after(cursor), limit=limit + 1
    )
    page = rows[:limit]
    files = repo.files_of(session, [r.id for r in page])
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor({"t": last.created_at.isoformat(), "i": str(last.id)})
    return Page[ExportOut](
        data=[_out(r, files.get(r.id, [])) for r in page], next_cursor=next_cursor
    )


def get_export(session: Session, ctx: UserContext, export_id: uuid.UUID) -> ExportOut:
    row = _own(session, ctx, export_id)
    return _out(row, repo.files_of(session, [row.id]).get(row.id, []))


def _needs_step_up(row: Export) -> bool:
    return row.kind == "student_list" or row.include_sensitive


def _stem(row: Export) -> str:
    return f"precheck-{row.profile_key}" if row.profile_key else "students"


def download_url(
    session: Session, ctx: UserContext, export_id: uuid.UUID, file_format: str | None = None
) -> ExportDownloadOut:
    """A presigned GET (<= 5 minutes, attachment) for one file of the caller's own ready
    export. The caller must still hold the export's permission; student lists and exports with
    sensitive values need step-up (FR-EXP-004). Audit: ``export.downloaded``."""
    row = _own(session, ctx, export_id)
    _need(ctx, PERMISSION_OF_KIND[row.kind], detail="You can no longer download this export.")
    if row.include_sensitive:
        _need(
            ctx,
            SENSITIVE,
            code="sensitive_not_allowed",
            detail="You can no longer download restricted details.",
        )
    if _needs_step_up(row):
        _require_step_up(ctx)
    if row.status == "expired" or row.files_deleted_at is not None:
        raise Conflict(
            "This export was deleted after 7 days. Make a new one.", code="export_expired"
        )
    if row.status == "failed":
        raise Conflict("This export could not be made. Make a new one.", code="export_failed")
    if row.status != "ready":
        raise Conflict("The export is not ready yet. Try again shortly.", code="export_not_ready")
    now = repo.now(session)
    if row.expires_at is not None and row.expires_at <= now:
        raise Conflict(
            "This export was deleted after 7 days. Make a new one.", code="export_expired"
        )
    files = {f.format: f for f in repo.files_of(session, [row.id]).get(row.id, [])}
    wanted = file_format or row.formats[0]
    file = files.get(wanted)
    if file is None:
        raise NotFound("This export has no file in that format")
    local = now.astimezone(IST).strftime("%Y%m%d")
    filename = f"schoolos-{_stem(row)}-{local}-{str(row.id)[:8]}.{file.format}"
    url, expires_at = documents.export_download_url(
        session,
        row.id,
        file.object_key,
        content_type=file.content_type,
        filename=filename,
        ttl_s=load_config().download_url_ttl_s,
    )
    _audit(
        session,
        "export.downloaded",
        row.id,
        {"kind": row.kind, "format": file.format, "file_id": file.id},
    )
    log.info("exports.downloaded", resource_type="export", resource_id=row.id)
    return ExportDownloadOut(
        url=url,
        expires_at=expires_at,
        format=file.format,
        content_type=file.content_type,
        filename=filename,
    )


# --- worker: building the files -------------------------------------------------------------------


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
    kind: str
    profile_key: str | None
    formats: tuple[str, ...]
    language: Language
    scope: Mapping[str, Any]
    columns: tuple[str, ...]
    include_sensitive: bool
    student_ids: tuple[uuid.UUID, ...]
    job_id: uuid.UUID | None
    requested_by: uuid.UUID
    membership_id: uuid.UUID

    @classmethod
    def of(cls, row: Export) -> _Snapshot:
        return cls(
            id=row.id,
            kind=row.kind,
            profile_key=row.profile_key,
            formats=tuple(row.formats),
            language="te" if row.language == "te" else "en",
            scope=dict(row.scope or {}),
            columns=tuple(row.columns or ()),
            include_sensitive=row.include_sensitive,
            student_ids=tuple(row.student_ids),
            job_id=row.job_id,
            requested_by=row.requested_by,
            membership_id=row.requested_by_membership,
        )


@dataclass(frozen=True, slots=True)
class _File:
    format: str
    filename: str
    data: bytes

    @property
    def content_type(self) -> str:
        return MIME[self.format]


@dataclass(frozen=True, slots=True)
class _Built:
    files: tuple[_File, ...]
    students: int
    findings: int = 0
    blockers: int = 0


def _permission_problem(ctx: UserContext, snap: _Snapshot) -> str | None:
    needed = [PERMISSION_OF_KIND[snap.kind], STUDENT_READ]
    if snap.kind != "student_list":
        needed.append(DQ_READ)
    if snap.include_sensitive:
        needed.append(SENSITIVE)
    return None if all(ctx.has(p) for p in needed) else "permission_revoked"


@dataclass(frozen=True, slots=True)
class _Placement:
    class_section: str | None
    class_code: str | None
    section: str | None
    roll_no: str | None
    sort: tuple[str, ...]


def _placements(session: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, _Placement]:
    """Class, section and roll number in the current academic year (sort key included)."""
    year = tenancy.get_current_academic_year(session)
    if year is None:
        return {}
    sections = {s.id: s for s in tenancy.list_sections(session, academic_year_id=year.id)}
    classes = {c.id: c for c in tenancy.list_classes(session)}
    out: dict[uuid.UUID, _Placement] = {}
    for sid, enrolments in students.active_enrolments(session, ids).items():
        current = [e for e in enrolments if e.academic_year_id == year.id]
        if not current:
            continue
        e = current[0]
        section = sections.get(e.section_id)
        klass = classes.get(section.class_id) if section else None
        code = klass.code if klass else None
        name = section.name if section else None
        roll = e.roll_no or ""
        out[sid] = _Placement(
            class_section=f"{code}-{name}" if code and name else None,
            class_code=code,
            section=name,
            roll_no=e.roll_no,
            sort=(
                f"{klass.sort_order:06d}" if klass else "999999",
                name or "",
                roll.rjust(8, "0") if roll.isdigit() else roll,
            ),
        )
    return out


def _format_value(
    value: str | None,
    *,
    key: str,
    data_type: str,
    date_format: str,
    value_maps: Mapping[str, Mapping[str, str]],
    cfg: ExportsConfig,
) -> str | None:
    if value is None:
        return None
    if key == "aadhaar_last4":
        return cfg.aadhaar_last4_display.format(last4=value[-4:])
    if data_type == "date":
        try:
            return dt.date.fromisoformat(value).strftime(date_format)
        except ValueError:
            return value
    mapped = value_maps.get(key, {}).get(value)
    return mapped if mapped is not None else value


def _now_ist() -> str:
    return dt.datetime.now(IST).strftime("%d/%m/%Y %H:%M")


def _scope_label(
    session: Session, scope: Mapping[str, Any], cfg: ExportsConfig, lang: Language
) -> str:
    year = tenancy.get_current_academic_year(session)
    sections = (
        {s.id: s for s in tenancy.list_sections(session, academic_year_id=year.id)} if year else {}
    )
    classes = {c.id: c for c in tenancy.list_classes(session)}
    labels: list[str] = []
    for raw in scope.get("section_ids", []):
        section = sections.get(uuid.UUID(str(raw)))
        if section is not None:
            klass = classes.get(section.class_id)
            labels.append(f"{klass.code if klass else '?'}-{section.name}")
    for raw in scope.get("class_ids", []):
        klass = classes.get(uuid.UUID(str(raw)))
        if klass is not None:
            labels.append(klass.display_te if lang == "te" else klass.display_en)
    return ", ".join(sorted(labels)) or cfg.label("scope_all", lang)


def _catalog(session: Session) -> dict[str, Any]:
    return {a.key: a for a in students.attribute_catalog(session)}


def _attr_label(catalog: Mapping[str, Any], key: str | None, lang: Language) -> str | None:
    if key is None:
        return None
    attr = catalog.get(key)
    if attr is None:
        return key
    label: str = attr.label_te if lang == "te" else attr.label_en
    return label


def _build_precheck(
    session: Session, ctx: UserContext, snap: _Snapshot, ids: Sequence[uuid.UUID]
) -> tuple[PrecheckInput, ExportsConfig]:
    cfg = load_config()
    profile = _profile(snap.profile_key or "")
    layout = profile.layout
    lang = snap.language
    catalog = _catalog(session)
    sensitive_fields = {
        k for k in layout.fields if k in catalog and catalog[k].classification == "C3"
    }
    fields = [k for k in layout.fields if k not in cfg.never_exported]
    canon = students.canonical_values(
        session,
        ids,
        set(fields) | {"full_name", "admission_no"},
        include_sensitive=snap.include_sensitive,
    )
    places = _placements(session, ids)
    ready: list[ReadyLine] = []
    for sid in ids:
        per = canon.get(sid, {})
        values: list[str | None] = []
        for key in fields:
            if key in sensitive_fields and not snap.include_sensitive:
                values.append(MASK)
                continue
            cv = per.get(key)
            attr = catalog.get(key)
            values.append(
                _format_value(
                    cv.value if cv is not None else None,
                    key=key,
                    data_type=attr.data_type if attr is not None else "text",
                    date_format=layout.date_format,
                    value_maps=layout.value_maps,
                    cfg=cfg,
                )
            )
        place = places.get(sid)
        name = per["full_name"].value if "full_name" in per else None
        ready.append(
            ReadyLine(
                student_id=sid,
                class_section=place.class_section if place else None,
                sort_key=(*(place.sort if place else ("999999", "", "")), name or "", str(sid)),
                values=tuple(values),
            )
        )
    findings = []
    for f in dq.findings_for_students(
        session, ctx, ids, profile_key=profile.key, limit=cfg.max_findings
    ):
        place = places.get(f.student.id)
        findings.append(
            FindingLine(
                student_id=f.student.id,
                severity=f.severity,
                admission_no=f.student.admission_no,
                student=f.student.display_name,
                class_section=place.class_section if place else None,
                rule_id=f.rule_id,
                field=_attr_label(catalog, f.attribute_key, lang),
                values="; ".join(
                    f"{cfg.source_label(v.source, lang)}: {v.masked or MASK}" for v in f.values
                ),
                explanation=f.explanation.te if lang == "te" else f.explanation.en,
                route="; ".join(r.te if lang == "te" else r.en for r in f.routes),
            )
        )
    data = PrecheckInput(
        language=lang,
        school=tenancy.get_tenant(session).name,
        profile_label=profile.label(lang),
        export_ref=snap.id,
        generated_at=_now_ist(),
        scope_label=_scope_label(session, snap.scope, cfg, lang),
        field_labels=tuple(_attr_label(catalog, k, lang) or k for k in fields),
        findings=tuple(findings),
        ready=tuple(ready),
        sensitive_masked=bool(sensitive_fields) and not snap.include_sensitive,
    )
    return data, cfg


def _student_list(
    session: Session, snap: _Snapshot, ids: Sequence[uuid.UUID]
) -> tuple[Table, Table, ExportsConfig]:
    cfg = load_config()
    layout = cfg.student_list
    lang = snap.language
    catalog = _catalog(session)
    attrs = [c for c in snap.columns if c in catalog and c not in cfg.never_exported]
    if any(catalog[c].classification == "C3" for c in attrs) and not snap.include_sensitive:
        raise _Stop("sensitive_not_allowed")  # the frozen request says otherwise: refuse
    canon = students.canonical_values(
        session, ids, set(attrs) | {"full_name"}, include_sensitive=snap.include_sensitive
    )
    places = _placements(session, ids)
    structure_labels = {"class": "col_class", "section": "col_section", "roll_no": "col_roll_no"}
    header = tuple(
        cfg.label(structure_labels[c], lang)
        if c in structure_labels
        else (_attr_label(catalog, c, lang) or c)
        for c in snap.columns
    )
    keyed: list[tuple[tuple[str, ...], tuple[object, ...]]] = []
    for sid in ids:
        per = canon.get(sid, {})
        place = places.get(sid)
        row: list[object] = []
        for c in snap.columns:
            if c == "class":
                row.append(place.class_code if place else None)
            elif c == "section":
                row.append(place.section if place else None)
            elif c == "roll_no":
                row.append(place.roll_no if place else None)
            else:
                cv = per.get(c)
                row.append(
                    _format_value(
                        cv.value if cv is not None else None,
                        key=c,
                        data_type=catalog[c].data_type if c in catalog else "text",
                        date_format=layout.date_format,
                        value_maps={},
                        cfg=cfg,
                    )
                )
        name = per["full_name"].value if "full_name" in per else ""
        sort = (*(place.sort if place else ("999999", "", "")), name or "", str(sid))
        keyed.append((sort, tuple(row)))
    keyed.sort(key=lambda item: item[0])
    table = Table(
        name=cfg.label("sheet_students", lang),
        header=header,
        rows=tuple(r for _, r in keyed),
    )
    summary = Table(
        name=cfg.label("sheet_summary", lang),
        header=(cfg.label("title_student_list", lang), ""),
        rows=(
            (cfg.label("school", lang), tenancy.get_tenant(session).name),
            (cfg.label("scope", lang), _scope_label(session, snap.scope, cfg, lang)),
            (cfg.label("generated_at", lang), _now_ist()),
            (cfg.label("export_ref", lang), str(snap.id)),
            (cfg.label("students", lang), len(ids)),
            ("", cfg.watermark_text()),
        ),
    )
    return table, summary, cfg


def _build(session: Session, ctx: UserContext, snap: _Snapshot) -> Callable[[PdfRenderer], _Built]:
    """Collect everything inside the transaction; return a closure that makes the files
    outside it (PDF rendering takes seconds)."""
    reach = set(students.list_students_in_scope(session, ctx))
    ids = [i for i in snap.student_ids if i in reach]
    if not ids:
        raise _Stop("no_students")
    if snap.kind == "student_list":
        table, summary, cfg = _student_list(session, snap, ids)
        title = cfg.label("title_student_list", snap.language)

        def make_list(_: PdfRenderer) -> _Built:
            files = []
            for fmt in snap.formats:
                data = (
                    write_csv(table, watermark=cfg.watermark_text())
                    if fmt == "csv"
                    else write_xlsx([table, summary], watermark=cfg.watermark_text(), title=title)
                )
                files.append(_File(fmt, f"students.{fmt}", data))
            return _Built(files=tuple(files), students=len(ids))

        return make_list
    data, cfg = _build_precheck(session, ctx, snap, ids)

    def make_precheck(renderer: PdfRenderer) -> _Built:
        report = build_precheck(data, cfg)
        stem = f"precheck-{snap.profile_key}"
        files = []
        for fmt in snap.formats:
            if fmt == "pdf":
                blob = renderer.render(render_precheck_html(report, cfg))
            else:
                blob = write_xlsx(report.tables, watermark=report.watermark, title=report.title)
            files.append(_File(fmt, f"{stem}.{fmt}", blob))
        return _Built(
            files=tuple(files),
            students=report.counts.students,
            findings=len(data.findings),
            blockers=report.counts.blockers,
        )

    return make_precheck


def _fail(tenant_id: uuid.UUID, export_id: uuid.UUID, code: str) -> bool:
    """Mark the export failed, fail its job, audit and tell the requester (once)."""
    with _worker_session(tenant_id) as session:
        row = repo.get_export(session, export_id, lock=True)
        if row is None or row.status not in LIVE:
            return False
        repo.update_export(
            session,
            export_id,
            {
                "status": "failed",
                "error_code": code,
                "finished_at": repo.now(session),
                "started_at": row.started_at or repo.now(session),
            },
        )
        if row.job_id is not None:
            ops.fail_job(session, row.job_id, code)
        _audit(
            session,
            "export.failed",
            export_id,
            {"kind": row.kind, "error_code": code},
            actor_id=row.requested_by,
        )
        notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=[row.requested_by_membership],
            template_key="export.failed",
            params={"export_id": export_id},
            resource_id=export_id,
            dedupe_key=f"export:{export_id}:failed",
        )
    log.warning("exports.failed", resource_type="export", resource_id=export_id, error_code=code)
    return True


def abandon(tenant_id: uuid.UUID, export_id: uuid.UUID, code: str = "worker_error") -> bool:
    """The worker gave up after its retries: the export shows ``failed``."""
    return _fail(tenant_id, export_id, code)


def run_export(
    tenant_id: uuid.UUID,
    export_id: uuid.UUID,
    *,
    renderer: PdfRenderer | None = None,
) -> str:
    """Worker: build and store the export's files for the member who asked (idempotent: a
    finished export is left alone). Returns the final status. Permanent problems (permission
    revoked, no students left in reach) fail the export; other errors propagate for a retry."""
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
    ctx = member_context(tenant_id, snap.requested_by, snap.membership_id)
    problem = _permission_problem(ctx, snap)
    if problem is not None:
        _fail(tenant_id, export_id, problem)
        return "failed"
    try:
        if snap.kind != "student_list":
            # US-501: the report shows the result of checking now, not of the last run.
            with _worker_session(tenant_id, snap.requested_by) as session:
                dq.run_checks(
                    session, ctx, student_ids=snap.student_ids, profile_key=snap.profile_key
                )
        with _worker_session(tenant_id, snap.requested_by) as session:
            make = _build(session, ctx, snap)
    except _Stop as stop:
        _fail(tenant_id, export_id, stop.code)
        return "failed"
    built = make(renderer or get_renderer())
    return _complete(tenant_id, snap, built)


def _complete(tenant_id: uuid.UUID, snap: _Snapshot, built: _Built) -> str:
    cfg = load_config()
    with _worker_session(tenant_id, snap.requested_by) as session:
        row = repo.get_export(session, snap.id, lock=True)
        if row is None or row.status not in LIVE:
            return row.status if row is not None else "missing"
        rows = []
        for f in built.files:
            key = documents.store_export_file(session, snap.id, f.filename, f.data, f.content_type)
            rows.append(
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "export_id": snap.id,
                    "format": f.format,
                    "object_key": key,
                    "content_type": f.content_type,
                    "size_bytes": len(f.data),
                    "sha256": hashlib.sha256(f.data).digest(),
                }
            )
        repo.insert_files(session, rows)
        now = repo.now(session)
        repo.update_export(
            session,
            snap.id,
            {
                "status": "ready",
                "finished_at": now,
                "expires_at": now + dt.timedelta(days=cfg.retention_days),
            },
        )
        counts = {
            "students": built.students,
            "findings": built.findings,
            "blockers": built.blockers,
        }
        if snap.job_id is not None:
            ops.finish_job(session, snap.job_id, counts)
        _audit(
            session,
            "export.completed",
            snap.id,
            {
                "kind": snap.kind,
                "profile_key": snap.profile_key,
                "formats": list(snap.formats),
                **counts,
                "files": [{"format": r["format"], "file_id": r["id"]} for r in rows],
            },
            actor_id=snap.requested_by,
        )
        notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=[snap.membership_id],
            template_key="export.ready",
            params={"export_id": snap.id, "students": built.students},
            resource_id=snap.id,
            dedupe_key=f"export:{snap.id}:ready",
        )
    log.info(
        "exports.completed",
        resource_type="export",
        resource_id=snap.id,
        count=built.students,
        action=snap.kind,
    )
    return "ready"


# --- retention ------------------------------------------------------------------------------------


def purge_expired(tenant_id: uuid.UUID, *, now: dt.datetime | None = None) -> int:
    """Daily: delete the files of this school's exports 7 days after they were ready (and the
    leftovers of failed ones); the rows stay as the record (docs/05 §13). Audited per export
    (``export.expired``, system actor)."""
    purged = 0
    with _worker_session(tenant_id) as session:
        at = now or repo.now(session)
        for row in repo.due_for_purge(session, now=at, failed_before=at - FAILED_ORPHAN_AGE):
            deleted = documents.delete_export_files(session, row.id)
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
                "export.expired",
                row.id,
                {"kind": row.kind, "objects": deleted},
                actor_type="system",
            )
            purged += 1
    if purged:
        log.info("exports.purged", tenant_id=tenant_id, count=purged)
    return purged


__all__ = [
    "BOARD",
    "EXPORT_PERMISSIONS",
    "GENERATE_EVENT",
    "GENERATE_TASK",
    "PORTAL",
    "PURGE_TASK",
    "RENDER_EVENT",
    "RENDER_TASK",
    "STUDENT_EXPORT",
    "abandon",
    "download_url",
    "get_export",
    "list_exports",
    "list_profiles",
    "member_context",
    "purge_expired",
    "request_precheck",
    "request_student_list",
    "run_export",
    "student_ids_digest",
]
