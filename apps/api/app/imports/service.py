"""Imports public API: spreadsheet onboarding (US-401; FR-IMP-001..007; SEC-013, SEC-015,
SEC-017; NFR-PERF-004). Other modules call only these functions.

Flow (docs/04 §7.1)::

    POST /imports          create_import()      batch "uploaded" + job + outbox event
                                                import.parse_requested
    worker imports.parse   run_parse()          read file (never evaluating formulas), find the
                                                header, suggest a mapping (or reuse the school's
                                                template for this header layout), then validate
                                                when the admission number column is mapped
    PUT  .../mapping       set_mapping()        the office corrects the mapping ("parsed")
    POST .../validate      request_validation() worker imports.validate -> run_validate()
    POST .../commit        request_commit()     worker imports.commit -> run_commit(): the file is
                                                re-read and re-validated, then ONE transaction
                                                creates students / records values through
                                                students.service, audits, queues
                                                ``import.committed`` and notifies the importer
    POST .../revert        revert()             within 24 h, if nothing depends on the batch
    beat imports.purge_raw_files                raw files deleted 90 days after commit

Data minimisation: ``sis.import_rows.parsed`` never holds C3 values (only their keys); commit
re-reads them from the SSE-KMS raw file and the student service encrypts them. A full Aadhaar
number anywhere in a row is a row error and is never stored (the error names the column only).
Workers act for the member who asked (their permissions and scope are re-resolved at job time).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.authz.resolver import build_snapshot
from app.core import purge as purging
from app.core import retention
from app.core.db import tenant_session
from app.core.errors import Conflict, DomainError, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import mask_aadhaar
from app.core.spreadsheet import (
    CSV_MIME,
    XLSX_MIME,
    column_letter,
    display_text,
    write_csv,
    write_xlsx,
)
from app.documents import service as documents
from app.identity import service as identity
from app.imports import cells
from app.imports import repository as repo
from app.imports.config import import_config
from app.imports.mapping import (
    IGNORE,
    header_signature,
    mapping_from_template,
    suggest,
    template_payload,
)
from app.imports.models import ImportBatch, ImportMappingTemplate, ImportRow
from app.imports.schemas import (
    ColumnOut,
    CommitIn,
    ImportCreate,
    ImportOut,
    ImportRowOut,
    ImportSheetOut,
    ImportSummary,
    Issue,
    MappingIn,
    RowEditIn,
    SheetCellOut,
    SheetColumnOut,
    SheetEditOut,
    SheetFormat,
    SheetReadOnly,
    SheetRowOut,
    TemplateCreate,
    TemplateOut,
)
from app.imports.sheet import (
    Cell,
    FileKind,
    Sheet,
    SheetError,
    SheetRow,
    read_sheet,
    with_edits,
)
from app.imports.validation import (
    AADHAAR_CODE,
    AADHAAR_MESSAGE_KEY,
    ANCHOR_SOURCE,
    AttributeSpec,
    ExistingStudent,
    RowResult,
    SectionInfo,
    ValidationContext,
    ValidationResult,
    admission_key,
    allowed_targets,
    is_full_aadhaar,
    issue,
    mapping_problems,
    validate_sheet,
)
from app.imports.values import ClassInfo, ClassResolver, cell_text
from app.notifications import service as notifications
from app.ops import service as ops
from app.students import rotation as key_rotation
from app.students import service as students
from app.students.schemas import StudentCreate, ValueIn
from app.tenancy import service as tenancy

log = get_logger(__name__)

RUN: Final = "import.run"
COMMIT: Final = "import.commit"
CREATE_STUDENT: Final = "student.create"
READ_SENSITIVE: Final = "student.read_sensitive"

PARSE_EVENT: Final = "import.parse_requested"
VALIDATE_EVENT: Final = "import.validate_requested"
COMMIT_EVENT: Final = "import.commit_requested"
COMMITTED_EVENT: Final = "import.committed"
REVERTED_EVENT: Final = "import.reverted"
PARSE_TASK: Final = "imports.parse"
VALIDATE_TASK: Final = "imports.validate"
COMMIT_TASK: Final = "imports.commit"
PURGE_TASK: Final = "imports.purge_raw_files"
ops.register_outbox_route(PARSE_EVENT, PARSE_TASK)
ops.register_outbox_route(VALIDATE_EVENT, VALIDATE_TASK)
ops.register_outbox_route(COMMIT_EVENT, COMMIT_TASK)

IST: Final = ZoneInfo("Asia/Kolkata")
MIME_KINDS: Final[dict[str, FileKind]] = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/csv": "csv",
}
_BATCH_IN_KEY: Final = re.compile(r"^t/[0-9a-f-]{36}/imports/([0-9a-f-]{36})/raw\.(?:xlsx|csv)$")
EDITABLE: Final = ("parsed", "validated")


# --- plumbing -------------------------------------------------------------------------------------


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
    batch_id: uuid.UUID,
    summary: Mapping[str, Any],
    *,
    resource_type: str = "import_batch",
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=batch_id,
        summary=summary,
        request_id=_request_id(),
    )


def _today() -> dt.date:
    return dt.datetime.now(IST).date()


def _uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _not_found() -> NotFound:
    return NotFound("Import not found")


def _visible(
    session: Session,
    ctx: UserContext,
    batch_id: uuid.UUID,
    permission: str,
    *,
    lock: bool = False,
) -> ImportBatch:
    """The batch if the caller may reach it: school-wide holders see every batch of the school,
    scoped holders only their own (404 otherwise, never 403)."""
    batch = repo.get_batch(session, batch_id, lock=lock)
    if batch is None:
        raise _not_found()
    if not ctx.scope_for(permission).school_wide and batch.created_by != ctx.user_id:
        raise _not_found()
    return batch


def member_context(
    tenant_id: uuid.UUID, user_id: uuid.UUID, membership_id: uuid.UUID
) -> UserContext:
    """The importer's CURRENT permissions and scopes, for work done on their behalf in a
    worker (a revoked role stops a queued job). Opens its own short transaction."""
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


# --- output ---------------------------------------------------------------------------------------


def _columns_out(batch: ImportBatch) -> list[ColumnOut]:
    return [
        ColumnOut(
            index=int(c["index"]),
            header=str(c.get("header", "")),
            suggested=c.get("suggested"),
            score=int(c.get("score", 0)),
            target=batch.mapping.get(str(c["index"])),
        )
        for c in batch.columns
    ]


def _out(session: Session, batch: ImportBatch) -> ImportOut:
    stats = {k: int(v) for k, v in (batch.stats or {}).items() if isinstance(v, int)}
    now = repo.now(session)
    return ImportOut(
        id=batch.id,
        kind=batch.kind,
        source=batch.source,
        status=batch.status,
        document_id=batch.document_id,
        file_kind=batch.file_kind,
        header_row=batch.header_row,
        columns=_columns_out(batch),
        mapping_template_id=batch.mapping_template_id,
        stats=stats,
        row_count=batch.row_count,
        error_count=batch.error_count,
        error_code=batch.error_code,
        job_id=batch.job_id,
        created_by=batch.created_by,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        committed_at=batch.committed_at,
        revert_deadline=batch.revert_deadline,
        reverted_at=batch.reverted_at,
        raw_file_deleted_at=batch.raw_file_deleted_at,
        version=batch.version,
        can_commit=batch.status == "validated" and stats.get("valid", 0) > 0,
        can_revert=batch.status == "committed"
        and batch.revert_deadline is not None
        and batch.revert_deadline > now,
    )


def _summary(batch: ImportBatch) -> ImportSummary:
    return ImportSummary(
        id=batch.id,
        source=batch.source,
        status=batch.status,
        row_count=batch.row_count,
        error_count=batch.error_count,
        created_by=batch.created_by,
        created_at=batch.created_at,
        committed_at=batch.committed_at,
        reverted_at=batch.reverted_at,
    )


def _row_out(row: ImportRow) -> ImportRowOut:
    parsed = row.parsed or {}
    return ImportRowOut(
        row_no=row.row_no,
        status=row.status,
        action=row.action,
        student_id=row.student_id,
        admission_no=parsed.get("admission_no"),
        values=dict(parsed.get("values") or {}),
        sensitive=list(parsed.get("sensitive") or []),
        section_id=_uuid(parsed.get("section_id")),
        class_section=parsed.get("class_label"),
        roll_no=parsed.get("roll_no"),
        errors=[Issue(**e) for e in row.errors],
        warnings=[Issue(**w) for w in row.warnings],
    )


def _template_out(t: ImportMappingTemplate) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        name=t.name,
        source=t.source,
        headers=list(t.headers),
        mapping=dict(t.mapping),
        created_by=t.created_by,
        created_at=t.created_at,
        last_used_at=t.last_used_at,
        version=t.version,
    )


# --- catalog and structure ------------------------------------------------------------------------


def _specs(session: Session) -> dict[str, AttributeSpec]:
    """The student catalog's attributes with their validation rules (students.attribute_rules):
    imports check cells with the same limits and formats the student service enforces."""
    return {
        a.key: AttributeSpec(
            key=a.key,
            data_type=a.data_type,
            classification=a.classification,
            is_identity=a.is_identity,
            allowed_sources=a.allowed_sources,
            allowed_values=a.allowed_values,
            max_length=a.max_length,
            pattern=a.pattern,
            not_future=a.not_future,
        )
        for a in students.attribute_rules(session)
    }


@dataclass(frozen=True, slots=True)
class _Structure:
    year_id: uuid.UUID | None
    sections: list[SectionInfo]
    resolver: ClassResolver


def _structure(session: Session) -> _Structure:
    cfg = import_config()
    year = tenancy.get_current_academic_year(session)
    classes = tenancy.list_classes(session)
    codes = {c.id: c.code for c in classes}
    sections = tenancy.list_sections(session, academic_year_id=year.id) if year else []
    return _Structure(
        year_id=year.id if year else None,
        sections=[
            SectionInfo(
                id=str(s.id),
                class_id=str(s.class_id),
                name=s.name,
                label=f"{codes.get(s.class_id, '?')}-{s.name}",
            )
            for s in sections
        ],
        resolver=ClassResolver(
            [ClassInfo(str(c.id), c.code, c.display_en, c.display_te) for c in classes],
            cfg.class_aliases,
            cfg.class_noise_words,
        ),
    )


def _sheet_admission_keys(sheet: Sheet, mapping: Mapping[str, str]) -> set[str]:
    index = next((int(k) for k, v in mapping.items() if v == "admission_no"), None)
    if index is None:
        return set()
    keys: set[str] = set()
    for row in sheet.rows:
        text = cell_text(row.cell(index).value)
        if text:
            keys.add(admission_key(text))
    return keys


def _existing_students(
    session: Session,
    ctx: UserContext,
    wanted: set[str],
    specs: Mapping[str, AttributeSpec],
    structure: _Structure,
    *,
    need_sections: bool,
) -> dict[str, ExistingStudent]:
    """Students (in the importer's reach) whose admission numbers appear in the file."""
    if not wanted:
        return {}
    ids = students.list_students_in_scope(session, ctx)
    canonical = students.canonical_values(session, ids, ["admission_no"])
    matched: dict[str, uuid.UUID] = {}
    for sid, values in canonical.items():
        adm = values.get("admission_no")
        if adm is not None and adm.value and admission_key(adm.value) in wanted:
            matched[admission_key(adm.value)] = sid
    if not matched:
        return {}
    identity_keys = [k for k, s in specs.items() if s.is_identity]
    recorded = students.source_values(session, list(matched.values()), identity_keys)
    section_of: dict[uuid.UUID, str] = {}
    if need_sections and structure.year_id is not None:
        wanted_ids = set(matched.values())
        for section in structure.sections:
            for sid in students.list_students_in_scope(
                session, ctx, section_ids=[uuid.UUID(section.id)]
            ):
                if sid in wanted_ids:
                    section_of[sid] = section.id
    out: dict[str, ExistingStudent] = {}
    for key, sid in matched.items():
        anchors = {
            attr: v[ANCHOR_SOURCE].value
            for attr, v in recorded.get(sid, {}).items()
            if ANCHOR_SOURCE in v and v[ANCHOR_SOURCE].value is not None
        }
        out[key] = ExistingStudent(
            id=str(sid),
            section_id=section_of.get(sid),
            anchor_values={k: str(v) for k, v in anchors.items()},
        )
    return out


def _validation_context(
    session: Session, ctx: UserContext, batch: ImportBatch, sheet: Sheet, permission: str
) -> ValidationContext:
    specs = _specs(session)
    structure = _structure(session)
    grant = ctx.scope_for(permission)
    allowed: frozenset[str] | None = None
    if not grant.school_wide:
        allowed = frozenset(
            s.id
            for s in structure.sections
            if uuid.UUID(s.id) in grant.section_ids or uuid.UUID(s.class_id) in grant.class_ids
        )
    targets = set(batch.mapping.values())
    return ValidationContext(
        source=batch.source,
        specs=specs,
        classes=structure.resolver,
        sections=structure.sections,
        has_current_year=structure.year_id is not None,
        allowed_sections=allowed,
        can_create=ctx.has(CREATE_STUDENT),
        existing=_existing_students(
            session,
            ctx,
            _sheet_admission_keys(sheet, batch.mapping),
            specs,
            structure,
            need_sections=bool({"class", "section", "class_section"} & targets)
            or allowed is not None,
        ),
        config=import_config(),
        today=_today(),
    )


def _row_values(result: RowResult, specs: Mapping[str, AttributeSpec]) -> dict[str, Any]:
    return {
        "id": new_id(),
        "row_no": result.row_no,
        "parsed": result.parsed(specs),
        "errors": result.errors,
        "warnings": result.warnings,
        "status": result.status,
        "action": result.action,
        "student_id": _uuid(result.student_id),
        "created_student": False,
        "student_version": None,
    }


def _store_validation(
    session: Session, batch: ImportBatch, result: ValidationResult, specs: Mapping[str, Any]
) -> ImportBatch:
    tenant_id = repo.current_tenant_id(session)
    repo.replace_rows(session, tenant_id, batch.id, [_row_values(r, specs) for r in result.rows])
    updated = repo.update_batch(
        session,
        batch.id,
        status="validated",
        stats=result.stats,
        row_count=len(result.rows),
        error_count=result.error_rows,
        error_code=None,
    )
    if updated is None:  # pragma: no cover - row locked by the caller
        raise _not_found()
    return updated


# --- file -----------------------------------------------------------------------------------------


def _raw_file(session: Session, batch: ImportBatch) -> tuple[bytes, FileKind]:
    """The batch's raw file (SHA-256 checked); ``SheetError`` for any problem."""
    if batch.document_id is None:
        raise SheetError("file_missing")
    try:
        obj = documents.document_object(session, batch.document_id)
    except NotFound as exc:
        raise SheetError("file_missing") from exc
    except Conflict as exc:
        raise SheetError(exc.code) from exc
    kind = MIME_KINDS.get(obj.mime_type)
    if kind is None:
        raise SheetError("unsupported_file_type")
    try:
        data = documents.read_document_object(session, obj)
    except (Conflict, NotFound) as exc:
        raise SheetError("file_changed" if isinstance(exc, Conflict) else "file_missing") from exc
    return data, kind


def _load_sheet(tenant_id: uuid.UUID, user_id: uuid.UUID, batch_id: uuid.UUID) -> Sheet:
    """Read and parse the batch's raw file with its staged cell edits applied (FR-IMP-008):
    parsing, validation and commit all see the edited sheet; the file itself never changes."""
    with tenant_session(tenant_id, user_id) as s:
        batch = repo.get_batch(s, batch_id)
        if batch is None:
            raise SheetError("file_missing")
        data, kind = _raw_file(s, batch)
        edits = cells.current_values(s, batch_id)
    return with_edits(read_sheet(data, kind, import_config().limits), edits)


# --- create, read, mapping (API) ------------------------------------------------------------------


def _batch_id_for(session: Session, object_key: str) -> uuid.UUID:
    """Reuse the storage batch id of the upload (docs/04 §8.2 ``imports/<batch_id>/raw``) when it
    is free, so the batch and its raw file share one id."""
    match = _BATCH_IN_KEY.match(object_key)
    if match is not None:
        candidate = uuid.UUID(match.group(1))
        if not repo.batch_id_taken(session, candidate):
            return candidate
    return new_id()


def create_import(session: Session, ctx: UserContext, data: ImportCreate) -> ImportOut:
    """Start importing an uploaded, scanned ``import_file`` document (``import.run``; 202).

    The document must be visible to the caller (404 otherwise), XLSX or CSV (415) and at most
    10 MB (413, FR-IMP-001). Parsing runs in a worker. Audit: ``import.created``.
    """
    if not documents.is_visible(session, ctx, data.document_id):
        raise NotFound("Document not found")
    try:
        obj = documents.document_object(session, data.document_id)
    except Conflict as exc:
        raise Conflict(
            "The file is still being checked for viruses. Try again in a minute.",
            code="document_not_ready",
        ) from exc
    if obj.purpose != "import_file":
        raise ValidationFailed([issue("document_id", "not_an_import_file")])
    kind = MIME_KINDS.get(obj.mime_type)
    if kind is None:
        raise documents.UnsupportedFileType("Import an XLSX or CSV file.")
    if obj.size_bytes > import_config().limits.max_file_bytes:
        raise documents.FileTooLarge("Import files can be at most 10 MB.")
    tenant_id = repo.current_tenant_id(session)
    batch_id = _batch_id_for(session, obj.object_key)
    job = ops.start_job(
        session,
        task_name=PARSE_TASK,
        idempotency_key=f"{PARSE_TASK}:{batch_id}",
        created_by=ctx.user_id,
    )
    with _db_errors():
        batch = repo.insert_batch(
            session,
            id=batch_id,
            tenant_id=tenant_id,
            kind=data.kind,
            source=data.source,
            status="uploaded",
            document_id=data.document_id,
            file_kind=kind,
            job_id=job.id,
            created_by=ctx.user_id,
        )
    _audit(
        session,
        "import.created",
        batch.id,
        {
            "document_id": data.document_id,
            "source": data.source,
            "kind": data.kind,
            "file_kind": kind,
        },
    )
    ops.enqueue_event(
        session,
        PARSE_EVENT,
        {
            "batch_id": batch.id,
            "job_id": job.id,
            "user_id": ctx.user_id,
            "membership_id": ctx.membership_id,
        },
    )
    log.info("imports.batch.created", resource_type="import_batch", resource_id=batch.id)
    return _out(session, batch)


def list_imports(
    session: Session,
    ctx: UserContext,
    *,
    limit: int,
    cursor: str | None = None,
    status: str | None = None,
) -> Page[ImportSummary]:
    """The school's imports, newest first (scoped holders: their own)."""
    before: uuid.UUID | None = None
    after = decode_cursor(cursor)
    if after is not None:
        try:
            before = uuid.UUID(str(after.get("k")))
        except ValueError as exc:
            raise ValidationFailed([issue("cursor", "invalid", "errors.invalid_cursor")]) from exc
    own = None if ctx.scope_for(RUN).school_wide else ctx.user_id
    rows = repo.list_batches(
        session, limit=limit + 1, before_id=before, created_by=own, status=status
    )
    page = rows[:limit]
    more = len(rows) > limit
    return Page[ImportSummary](
        data=[_summary(b) for b in page],
        next_cursor=encode_cursor({"k": str(page[-1].id)}) if more and page else None,
    )


def get_import(session: Session, ctx: UserContext, batch_id: uuid.UUID) -> ImportOut:
    return _out(session, _visible(session, ctx, batch_id, RUN))


def list_rows(
    session: Session,
    ctx: UserContext,
    batch_id: uuid.UUID,
    *,
    status: str | None,
    limit: int,
    cursor: str | None = None,
) -> Page[ImportRowOut]:
    """Rows of a batch in file order (``status=error`` for the rows to fix; ``warning``)."""
    batch = _visible(session, ctx, batch_id, RUN)
    after_row: int | None = None
    decoded = decode_cursor(cursor)
    if decoded is not None:
        value = decoded.get("r")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValidationFailed([issue("cursor", "invalid", "errors.invalid_cursor")])
        after_row = value
    rows = repo.list_rows(
        session,
        batch.id,
        status=None if status in (None, "warning") else status,
        with_warnings=status == "warning",
        after_row_no=after_row,
        limit=limit + 1,
    )
    page = rows[:limit]
    more = len(rows) > limit
    return Page[ImportRowOut](
        data=[_row_out(r) for r in page],
        next_cursor=encode_cursor({"r": page[-1].row_no}) if more and page else None,
    )


def set_mapping(
    session: Session,
    ctx: UserContext,
    batch_id: uuid.UUID,
    data: MappingIn,
    *,
    expected_version: int,
) -> ImportOut:
    """Replace the column mapping (``If-Match``). Previous validation results are cleared; the
    batch goes back to ``parsed`` until it is validated again. Audit: ``import.mapping_changed``."""
    batch = _visible(session, ctx, batch_id, RUN, lock=True)
    if batch.version != expected_version:
        raise PreconditionFailed("The import was changed meanwhile. Reload and try again.")
    if batch.status not in EDITABLE:
        raise Conflict("This import can no longer be changed.", code="import_not_editable")
    mapping: dict[str, str] = {}
    problems: list[dict[str, str]] = []
    for i, column in enumerate(data.columns):
        if str(column.index) in mapping:
            problems.append(issue(f"columns.{i}.index", "duplicate_column"))
        if column.target != IGNORE:
            mapping[str(column.index)] = column.target
    specs = _specs(session)
    problems += mapping_problems(mapping, len(batch.columns), specs, batch.source)
    if not ctx.has(READ_SENSITIVE):
        # A restricted column may go to "ignore" or another restricted field, never to a field
        # whose checked values the caller could then read (audit 2026-10-06 R-06).
        restricted = _restricted_columns(batch, len(batch.columns), specs)
        for i, column in enumerate(data.columns):
            spec = specs.get(column.target)
            if (
                column.index in restricted
                and column.target != IGNORE
                and not (spec and spec.sensitive)
            ):
                problems.append(issue(f"columns.{i}.target", "column_restricted"))
    if problems:
        raise ValidationFailed(problems)
    repo.replace_rows(session, batch.tenant_id, batch.id, [])
    updated = repo.update_batch(
        session,
        batch.id,
        columns=_sticky_restrictions(batch, mapping, specs),
        mapping=mapping,
        mapping_template_id=None,
        status="parsed",
        error_count=0,
        row_count=0,
        stats={},
        error_code=None,
    )
    if updated is None:  # pragma: no cover - locked above
        raise _not_found()
    _audit(
        session,
        "import.mapping_changed",
        batch.id,
        {"columns": len(mapping), "targets": sorted(set(mapping.values()))},
    )
    return _out(session, updated)


def _start_job(session: Session, ctx: UserContext, task: str, batch_id: uuid.UUID) -> ops.JobRun:
    return ops.start_job(
        session,
        task_name=task,
        idempotency_key=f"{task}:{batch_id}:{new_id()}",
        created_by=ctx.user_id,
    )


def request_validation(session: Session, ctx: UserContext, batch_id: uuid.UUID) -> ImportOut:
    """Validate every row in a worker (202). Needs the admission number column mapped."""
    batch = _visible(session, ctx, batch_id, RUN, lock=True)
    if batch.status not in EDITABLE:
        raise Conflict("This import is not ready to be checked.", code="import_not_editable")
    if "admission_no" not in batch.mapping.values():
        raise ValidationFailed([issue("columns", "admission_no_not_mapped")])
    job = _start_job(session, ctx, VALIDATE_TASK, batch.id)
    updated = repo.update_batch(
        session, batch.id, status="validating", job_id=job.id, error_code=None
    )
    if updated is None:  # pragma: no cover
        raise _not_found()
    ops.enqueue_event(
        session,
        VALIDATE_EVENT,
        {
            "batch_id": batch.id,
            "job_id": job.id,
            "user_id": ctx.user_id,
            "membership_id": ctx.membership_id,
        },
    )
    return _out(session, updated)


def request_commit(
    session: Session, ctx: UserContext, batch_id: uuid.UUID, data: CommitIn
) -> ImportOut:
    """Commit a validated batch in a worker (``import.commit``; 202). All-or-nothing: with
    errors the commit is refused unless ``skip_error_rows`` (FR-IMP-004)."""
    batch = _visible(session, ctx, batch_id, COMMIT, lock=True)
    if batch.status != "validated":
        raise Conflict("Check the file before adding it.", code="import_not_validated")
    valid = int((batch.stats or {}).get("valid", 0))
    if valid == 0:
        raise Conflict("No row is ready to add.", code="nothing_to_commit")
    if batch.error_count and not data.skip_error_rows:
        raise Conflict(
            "Some rows have errors. Fix them in the file, or add only the valid rows.",
            code="import_has_errors",
        )
    job = _start_job(session, ctx, COMMIT_TASK, batch.id)
    updated = repo.update_batch(
        session, batch.id, status="committing", job_id=job.id, error_code=None
    )
    if updated is None:  # pragma: no cover
        raise _not_found()
    _audit(
        session,
        "import.commit_requested",
        batch.id,
        {
            "valid_rows": valid,
            "error_rows": batch.error_count,
            "skip_error_rows": data.skip_error_rows,
        },
    )
    ops.enqueue_event(
        session,
        COMMIT_EVENT,
        {
            "batch_id": batch.id,
            "job_id": job.id,
            "user_id": ctx.user_id,
            "membership_id": ctx.membership_id,
            "skip_error_rows": data.skip_error_rows,
        },
    )
    return _out(session, updated)


# --- staged sheet: view, edit, download (FR-IMP-008, FR-IMP-009) ---------------------------------

NOT_READY: Final = ("uploaded", "parsing")
IN_PROGRESS: Final = ("validating", "committing", "reverting")
_EDIT_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f]")
_READ_ONLY_MESSAGES: Final[dict[str, str]] = {
    "committed": (
        "These rows were added to the student records, so the sheet can no longer be edited. "
        "Correct a record on the student's profile or with a change request."
    ),
    "reverted": "This import was reverted. Upload the file again to import it.",
    "in_progress": "The file is being checked or added right now. Try again in a minute.",
    "failed": "This import stopped with an error. Upload the file again.",
}


