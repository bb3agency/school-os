"""Documents public API: uploads, registration, versions, ACLs, downloads, scanning.

Other modules call only these functions (CLAUDE.md §4). Routes in ``documents.api`` wrap them
with ``require(...)``; the permission assumed is stated per function. Mutations audit in the
caller's transaction (CLAUDE.md §6.7) and hand follow-up work to workers through the
transactional outbox (``ops.enqueue_event``), so a scan is queued if and only if the document
row commits.

Upload flow (docs/04 §8.2, docs/07 §10, SEC-016)::

    create_upload()  -> kb.upload_intents row + presigned POST (exact staging key
                        t/<tenant>/uploads/<intent>/..., Content-Type, size)
    browser          -> POST file straight to S3
    register_document() / add_version()
                     -> intent checks (same user, unused, unexpired) -> HEAD (size) ->
                        one GET: magic bytes / text sniffing, SHA-256, ETag -> dedupe ->
                        copy to the final key only if the ETag is unchanged -> document +
                        version (queued) + ACL -> audit -> outbox "scan"
    worker           -> scan_version(): AV scan -> ready | quarantined (+ audit)

Visibility (docs/05 §6, fail closed): holders of ``document.manage_acl`` see every document;
other ``document.read`` holders see a document when an ACL entry matches their role,
membership, section or class; a document with an EMPTY ACL is visible only to school-wide
readers. Out-of-scope documents answer 404 (never reveal existence).

PRV-016 (images that showed a full Aadhaar number): :func:`replace_with_redacted` stores a
worker-made redacted copy as the next version and :func:`discard_version` retires the original.
A discarded version keeps its row (history, audit) as ``quarantined`` with the reason in
``error``; its object is deleted after commit (outbox ``document.version.discarded`` ->
``documents.discard_object``, plus a daily sweep), tagged for the bucket's one-day lifecycle rule.

Requirements: FR-DOC-001..006, FR-DOC-008 (status tracking), SEC-016, FR-DOC-007 (storage part),
PRV-016.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import ScopeGrant, UserContext
from app.authz.http import decode_cursor, encode_cursor
from app.core import purge as purging
from app.core.config import Settings, get_settings
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
from app.core.redaction import contains_full_aadhaar
from app.core.spreadsheet import (
    CSV_MIME,
    XLSX_MIME,
    SpreadsheetError,
    column_letter,
    display_text,
    write_csv,
    write_xlsx,
)
from app.core.spreadsheet import FileKind as SpreadsheetKind
from app.documents import filetypes, sheets
from app.documents import repository as repo
from app.documents.filetypes import FileKind
from app.documents.models import Document, DocumentAcl, DocumentVersion, UploadIntent
from app.documents.scanning import AvScanner, ScanResult, Verdict, build_scanner
from app.documents.schemas import (
    AclEntry,
    AclEntryOut,
    DocSheetCellOut,
    DocSheetColumnOut,
    DocSheetRowOut,
    DocumentCreate,
    DocumentDetail,
    DocumentOut,
    DocumentSheetOut,
    DocumentUpdate,
    DownloadUrlOut,
    SheetCellEdit,
    SheetExportIn,
    SheetReadOnly,
    SheetSaveIn,
    UploadCreate,
    UploaderOut,
    UploadOut,
    VersionCreate,
    VersionOut,
)
from app.documents.storage import (
    LIFECYCLE_EXPORT,
    LIFECYCLE_TENANT_EXPORT,
    ObjectChanged,
    ObjectStore,
    ObjectStoreError,
    ObjectWriter,
    derived_key,
    document_key,
    document_prefix,
    export_key,
    export_prefix,
    get_object_store,
    import_key,
    key_in_tenant,
    tenant_export_key,
    tenant_prefix,
    upload_key,
)
from app.identity import service as identity
from app.identity.principal import STEP_UP_MAX_AGE
from app.notifications import service as notifications
from app.ops import service as ops
from app.tenancy import service as tenancy

log = get_logger(__name__)

UPLOAD: Final = "document.upload"
READ: Final = "document.read"
MANAGE: Final = "document.manage_acl"

SCAN_EVENT: Final = "document.version.registered"
DELETED_EVENT: Final = "document.deleted"
DISCARDED_EVENT: Final = "document.version.discarded"
SCAN_TASK: Final = "documents.scan"
PURGE_TASK: Final = "documents.purge_objects"
DISCARD_TASK: Final = "documents.discard_object"
# W3-06: objects the upload path no longer needs (rejected, duplicate or half-made uploads, the
# staging copy after promotion). The api only queues them; its role cannot tag or delete under
# ``t/*`` (infra/terraform), so only the worker discards (``documents.discard_unused_object``).
OBJECT_DISCARD_EVENT: Final = "document.object.discard_requested"
OBJECT_DISCARD_TASK: Final = "documents.discard_unused_object"
ops.register_outbox_route(SCAN_EVENT, SCAN_TASK)
ops.register_outbox_route(DELETED_EVENT, PURGE_TASK)
ops.register_outbox_route(DISCARDED_EVENT, DISCARD_TASK)
ops.register_outbox_route(OBJECT_DISCARD_EVENT, OBJECT_DISCARD_TASK)

# An intent can be registered for a while after its presigned POST expired (slow uploads).
INTENT_GRACE: Final = dt.timedelta(minutes=30)
UNUSABLE_STATUSES: Final = ("quarantined", "failed")
SENSITIVITY_ORDER: Final = {"C1": 1, "C2": 2, "C3": 3}


# --- errors ---------------------------------------------------------------------------------


class UnsupportedFileType(DomainError):
    status, code, title = 415, "unsupported_file_type", "This type of file is not accepted"


class FileTooLarge(DomainError):
    status, code, title = 413, "file_too_large", "The file is too large"


# --- extension hooks (M2 ingestion, notifications, retention) --------------------------------

VersionHook = Callable[[Session, uuid.UUID, uuid.UUID], None]
"""``hook(session, document_id, version_id)`` in the worker's tenant transaction."""

QUARANTINE_HOOKS: list[VersionHook] = []
"""Called when a version is quarantined (notifications: tell the uploader/office admin)."""

READY_HOOKS: list[VersionHook] = []
"""Called when a version becomes ``ready`` (M2: text extraction, chunking, embeddings)."""

VERSION_DISCARDED_HOOKS: list[VersionHook] = []
"""``hook(session, document_id, version_id)`` after a version was discarded (PRV-016:
``discard_version`` / ``replace_with_redacted``), in the same transaction as the status change
and its ``document.version_discarded`` audit event (knowledge: remove the version's chunks and
cached vectors at once, docs/08 §7 erasure chain). Not called for a version already
discarded; a hook that raises rolls the discard back."""

DeleteGuard = Callable[[Session, uuid.UUID], str | None]
"""``guard(session, document_id)`` returns an error code to refuse deletion (retention)."""

DELETE_GUARDS: list[DeleteGuard] = []

DeletedHook = Callable[[Session, uuid.UUID], None]
DELETING_HOOKS: list[DeletedHook] = []
"""``hook(session, document_id)`` right before a document's rows are deleted (by a user or a
retention job), after every ``DELETE_GUARDS`` entry let it go, in the same transaction: for
derived data that no foreign key reaches and that can only be found through the rows about to
go (knowledge: the embedding-cache vectors of the document's chunks, keyed by text digest;
docs/08 §7 erasure chain). A hook that raises rolls the delete back."""

DELETED_HOOKS: list[DeletedHook] = []
"""``hook(session, document_id)`` after a document was deleted (by a user or a retention job):
its rows are gone, ``document.deleted`` is audited and the object purge is queued, all in the
same transaction, so whatever a hook writes (e.g. knowledge flagging verified answers that cite
the document, docs/06 §4.8) commits or rolls back with the delete. Never called for a refused
delete (a ``DELETE_GUARDS`` code, evidence in use); a hook that raises rolls the delete back."""

AclChangedHook = Callable[[Session, uuid.UUID], None]
ACL_CHANGED_HOOKS: list[AclChangedHook] = []
"""M2: rewrite ``acl_*`` arrays on the document's chunks (docs/05 §6)."""

StatusChangedHook = Callable[[Session, uuid.UUID, str], None]
STATUS_CHANGED_HOOKS: list[StatusChangedHook] = []
"""``hook(session, document_id, status)`` after archive/unarchive, in the same transaction
(M2: keep archived documents out of retrieval)."""

MetadataChangedHook = Callable[[Session, uuid.UUID, frozenset[str]], None]
METADATA_CHANGED_HOOKS: list[MetadataChangedHook] = []
"""``hook(session, document_id, fields)`` after ``PATCH /documents/{id}`` changed metadata
(``fields``: the changed field NAMES from :data:`METADATA_FIELDS`), after the update and its
audit event, in the same transaction: whatever a hook writes (e.g. an outbox event for
knowledge to refresh chunk titles and facets once the change commits) commits or rolls back
with the change. Not called for a no-op or refused PATCH."""


# --- purpose rules --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PurposeRule:
    kinds: tuple[str, ...]
    max_bytes: int
    doc_types: tuple[str, ...]
    default_doc_type: str
    min_sensitivity: str
    versionable: bool = True


_GENERAL_DOC_TYPES: Final = (
    "circular",
    "policy",
    "minutes",
    "certificate",
    "letter",
    "form",
    "report",
    "other",
)


def purpose_rule(purpose: str, settings: Settings | None = None) -> PurposeRule:
    s = settings or get_settings()
    allowed = tuple(k for k in s.documents_allowed_kinds if k in filetypes.KINDS)
    scans = tuple(k for k in ("pdf", "jpg", "png") if k in allowed)
    if purpose == "evidence":
        return PurposeRule(
            scans,
            s.documents_max_upload_bytes,
            ("evidence", "certificate", "letter", "form", "other"),
            "evidence",
            "C3",  # identity evidence scans are C3 (docs/05 §8)
        )
    if purpose == "register_scan":
        return PurposeRule(
            scans, s.documents_max_upload_bytes, ("register_scan",), "register_scan", "C2"
        )
    if purpose == "import_file":
        kinds = tuple(k for k in s.documents_import_allowed_kinds if k in ("xlsx", "csv"))
        return PurposeRule(
            kinds,
            s.documents_import_max_upload_bytes,
            ("import_file",),
            "import_file",
            "C2",
            versionable=False,
        )
    if purpose == "certificate":  # generated by app.certificates, never uploaded
        return PurposeRule(
            ("pdf",),
            s.documents_max_upload_bytes,
            ("certificate",),
            "certificate",
            "C2",
            versionable=False,
        )
    if purpose in ("circular", "policy", "other"):
        default = purpose if purpose != "other" else "other"
        return PurposeRule(allowed, s.documents_max_upload_bytes, _GENERAL_DOC_TYPES, default, "C1")
    raise ValueError(f"unknown purpose {purpose!r}")


# --- helpers --------------------------------------------------------------------------------


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    document_id: uuid.UUID,
    summary: dict[str, Any],
    *,
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type="document",
        resource_id=document_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=_request_id(),
    )


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _invalid(field: str, code: str) -> ValidationFailed:
    return ValidationFailed([_error(field, code)])


def _refuse_archived(doc: Document) -> None:
    """Archived documents are read-only until unarchived (FR-DOC-005, FR-DOC-006)."""
    if doc.status == "archived":
        raise Conflict("This document is archived. Unarchive it first.", code="document_archived")


def _not_found() -> NotFound:
    return NotFound("Document not found")


def _raw_restricted(doc: Document) -> bool:
    """Whether the stored file itself is opened only by ``student.read_sensitive`` holders and
    its uploader: restricted (C3) documents, and raw import spreadsheets, whose restricted (C3)
    columns only the import's own sheet and export hide (FR-IMP-008/009; audit DL-02)."""
    return doc.sensitivity == "C3" or doc.purpose == "import_file"


def _visibility(session: Session, ctx: UserContext) -> repo.Visibility:
    everything = ctx.has(MANAGE) and ctx.scope_for(MANAGE).school_wide
    read = ctx.has(READ)
    grant = ctx.scope_for(READ)
    sections: set[str] = set()
    classes: set[str] = set()
    if read and not grant.school_wide:
        sections, classes = _reach(session, grant)
    return repo.Visibility(
        everything=everything,
        read=read,
        school_wide=read and grant.school_wide,
        roles=frozenset(ctx.roles),
        membership_ref=str(ctx.membership_id),
        section_refs=frozenset(sections),
        class_refs=frozenset(classes),
    )


def _reach(session: Session, grant: ScopeGrant) -> tuple[set[str], set[str]]:
    """Sections and classes a scoped grant reaches (class scope covers its sections; a section
    scope makes its class match class-level ACL entries)."""
    all_sections = tenancy.list_sections(session)
    sections = {
        str(s.id)
        for s in all_sections
        if s.id in grant.section_ids or s.class_id in grant.class_ids
    }
    classes = {str(c) for c in grant.class_ids} | {
        str(s.class_id) for s in all_sections if s.id in grant.section_ids
    }
    return sections, classes


@dataclass(frozen=True, slots=True)
class _Viewer:
    """Who is looking (for ``uploaded_by_me``) and the uploaders' member refs."""

    user_id: uuid.UUID | None
    members: dict[uuid.UUID, tuple[uuid.UUID, str]]

    def uploader(self, user_id: uuid.UUID) -> UploaderOut | None:
        ref = self.members.get(user_id)
        return UploaderOut(membership_id=ref[0], display_name=ref[1]) if ref else None


def _viewer(
    session: Session,
    ctx: UserContext | None,
    docs: Sequence[Document],
    versions: Sequence[DocumentVersion],
) -> _Viewer:
    uploaders = {d.created_by for d in docs} | {v.created_by for v in versions}
    return _Viewer(
        user_id=ctx.user_id if ctx is not None else None,
        members=identity.members_for_users(session, uploaders),
    )


def _version_out(v: DocumentVersion, viewer: _Viewer) -> VersionOut:
    return VersionOut(
        id=v.id,
        version_no=v.version_no,
        mime_type=v.mime_type,
        size_bytes=v.size_bytes,
        status=v.status,
        error=v.error,
        created_at=v.created_at,
        uploaded_by=viewer.uploader(v.created_by),
        uploaded_by_me=v.created_by == viewer.user_id,
    )


def _document_out(
    doc: Document,
    versions: Sequence[DocumentVersion],
    acl: Sequence[DocumentAcl],
    viewer: _Viewer,
    *,
    detail: bool = False,
) -> DocumentOut:
    current = next((v for v in versions if v.id == doc.current_version_id), None)
    data: dict[str, Any] = {
        "id": doc.id,
        "purpose": doc.purpose,
        "doc_type": doc.doc_type,
        "title": doc.title,
        "issuer": doc.issuer,
        "issued_on": doc.issued_on,
        "academic_year_id": doc.academic_year_id,
        "language": doc.language,
        "sensitivity": doc.sensitivity,
        "status": doc.status,
        "current_version": _version_out(current, viewer) if current else None,
        "acl": [
            AclEntryOut(principal_type=a.principal_type, principal_ref=a.principal_ref) for a in acl
        ],
        "created_by": doc.created_by,
        "uploaded_by": viewer.uploader(doc.created_by),
        "uploaded_by_me": doc.created_by == viewer.user_id,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "version": doc.version,
    }
    if detail:
        data["allowed_doc_types"] = list(purpose_rule(doc.purpose).doc_types)
        return DocumentDetail(**data, versions=[_version_out(v, viewer) for v in versions])
    return DocumentOut(**data)


def _load_out(
    session: Session, ctx: UserContext | None, doc: Document, *, detail: bool = False
) -> DocumentOut:
    versions = repo.versions_of(session, [doc.id])
    acl = repo.acl_of(session, [doc.id])[doc.id]
    viewer = _viewer(session, ctx, [doc], versions)
    return _document_out(doc, versions, acl, viewer, detail=detail)


# --- ACL validation -------------------------------------------------------------------------


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _known_refs(session: Session, principal_type: str) -> set[str]:
    """Valid references of one principal type in the current school (normalised strings)."""
    if principal_type == "role":
        return {r.key for r in identity.list_roles(session)}
    if principal_type == "section":
        return {str(s.id) for s in tenancy.list_sections(session)}
    if principal_type == "class":
        return {str(c.id) for c in tenancy.list_classes(session)}
    users, _ = identity.list_users(session, limit=100_000)
    return {str(u.membership_id) for u in users}


def validate_acl(
    session: Session, entries: Sequence[AclEntry], *, upload_grant: ScopeGrant | None = None
) -> list[tuple[str, str]]:
    """Check every polymorphic reference inside this tenant (docs/05 §3.5) and, for scoped
    uploaders, that the ACL stays within their sections/classes. Returns normalised pairs."""
    errors: list[dict[str, str]] = []
    out: list[tuple[str, str]] = []
    known: dict[str, set[str]] = {}
    for i, entry in enumerate(entries):
        ref = entry.principal_ref
        if entry.principal_type != "role":
            parsed = _parse_uuid(ref)
            if parsed is None:
                errors.append(_error(f"acl.{i}.principal_ref", "invalid"))
                continue
            ref = str(parsed)
        if entry.principal_type not in known:
            known[entry.principal_type] = _known_refs(session, entry.principal_type)
        if ref not in known[entry.principal_type]:
            errors.append(_error(f"acl.{i}.principal_ref", "not_found"))
            continue
        out.append((entry.principal_type, ref))
    if errors:
        raise ValidationFailed(errors)
    if upload_grant is not None and not upload_grant.school_wide:
        _check_scoped_acl(session, out, upload_grant)
    return out


def _check_scoped_acl(session: Session, acl: Sequence[tuple[str, str]], grant: ScopeGrant) -> None:
    """A scoped uploader (e.g. class teacher) must restrict the document to their own sections
    or classes; otherwise a document could be hidden from them or shown school-wide."""
    if not acl:
        raise _invalid("acl", "acl_required_for_scoped_upload")
    sections, _ = _reach(session, grant)
    for i, (ptype, ref) in enumerate(acl):
        allowed = (ptype == "section" and ref in sections) or (
            ptype == "class" and ref in {str(c) for c in grant.class_ids}
        )
        if not allowed:
            raise _invalid(f"acl.{i}", "acl_outside_scope")


# --- uploads --------------------------------------------------------------------------------


def create_upload(session: Session, ctx: UserContext, data: UploadCreate) -> UploadOut:
    """Issue a presigned POST for one file (permission ``document.upload``).

    The declared kind must be allowlisted for the purpose and agree with the file extension;
    the size must be within the purpose's limit (FR-DOC-001: 25 MB, imports 10 MB).
    ``document_id`` asks for a new version of a document the caller can see.
    """
    settings = get_settings()
    rule = purpose_rule(data.purpose, settings)
    kind = filetypes.kind_for_content_type(data.content_type)
    if kind is None or kind.key not in rule.kinds:
        raise UnsupportedFileType(
            "Upload a PDF, JPG, PNG, DOCX or XLSX file (spreadsheet imports: XLSX or CSV)."
        )
    if filetypes.kind_for_filename(data.filename) is not kind:
        raise UnsupportedFileType("The file name extension does not match the file type.")
    if data.size_bytes > rule.max_bytes:
        raise FileTooLarge(f"Files for this purpose can be at most {rule.max_bytes // 2**20} MB.")

    tenant_id = repo.current_tenant_id(session)
    batch_id: uuid.UUID | None = None
    if data.document_id is not None:
        doc = repo.get_document(
            session, data.document_id, visibility=_visibility(session, ctx), for_update=True
        )
        if doc is None:
            raise _not_found()
        _refuse_archived(doc)
        if doc.purpose != data.purpose:
            raise _invalid("purpose", "purpose_mismatch")
        if not purpose_rule(doc.purpose, settings).versionable:
            raise Conflict("Import files cannot get new versions.", code="not_versionable")
        document_id = doc.id
        version_no = repo.max_version_no(session, doc.id) + 1
    else:
        document_id, version_no = new_id(), 1
        if data.purpose == "import_file":
            batch_id = new_id()

    now = _now()
    ttl = settings.documents_upload_url_ttl_s
    intent_id = new_id()
    key = upload_key(tenant_id, intent_id, kind.ext)
    with _db_errors():
        intent = repo.insert_intent(
            session,
            id=intent_id,
            tenant_id=tenant_id,
            purpose=data.purpose,
            document_id=document_id,
            version_no=version_no,
            batch_id=batch_id,
            object_key=key,
            declared_content_type=kind.mime,
            declared_size=data.size_bytes,
            max_bytes=rule.max_bytes,
            created_by=ctx.user_id,
            created_at=now,
            expires_at=now + dt.timedelta(seconds=ttl) + INTENT_GRACE,
        )
    post = get_object_store().presigned_post(
        key=key, content_type=kind.mime, max_bytes=data.size_bytes, expires_s=ttl
    )
    log.info("documents.upload.issued", resource_type="upload_intent", resource_id=intent.id)
    return UploadOut(
        upload_id=intent.id,
        url=post.url,
        fields=post.fields,
        expires_at=post.expires_at,
        max_bytes=data.size_bytes,
        purpose=data.purpose,
        document_id=document_id if data.document_id is not None else None,
        batch_id=batch_id,
    )


def _claim_intent(
    session: Session, ctx: UserContext, upload_id: uuid.UUID, *, document_id: uuid.UUID | None
) -> UploadIntent:
    intent = repo.lock_intent(session, upload_id)
    if intent is None or intent.created_by != ctx.user_id:
        raise NotFound("Upload not found")
    if intent.consumed_at is not None:
        raise Conflict("This upload was registered already.", code="upload_already_used")
    if intent.expires_at <= _now():
        raise Conflict("This upload expired. Please upload the file again.", code="upload_expired")
    if document_id is None and intent.version_no != 1:
        raise _invalid("upload_id", "upload_is_for_a_new_version")
    if document_id is not None and intent.document_id != document_id:
        raise _invalid("upload_id", "upload_is_for_another_document")
    if not key_in_tenant(intent.object_key, repo.current_tenant_id(session)):
        # Defence in depth: the DB CHECK already pins the tenant prefix.
        raise NotFound("Upload not found")
    return intent


@dataclass(frozen=True, slots=True)
class _Verified:
    sha256: bytes
    size: int
    kind: FileKind
    etag: str


def _discard_now(store: ObjectStore, key: str) -> None:
    """Worker only: remove an object the system no longer needs. An automatic deletion, so it
    is discarded: the bucket rule ``discarded-1d`` expires the bytes after a day (docs/08 §7)."""
    try:
        store.discard(key)
    except ObjectStoreError:
        log.warning("documents.upload.discard_failed", error_code="delete_failed")


_UUID_RE: Final = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_TENANT_RE: Final = rf"^t/(?P<t>{_UUID_RE})/"
_UPLOAD_KEY: Final = re.compile(rf"{_TENANT_RE}uploads/(?P<u>{_UUID_RE})/original\.[a-z]+$")
_DOC_KEY: Final = re.compile(
    rf"{_TENANT_RE}docs/(?P<d>{_UUID_RE})/v(?P<n>[1-9][0-9]{{0,5}})/original\.(?P<e>[a-z]+)$"
)
_IMPORT_KEY: Final = re.compile(rf"{_TENANT_RE}imports/(?P<b>{_UUID_RE})/raw\.(?P<e>[a-z]+)$")


def _discard_payload(key: str) -> dict[str, Any] | None:
    """The IDs-only payload of :data:`OBJECT_DISCARD_EVENT` for an upload-path key (object keys
    never go into the outbox; the worker rebuilds the key from the IDs and checks it)."""
    if m := _UPLOAD_KEY.match(key):
        return {"upload_id": m["u"]}
    requested_at = _now().isoformat()
    if m := _DOC_KEY.match(key):
        return {
            "document_id": m["d"],
            "version_no": int(m["n"]),
            "ext": m["e"],
            "requested_at": requested_at,
        }
    if m := _IMPORT_KEY.match(key):
        return {"batch_id": m["b"], "ext": m["e"], "requested_at": requested_at}
    return None


def _request_discard(session: Session, key: str) -> None:
    """Queue the discard of ``key`` in the caller's transaction (W3-06: the worker deletes)."""
    payload = _discard_payload(key)
    if payload is None:  # pragma: no cover - every upload-path key matches a layout
        log.error("documents.discard.unknown_key", error_code="unknown_key_layout")
        return
    ops.enqueue_event(session, OBJECT_DISCARD_EVENT, payload)