@dataclass(frozen=True, slots=True)
class SheetFile:
    """A generated download: bytes in memory, never stored."""

    filename: str
    media_type: str
    content: bytes


def _read_only_reason(batch: ImportBatch) -> SheetReadOnly | None:
    if batch.status in EDITABLE:
        return None
    if batch.status in ("committed", "reverted", "failed"):
        return batch.status  # type: ignore[return-value]  # literal members checked above
    return "in_progress"


def _staged_sheet(
    session: Session, batch: ImportBatch
) -> tuple[Sheet, dict[cells.CellKey, str | None]]:
    """The raw file with the batch's current cell edits applied, in the caller's transaction."""
    if batch.status in NOT_READY:
        raise Conflict(
            "The file is still being read. Try again in a minute.", code="import_not_ready"
        )
    try:
        data, kind = _raw_file(session, batch)
        sheet = read_sheet(data, kind, import_config().limits)
    except SheetError as exc:
        if exc.code == "file_missing":
            raise Conflict(
                "The uploaded file is no longer kept (it is deleted after the school's "
                "retention period, 90 days unless changed).",
                code="file_missing",
            ) from exc
        raise Conflict(
            "The uploaded file cannot be read. Upload the file again.", code=exc.code
        ) from exc
    edits = cells.current_values(session, batch.id)
    return with_edits(sheet, edits), edits