def _request_discard_committed(tenant_id: uuid.UUID, key: str) -> None:
    """Queue the discard of ``key`` in its own short transaction: for refusals and errors, whose
    request transaction rolls back (W3-06). A failure is logged, never raised: the daily
    :func:`purge_expired_uploads` removes a forgotten staging object anyway."""
    try:
        with tenant_session(tenant_id) as s:
            _request_discard(s, key)
    except Exception:
        log.warning("documents.discard.queue_failed", error_code="outbox_unavailable")


def _reject(intent: UploadIntent, code: str) -> UnsupportedFileType:
    _request_discard_committed(intent.tenant_id, intent.object_key)
    log.warning(
        "documents.upload.rejected",
        resource_type="upload_intent",
        resource_id=intent.id,
        error_code=code,
    )
    return UnsupportedFileType(
        "The file's content is not the type it claims to be. Upload the original PDF, image or "
        "office file.",
        code="unsupported_file_type" if code == "type_mismatch" else code,
    )


def _check_stored_head(store: ObjectStore, intent: UploadIntent) -> int:
    """HEAD: the object exists, has the declared size within the limit, and is encrypted."""
    try:
        head = store.head(intent.object_key)
    except ObjectStoreError as exc:
        raise Conflict(
            "The file could not be checked. Try again.", code="storage_unavailable"
        ) from exc
    if head is None:
        raise Conflict("Upload the file before registering it.", code="upload_missing")
    if head.size > intent.max_bytes:
        _request_discard_committed(intent.tenant_id, intent.object_key)
        raise FileTooLarge("The uploaded file is larger than allowed.")
    if head.size != intent.declared_size:
        _request_discard_committed(intent.tenant_id, intent.object_key)
        raise _invalid("upload_id", "size_mismatch")
    kms = getattr(store, "kms_key_id", None)
    if kms and head.sse != "aws:kms":
        _request_discard_committed(intent.tenant_id, intent.object_key)
        raise Conflict("The file was not stored encrypted. Upload it again.", code="not_encrypted")
    return head.size


def _verify_object(store: ObjectStore, intent: UploadIntent) -> _Verified:
    """HEAD, size, magic bytes (or CSV text), structural tail check and streamed SHA-256."""
    kind = filetypes.kind_for_content_type(intent.declared_content_type)
    if kind is None:  # pragma: no cover - CHECK constraint on the column
        raise UnsupportedFileType()
    size = _check_stored_head(store, intent)
    # One GET: every check below runs on exactly the bytes whose ETag is then copied.
    opened = store.open(intent.object_key)
    digest = hashlib.sha256()
    csv = filetypes.CsvChecker() if kind.key == "csv" else None
    first = bytearray()
    tail = b""
    total = 0
    try:
        for chunk in opened.chunks:
            total += len(chunk)
            if total > intent.max_bytes:
                _request_discard_committed(intent.tenant_id, intent.object_key)
                raise FileTooLarge("The uploaded file is larger than allowed.")
            digest.update(chunk)
            if len(first) < filetypes.HEAD_BYTES:
                first += chunk[: filetypes.HEAD_BYTES - len(first)]
            tail = (tail + chunk)[-filetypes.TAIL_BYTES :]
            if csv is not None:
                csv.feed(chunk)
    finally:
        opened.close()
    if total != size:
        _request_discard_committed(intent.tenant_id, intent.object_key)
        raise _invalid("upload_id", "size_mismatch")
    code = filetypes.check_head(kind, bytes(first))
    if code is None and csv is None:
        code = filetypes.check_tail(kind, tail)
    if code is None and csv is not None:
        code = csv.finish()
    if code is not None:
        raise _reject(intent, code)
    return _Verified(digest.digest(), total, kind, opened.etag)


def _final_key(intent: UploadIntent, kind: FileKind) -> str:
    if intent.batch_id is not None:
        return import_key(intent.tenant_id, intent.batch_id, kind.ext)
    return document_key(intent.tenant_id, intent.document_id, intent.version_no, kind.ext)


def _promote(
    session: Session, store: ObjectStore, intent: UploadIntent, verified: _Verified
) -> str:
    """Copy the verified bytes to the final key (only if unchanged) and queue the staging copy's
    discard in the caller's transaction (W3-06)."""
    final = _final_key(intent, verified.kind)
    try:
        store.copy(
            intent.object_key, final, if_match=verified.etag, content_type=verified.kind.mime
        )
    except ObjectChanged as exc:
        _request_discard_committed(intent.tenant_id, intent.object_key)
        raise Conflict(
            "The file changed while it was being checked. Upload it again.", code="upload_changed"
        ) from exc
    except ObjectStoreError as exc:
        raise Conflict(
            "The file could not be stored. Try again.", code="storage_unavailable"
        ) from exc
    _request_discard(session, intent.object_key)
    return final


@contextmanager
def _undo_object_on_error(tenant_id: uuid.UUID, key: str) -> Iterator[None]:
    """On any error, queue the discard of the object just written (its transaction rolls back,
    so in a transaction of its own; W3-06: the worker deletes, after checking that no version
    uses the key)."""
    try:
        yield
    except BaseException:
        _request_discard_committed(tenant_id, key)
        raise


def _check_duplicate(
    session: Session, ctx: UserContext, store: ObjectStore, intent: UploadIntent, sha: bytes
) -> None:
    """Dedupe within the school. Only a duplicate the caller can see is reported (with its id);
    an invisible one is not revealed and the upload proceeds."""
    matches = repo.versions_with_sha(session, sha, exclude_statuses=UNUSABLE_STATUSES)
    if not matches:
        return
    visibility = _visibility(session, ctx)
    for match in matches:
        if repo.get_document(session, match.document_id, visibility=visibility) is not None:
            _request_discard_committed(intent.tenant_id, intent.object_key)
            raise Conflict(
                f"This file is already in SchoolOS as document {match.document_id}.",
                code="duplicate_document",
            )


def _resolve_metadata(data: DocumentCreate, rule: PurposeRule) -> tuple[str, str]:
    doc_type = data.doc_type or rule.default_doc_type
    if doc_type not in rule.doc_types:
        raise _invalid("doc_type", "doc_type_not_allowed_for_purpose")
    sensitivity = data.sensitivity or rule.min_sensitivity
    if SENSITIVITY_ORDER[sensitivity] < SENSITIVITY_ORDER[rule.min_sensitivity]:
        raise _invalid("sensitivity", "sensitivity_below_minimum")
    return doc_type, sensitivity


def register_document(session: Session, ctx: UserContext, data: DocumentCreate) -> DocumentOut:
    """Register an uploaded object as a new document (permission ``document.upload``).

    Returns the document with version 1 ``queued``; the malware scan runs in a worker.
    """
    intent = _claim_intent(session, ctx, data.upload_id, document_id=None)
    rule = purpose_rule(intent.purpose)
    doc_type, sensitivity = _resolve_metadata(data, rule)
    if data.academic_year_id is not None:
        try:
            tenancy.get_academic_year(session, data.academic_year_id)
        except NotFound as exc:
            raise _invalid("academic_year_id", "not_found") from exc
    acl = validate_acl(session, data.acl, upload_grant=ctx.scope_for(UPLOAD))
    store = get_object_store()
    verified = _verify_object(store, intent)
    _check_duplicate(session, ctx, store, intent, verified.sha256)
    final_key = _promote(session, store, intent, verified)

    tenant_id = repo.current_tenant_id(session)
    version_id = new_id()
    with _undo_object_on_error(intent.tenant_id, final_key), _db_errors():
        doc = repo.insert_document(
            session,
            id=intent.document_id,
            tenant_id=tenant_id,
            purpose=intent.purpose,
            doc_type=doc_type,
            title=data.title,
            issuer=data.issuer,
            issued_on=data.issued_on,
            academic_year_id=data.academic_year_id,
            language=data.language,
            sensitivity=sensitivity,
            current_version_id=version_id,
            created_by=ctx.user_id,
        )
        version = _insert_version(
            session,
            ctx=ctx,
            intent=intent,
            version_id=version_id,
            verified=verified,
            object_key=final_key,
        )
        repo.replace_acl(session, tenant_id, doc.id, acl)
        repo.consume_intent(session, intent.id, _now())
        _audit(
            session,
            "document.registered",
            doc.id,
            {
                "version_no": version.version_no,
                "purpose": doc.purpose,
                "doc_type": doc.doc_type,
                "sensitivity": doc.sensitivity,
                "mime_type": version.mime_type,
                "size_bytes": version.size_bytes,
                "acl_entries": len(acl),
                "batch_id": intent.batch_id,
            },
        )
        ops.enqueue_event(session, SCAN_EVENT, {"document_id": doc.id, "version_id": version.id})
    log.info("documents.registered", resource_type="document", resource_id=doc.id)
    return _load_out(session, ctx, doc)


def _insert_version(
    session: Session,
    *,
    ctx: UserContext,
    intent: UploadIntent,
    version_id: uuid.UUID,
    verified: _Verified,
    object_key: str,
) -> DocumentVersion:
    return repo.insert_version(
        session,
        id=version_id,
        tenant_id=intent.tenant_id,
        document_id=intent.document_id,
        version_no=intent.version_no,
        object_key=object_key,
        sha256=verified.sha256,
        mime_type=verified.kind.mime,
        size_bytes=verified.size,
        status="queued",
        created_by=ctx.user_id,
    )


def add_version(
    session: Session, ctx: UserContext, document_id: uuid.UUID, data: VersionCreate
) -> DocumentOut:
    """Register an uploaded object as the next version (permission ``document.upload``; the
    document must be visible to the caller). History is kept (FR-DOC-006). An archived
    document answers 409 ``document_archived`` (the row lock orders this against a concurrent
    archive or unarchive)."""
    doc = repo.get_document(
        session, document_id, visibility=_visibility(session, ctx), for_update=True
    )
    if doc is None:
        raise _not_found()
    _refuse_archived(doc)
    intent = _claim_intent(session, ctx, data.upload_id, document_id=doc.id)
    if intent.version_no != repo.max_version_no(session, doc.id) + 1:
        raise Conflict(
            "A newer version was added meanwhile. Upload the file again.", code="version_conflict"
        )
    store = get_object_store()
    verified = _verify_object(store, intent)
    latest = repo.get_version(session, doc.id)
    if latest is not None and latest.sha256 == verified.sha256:
        _request_discard_committed(intent.tenant_id, intent.object_key)
        raise Conflict("This file is the same as the current version.", code="version_unchanged")
    final_key = _promote(session, store, intent, verified)
    version_id = new_id()
    with _undo_object_on_error(intent.tenant_id, final_key), _db_errors():
        version = _insert_version(
            session,
            ctx=ctx,
            intent=intent,
            version_id=version_id,
            verified=verified,
            object_key=final_key,
        )
        updated = repo.update_document(
            session, doc.id, expected_version=None, current_version_id=version.id
        )
        if updated is None:  # pragma: no cover - row locked above
            raise _not_found()
        repo.consume_intent(session, intent.id, _now())
        _audit(
            session,
            "document.version_added",
            doc.id,
            {
                "version_no": version.version_no,
                "mime_type": version.mime_type,
                "size_bytes": version.size_bytes,
            },
        )
        ops.enqueue_event(session, SCAN_EVENT, {"document_id": doc.id, "version_id": version.id})
    return _load_out(session, ctx, updated)


# --- reads ----------------------------------------------------------------------------------


def list_documents(
    session: Session,
    ctx: UserContext,
    *,
    limit: int,
    before_id: uuid.UUID | None = None,
    purpose: str | None = None,
    doc_type: str | None = None,
    academic_year_id: uuid.UUID | None = None,
    status: str | None = None,
) -> tuple[list[DocumentOut], uuid.UUID | None]:
    """Documents the caller may see, newest first (permission ``document.read``); returns the
    page and the id to continue before, if there are more."""
    docs = repo.list_documents(
        session,
        _visibility(session, ctx),
        limit=limit + 1,
        before_id=before_id,
        purpose=purpose,
        doc_type=doc_type,
        academic_year_id=academic_year_id,
        status=status,
    )
    more = len(docs) > limit
    docs = docs[:limit]
    ids = [d.id for d in docs]
    versions = repo.versions_of(session, ids)
    acl = repo.acl_of(session, ids)
    by_doc: dict[uuid.UUID, list[DocumentVersion]] = {i: [] for i in ids}
    for v in versions:
        by_doc[v.document_id].append(v)
    viewer = _viewer(session, ctx, docs, versions)
    page = [_document_out(d, by_doc[d.id], acl[d.id], viewer) for d in docs]
    return page, (docs[-1].id if more and docs else None)


def get_document(session: Session, ctx: UserContext, document_id: uuid.UUID) -> DocumentDetail:
    """One document with its version history (permission ``document.read``; 404 when the
    caller's ACL/scope does not reach it)."""
    doc = repo.get_document(session, document_id, visibility=_visibility(session, ctx))
    if doc is None:
        raise _not_found()
    return DocumentDetail.model_validate(_load_out(session, ctx, doc, detail=True).model_dump())


def get_download_url(
    session: Session, ctx: UserContext, document_id: uuid.UUID, version_no: int | None = None
) -> DownloadUrlOut:
    """A presigned GET valid <= 5 minutes, forced to download as an attachment with the
    verified content type (FR-DOC-004). Only scanned (``ready``) versions are served; C3
    documents and raw import files (which may hold restricted columns that only the import's own
    sheet hides, FR-IMP-008) also need ``student.read_sensitive`` unless the caller uploaded
    them."""
    doc = repo.get_document(session, document_id, visibility=_visibility(session, ctx))
    if doc is None:
        raise _not_found()
    if _raw_restricted(doc) and not (
        ctx.has("student.read_sensitive") or doc.created_by == ctx.user_id
    ):
        raise Forbidden(
            "Restricted (C3) files can be opened only by staff allowed to see sensitive data.",
            code="sensitive_document",
        )
    if version_no is None:
        version = repo.latest_version_with_status(session, doc.id, ("ready",))
        if version is None:
            raise Conflict(
                "The file is still being checked. Try again shortly.", code="document_not_ready"
            )
    else:
        version = repo.get_version(session, doc.id, version_no)
        if version is None:
            raise NotFound("Version not found")
        if version.status != "ready":
            raise Conflict("This version cannot be downloaded yet.", code="document_not_ready")
    kind = filetypes.kind_for_content_type(version.mime_type)
    ext = kind.ext if kind else "bin"
    filename = f"{doc.doc_type}-{str(doc.id)[:8]}-v{version.version_no}.{ext}"
    ttl = get_settings().documents_download_url_ttl_s
    url, expires_at = get_object_store().presigned_get(
        key=version.object_key, content_type=version.mime_type, filename=filename, expires_s=ttl
    )
    _audit(session, "document.download_url_issued", doc.id, {"version_no": version.version_no})
    return DownloadUrlOut(
        url=url,
        expires_at=expires_at,
        version_no=version.version_no,
        mime_type=version.mime_type,
        filename=filename,
    )


# --- ACL and delete -------------------------------------------------------------------------


def set_acl(
    session: Session,
    ctx: UserContext,
    document_id: uuid.UUID,
    entries: Sequence[AclEntry],
    *,
    expected_version: int,
) -> DocumentOut:
    """Replace who can see a document (permission ``document.manage_acl``; ``If-Match``)."""
    doc = repo.get_document(
        session, document_id, visibility=_visibility(session, ctx), for_update=True
    )
    if doc is None:
        raise _not_found()
    if doc.version != expected_version:
        raise PreconditionFailed()
    acl = validate_acl(session, entries)
    before = {(a.principal_type, a.principal_ref) for a in repo.acl_of(session, [doc.id])[doc.id]}
    after = set(acl)
    with _db_errors():
        repo.replace_acl(session, doc.tenant_id, doc.id, acl)
        updated = repo.update_document(session, doc.id, expected_version=expected_version)
        if updated is None:  # pragma: no cover - row locked above
            raise PreconditionFailed()
        _audit(
            session,
            "document.acl_changed",
            doc.id,
            {
                "added": len(after - before),
                "removed": len(before - after),
                "entries": len(after),
                "principal_types": sorted({t for t, _ in after}),
            },
        )
        for hook in ACL_CHANGED_HOOKS:
            hook(session, doc.id)
    return _load_out(session, ctx, updated)


# --- metadata and archive (FR-DOC-005) --------------------------------------------------------

METADATA_FIELDS: Final = ("title", "doc_type", "language", "issuer", "issued_on")


def update_document(
    session: Session,
    ctx: UserContext,
    document_id: uuid.UUID,
    data: DocumentUpdate,
    *,
    expected_version: int,
) -> DocumentOut:
    """Change title, type, language, issuer or date (permission ``document.upload``; the
    document must be visible to the caller, like adding a version; ``If-Match``).

    404 outside the caller's ACL/scope, 412 for a stale version, 409 ``document_archived`` for
    an archived document, 422 ``doc_type_not_allowed_for_purpose``. Unchanged values are
    ignored (no new version). Audit: ``document.metadata_updated`` with the changed field
    NAMES only (titles and issuers may name people).
    """
    doc = repo.get_document(
        session, document_id, visibility=_visibility(session, ctx), for_update=True
    )
    if doc is None:
        raise _not_found()
    if doc.version != expected_version:
        raise PreconditionFailed()
    _refuse_archived(doc)
    values = {
        k: getattr(data, k)
        for k in METADATA_FIELDS
        if k in data.model_fields_set and getattr(data, k) != getattr(doc, k)
    }
    if "doc_type" in values and values["doc_type"] not in purpose_rule(doc.purpose).doc_types:
        raise _invalid("doc_type", "doc_type_not_allowed_for_purpose")
    if not values:
        return _load_out(session, ctx, doc)
    with _db_errors():
        updated = repo.update_document(session, doc.id, expected_version=expected_version, **values)
        if updated is None:  # pragma: no cover - row locked above
            raise PreconditionFailed()
        _audit(session, "document.metadata_updated", doc.id, {"fields": sorted(values)})
    changed = frozenset(values)
    for hook in METADATA_CHANGED_HOOKS:
        hook(session, doc.id, changed)
    return _load_out(session, ctx, updated)


def set_document_status(
    session: Session,
    ctx: UserContext,
    document_id: uuid.UUID,
    *,
    archived: bool,
    expected_version: int,
) -> DocumentOut:
    """Archive or unarchive a document (permission ``document.manage_acl``, like the ACL and
    delete; ``If-Match``). Archived documents are kept (history, evidence links, downloads)
    and listed with ``status=archived``. Already in that state: returned unchanged. Audit:
    ``document.archived`` / ``document.unarchived``; :data:`STATUS_CHANGED_HOOKS` run in the
    same transaction."""
    doc = repo.get_document(
        session, document_id, visibility=_visibility(session, ctx), for_update=True
    )
    if doc is None:
        raise _not_found()
    if doc.version != expected_version:
        raise PreconditionFailed()
    status = "archived" if archived else "active"
    if doc.status == status:
        return _load_out(session, ctx, doc)
    with _db_errors():
        updated = repo.update_document(
            session, doc.id, expected_version=expected_version, status=status
        )
        if updated is None:  # pragma: no cover - row locked above
            raise PreconditionFailed()
        _audit(
            session,
            "document.archived" if archived else "document.unarchived",
            doc.id,
            {"purpose": doc.purpose},
        )
        for hook in STATUS_CHANGED_HOOKS:
            hook(session, doc.id, status)
    return _load_out(session, ctx, updated)


def delete_document(session: Session, ctx: UserContext, document_id: uuid.UUID) -> None:
    """Hard-delete a document, its versions and ACL; objects are purged by a worker after
    commit (permission ``document.manage_acl``). Evidence still linked to a student record, or
    refused by a retention guard, answers 409 ``document_in_use``."""
    doc = repo.get_document(
        session, document_id, visibility=_visibility(session, ctx), for_update=True
    )
    if doc is None:
        raise _not_found()
    _delete(session, doc, reason=None)


RETENTION_REASONS: Final = frozenset({"import_raw_file", "records_sheet_read"})
"""Retention categories with a deletion job (docs/05 §13): import raw files, 90 days after
commit (FR-IMP-007); attendance and marks sheets, as soon as ``app.academics`` has read them
(FR-ATT-004, FR-MRK-004: the entries go back to the person, who commits them). Other categories
get a code when their trigger is specified."""


def delete_for_retention(session: Session, document_id: uuid.UUID, *, reason: str) -> bool:
    """System deletion of a document whose retention period ended (docs/05 §13, FR-DOC-007).

    For retention jobs, in the caller's ``tenant_session`` (RLS: this school's documents only;
    no user, so no visibility filter). Same effect as :func:`delete_document`: the document, its
    versions and ACL go now, every stored object after commit (outbox ``document.deleted`` ->
    ``documents.purge_objects``). ``DELETE_GUARDS`` and evidence still linked to a student
    record refuse it (409 with the guard's code / ``document_in_use``). Audit
    ``document.deleted`` with the system as actor and ``reason``. Returns False if the document
    does not exist (already deleted, or not this school's).
    """
    if reason not in RETENTION_REASONS:
        raise ValueError(f"unknown retention reason: {reason}")
    doc = repo.get_document(session, document_id, for_update=True)
    if doc is None:
        return False
    _delete(session, doc, reason=reason)
    return True


def _import_batch_ids(keys: Sequence[str]) -> list[uuid.UUID]:
    """Import batches whose raw files (``t/<tenant>/imports/<batch>/...``) the purge must also
    remove. The outbox event carries these IDs, never the keys themselves (invariant 5)."""
    out: set[uuid.UUID] = set()
    for key in keys:
        parts = key.split("/")
        if len(parts) > 3 and parts[2] == "imports":
            batch_id = _parse_uuid(parts[3])
            if batch_id is not None:
                out.add(batch_id)
    return sorted(out)


def _delete(session: Session, doc: Document, *, reason: str | None) -> None:
    for guard in DELETE_GUARDS:
        code = guard(session, doc.id)
        if code is not None:
            raise Conflict("This document must be kept (retention rules).", code=code)
    keys = repo.object_keys_of(session, doc.id)
    batch_ids = _import_batch_ids(keys)
    summary: dict[str, Any] = {"purpose": doc.purpose, "versions": len(keys)}
    if reason is not None:
        summary["reason"] = reason
    with _db_errors():
        for deleting in DELETING_HOOKS:
            deleting(session, doc.id)
        repo.delete_document(session, doc.id)
        _audit(session, "document.deleted", doc.id, summary, system=reason is not None)
        # ``discard``: an automatic retention deletion tags the objects for the bucket's 1-day
        # rule; a person's delete keeps the 90-day recovery window (docs/08 §7).
        ops.enqueue_event(
            session,
            DELETED_EVENT,
            {"document_id": doc.id, "batch_ids": batch_ids, "discard": reason is not None},
        )
        for hook in DELETED_HOOKS:
            hook(session, doc.id)


def purge_document_objects(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    batch_ids: Sequence[uuid.UUID] = (),
    *,
    discard: bool = False,
    store: ObjectStore | None = None,
) -> int:
    """Worker: remove every stored object of a deleted document (FR-DOC-007 storage part).

    ``discard`` (automatic retention deletions, :func:`delete_for_retention`): each object is
    tagged ``sos-lifecycle=discarded`` before its delete, so the bucket rule ``discarded-1d``
    expires the noncurrent bytes after one day. Otherwise (a person's delete) a plain delete
    keeps them for the 90-day recovery window (docs/08 §7). Idempotent: a retry lists what is
    left and removes only that."""
    store = store or get_object_store()
    remove = store.purge_prefix if discard else store.delete_prefix
    deleted = remove(document_prefix(tenant_id, document_id))
    for batch_id in batch_ids:
        deleted += remove(f"{tenant_prefix(tenant_id)}imports/{batch_id}/")
    return deleted


# --- scanning (worker) ----------------------------------------------------------------------


QUARANTINED_TEMPLATE: Final = "document.quarantined"