def _restricted_columns(
    batch: ImportBatch, width: int, specs: Mapping[str, AttributeSpec]
) -> set[int]:
    """Columns that fill a restricted (C3) field, or whose header was suggested for one (even
    when the office chose not to import them): never shown or edited in the sheet."""
    out: set[int] = set()
    for key, target in batch.mapping.items():
        spec = specs.get(target)
        if key.isdigit() and int(key) < width and spec is not None and spec.sensitive:
            out.add(int(key))
    for column in batch.columns or ():
        index, suggested = column.get("index"), column.get("suggested")
        spec = specs.get(suggested) if isinstance(suggested, str) else None
        sticky = column.get("restricted") is True
        if isinstance(index, int) and 0 <= index < width and (sticky or (spec and spec.sensitive)):
            out.add(index)
    return out


def _sticky_restrictions(
    batch: ImportBatch, mapping: Mapping[str, str], specs: Mapping[str, AttributeSpec]
) -> list[dict[str, Any]]:
    """The batch's columns with ``restricted`` set on every column that was ever mapped to a
    restricted (C3) field, by the old or the new mapping. Unmapping such a column, or mapping it
    to a C2 field, must not show its values to someone without ``student.read_sensitive``
    (audit 2026-10-06 R-06)."""
    sensitive = {
        int(k)
        for m in (batch.mapping, mapping)
        for k, target in m.items()
        if k.isdigit() and (spec := specs.get(target)) is not None and spec.sensitive
    }
    return [
        {**c, "restricted": True} if c.get("index") in sensitive else dict(c)
        for c in batch.columns or ()
    ]


def _sheet_cell(cell: Cell, *, restricted: bool, edited: bool) -> SheetCellOut:
    if restricted:
        return SheetCellOut(value=None, edited=edited, restricted=True, formula=False)
    text = display_text(cell.value)
    return SheetCellOut(
        value=mask_aadhaar(text) if text is not None else None,
        edited=edited,
        restricted=False,
        formula=cell.formula,
    )


def _sheet_row(
    row: SheetRow,
    width: int,
    stored: ImportRow | None,
    restricted: set[int],
    edited: set[tuple[int, int]],
) -> SheetRowOut:
    return SheetRowOut(
        row_no=row.row_no,
        cells=[
            _sheet_cell(row.cell(i), restricted=i in restricted, edited=(row.row_no, i) in edited)
            for i in range(width)
        ],
        status=stored.status if stored is not None else None,
        errors=[Issue(**e) for e in stored.errors] if stored is not None else [],
        warnings=[Issue(**w) for w in stored.warnings] if stored is not None else [],
    )


def _sheet_offset(cursor: str | None) -> int:
    decoded = decode_cursor(cursor)
    if decoded is None:
        return 0
    value = decoded.get("o")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationFailed([issue("cursor", "invalid", "errors.invalid_cursor")])
    return value


def get_sheet(
    session: Session,
    ctx: UserContext,
    batch_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
) -> ImportSheetOut:
    """A page of the staged sheet: every uploaded column (with the field it fills), the cells
    with staged edits applied, and each row's check result (``import.run``). Restricted (C3)
    columns show no values; Aadhaar-like numbers are masked (invariant 4)."""
    batch = _visible(session, ctx, batch_id, RUN)
    sheet, edits = _staged_sheet(session, batch)
    offset = _sheet_offset(cursor)
    width = len(sheet.headers)
    specs = _specs(session)
    restricted = _restricted_columns(batch, width, specs)
    reason = _read_only_reason(batch)
    page = sheet.rows[offset : offset + limit]
    stored = {r.row_no: r for r in repo.rows_by_no(session, batch.id, [r.row_no for r in page])}
    edited = set(edits)
    more = offset + limit < len(sheet.rows)
    return ImportSheetOut(
        import_id=batch.id,
        status=batch.status,
        version=batch.version,
        editable=reason is None,
        read_only_reason=reason,
        header_row=sheet.header_row,
        total_rows=len(sheet.rows),
        offset=offset,
        edited_cells=len(edits),
        columns=[
            SheetColumnOut(
                index=i,
                letter=column_letter(i),
                header=mask_aadhaar(header)[:100],
                target=batch.mapping.get(str(i)),
                restricted=i in restricted,
                editable=reason is None and i not in restricted,
            )
            for i, header in enumerate(sheet.headers)
        ],
        data=[_sheet_row(r, width, stored.get(r.row_no), restricted, edited) for r in page],
        next_cursor=encode_cursor({"o": offset + limit}) if more else None,
    )