def _notify_quarantined(
    session: Session,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    uploader: uuid.UUID,
) -> None:
    """Tell the person who uploaded the version that the virus check blocked it (FR-DOC-002,
    FR-NOT-001), in the scan's transaction: ids only, once per version (dedupe key). Nobody is
    told when the uploader is no longer an active member of this school."""
    try:
        person = identity.get_user(session, uploader)
    except NotFound:
        return
    if person.status != "active":
        return
    notifications.notify(
        session,
        tenant_id=tenant_id,
        recipients=[person.membership_id],
        template_key=QUARANTINED_TEMPLATE,
        params={"document_id": str(document_id)},
        resource_id=document_id,
        dedupe_key=f"{QUARANTINED_TEMPLATE}:{version_id}",
    )


def scan_version(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    scanner: AvScanner | None = None,
    store: ObjectStore | None = None,
) -> str:
    """Scan one version and move it to ``ready`` or ``quarantined`` (FR-DOC-002, FR-DOC-008).

    Idempotent: a version already past scanning is left alone and its status returned.
    Raises ``ScannerUnavailable`` (the task retries) without changing a verdict.
    """
    scanner = scanner or build_scanner(get_settings())
    store = store or get_object_store()
    with tenant_session(tenant_id) as s:
        version = repo.set_version_status(
            s, version_id, "scanning", from_statuses=("queued", "scanning")
        )
        if version is None:
            current = repo.get_version(s, document_id, version_id=version_id)
            return current.status if current is not None else "missing"
        key = version.object_key
    result: ScanResult = scanner.scan(store.iter_chunks(key))
    with tenant_session(tenant_id) as s:
        if result.verdict is Verdict.INFECTED:
            updated = repo.set_version_status(
                s, version_id, "quarantined", error="malware_detected", from_statuses=("scanning",)
            )
            if updated is None:
                return "unchanged"
            _audit(
                s,
                "document.quarantined",
                document_id,
                {
                    "version_no": updated.version_no,
                    "engine": result.engine,
                    "signature": result.signature or "unknown",
                },
                system=True,
            )
            for hook in QUARANTINE_HOOKS:
                hook(s, document_id, version_id)
            _notify_quarantined(s, tenant_id, document_id, version_id, updated.created_by)
            log.warning(
                "documents.scan.quarantined", resource_type="document", resource_id=document_id
            )
            return "quarantined"
        # M1: evidence, register scans and import files are usable once clean. Text extraction,
        # chunking and embeddings (M2) plug in through READY_HOOKS.
        updated = repo.set_version_status(s, version_id, "ready", from_statuses=("scanning",))
        if updated is None:
            return "unchanged"
        for hook in READY_HOOKS:
            hook(s, document_id, version_id)
        log.info("documents.scan.clean", resource_type="document", resource_id=document_id)
        return "ready"


def mark_scan_failed(
    tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID, error_code: str
) -> None:
    """Worker gave up retrying (scanner unavailable): the version is ``failed``, never served."""
    with tenant_session(tenant_id) as s:
        updated = repo.set_version_status(
            s, version_id, "failed", error=error_code, from_statuses=("queued", "scanning")
        )
        if updated is not None:
            _audit(
                s,
                "document.scan_failed",
                document_id,
                {"version_no": updated.version_no, "code": error_code},
                system=True,
            )


def purge_expired_uploads(tenant_id: uuid.UUID, *, store: ObjectStore | None = None) -> int:
    """Worker (daily): delete expired, never-registered uploads and their objects."""
    store = store or get_object_store()
    purged = 0
    with tenant_session(tenant_id) as s:
        now = _now()
        stale = repo.expired_intents(s, now, limit=500)
        for intent in stale:
            if key_in_tenant(intent.object_key, tenant_id):
                _discard_now(store, intent.object_key)
        # A presigned POST stays usable for a few minutes after registration: drop any staging
        # object re-created meanwhile, then the used intent rows.
        used = repo.consumed_intents_before(s, now - dt.timedelta(days=1), limit=500)
        for intent in used:
            if key_in_tenant(intent.object_key, tenant_id):
                _discard_now(store, intent.object_key)
        repo.delete_intents(s, [i.id for i in (*stale, *used)])
        purged = len(stale)
    return purged


# --- service API for other modules (CONTRACT §8) --------------------------------------------


def evidence_exists(session: Session, document_id: uuid.UUID) -> bool:
    """True when ``document_id`` is a document of the current school with at least one version
    that is not quarantined/failed (used by change requests before accepting evidence)."""
    if repo.get_document(session, document_id) is None:
        return False
    usable = repo.latest_version_with_status(
        session,
        document_id,
        ("queued", "scanning", "extracting", "chunking", "embedding", "ready"),
    )
    return usable is not None


def is_visible(session: Session, ctx: UserContext, document_id: uuid.UUID) -> bool:
    """Whether the caller's ACL/scope reaches the document (for other modules' scoped reads)."""
    return repo.get_document(session, document_id, visibility=_visibility(session, ctx)) is not None