def _edit_problems(data: RowEditIn, width: int, restricted: set[int]) -> list[dict[str, str]]:
    """Field errors for a row edit; values are never echoed (invariant 4 and 5)."""
    limits = import_config().limits
    problems: list[dict[str, str]] = []
    seen: set[int] = set()
    for i, change in enumerate(data.cells):
        where = f"cells.{i}"
        if change.column in seen:
            problems.append(issue(f"{where}.column", "duplicate_column"))
        seen.add(change.column)
        if change.column >= width:
            problems.append(issue(f"{where}.column", "unknown_column"))
        elif change.column in restricted:
            problems.append(issue(f"{where}.column", "column_restricted"))
        value = change.value
        if value is None:
            continue
        if is_full_aadhaar(value):
            problems.append(issue(f"{where}.value", AADHAAR_CODE, AADHAAR_MESSAGE_KEY))
        elif _EDIT_CONTROL_RE.search(value):
            problems.append(issue(f"{where}.value", "control_characters"))
        elif len(value) > limits.max_cell_chars:
            problems.append(issue(f"{where}.value", "too_long"))
    return problems


def _refresh_checks(
    session: Session,
    ctx: UserContext,
    batch: ImportBatch,
    sheet: Sheet,
    specs: Mapping[str, AttributeSpec],
) -> tuple[ValidationResult, list[int]]:
    """Re-check the edited sheet with the existing validation (FR-IMP-003) and store the rows
    whose result changed; returns the result and the changed row numbers."""
    context = _validation_context(session, ctx, batch, sheet, RUN)
    result = validate_sheet(sheet, batch.mapping, context)
    stored = {r.row_no: r for r in repo.all_rows(session, batch.id)}
    if set(stored) != {r.row_no for r in result.rows}:
        rows = [_row_values(r, specs) for r in result.rows]
        repo.replace_rows(session, batch.tenant_id, batch.id, rows)
        return result, [r.row_no for r in result.rows]
    changed: list[int] = []
    for r in result.rows:
        values = _row_values(r, specs)
        old = stored[r.row_no]
        if (
            old.parsed != values["parsed"]
            or old.errors != values["errors"]
            or old.warnings != values["warnings"]
            or old.status != values["status"]
            or old.action != values["action"]
            or old.student_id != values["student_id"]
        ):
            repo.update_row(
                session,
                batch.id,
                r.row_no,
                parsed=values["parsed"],
                errors=values["errors"],
                warnings=values["warnings"],
                status=values["status"],
                action=values["action"],
                student_id=values["student_id"],
            )
            changed.append(r.row_no)
    return result, changed


def edit_row(
    session: Session,
    ctx: UserContext,
    batch_id: uuid.UUID,
    row_no: int,
    data: RowEditIn,
    *,
    expected_version: int,
) -> SheetEditOut:
    """Change cells of one staged row before the import is added (``import.run``; ``If-Match``
    with the import's ETag; FR-IMP-008).

    Only a ``parsed`` or ``validated`` import can be edited (409 ``import_not_editable`` once
    it was added, reverted, or while a check runs): official records are never edited here
    (invariant 6). The raw file is kept as uploaded; each edit is stored encrypted with who and
    when (``sis.import_cell_edits``) and applied whenever the file is read, so the commit adds
    the edited values. A full Aadhaar number, control characters, over-long text, restricted
    (C3) or unknown columns answer 422 without storing anything. A checked import re-checks
    the edited sheet with the same rules as ``validate`` and stores the rows whose result
    changed. Audit: ``import.cell_edited`` per cell (row, column and field only, never values).
    """
    batch = _visible(session, ctx, batch_id, RUN, lock=True)
    if batch.version != expected_version:
        raise PreconditionFailed(
            "The import was changed meanwhile. Reload the sheet and try again."
        )
    reason = _read_only_reason(batch)
    if reason is not None:
        raise Conflict(_READ_ONLY_MESSAGES[reason], code="import_not_editable")
    sheet, edits = _staged_sheet(session, batch)
    width = len(sheet.headers)
    specs = _specs(session)
    restricted = _restricted_columns(batch, width, specs)
    problems = _edit_problems(data, width, restricted)
    if problems:
        raise ValidationFailed(problems)
    row = next((r for r in sheet.rows if r.row_no == row_no), None)
    if row is None:
        raise NotFound("Row not found")
    changes = {
        c.column: c.value for c in data.cells if display_text(row.cell(c.column).value) != c.value
    }
    if not changes:
        stored = repo.rows_by_no(session, batch.id, [row_no])
        return SheetEditOut(
            row=_sheet_row(row, width, stored[0] if stored else None, restricted, set(edits)),
            version=batch.version,
            status=batch.status,
            row_count=batch.row_count,
            error_count=batch.error_count,
            changed_rows=[],
        )
    new_version = batch.version + 1
    records: list[dict[str, Any]] = []
    for column, value in changes.items():
        edit_id = new_id()
        # The value before comes from the uploaded file: an Aadhaar number in it is masked
        # before the history stores it (invariant 4; the new value was refused above).
        before = display_text(row.cell(column).value)
        old = mask_aadhaar(before) if before is not None else None
        old_blob, old_key = cells.encrypt(session, old, column=cells.OLD_COLUMN, edit_id=edit_id)
        new_blob, new_key = cells.encrypt(session, value, column=cells.NEW_COLUMN, edit_id=edit_id)
        records.append(
            {
                "id": edit_id,
                "tenant_id": batch.tenant_id,
                "batch_id": batch.id,
                "batch_version": new_version,
                "row_no": row_no,
                "column_index": column,
                "old_value_ciphertext": old_blob,
                "new_value_ciphertext": new_blob,
                "key_version": new_key or old_key,
                "edited_by": ctx.user_id,
            }
        )
    repo.insert_cell_edits(session, records)
    edited_sheet = with_edits(sheet, {(row_no, c): v for c, v in changes.items()})
    changed: list[int] = []
    counts: dict[str, Any] = {}
    if batch.status == "validated":
        result, changed = _refresh_checks(session, ctx, batch, edited_sheet, specs)
        counts = {
            "stats": result.stats,
            "row_count": len(result.rows),
            "error_count": result.error_rows,
        }
    updated = repo.update_batch(session, batch.id, **counts)
    if updated is None:  # pragma: no cover - locked above
        raise _not_found()
    for record in records:
        column = int(record["column_index"])
        target = batch.mapping.get(str(column))
        _audit(
            session,
            "import.cell_edited",
            batch.id,
            {
                "edit_id": record["id"],
                "row_no": row_no,
                "column": column,
                "field": target if target is not None else f"column_{column + 1}",
                "cleared": changes[column] is None,
                "revalidated": batch.status == "validated",
            },
        )
    log.info(
        "imports.sheet.cells_edited",
        resource_type="import_batch",
        resource_id=batch.id,
        count=len(records),
    )
    edited = set(edits) | {(row_no, c) for c in changes}
    new_row = next(r for r in edited_sheet.rows if r.row_no == row_no)
    stored = repo.rows_by_no(session, batch.id, [row_no])
    return SheetEditOut(
        row=_sheet_row(new_row, width, stored[0] if stored else None, restricted, edited),
        version=updated.version,
        status=updated.status,
        row_count=updated.row_count,
        error_count=updated.error_count,
        changed_rows=[n for n in changed if n != row_no],
    )


def export_sheet(
    session: Session, ctx: UserContext, batch_id: uuid.UUID, fmt: SheetFormat
) -> SheetFile:
    """The staged sheet with its edits as CSV (UTF-8 with BOM) or XLSX (FR-IMP-009;
    ``import.run`` and a recent sign-in with MFA, FR-EXP-004).

    One header row (the file's headers) and every data row in file order. Aadhaar-like numbers
    are masked, formula-like text is neutralised (SEC-017), XLSX cells are text. Restricted (C3)
    columns keep their header but are empty unless the caller may see sensitive fields
    (``student.read_sensitive``). Audit ``import.sheet_exported`` (counts only) in this
    transaction, before the file is returned; nothing is stored.
    """
    batch = _visible(session, ctx, batch_id, RUN)
    sheet, edits = _staged_sheet(session, batch)
    width = len(sheet.headers)
    restricted = _restricted_columns(batch, width, _specs(session))
    reveal = ctx.has(READ_SENSITIVE)
    hidden = set() if reveal else restricted
    header = list(sheet.headers)
    rows = [
        [None if i in hidden else display_text(row.cell(i).value) for i in range(width)]
        for row in sheet.rows
    ]
    stem = f"import-{str(batch.id)[:8]}-sheet"
    if fmt == "csv":
        out = SheetFile(f"{stem}.csv", CSV_MIME, write_csv(header, rows))
    else:
        out = SheetFile(
            f"{stem}.xlsx",
            XLSX_MIME,
            write_xlsx(header, rows, title="Import", watermark=import_config().sheet_watermark),
        )
    _audit(
        session,
        "import.sheet_exported",
        batch.id,
        {
            "format": fmt,
            "rows": len(rows),
            "columns": width,
            "edited_cells": len(edits),
            "restricted_columns": len(restricted),
            "restricted_included": bool(restricted) and reveal,
            "status": batch.status,
        },
    )
    log.info(
        "imports.sheet.exported",
        resource_type="import_batch",
        resource_id=batch.id,
        count=len(rows),
    )
    return out


# --- templates ------------------------------------------------------------------------------------


def list_templates(session: Session, ctx: UserContext) -> list[TemplateOut]:
    return [_template_out(t) for t in repo.list_templates(session)]


def create_template(session: Session, ctx: UserContext, data: TemplateCreate) -> TemplateOut:
    """Save an import's mapping for files with the same headers (FR-IMP-002). Audit:
    ``import.template_created``."""
    if is_full_aadhaar(data.name):
        raise ValidationFailed([issue("name", AADHAAR_CODE, "errors.aadhaar_last4_only")])
    batch = _visible(session, ctx, data.import_id, RUN)
    if not batch.columns or not batch.mapping:
        raise Conflict("This import has no column mapping yet.", code="mapping_missing")
    headers = [str(c.get("header", "")) for c in batch.columns]
    with _db_errors():
        template = repo.insert_template(
            session,
            id=new_id(),
            tenant_id=batch.tenant_id,
            name=data.name,
            source=batch.source,
            header_signature=header_signature(headers),
            headers=headers,
            mapping=template_payload(headers, batch.mapping),
            created_by=ctx.user_id,
        )
    _audit(
        session,
        "import.template_created",
        template.id,
        {"import_id": batch.id, "columns": len(template.mapping)},
        resource_type="import_template",
    )
    return _template_out(template)


# --- worker: parse and validate -------------------------------------------------------------------


def _columns(sheet: Sheet, suggestions: Sequence[Any]) -> list[dict[str, Any]]:
    out = []
    for i, header in enumerate(sheet.headers):
        hint = suggestions[i] if i < len(suggestions) else None
        out.append(
            {
                "index": i,
                # Headers are labels, but a mis-detected header row could be data: mask Aadhaar.
                "header": mask_aadhaar(header)[:100],
                "suggested": hint.target if hint is not None else None,
                "score": hint.score if hint is not None else 0,
            }
        )
    return out


def _fail(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    batch_id: uuid.UUID,
    job_id: uuid.UUID | None,
    code: str,
    *,
    status: str,
) -> None:
    with tenant_session(tenant_id, user_id) as s:
        repo.update_batch(s, batch_id, status=status, error_code=code)
        if job_id is not None:
            ops.fail_job(s, job_id, code)
    log.warning(
        "imports.job.failed", resource_type="import_batch", resource_id=batch_id, error_code=code
    )


def abandon(
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    job_id: uuid.UUID | None,
    user_id: uuid.UUID,
    code: str,
    *,
    status: str,
) -> None:
    """Worker gave up retrying: record the reason on the batch and the job."""
    _fail(tenant_id, user_id, batch_id, job_id, code, status=status)


def _notify_validated(
    session: Session, batch: ImportBatch, membership_id: uuid.UUID, job_id: uuid.UUID | None
) -> None:
    notifications.notify(
        session,
        tenant_id=batch.tenant_id,
        recipients=[membership_id],
        template_key="import.validated",
        params={
            "import_id": str(batch.id),
            "valid_rows": int(batch.stats.get("valid", 0)),
            "error_rows": batch.error_count,
        },
        resource_id=batch.id,
        dedupe_key=f"import.validated:{batch.id}:{job_id}",
    )


def _claim(
    tenant_id: uuid.UUID, user_id: uuid.UUID, batch_id: uuid.UUID, statuses: Sequence[str], to: str
) -> bool:
    with tenant_session(tenant_id, user_id) as s:
        batch = repo.get_batch(s, batch_id, lock=True)
        if batch is None or batch.status not in statuses:
            return False
        if batch.status != to:
            repo.update_batch(s, batch_id, status=to)
        return True