def is_own_upload(session: Session, ctx: UserContext, document_id: uuid.UUID) -> bool:
    """Whether the caller can see the document AND uploaded it themselves (for flows that read
    an upload and then delete it, such as attendance and marks sheets: audit DL-04)."""
    doc = repo.get_document(session, document_id, visibility=_visibility(session, ctx))
    return doc is not None and doc.created_by == ctx.user_id


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Location and facts of one stored version, for workers (imports, extraction)."""

    document_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    purpose: str
    object_key: str
    mime_type: str
    size_bytes: int
    sha256_hex: str
    status: str


def document_object(
    session: Session,
    document_id: uuid.UUID,
    version_no: int | None = None,
    *,
    require_ready: bool = True,
) -> StoredObject:
    """The stored object of a version (latest ``ready`` one by default) in the current tenant.

    Workers only: no caller scope is applied here; the job that calls it was authorised when it
    was started. Unscanned/quarantined versions are refused unless ``require_ready=False``.
    """
    doc = repo.get_document(session, document_id)
    if doc is None:
        raise _not_found()
    if version_no is None and require_ready:
        version = repo.latest_version_with_status(session, document_id, ("ready",))
    else:
        version = repo.get_version(session, document_id, version_no)
    if version is None or (require_ready and version.status != "ready"):
        raise Conflict("The file has not passed the malware scan.", code="document_not_ready")
    return StoredObject(
        document_id=doc.id,
        version_id=version.id,
        version_no=version.version_no,
        purpose=doc.purpose,
        object_key=version.object_key,
        mime_type=version.mime_type,
        size_bytes=version.size_bytes,
        sha256_hex=version.sha256.hex(),
        status=version.status,
    )


def read_document_object(
    session: Session, obj: StoredObject, *, store: ObjectStore | None = None
) -> bytes:
    """Read a whole stored object (bounded by the upload limit) and check its SHA-256."""
    tenant_id = repo.current_tenant_id(session)
    if not key_in_tenant(obj.object_key, tenant_id):
        raise _not_found()
    store = store or get_object_store()
    data = b"".join(store.iter_chunks(obj.object_key))
    if hashlib.sha256(data).hexdigest() != obj.sha256_hex:
        raise Conflict("The stored file changed after upload.", code="integrity_mismatch")
    return data


DISCARD_REASONS: Final = frozenset({"aadhaar_redacted", "aadhaar_unredactable"})
"""``error`` codes of discarded versions: replaced by a redacted copy, or not redactable."""
DISCARD_SWEEP_WINDOW: Final = dt.timedelta(days=7)
REDACTED_KINDS: Final = ("jpg", "png")


def _locked_version(
    session: Session, document_id: uuid.UUID, version_no: int
) -> tuple[Document, DocumentVersion]:
    doc = repo.get_document(session, document_id, for_update=True)
    if doc is None:
        raise _not_found()
    version = repo.get_version(session, document_id, version_no, for_update=True)
    if version is None:
        raise NotFound("Version not found")
    return doc, version


def _discard_version(session: Session, version: DocumentVersion, reason_code: str) -> bool:
    if version.status == "quarantined" and version.error in DISCARD_REASONS:
        return False
    updated = repo.set_version_status(session, version.id, "quarantined", error=reason_code)
    if updated is None:  # pragma: no cover - row locked by the caller
        return False
    _audit(
        session,
        "document.version_discarded",
        version.document_id,
        {"version_no": version.version_no, "reason": reason_code},
        system=True,
    )
    # IDs only: the worker reads the object key from the version row (an object key is not an
    # ID and must never need to pass the payload sanitiser; invariant 5).
    ops.enqueue_event(
        session, DISCARDED_EVENT, {"document_id": version.document_id, "version_id": version.id}
    )
    for hook in VERSION_DISCARDED_HOOKS:
        hook(session, version.document_id, version.id)
    return True


def discard_version(
    session: Session, document_id: uuid.UUID, version_no: int, reason_code: str
) -> bool:
    """PRV-016: a version whose file must not be kept (e.g. a register page that showed a full
    Aadhaar number and could not be redacted). Workers only, in the caller's transaction.

    The version becomes ``quarantined`` with ``error`` = ``reason_code`` (never served, not
    usable evidence) and its object is deleted after commit (outbox ``document.version.discarded``
    -> ``documents.discard_object``; :func:`sweep_discarded_objects` retries daily). The row is
    kept for history and audit (``document.version_discarded``, system actor). Returns False if
    the version was already discarded.
    """
    if reason_code not in DISCARD_REASONS:
        raise ValueError(f"unknown discard reason: {reason_code}")
    _doc, version = _locked_version(session, document_id, version_no)
    return _discard_version(session, version, reason_code)


def replace_with_redacted(
    session: Session,
    document_id: uuid.UUID,
    version_no: int,
    data: bytes,
    mime_type: str,
    *,
    regions: int,
    store: ObjectStore | None = None,
) -> int:
    """PRV-016: store ``data``, a redacted copy of version ``version_no`` made by a worker
    (sensitive regions blacked out, metadata stripped), as the next version, make it current,
    and discard the original (:func:`discard_version`, reason ``aadhaar_redacted``). Returns the
    new version number. Workers only, in the caller's transaction.

    The copy goes through the normal version lifecycle: stored (SSE-KMS) under
    ``v<n>/original.<ext>``, recorded ``queued`` with its SHA-256 and size, and malware-scanned
    (outbox ``document.version.registered``) before any download; like every queued version it
    is usable evidence at once. Audit ``document.version_redacted`` (system actor; IDs, counts
    and codes only). JPEG or PNG within the purpose's size limit only.
    """
    kind = filetypes.kind_for_content_type(mime_type)
    if kind is None or kind.key not in REDACTED_KINDS or filetypes.sniff(data) is not kind:
        raise UnsupportedFileType("A redacted copy must be a JPEG or PNG image.")
    doc, original = _locked_version(session, document_id, version_no)
    if original.status in UNUSABLE_STATUSES:
        raise Conflict("The original file is no longer available.", code="document_not_ready")
    if len(data) > purpose_rule(doc.purpose).max_bytes:
        raise FileTooLarge("The redacted copy is larger than allowed.")
    tenant_id = repo.current_tenant_id(session)
    new_no = repo.max_version_no(session, doc.id) + 1
    key = document_key(tenant_id, doc.id, new_no, kind.ext)
    store = store or get_object_store()
    try:
        store.put(key, data, kind.mime)
    except ObjectStoreError as exc:
        raise Conflict(
            "The redacted copy could not be stored. Try again.", code="storage_unavailable"
        ) from exc
    with _undo_object_on_error(tenant_id, key), _db_errors():
        version = repo.insert_version(
            session,
            id=new_id(),
            tenant_id=tenant_id,
            document_id=doc.id,
            version_no=new_no,
            object_key=key,
            sha256=hashlib.sha256(data).digest(),
            mime_type=kind.mime,
            size_bytes=len(data),
            status="queued",
            created_by=original.created_by,
        )
        updated = repo.update_document(
            session, doc.id, expected_version=None, current_version_id=version.id
        )
        if updated is None:  # pragma: no cover - row locked above
            raise _not_found()
        _discard_version(session, original, "aadhaar_redacted")
        _audit(
            session,
            "document.version_redacted",
            doc.id,
            {
                "version_no": original.version_no,
                "redacted_version_no": new_no,
                "regions": regions,
                "mime_type": kind.mime,
                "size_bytes": len(data),
            },
            system=True,
        )
        ops.enqueue_event(session, SCAN_EVENT, {"document_id": doc.id, "version_id": version.id})
    log.info("documents.version.redacted", resource_type="document", resource_id=doc.id)
    return new_no


def _payload_uuid(payload: Mapping[str, Any], name: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(payload[name]))
    except (KeyError, ValueError):
        return None


def _payload_time(payload: Mapping[str, Any]) -> dt.datetime | None:
    try:
        at = dt.datetime.fromisoformat(str(payload["requested_at"]))
    except (KeyError, ValueError):
        return None
    return at if at.tzinfo is not None else None


def _requested_final_key(tenant_id: uuid.UUID, payload: Mapping[str, Any]) -> str | None:
    """The document or import key an :data:`OBJECT_DISCARD_EVENT` names, rebuilt from IDs."""
    ext = payload.get("ext")
    if not isinstance(ext, str) or ext not in {k.ext for k in filetypes.KINDS.values()}:
        return None
    if (document_id := _payload_uuid(payload, "document_id")) is not None:
        version_no = payload.get("version_no")
        if isinstance(version_no, bool) or not isinstance(version_no, int) or version_no < 1:
            return None
        return document_key(tenant_id, document_id, version_no, ext)
    if (batch_id := _payload_uuid(payload, "batch_id")) is not None:
        return import_key(tenant_id, batch_id, ext)
    return None


def discard_unused_object(
    tenant_id: uuid.UUID, payload: Mapping[str, Any], *, store: ObjectStore | None = None
) -> bool:
    """Worker (outbox :data:`OBJECT_DISCARD_EVENT`, W3-06): discard an object the upload path
    no longer needs. Only the worker role may tag or delete under ``t/*``.

    The event carries IDs only, and the api's database role can write the outbox, so nothing in
    it is trusted: the key is rebuilt under this school's prefix from the IDs.

    - ``upload_id``: the staging object of that upload intent of this school (never the final
      copy; a staging object is never the file of record).
    - ``document_id`` + ``version_no`` + ``ext``, or ``batch_id`` + ``ext``: a document or
      import key written by a request that then failed. Discarded only if no version of this
      school points at it and the object is not newer than ``requested_at`` (a retry may have
      copied it again meanwhile).

    Idempotent (a key that is already gone is fine). Returns True when a key was discarded.
    """
    store = store or get_object_store()
    with tenant_session(tenant_id) as s:
        if (upload_id := _payload_uuid(payload, "upload_id")) is not None:
            intent = repo.get_intent(s, upload_id)
            key = intent.object_key if intent is not None else None
            if key is not None and not _UPLOAD_KEY.match(key):
                key = None
        else:
            key = _requested_final_key(tenant_id, payload)
            if key is not None and repo.object_key_in_use(s, key):
                log.warning(
                    "documents.discard.refused", tenant_id=str(tenant_id), error_code="key_in_use"
                )
                return False
    if key is None or not key_in_tenant(key, tenant_id):
        log.warning("documents.discard.refused", tenant_id=str(tenant_id), error_code="bad_event")
        return False
    if upload_id is None:
        requested_at = _payload_time(payload)
        head = store.head(key)
        if head is None:
            return False  # gone already: nothing to do
        modified = head.last_modified
        if requested_at is None or (modified is not None and modified > requested_at):
            log.info("documents.discard.skipped", tenant_id=str(tenant_id), error_code="newer")
            return False
    store.discard(key)
    log.info("documents.object.discarded", tenant_id=str(tenant_id))
    return True


def discard_object(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    object_key: str | None = None,
    *,
    store: ObjectStore | None = None,
) -> bool:
    """Worker (outbox ``document.version.discarded``): delete a discarded version's object.

    The event carries IDs only; the key comes from the version row, which must belong to this
    school's document and be discarded (``quarantined`` with a discard reason). No version row
    (the document was deleted meanwhile): nothing to do, the document's purge removes every
    object under its prefix. Events queued before the IDs-only payload also carry
    ``object_key`` (backward compatible): it must lie under this school's document and, while
    the version row exists, be that version's key; with no row it is discarded anyway.
    Idempotent. Returns True when an object was discarded.
    """
    prefix = document_prefix(tenant_id, document_id)
    if object_key is not None and (
        not key_in_tenant(object_key, tenant_id) or not object_key.startswith(prefix)
    ):
        return False
    with tenant_session(tenant_id) as s:
        version = repo.get_version(s, document_id, version_id=version_id)
        if version is None:
            key = object_key
        elif (
            version.status == "quarantined"
            and version.error in DISCARD_REASONS
            and object_key in (None, version.object_key)
        ):
            key = version.object_key
        else:
            key = None
    if key is None or not key_in_tenant(key, tenant_id) or not key.startswith(prefix):
        return False
    (store or get_object_store()).discard(key)
    log.info("documents.version.discarded", resource_type="document", resource_id=document_id)
    return True


def sweep_discarded_objects(tenant_id: uuid.UUID, *, store: ObjectStore | None = None) -> int:
    """Worker (daily): discard again the objects of versions discarded in the last 7 days, in
    case the outbox task gave up (idempotent; PRV-016). Returns how many keys were handled."""
    store = store or get_object_store()
    with tenant_session(tenant_id) as s:
        keys = [
            v.object_key
            for v in repo.discarded_versions(
                s, sorted(DISCARD_REASONS), since=_now() - DISCARD_SWEEP_WINDOW, limit=500
            )
            if key_in_tenant(v.object_key, tenant_id)
        ]
    for key in keys:
        store.discard(key)
    return len(keys)


def store_page_image(
    session: Session,
    document_id: uuid.UUID,
    version_no: int,
    page_no: int,
    png: bytes,
    *,
    store: ObjectStore | None = None,
) -> str:
    """Store a rendered page (PNG) under the version's ``derived/pages/`` (extraction, M1
    wave 2; citation previews, M2). Returns the object key."""
    if filetypes.sniff(png) is not filetypes.PNG:
        raise UnsupportedFileType("Page images must be PNG.")
    if repo.get_version(session, document_id, version_no) is None:
        raise _not_found()
    key = derived_key(
        repo.current_tenant_id(session), document_id, version_no, f"pages/{page_no}.png"
    )
    (store or get_object_store()).put(key, png, filetypes.PNG.mime)
    return key


# --- sheets: view, save as new version, download (FR-DOC-009..011) ------------------------------

SHEET_KINDS: Final[dict[str, SpreadsheetKind]] = {
    filetypes.XLSX.mime: "xlsx",
    filetypes.CSV.mime: "csv",
}
_SHEET_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class SheetFile:
    """A generated download: bytes in memory, never stored."""

    filename: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class _SheetSource:
    doc: Document
    version: DocumentVersion
    grid: sheets.Grid


def _sheet_source(
    session: Session,
    ctx: UserContext,
    document_id: uuid.UUID,
    *,
    version_no: int | None = None,
    for_update: bool = False,
) -> _SheetSource:
    """The document's newest checked version (or ``version_no``) read as a sheet: visible to
    the caller (404), C3 only for sensitive readers or the uploader (403, as downloads), not
    an import file (409 ``import_file_sheet``), scanned (409), XLSX or CSV (415) and within the
    viewer limits (413)."""
    doc = repo.get_document(
        session, document_id, visibility=_visibility(session, ctx), for_update=for_update
    )
    if doc is None:
        raise _not_found()
    if doc.sensitivity == "C3" and not (
        ctx.has("student.read_sensitive") or doc.created_by == ctx.user_id
    ):
        raise Forbidden(
            "Restricted (C3) files can be opened only by staff allowed to see sensitive data.",
            code="sensitive_document",
        )
    if doc.purpose == "import_file":
        # Its restricted (C3) columns are hidden only by the import's own sheet (FR-IMP-008).
        raise Conflict(
            "This file was uploaded for an import. Open it from the import to see its rows.",
            code="import_file_sheet",
        )
    if version_no is None:
        version = repo.latest_version_with_status(session, doc.id, ("ready",))
        if version is None:
            raise Conflict(
                "The file is still being checked. Try again shortly.", code="document_not_ready"
            )
    else:
        version = repo.get_version(session, doc.id, version_no)
        if version is None:
            raise NotFound("Version not found")
        if version.status != "ready":
            raise Conflict("This version cannot be opened yet.", code="document_not_ready")
    kind = SHEET_KINDS.get(version.mime_type)
    if kind is None:
        raise UnsupportedFileType(
            "Only spreadsheets (XLSX or CSV) open as a sheet. Download the file instead.",
            code="not_a_sheet",
        )
    limits = sheets.sheet_config().limits
    if version.size_bytes > limits.max_file_bytes:
        raise FileTooLarge(
            f"Sheets larger than {limits.max_file_bytes // 2**20} MB cannot be opened here. "
            "Download the file instead."
        )
    obj = StoredObject(
        document_id=doc.id,
        version_id=version.id,
        version_no=version.version_no,
        purpose=doc.purpose,
        object_key=version.object_key,
        mime_type=version.mime_type,
        size_bytes=version.size_bytes,
        sha256_hex=version.sha256.hex(),
        status=version.status,
    )
    data = read_document_object(session, obj)
    try:
        grid = sheets.read_grid(data, kind, limits)
    except SpreadsheetError as exc:
        raise Conflict(
            "This spreadsheet cannot be shown here. Download the file instead.", code=exc.code
        ) from exc
    return _SheetSource(doc, version, grid)


def _require_step_up(ctx: UserContext) -> None:
    """FR-EXP-004 / SEC-005: an MFA sign-in within 5 minutes (428 ``step_up_required``)."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def _sheet_read_only(ctx: UserContext, source: _SheetSource) -> SheetReadOnly | None:
    """Why the caller cannot save edits of this sheet as a new version (``None``: they can);
    the first rule that applies, in this order."""
    doc, grid = source.doc, source.grid
    rules: tuple[tuple[bool, SheetReadOnly], ...] = (
        (not ctx.has(UPLOAD), "no_permission"),
        (not purpose_rule(doc.purpose).versionable or grid.kind != "xlsx", "not_versionable"),
        (doc.status == "archived", "archived"),
        (grid.sheet_count > 1, "several_sheets"),
        (grid.formula_cells > 0, "formulas"),
        (doc.current_version_id != source.version.id, "newer_version"),
    )
    return next((reason for applies, reason in rules if applies), None)


_SAVE_REFUSALS: Final[dict[str, tuple[str, str]]] = {
    "no_permission": ("You cannot upload new versions of documents.", "forbidden"),
    "not_versionable": (
        "This file cannot get new versions here. Download it, edit it and upload it again.",
        "not_versionable",
    ),
    "archived": ("This document is archived. Unarchive it first.", "document_archived"),
    "several_sheets": (
        "This workbook has more than one sheet. Edit it in a spreadsheet program and upload a "
        "new version, so the other sheets are kept.",
        "workbook_has_several_sheets",
    ),
    "formulas": (
        "This sheet has formulas. Edit it in a spreadsheet program and upload a new version, so "
        "the formulas are kept.",
        "workbook_has_formulas",
    ),
    "newer_version": (
        "A newer version of this document is being checked. Reload the sheet when it is ready.",
        "version_conflict",
    ),
}


def _sheet_edits(
    data: Sequence[SheetCellEdit], grid: sheets.Grid
) -> dict[sheets.CellKey, str | None]:
    """Checked edits keyed by cell; 422 without echoing any value (invariants 4 and 5)."""
    cfg = sheets.sheet_config()
    problems: list[dict[str, str]] = []
    out: dict[sheets.CellKey, str | None] = {}
    if len(data) > cfg.max_edit_cells:
        raise _invalid("edits", "too_many_edits")
    for i, edit in enumerate(data):
        where = f"edits.{i}"
        key = (edit.row_no, edit.column)
        if key in out:
            problems.append(_error(where, "duplicate_cell"))
        if edit.row_no > len(grid.rows):
            problems.append(_error(f"{where}.row_no", "unknown_row"))
        if edit.column >= sheets.sheet_config().limits.max_columns:
            problems.append(_error(f"{where}.column", "unknown_column"))
        value = edit.value
        if value is not None:
            if contains_full_aadhaar(value):
                problems.append(
                    {
                        "field": f"{where}.value",
                        "code": "aadhaar_full_number_rejected",
                        "message_key": "errors.aadhaar_last4_only",
                    }
                )
            elif _SHEET_CONTROL_RE.search(value):
                problems.append(_error(f"{where}.value", "control_characters"))
            elif len(value) > cfg.max_value_chars:
                problems.append(_error(f"{where}.value", "too_long"))
        out[key] = value
    if problems:
        raise ValidationFailed(problems)
    return out


def _sheet_offset(cursor: str | None) -> int:
    decoded = decode_cursor(cursor)
    if decoded is None:
        return 0
    value = decoded.get("o")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _invalid("cursor", "invalid")
    return value


def get_sheet(
    session: Session,
    ctx: UserContext,
    document_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
) -> DocumentSheetOut:
    """A page of the first worksheet of the newest checked XLSX/CSV version (permission
    ``document.read``; FR-DOC-009). Row 1 gives the column names; Aadhaar-like numbers are
    masked; formulas are shown as text and never evaluated."""
    source = _sheet_source(session, ctx, document_id)
    grid = source.grid
    offset = _sheet_offset(cursor)
    first = offset + 2  # data rows start at spreadsheet row 2
    page = range(first, min(first + limit, len(grid.rows) + 1))
    reason = _sheet_read_only(ctx, source)
    return DocumentSheetOut(
        document_id=source.doc.id,
        version_no=source.version.version_no,
        version=source.doc.version,
        kind=grid.kind,
        sheet_count=grid.sheet_count,
        editable=reason is None,
        read_only_reason=reason,
        total_rows=grid.data_rows,
        offset=offset,
        columns=[
            DocSheetColumnOut(
                index=i, letter=column_letter(i), header=sheets.cell_display(grid.cell(1, i))
            )
            for i in range(grid.width)
        ],
        data=[
            DocSheetRowOut(
                row_no=row_no,
                cells=[
                    DocSheetCellOut(
                        value=sheets.cell_display(grid.cell(row_no, i)),
                        formula=grid.cell(row_no, i).formula,
                    )
                    for i in range(grid.width)
                ],
            )
            for row_no in page
        ],
        next_cursor=encode_cursor({"o": offset + limit})
        if offset + limit < grid.data_rows
        else None,
    )


def _cell_refs(edits: Mapping[sheets.CellKey, str | None]) -> list[str]:
    return [f"{column_letter(c)}{r}" for r, c in sorted(edits)][:100]


def save_sheet_version(
    session: Session,
    ctx: UserContext,
    document_id: uuid.UUID,
    data: SheetSaveIn,
    *,
    expected_version: int,
    store: ObjectStore | None = None,
) -> DocumentOut:
    """Save edited cells as the next version (permission ``document.upload``; ``If-Match``;
    FR-DOC-010). The stored file is never changed: the edited sheet is written as a new XLSX
    version (values only; Aadhaar-like numbers masked), stored SSE-KMS under the tenant prefix,
    made current, and queued for the malware scan and indexing like any upload (FR-DOC-002,
    FR-DOC-006). Refused (409) for import files and CSVs, archived documents, workbooks with
    more than one sheet or with formulas, and when ``base_version_no`` is not the current
    version. Audit ``document.version_added`` and ``document.sheet_edited`` (cell references and
    counts, never values)."""
    source = _sheet_source(
        session, ctx, document_id, version_no=data.base_version_no, for_update=True
    )
    doc = source.doc
    if doc.version != expected_version:
        raise PreconditionFailed("The document was changed meanwhile. Reload the sheet.")
    reason = _sheet_read_only(ctx, source)
    if reason is not None:
        message, code = _SAVE_REFUSALS[reason]
        if reason == "no_permission":
            raise Forbidden(message, code=code)
        raise Conflict(message, code=code)
    # Only cells whose value really changes count (a rebuilt workbook never has the same bytes
    # as the uploaded one, so comparing file hashes would never find "nothing changed").
    edits = sheets.changed_cells(source.grid, _sheet_edits(data.edits, source.grid))
    if not edits:
        raise Conflict("The sheet is the same as the current version.", code="version_unchanged")
    edited = sheets.apply_edits(source.grid, edits)
    content = sheets.build_xlsx(edited, title=doc.title)
    rule = purpose_rule(doc.purpose)
    if len(content) > rule.max_bytes:
        raise FileTooLarge("The edited sheet is larger than allowed for this document.")
    digest = hashlib.sha256(content).digest()
    tenant_id = repo.current_tenant_id(session)
    new_no = repo.max_version_no(session, doc.id) + 1
    key = document_key(tenant_id, doc.id, new_no, filetypes.XLSX.ext)
    store = store or get_object_store()
    try:
        store.put(key, content, filetypes.XLSX.mime)
    except ObjectStoreError as exc:
        raise Conflict(
            "The new version could not be stored. Try again.", code="storage_unavailable"
        ) from exc
    with _undo_object_on_error(tenant_id, key), _db_errors():
        version = repo.insert_version(
            session,
            id=new_id(),
            tenant_id=tenant_id,
            document_id=doc.id,
            version_no=new_no,
            object_key=key,
            sha256=digest,
            mime_type=filetypes.XLSX.mime,
            size_bytes=len(content),
            status="queued",
            created_by=ctx.user_id,
        )
        updated = repo.update_document(
            session, doc.id, expected_version=None, current_version_id=version.id
        )
        if updated is None:  # pragma: no cover - row locked above
            raise _not_found()
        _audit(
            session,
            "document.version_added",
            doc.id,
            {
                "version_no": version.version_no,
                "mime_type": version.mime_type,
                "size_bytes": version.size_bytes,
                "source": "sheet_editor",
                "base_version_no": source.version.version_no,
            },
        )
        _audit(
            session,
            "document.sheet_edited",
            doc.id,
            {
                "version_no": version.version_no,
                "base_version_no": source.version.version_no,
                "edited_cells": len(edits),
                "cells": _cell_refs(edits),
            },
        )
        ops.enqueue_event(session, SCAN_EVENT, {"document_id": doc.id, "version_id": version.id})
    log.info(
        "documents.sheet.saved", resource_type="document", resource_id=doc.id, count=len(edits)
    )
    return _load_out(session, ctx, updated)


def export_sheet(
    session: Session, ctx: UserContext, document_id: uuid.UUID, data: SheetExportIn
) -> SheetFile:
    """The sheet (with any unsaved ``edits``) as CSV (UTF-8 with BOM) or XLSX (permission
    ``document.read``; FR-DOC-011). Personal (C2) and restricted (C3) documents need a recent
    sign-in with MFA (428 ``step_up_required``; FR-EXP-004). Row 1 stays the header row;
    Aadhaar-like numbers are masked, formula-like text is neutralised and XLSX cells are text
    (SEC-017). Audit ``document.sheet_exported`` (counts only) in this transaction; nothing is
    stored."""
    source = _sheet_source(session, ctx, document_id, version_no=data.base_version_no)
    if source.doc.sensitivity != "C1":
        _require_step_up(ctx)
    edits = _sheet_edits(data.edits, source.grid)
    grid = sheets.apply_edits(source.grid, edits)
    values = [
        [display_text(grid.cell(r, c).value) for c in range(grid.width)]
        for r in range(1, len(grid.rows) + 1)
    ]
    header, rows = values[0], values[1:]
    stem = f"{source.doc.doc_type}-{str(source.doc.id)[:8]}-v{source.version.version_no}-sheet"
    if data.format == "csv":
        out = SheetFile(f"{stem}.csv", CSV_MIME, write_csv(header, rows))
    else:
        out = SheetFile(
            f"{stem}.xlsx",
            XLSX_MIME,
            write_xlsx(
                header, rows, title=source.doc.title, watermark=sheets.sheet_config().watermark
            ),
        )
    _audit(
        session,
        "document.sheet_exported",
        source.doc.id,
        {
            "format": data.format,
            "version_no": source.version.version_no,
            "rows": len(rows),
            "columns": grid.width,
            "edited_cells": len(edits),
        },
    )
    log.info(
        "documents.sheet.exported",
        resource_type="document",
        resource_id=source.doc.id,
        count=len(rows),
    )
    return out


# --- generated documents (app.certificates; FR-CERT-010) -----------------------------------------

GENERATED_PURPOSES: Final = frozenset({"certificate"})
"""Purposes of documents the system writes itself; never uploadable (``UploadCreate``)."""


def store_generated_document(
    session: Session,
    *,
    purpose: str,
    title: str,
    content: bytes,
    created_by: uuid.UUID,
    acl_roles: Sequence[str],
    issued_on: dt.date | None = None,
    academic_year_id: uuid.UUID | None = None,
    language: str | None = None,
    store: ObjectStore | None = None,
) -> uuid.UUID:
    """Store a PDF the system generated (a certificate, FR-CERT-010) as a new document of the
    current school: version 1 under ``t/<tenant>/docs/<id>/v1/original.pdf`` (private bucket,
    SSE-KMS through the object store), ``doc_type`` = the purpose's only type, the purpose's
    minimum sensitivity, an ACL of ``acl_roles`` (role keys), and queued for the malware scan
    and indexing like any upload (FR-DOC-002, FR-DOC-008). Worker use: the caller authorised the
    work when it was started; ``created_by`` is the user the document is attributed to.
    Audit ``document.generated`` (system actor). Returns the document id."""
    if purpose not in GENERATED_PURPOSES:
        raise ValueError(f"not a generated purpose: {purpose}")
    rule = purpose_rule(purpose)
    if filetypes.sniff(content[:16]) is not filetypes.PDF:
        raise UnsupportedFileType("Generated documents are PDF files.")
    if len(content) > rule.max_bytes:
        raise FileTooLarge("The generated file is larger than allowed for documents.")
    tenant_id = repo.current_tenant_id(session)
    document_id, version_id = new_id(), new_id()
    key = document_key(tenant_id, document_id, 1, filetypes.PDF.ext)
    store = store or get_object_store()
    store.put(key, content, filetypes.PDF.mime)
    with _undo_object_on_error(tenant_id, key), _db_errors():
        repo.insert_document(
            session,
            id=document_id,
            tenant_id=tenant_id,
            purpose=purpose,
            doc_type=rule.default_doc_type,
            title=title,
            issued_on=issued_on,
            academic_year_id=academic_year_id,
            language=language,
            sensitivity=rule.min_sensitivity,
            current_version_id=version_id,
            created_by=created_by,
        )
        repo.insert_version(
            session,
            id=version_id,
            tenant_id=tenant_id,
            document_id=document_id,
            version_no=1,
            object_key=key,
            sha256=hashlib.sha256(content).digest(),
            mime_type=filetypes.PDF.mime,
            size_bytes=len(content),
            status="queued",
            created_by=created_by,
        )
        repo.replace_acl(session, tenant_id, document_id, [("role", r) for r in acl_roles])
        _audit(
            session,
            "document.generated",
            document_id,
            {
                "purpose": purpose,
                "doc_type": rule.default_doc_type,
                "sensitivity": rule.min_sensitivity,
                "size_bytes": len(content),
                "acl_entries": len(acl_roles),
            },
            system=True,
        )
        ops.enqueue_event(
            session, SCAN_EVENT, {"document_id": document_id, "version_id": version_id}
        )
    log.info("documents.generated", resource_type="document", resource_id=document_id)
    return document_id


def generated_download_url(
    session: Session,
    document_id: uuid.UUID,
    *,
    filename: str,
    ttl_s: int,
    store: ObjectStore | None = None,
) -> tuple[str, dt.datetime]:
    """A presigned GET (at most 5 minutes, ``Content-Disposition: attachment``) for the latest
    scanned version of a generated document of the current school (FR-DOC-004). The caller
    (``app.certificates``) checked who may download it and audits the download. 409
    ``document_not_ready`` until the malware scan passed."""
    doc = repo.get_document(session, document_id)
    if doc is None or doc.purpose not in GENERATED_PURPOSES:
        raise _not_found()
    version = repo.latest_version_with_status(session, doc.id, ("ready",))
    if version is None:
        raise Conflict(
            "The file is still being checked. Try again shortly.", code="document_not_ready"
        )
    ttl = min(ttl_s, get_settings().documents_download_url_ttl_s)
    return (store or get_object_store()).presigned_get(
        key=version.object_key, content_type=version.mime_type, filename=filename, expires_s=ttl
    )


def archive_generated_document(session: Session, document_id: uuid.UUID) -> bool:
    """Archive a generated document (e.g. its certificate was cancelled, FR-CERT-008): kept
    for the record, out of retrieval (:data:`STATUS_CHANGED_HOOKS`). System actor; returns
    False when it is already archived or not a generated document of this school."""
    doc = repo.get_document(session, document_id, for_update=True)
    if doc is None or doc.purpose not in GENERATED_PURPOSES or doc.status == "archived":
        return False
    with _db_errors():
        updated = repo.update_document(
            session, doc.id, expected_version=doc.version, status="archived"
        )
        if updated is None:  # pragma: no cover - row locked above
            return False
        _audit(session, "document.archived", doc.id, {"purpose": doc.purpose}, system=True)
        for hook in STATUS_CHANGED_HOOKS:
            hook(session, doc.id, "archived")
    return True


# --- export files (app.exports; docs/04 §8.2 t/<tenant>/exports/<export_id>/<file>) ---------------


def store_export_file(
    session: Session,
    export_id: uuid.UUID,
    filename: str,
    data: bytes,
    content_type: str,
    *,
    store: ObjectStore | None = None,
) -> str:
    """Store a generated export file (worker) in the private bucket under the current school's
    ``exports/`` prefix (SSE-KMS through the object store); returns the object key. Export
    files are not documents: they are never scanned, indexed or listed, and are deleted after
    the export retention period (:func:`delete_export_files`)."""
    if not data:
        raise ValueError("export files are never empty")
    key = export_key(repo.current_tenant_id(session), export_id, filename)
    # Tagged for the bucket's 7-day lifecycle rule (docs/05 §13), a backstop to the purge job.
    (store or get_object_store()).put(key, data, content_type, lifecycle=LIFECYCLE_EXPORT)
    return key


def export_download_url(
    session: Session,
    export_id: uuid.UUID,
    object_key: str,
    *,
    content_type: str,
    filename: str,
    ttl_s: int,
    store: ObjectStore | None = None,
) -> tuple[str, dt.datetime]:
    """A presigned GET (at most 5 minutes, ``Content-Disposition: attachment``) for an export
    file of the current school (FR-DOC-004). The caller (``app.exports``) has checked who may
    download it and audits the download."""
    tenant_id = repo.current_tenant_id(session)
    if not (
        key_in_tenant(object_key, tenant_id)
        and object_key.startswith(export_prefix(tenant_id, export_id))
    ):
        raise _not_found()
    ttl = min(ttl_s, get_settings().documents_download_url_ttl_s)
    return (store or get_object_store()).presigned_get(
        key=object_key, content_type=content_type, filename=filename, expires_s=ttl
    )


def delete_export_files(
    session: Session, export_id: uuid.UUID, *, store: ObjectStore | None = None
) -> int:
    """Delete every stored file of one export of the current school (retention, docs/05 §13).
    An automatic deletion: the files are discarded (bucket rule ``discarded-1d``, docs/08 §7)."""
    prefix = export_prefix(repo.current_tenant_id(session), export_id)
    return (store or get_object_store()).purge_prefix(prefix)


# --- the school's full data export (app.admin; FR-ADM-001, US-1201) -------------------------------

TENANT_EXPORT_MIME: Final = "application/zip"


def export_records(session: Session) -> list[RecordTable]:
    """Worker only (``app.admin`` full export, ``tenant.export_all`` checked by the caller):
    every document, version and ACL entry of the current school as record tables (metadata;
    the files come from :func:`export_files`). Titles and issuers are C2 at most."""
    return repo.export_record_tables(session)


WITHHELD_FILE_REASON: Final = "restricted, not included"
WITHHELD_TABLE: Final = "documents_withheld"


def _export_split(
    session: Session, *, include_sensitive: bool
) -> tuple[list[StoredObject], list[tuple[DocumentVersion, Document]]]:
    shipped: list[StoredObject] = []
    withheld: list[tuple[DocumentVersion, Document]] = []
    for v, doc in repo.ready_versions(session):
        if _raw_restricted(doc) and not include_sensitive:
            withheld.append((v, doc))
            continue
        shipped.append(
            StoredObject(
                document_id=v.document_id,
                version_id=v.id,
                version_no=v.version_no,
                purpose=doc.purpose,
                object_key=v.object_key,
                mime_type=v.mime_type,
                size_bytes=v.size_bytes,
                sha256_hex=v.sha256.hex(),
                status=v.status,
            )
        )
    return shipped, withheld


def export_files(session: Session, *, include_sensitive: bool = False) -> list[StoredObject]:
    """Worker only (as :func:`export_records`): every version of every document that passed the
    malware scan, oldest first. Quarantined, failed and discarded versions (PRV-016) are never
    exported. Restricted files (C3 documents and raw import spreadsheets, whose stored file only
    ``student.read_sensitive`` holders open) are left out unless ``include_sensitive`` (the
    export was asked with restricted details; audit 2026-10-04, DL-07): they are listed in
    :func:`export_withheld_files` instead."""
    return _export_split(session, include_sensitive=include_sensitive)[0]


def export_withheld_files(session: Session, *, include_sensitive: bool) -> list[RecordTable]:
    """Worker only: the ``documents_withheld`` record table of the full export, one row per
    ready version :func:`export_files` leaves out (document id, version, title, classification
    and the reason ``restricted, not included``; no file bytes). Empty when
    ``include_sensitive`` (DL-07). Titles are C2 at most."""
    _, withheld = _export_split(session, include_sensitive=include_sensitive)
    rows: list[tuple[object, ...]] = [
        (
            doc.id,
            v.id,
            v.version_no,
            doc.title,
            doc.purpose,
            doc.sensitivity,
            WITHHELD_FILE_REASON,
        )
        for v, doc in withheld
    ]
    return [
        RecordTable(
            name=WITHHELD_TABLE,
            columns=(
                "document_id",
                "version_id",
                "version_no",
                "title",
                "purpose",
                "classification",
                "reason",
            ),
            rows=rows,
            notes=("restricted_files_withheld",) if rows else (),
        )
    ]


def iter_export_file(
    tenant_id: uuid.UUID, obj: StoredObject, *, store: ObjectStore | None = None
) -> Iterator[bytes]:
    """The bytes of one stored version of ``tenant_id``'s documents, in chunks, for the full
    export (no transaction held while streaming). The SHA-256 recorded at upload is checked when
    the last chunk has been read: a changed object raises ``Conflict`` (``integrity_mismatch``)
    and the export fails instead of shipping it."""
    if not key_in_tenant(obj.object_key, tenant_id):
        raise _not_found()
    digest = hashlib.sha256()
    for chunk in (store or get_object_store()).iter_chunks(obj.object_key):
        digest.update(chunk)
        yield chunk
    if digest.hexdigest() != obj.sha256_hex:
        raise Conflict("The stored file changed after upload.", code="integrity_mismatch")


def open_tenant_export(
    session: Session, export_id: uuid.UUID, *, store: ObjectStore | None = None
) -> tuple[str, ObjectWriter]:
    """A streamed upload for the school's full export archive (worker): the key
    ``t/<tenant_id>/tenant-export/<export_id>.zip`` in the private bucket (SSE-KMS), tagged for
    the bucket's ``tenant-export-2d`` rule. Nothing exists until the writer is closed."""
    key = tenant_export_key(repo.current_tenant_id(session), export_id)
    writer = (store or get_object_store()).open_writer(
        key, TENANT_EXPORT_MIME, lifecycle=LIFECYCLE_TENANT_EXPORT
    )
    return key, writer


def _tenant_export_key_checked(session: Session, export_id: uuid.UUID, object_key: str) -> str:
    expected = tenant_export_key(repo.current_tenant_id(session), export_id)
    if object_key != expected:
        raise _not_found()
    return expected


def tenant_export_download_url(
    session: Session,
    export_id: uuid.UUID,
    object_key: str,
    *,
    filename: str,
    ttl_s: int,
    store: ObjectStore | None = None,
) -> tuple[str, dt.datetime]:
    """A presigned GET (at most 5 minutes, ``attachment``) for the full export archive of the
    current school. The caller (``app.admin``) has checked who may download it and audits it."""
    key = _tenant_export_key_checked(session, export_id, object_key)
    ttl = min(ttl_s, get_settings().documents_download_url_ttl_s)
    return (store or get_object_store()).presigned_get(
        key=key, content_type=TENANT_EXPORT_MIME, filename=filename, expires_s=ttl
    )


def delete_tenant_export(
    session: Session, export_id: uuid.UUID, object_key: str, *, store: ObjectStore | None = None
) -> None:
    """Delete the full export archive of the current school (retention: 24 hours after it was
    ready, or an abandoned build). An automatic deletion: the archive is discarded, so the
    bucket rule ``discarded-1d`` expires the noncurrent copy after a day (docs/08 §7)."""
    key = _tenant_export_key_checked(session, export_id, object_key)
    (store or get_object_store()).discard(key)


__all__ = [
    "ACL_CHANGED_HOOKS",
    "DELETED_HOOKS",
    "DELETE_GUARDS",
    "DELETING_HOOKS",
    "DISCARDED_EVENT",
    "DISCARD_REASONS",
    "DISCARD_TASK",
    "GENERATED_PURPOSES",
    "OBJECT_DISCARD_EVENT",
    "OBJECT_DISCARD_TASK",
    "QUARANTINE_HOOKS",
    "READY_HOOKS",
    "RETENTION_REASONS",
    "STATUS_CHANGED_HOOKS",
    "TENANT_EXPORT_MIME",
    "VERSION_DISCARDED_HOOKS",
    "WITHHELD_FILE_REASON",
    "WITHHELD_TABLE",
    "FileTooLarge",
    "ObjectStore",
    "ObjectWriter",
    "SheetFile",
    "StoredObject",
    "UnsupportedFileType",
    "add_version",
    "archive_generated_document",
    "count_tenant_objects",
    "create_upload",
    "delete_document",
    "delete_export_files",
    "delete_for_retention",
    "delete_tenant_export",
    "discard_object",
    "discard_unused_object",
    "discard_version",
    "document_object",
    "evidence_exists",
    "export_download_url",
    "export_files",
    "export_records",
    "export_withheld_files",
    "export_sheet",
    "generated_download_url",
    "get_document",
    "get_download_url",
    "get_sheet",
    "is_visible",
    "iter_export_file",
    "list_documents",
    "mark_scan_failed",
    "open_tenant_export",
    "purge_document_objects",
    "purge_expired_uploads",
    "purge_tenant_data",
    "purge_tenant_objects",
    "read_document_object",
    "register_document",
    "replace_with_redacted",
    "save_sheet_version",
    "scan_version",
    "set_acl",
    "set_document_status",
    "store_export_file",
    "store_generated_document",
    "store_page_image",
    "sweep_discarded_objects",
    "tenant_data_counts",
    "tenant_export_download_url",
    "update_document",
    "validate_acl",
]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Documents with their versions and ACLs, upload intents (files: see below).
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=("kb.upload_intents", "kb.documents"),
    cascaded=("kb.document_versions", "kb.document_acl"),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="documents", count=tenant_data_counts, purge=purge_tenant_data)
)


def count_tenant_objects(tenant_id: uuid.UUID, *, store: ObjectStore | None = None) -> int:
    """Offboarding: current files under ``t/<tenant_id>/`` (documents, imports, exports,
    uploads)."""
    return (store or get_object_store()).count_prefix(tenant_prefix(tenant_id))


def purge_tenant_objects(tenant_id: uuid.UUID, *, store: ObjectStore | None = None) -> int:
    """Offboarding (ADR-0029): discard every file under ``t/<tenant_id>/`` (tag
    ``sos-lifecycle=discarded``, delete; the noncurrent versions expire after a day).
    Idempotent and resumable; returns the number of files deleted in this call."""
    deleted = (store or get_object_store()).purge_prefix(tenant_prefix(tenant_id))
    log.info("documents.tenant_objects_purged", tenant_id=str(tenant_id), objects=deleted)
    return deleted


tenancy.register_object_owner(
    tenancy.TenantObjectOwner(
        name="documents", count=count_tenant_objects, purge=purge_tenant_objects
    )
)