def run_parse(
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    *,
    job_id: uuid.UUID | None,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> str:
    """Worker: parse the file, suggest (or reuse) a mapping and validate when possible."""
    if not _claim(tenant_id, user_id, batch_id, ("uploaded", "parsing"), "parsing"):
        return "skipped"
    try:
        sheet = _load_sheet(tenant_id, user_id, batch_id)
    except SheetError as exc:
        _fail(tenant_id, user_id, batch_id, job_id, exc.code, status="failed")
        return "failed"
    ctx = member_context(tenant_id, user_id, membership_id)
    cfg = import_config()
    with tenant_session(tenant_id, user_id) as s:
        batch = repo.get_batch(s, batch_id, lock=True)
        if batch is None or batch.status != "parsing":
            return "skipped"
        specs = _specs(s)
        targets = allowed_targets(specs, batch.source)
        template = repo.find_template(s, header_signature(sheet.headers), batch.source)
        suggestions = suggest(
            sheet.headers, cfg.synonyms, allowed_targets=targets, threshold=cfg.suggest_threshold
        )
        if template is not None:
            mapping = mapping_from_template(sheet.headers, template.mapping, targets)
            repo.touch_template(s, template.id)
        else:
            mapping = {str(h.index): h.target for h in suggestions if h.target is not None}
        batch = repo.update_batch(
            s,
            batch.id,
            status="parsed",
            header_row=sheet.header_row,
            columns=_columns(sheet, suggestions),
            mapping=mapping,
            mapping_template_id=template.id if template is not None else None,
            row_count=len(sheet.rows),
            stats={"rows": len(sheet.rows), "formula_cells": sheet.formula_cells},
            error_code=None,
        )
        assert batch is not None  # noqa: S101 - locked above
        _audit(
            s,
            "import.parsed",
            batch.id,
            {
                "rows": len(sheet.rows),
                "columns": len(sheet.headers),
                "mapped_columns": len(mapping),
                "template_id": template.id if template is not None else None,
                "formula_cells": sheet.formula_cells,
            },
        )
        validated = False
        if "admission_no" in mapping.values() and ctx.has(RUN):
            result = validate_sheet(sheet, mapping, _validation_context(s, ctx, batch, sheet, RUN))
            batch = _store_validation(s, batch, result, specs)
            _notify_validated(s, batch, membership_id, job_id)
            validated = True
        if job_id is not None:
            ops.finish_job(s, job_id, {"rows": len(sheet.rows), "validated": validated})
    log.info(
        "imports.parse.done",
        resource_type="import_batch",
        resource_id=batch_id,
        count=len(sheet.rows),
    )
    return "validated" if validated else "parsed"


def run_validate(
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    *,
    job_id: uuid.UUID | None,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> str:
    """Worker: validate every row with the current mapping (FR-IMP-003, NFR-PERF-004)."""
    with tenant_session(tenant_id, user_id) as s:
        batch = repo.get_batch(s, batch_id)
        if batch is None or batch.status != "validating":
            return "skipped"
    ctx = member_context(tenant_id, user_id, membership_id)
    if not ctx.has(RUN):
        _fail(tenant_id, user_id, batch_id, job_id, "permission_revoked", status="parsed")
        return "failed"
    try:
        sheet = _load_sheet(tenant_id, user_id, batch_id)
    except SheetError as exc:
        _fail(tenant_id, user_id, batch_id, job_id, exc.code, status="failed")
        return "failed"
    with tenant_session(tenant_id, user_id) as s:
        batch = repo.get_batch(s, batch_id, lock=True)
        if batch is None or batch.status != "validating":
            return "skipped"
        specs = _specs(s)
        result = validate_sheet(
            sheet, batch.mapping, _validation_context(s, ctx, batch, sheet, RUN)
        )
        batch = _store_validation(s, batch, result, specs)
        _audit(
            s,
            "import.validated",
            batch.id,
            {
                "rows": len(result.rows),
                "valid_rows": result.stats["valid"],
                "error_rows": result.error_rows,
            },
        )
        _notify_validated(s, batch, membership_id, job_id)
        if job_id is not None:
            ops.finish_job(s, job_id, {"rows": len(result.rows), "errors": result.error_rows})
    log.info(
        "imports.validate.done",
        resource_type="import_batch",
        resource_id=batch_id,
        count=len(result.rows),
    )
    return "validated"


# --- worker: commit -------------------------------------------------------------------------------


class _CommitAborted(Exception):
    """Commit rolled back; ``result`` (with any new row error) is stored for the office."""

    def __init__(self, code: str, result: ValidationResult) -> None:
        super().__init__(code)
        self.code = code
        self.result = result


_VALUE_FIELD = re.compile(r"^values\.(\d+)(?:\.|$)")


def _row_failure(exc: DomainError, keys: Sequence[str], key: str | None = None) -> dict[str, str]:
    """The student service's refusal as a row error naming the attribute (not the request
    field ``values.<i>.value`` of the internal call)."""
    field_name = key or "row"
    code = exc.code
    if isinstance(exc, ValidationFailed) and exc.errors:
        first = exc.errors[0]
        code = str(first.get("code", exc.code))
        raw = str(first.get("field", ""))
        match = _VALUE_FIELD.match(raw)
        if key is None and match is not None and int(match.group(1)) < len(keys):
            field_name = keys[int(match.group(1))]
        elif key is None and raw in ("section_id", "roll_no", "values"):
            field_name = raw
    return issue(field_name, code)


def _apply_row(
    session: Session, ctx: UserContext, batch: ImportBatch, row: RowResult
) -> tuple[uuid.UUID, bool, int]:
    """Write one valid row through students.service; (student id, created, student version)."""
    keys = list(row.values)
    if row.action == "create":
        try:
            created = students.create_student(
                session,
                ctx,
                StudentCreate(
                    values=[
                        ValueIn(attribute_key=k, source=batch.source, value=row.values[k])
                        for k in keys
                    ],
                    section_id=_uuid(row.section_id),
                    roll_no=row.roll_no,
                ),
                import_batch_id=batch.id,
            )
        except DomainError as exc:
            raise _RowRefused(_row_failure(exc, keys)) from exc
        return created.id, True, created.version
    student_id = uuid.UUID(str(row.student_id))
    version = 0
    for key in keys:
        try:
            recorded = students.record_value(
                session,
                ctx,
                student_id,
                key,
                batch.source,
                row.values[key],
                import_batch_id=batch.id,
            )
        except DomainError as exc:
            raise _RowRefused(_row_failure(exc, keys, key)) from exc
        version = recorded.student_version
    return student_id, False, version


class _RowRefused(Exception):
    def __init__(self, error: dict[str, str]) -> None:
        super().__init__(error["code"])
        self.error = error


def _commit(
    session: Session,
    ctx: UserContext,
    batch: ImportBatch,
    sheet: Sheet,
    *,
    membership_id: uuid.UUID,
    job_id: uuid.UUID | None,
    skip_error_rows: bool,
) -> dict[str, int]:
    cfg = import_config()
    specs = _specs(session)
    result = validate_sheet(
        sheet, batch.mapping, _validation_context(session, ctx, batch, sheet, COMMIT)
    )
    valid_rows = [r for r in result.rows if r.status == "valid"]
    if not valid_rows:
        raise _CommitAborted("nothing_to_commit", result)
    if result.error_rows and not skip_error_rows:
        raise _CommitAborted("revalidation_errors", result)
    applied: dict[int, tuple[uuid.UUID, bool, int]] = {}
    for row in valid_rows:
        try:
            applied[row.row_no] = _apply_row(session, ctx, batch, row)
        except _RowRefused as exc:
            row.errors.append(exc.error)
            row.status = "error"
            raise _CommitAborted("commit_row_failed", result) from exc
    rows: list[dict[str, Any]] = []
    for r in result.rows:
        values = _row_values(r, specs)
        if r.row_no in applied:
            student_id, created, version = applied[r.row_no]
            values.update(
                status="committed",
                student_id=student_id,
                created_student=created,
                student_version=version,
            )
        else:
            values["status"] = "skipped"
        rows.append(values)
    repo.replace_rows(session, batch.tenant_id, batch.id, rows)
    created_count = sum(1 for _, c, _ in applied.values() if c)
    student_ids = {sid for sid, _, _ in applied.values()}
    stats = {
        **result.stats,
        "committed": len(applied),
        "skipped": len(result.rows) - len(applied),
        "created": created_count,
        "updated": len(applied) - created_count,
    }
    now = repo.now(session)
    updated = repo.update_batch(
        session,
        batch.id,
        status="committed",
        stats=stats,
        row_count=len(result.rows),
        error_count=result.error_rows,
        error_code=None,
        committed_at=now,
        committed_by=ctx.user_id,
        revert_deadline=now + dt.timedelta(hours=cfg.revert_window_hours),
    )
    assert updated is not None  # noqa: S101 - locked by the caller
    _audit(
        session,
        "import.committed",
        batch.id,
        {
            "source": batch.source,
            "rows": len(applied),
            "students_created": created_count,
            "students_updated": len(applied) - created_count,
            "rows_skipped": len(result.rows) - len(applied),
        },
    )
    ops.enqueue_event(
        session, COMMITTED_EVENT, {"batch_id": batch.id, "student_ids_count": len(student_ids)}
    )
    notifications.notify(
        session,
        tenant_id=batch.tenant_id,
        recipients=[membership_id],
        template_key="import.committed",
        params={"import_id": str(batch.id), "rows": len(applied)},
        resource_id=batch.id,
        dedupe_key=f"import.committed:{batch.id}",
    )
    if job_id is not None:
        ops.finish_job(session, job_id, {"rows": len(applied), "created": created_count})
    return stats


def _store_aborted(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    batch_id: uuid.UUID,
    job_id: uuid.UUID | None,
    aborted: _CommitAborted,
) -> None:
    with tenant_session(tenant_id, user_id) as s:
        batch = repo.get_batch(s, batch_id, lock=True)
        if batch is None or batch.status != "committing":
            return
        for row in aborted.result.rows:
            row.status = "error" if row.errors else "valid"
        stats = dict(aborted.result.stats)
        stats["valid"] = sum(1 for r in aborted.result.rows if r.status == "valid")
        stats["errors"] = sum(1 for r in aborted.result.rows if r.status == "error")
        fixed = ValidationResult(aborted.result.rows, stats)
        batch = _store_validation(s, batch, fixed, _specs(s))
        repo.update_batch(s, batch.id, error_code=aborted.code)
        if job_id is not None:
            ops.fail_job(s, job_id, aborted.code)
    log.warning(
        "imports.commit.aborted",
        resource_type="import_batch",
        resource_id=batch_id,
        error_code=aborted.code,
    )


def run_commit(  # noqa: PLR0911 - one outcome code per exit path
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    *,
    job_id: uuid.UUID | None,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    skip_error_rows: bool = False,
) -> str:
    """Worker: re-read, re-validate and commit the batch in ONE transaction (FR-IMP-004).

    Any failure rolls everything back; the batch returns to ``validated`` with the reason
    (``error_code``) and, when a row was refused, that row's error.
    """
    with tenant_session(tenant_id, user_id) as s:
        batch = repo.get_batch(s, batch_id)
        if batch is None or batch.status != "committing":
            return "skipped"
    ctx = member_context(tenant_id, user_id, membership_id)
    if not ctx.has(COMMIT):
        _fail(tenant_id, user_id, batch_id, job_id, "permission_revoked", status="validated")
        return "failed"
    try:
        sheet = _load_sheet(tenant_id, user_id, batch_id)
    except SheetError as exc:
        _fail(tenant_id, user_id, batch_id, job_id, exc.code, status="validated")
        return "failed"
    try:
        with tenant_session(tenant_id, user_id) as s:
            batch = repo.get_batch(s, batch_id, lock=True)
            if batch is None or batch.status != "committing":
                return "skipped"
            stats = _commit(
                s,
                ctx,
                batch,
                sheet,
                membership_id=membership_id,
                job_id=job_id,
                skip_error_rows=skip_error_rows,
            )
    except _CommitAborted as aborted:
        _store_aborted(tenant_id, user_id, batch_id, job_id, aborted)
        return "aborted"
    except Exception as exc:
        log.error(
            "imports.commit.failed",
            resource_type="import_batch",
            resource_id=batch_id,
            error_type=type(exc).__name__,
        )
        _fail(tenant_id, user_id, batch_id, job_id, "commit_failed", status="validated")
        return "failed"
    log.info(
        "imports.commit.done",
        resource_type="import_batch",
        resource_id=batch_id,
        count=stats["committed"],
    )
    return "committed"


# --- revert ---------------------------------------------------------------------------------------


def _notify_reverted(session: Session, ctx: UserContext, batch: ImportBatch, rows: int) -> None:
    """Tell the person who uploaded the file that someone else undid it (FR-NOT-001). No
    notice when they reverted it themselves, or when they are no longer an active member."""
    if batch.created_by == ctx.user_id:
        return
    try:
        importer = identity.get_user(session, batch.created_by)
    except NotFound:
        return
    if importer.status != "active":
        return
    notifications.notify(
        session,
        tenant_id=batch.tenant_id,
        recipients=[importer.membership_id],
        template_key="import.reverted",
        params={"import_id": str(batch.id), "rows": rows},
        resource_id=batch.id,
        dedupe_key=f"import.reverted:{batch.id}",
    )


def revert(session: Session, ctx: UserContext, batch_id: uuid.UUID) -> ImportOut:
    """Undo a committed batch within 24 hours (``import.commit``; FR-IMP-005).

    The student records are the students module's: :func:`students.plan_import_revert` refuses
    (409 ``import_has_dependents``) when anything was recorded on top of the batch (a student it
    created was changed since, one of its values was superseded, a value it replaced is a
    verified or register identity value), and :func:`students.revert_import` then, in this
    transaction, withdraws its values, restores the values they replaced, refreshes the
    profiles and removes the students it created (other records pointing at them, such as
    change requests, refuse the revert). Data-quality findings about removed students are
    derived data and go with them (``ON DELETE CASCADE``). Audit: ``import.reverted`` (the
    student events are the students module's); outbox ``import.reverted``.
    """
    batch = _visible(session, ctx, batch_id, COMMIT, lock=True)
    if batch.status != "committed":
        raise Conflict("Only an added import can be reverted.", code="import_not_committed")
    now = repo.now(session)
    if batch.revert_deadline is None or batch.revert_deadline <= now:
        raise Conflict("Imports can be reverted only within 24 hours.", code="revert_window_closed")
    rows = repo.committed_rows(session, batch.id)
    created = {r.student_id: r.student_version for r in rows if r.created_student and r.student_id}
    plan = students.plan_import_revert(session, batch.id, created)
    with _db_errors():
        repo.update_batch(session, batch.id, status="reverting")
        result = students.revert_import(session, ctx, plan)
    repo.mark_rows_reverted(session, batch.id)
    updated = repo.update_batch(
        session, batch.id, status="reverted", reverted_at=now, reverted_by=ctx.user_id
    )
    assert updated is not None  # noqa: S101 - locked above
    _audit(
        session,
        "import.reverted",
        batch.id,
        {
            "students_removed": result.students_removed,
            "values_withdrawn": result.values_withdrawn,
            "values_restored": result.values_restored,
        },
    )
    ops.enqueue_event(session, REVERTED_EVENT, {"batch_id": batch.id})
    _notify_reverted(session, ctx, batch, len(rows))
    log.info(
        "imports.reverted",
        resource_type="import_batch",
        resource_id=batch.id,
        count=result.students_removed,
    )
    return _out(session, updated)


# --- retention (FR-IMP-007) -----------------------------------------------------------------------


RAW_FILE_RETENTION_CATEGORY: Final = "import_raw_files"


def raw_file_retention_days(session: Session) -> int:
    """How long this school keeps raw import files after commit: its retention setting
    (FR-ADM-002, ``/admin/retention``) or the default (docs/05 §13: 90 days)."""
    return retention.days(
        session, RAW_FILE_RETENTION_CATEGORY, default=import_config().raw_file_retention_days
    )


def _retention_guard(session: Session, document_id: uuid.UUID) -> str | None:
    """documents.DELETE_GUARDS: keep an import's raw file while a job uses it, and for the
    retention period after commit (docs/05 §13: 90 days unless the school set a shorter one)."""
    batches = repo.batches_for_document(session, document_id)
    if not batches:
        return None
    now = repo.now(session)
    keep = dt.timedelta(days=raw_file_retention_days(session))
    for batch in batches:
        if batch.status in repo.LIVE_STATUSES:
            return "import_in_progress"
        if (
            batch.status == "committed"
            and batch.committed_at is not None
            and (batch.committed_at + keep > now)
        ):
            return "import_file_retained"
    return None


if _retention_guard not in documents.DELETE_GUARDS:
    documents.DELETE_GUARDS.append(_retention_guard)

# DEK rotation re-encrypts the staged cell edits too (SEC-012; docs/05 §9).
key_rotation.register_reencryptor("import_cell_edits", cells.reencrypt_batch)


def purge_raw_files(tenant_id: uuid.UUID, *, now: dt.datetime | None = None) -> int:
    """Delete raw import files kept past the school's retention period (daily job, FR-IMP-007;
    the period is the school's setting, FR-ADM-002, else 90 days); the parsed rows stay.
    Returns the number of documents deleted."""
    moment = now or dt.datetime.now(dt.UTC)
    deleted = 0
    with tenant_session(tenant_id) as s:
        cutoff = moment - dt.timedelta(days=raw_file_retention_days(s))
        due = repo.batches_due_for_file_deletion(s, cutoff)
        by_document: dict[uuid.UUID, list[ImportBatch]] = {}
        for batch in due:
            if batch.document_id is not None:
                by_document.setdefault(batch.document_id, []).append(batch)
        for document_id, batches in by_document.items():
            try:
                with s.begin_nested():  # a refused delete must not abort the other documents
                    deleted_now = documents.delete_for_retention(
                        s, document_id, reason="import_raw_file"
                    )
            except Conflict:
                continue  # kept by another batch, or still evidence for a record
            if not deleted_now:
                continue  # already gone
            repo.mark_raw_file_deleted(s, [b.id for b in batches], moment)
            for batch in batches:
                # Staged edits hold values from the file (and the values before them): they go
                # with it; who edited which cell, and when, stays (FR-IMP-007, FR-IMP-008).
                erased = repo.erase_cell_edit_values(s, [batch.id])
                audit.record(
                    s,
                    action="import.raw_file_deleted",
                    resource_type="import_batch",
                    resource_id=batch.id,
                    summary={
                        "document_id": document_id,
                        "reason": "retention",
                        **({"cell_edits_erased": erased} if erased else {}),
                    },
                    actor_type="system",
                )
            deleted += 1
    if deleted:
        log.info("imports.raw_files.purged", tenant_id=tenant_id, count=deleted)
    return deleted


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: the school's import batches (status, counts, column mapping, who and when)
    and mapping templates for its full data export (``app.admin``; the caller checked
    ``tenant.export_all`` and audits the export). Parsed rows and staged cell edits are not
    exported: the values an import recorded are in the student values."""
    return repo.export_record_tables(session)


__all__ = [
    "COMMIT",
    "COMMITTED_EVENT",
    "COMMIT_TASK",
    "PARSE_TASK",
    "PURGE_TASK",
    "RAW_FILE_RETENTION_CATEGORY",
    "REVERTED_EVENT",
    "RUN",
    "VALIDATE_TASK",
    "SheetFile",
    "abandon",
    "create_import",
    "create_template",
    "edit_row",
    "export_records",
    "export_sheet",
    "get_import",
    "get_sheet",
    "list_imports",
    "list_rows",
    "list_templates",
    "member_context",
    "purge_raw_files",
    "purge_tenant_data",
    "raw_file_retention_days",
    "request_commit",
    "request_validation",
    "revert",
    "run_commit",
    "run_parse",
    "run_validate",
    "set_mapping",
    "tenant_data_counts",
]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Import batches with their rows and staged cell edits, mapping templates.
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=("sis.import_batches", "sis.import_mapping_templates"),
    cascaded=("sis.import_rows", "sis.import_cell_edits"),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="imports", count=tenant_data_counts, purge=purge_tenant_data)
)
