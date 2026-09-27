"""Apply synthetic students and documents through the module services (docs/12 §3). NEVER real
data; callers have checked ``SOS_ENV`` (local/ci) before importing this module.

Students (:mod:`app.devtools.students`) are created as the school's first office admin
(``student.create`` / ``student.update_nonidentity``) through ``students.create_student``
(values from every source + current-year enrolment), ``students.add_guardian`` and, for the
DQ-012 injection, ``students.enrol`` into last year's section. There is no bulk create in the
students service, so students go in chunks of :data:`CHUNK` per transaction: a chunk is atomic,
which is what makes an interrupted run resumable. RLS, validation (incl. full-Aadhaar rejection),
C3 encryption, audit events and the ``student.values.changed`` outbox events apply exactly as
for a clerk.

Documents (:mod:`app.devtools.register_pages`, :mod:`app.devtools.corpus`) go through
``documents.create_upload`` -> presigned POST to the object store (local SeaweedFS) ->
``documents.register_document``. The malware scan (dev scanner) runs in the worker afterwards,
as for any upload. The uploader is injectable (tests use an in-memory store).

Idempotent: students are found by admission number (canonical ``admission_no``), documents by
title; only what is missing is added.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Final

import httpx
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.db import tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.devtools.corpus import DOCX_MIME, build_corpus
from app.devtools.plan import TenantPlan
from app.devtools.register_pages import RegisterPage, build_register_pages
from app.devtools.students import PREVIOUS_YEAR_START, SchoolStudents, StudentSpec
from app.documents import service as documents
from app.documents.schemas import DocumentCreate, UploadCreate, UploadOut
from app.students import service as students
from app.students.schemas import EnrollmentIn, GuardianCreate, StudentCreate
from app.tenancy import service as tenancy

CHUNK: Final = 50
DOC_PAGE: Final = 100
UPLOAD_TIMEOUT_S: Final = 30.0
PNG_MIME: Final = "image/png"

log = get_logger(__name__)

Uploader = Callable[[UploadOut, bytes, str, str], None]
"""``upload(presigned, data, content_type, filename)`` sends the file like a browser would."""


class StudentSeedError(RuntimeError):
    """A synthetic student or document could not be created (message holds codes only)."""


def http_uploader(presigned: UploadOut, data: bytes, content_type: str, filename: str) -> None:
    """POST the file to the presigned URL (multipart form, file field last, as S3 requires)."""
    response = httpx.post(
        presigned.url,
        data=presigned.fields,
        files={"file": (filename, data, content_type)},
        timeout=UPLOAD_TIMEOUT_S,
    )
    if response.status_code not in (200, 201, 204):
        raise StudentSeedError(f"object store refused the upload (HTTP {response.status_code})")


@dataclass(frozen=True, slots=True)
class Pages:
    batches: int
    per_batch: int


PAGES_BY_PROFILE: Final[dict[str, Pages]] = {
    "none": Pages(0, 0),
    "small": Pages(1, 2),
    "full": Pages(3, 3),
}


@dataclass
class Structure:
    current_year_id: uuid.UUID
    current: dict[tuple[str, str], uuid.UUID]
    previous: dict[tuple[str, str], uuid.UUID]


def load_structure(session: Session, plan: TenantPlan) -> Structure:
    years = {y.label: y for y in tenancy.list_academic_years(session)}
    classes = {c.id: c.code for c in tenancy.list_classes(session)}
    current_label = plan.current_year.label
    previous_label = next(y.label for y in plan.years if not y.is_current)

    def sections(label: str) -> dict[tuple[str, str], uuid.UUID]:
        return {
            (classes.get(s.class_id, ""), s.name): s.id
            for s in tenancy.list_sections(session, academic_year_id=years[label].id)
        }

    return Structure(years[current_label].id, sections(current_label), sections(previous_label))


def existing_admission_numbers(session: Session, ctx: UserContext) -> dict[str, uuid.UUID]:
    ids = students.list_students_in_scope(session, ctx)
    canonical = students.canonical_values(session, ids, ["admission_no"])
    out: dict[str, uuid.UUID] = {}
    for sid, per in canonical.items():
        value = per["admission_no"].value if "admission_no" in per else None
        if value:
            out[value] = sid
    return out


def _chunks(items: Sequence[StudentSpec], size: int) -> Iterator[Sequence[StudentSpec]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _create(session: Session, ctx: UserContext, spec: StudentSpec, structure: Structure) -> int:
    """One student with guardians (and the extra enrolment); returns rows created (guardians)."""
    out = students.create_student(
        session,
        ctx,
        StudentCreate.model_validate(
            {
                "values": [
                    {"attribute_key": k, "source": s, "value": v} for k, s, v in spec.values
                ],
                "section_id": structure.current[spec.section],
                "roll_no": spec.roll_no,
            }
        ),
    )
    for g in spec.guardians:
        students.add_guardian(
            session,
            ctx,
            out.id,
            GuardianCreate.model_validate(
                {
                    "relationship": g.relationship,
                    "is_primary": g.is_primary,
                    "full_name": g.full_name,
                    "address": g.address,
                }
            ),
        )
    if spec.previous_section is not None:
        students.enrol(
            session,
            ctx,
            out.id,
            EnrollmentIn(
                section_id=structure.previous[spec.previous_section],
                started_on=PREVIOUS_YEAR_START,
            ),
        )
    return len(spec.guardians)


def seed_students(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: UserContext,
    plan: TenantPlan,
    school: SchoolStudents,
    created: dict[str, int],
) -> None:
    with tenant_session(tenant_id, user_id) as session:
        structure = load_structure(session, plan)
        existing = existing_admission_numbers(session, ctx)
    missing = [s for s in school.students if s.admission_no not in existing]
    for chunk in _chunks(missing, CHUNK):
        try:
            with tenant_session(tenant_id, user_id) as session:
                guardians = sum(_create(session, ctx, spec, structure) for spec in chunk)
        except DomainError as exc:
            # Codes only: the error detail may echo a (synthetic) value.
            raise StudentSeedError(
                f"tenant {tenant_id}: creating a synthetic student failed ({exc.code})"
            ) from exc
        created["students"] = created.get("students", 0) + len(chunk)
        created["guardians"] = created.get("guardians", 0) + guardians
        created["extra_enrolments"] = created.get("extra_enrolments", 0) + sum(
            1 for s in chunk if s.previous_section is not None
        )
        log.info(
            "devtools.seed_synthetic.students",
            tenant_id=str(tenant_id),
            count=len(chunk),
        )


# --- documents -------------------------------------------------------------------------------


def _document_titles(session: Session, ctx: UserContext) -> list[str]:
    titles: list[str] = []
    before: uuid.UUID | None = None
    while True:
        page, before = documents.list_documents(session, ctx, limit=DOC_PAGE, before_id=before)
        titles.extend(d.title for d in page)
        if before is None:
            return titles


def _existing_titles(session: Session, ctx: UserContext) -> set[str]:
    return set(_document_titles(session, ctx))


def count_documents(session: Session, ctx: UserContext) -> int:
    """Documents the seeding member can see (the summary's ``documents`` count)."""
    return len(_document_titles(session, ctx))


@dataclass(frozen=True, slots=True)
class _Upload:
    title: str
    filename: str
    content_type: str
    data: bytes
    purpose: str
    doc_type: str
    language: str | None
    issued_on: dt.date | None = None


def _uploads(plan: TenantPlan, school: SchoolStudents, pages: list[RegisterPage]) -> list[_Upload]:
    out = [
        _Upload(p.title, p.filename, PNG_MIME, p.png, "register_scan", "register_scan", None)
        for p in pages
    ]
    out += [
        _Upload(
            d.title, d.filename, DOCX_MIME, d.docx(), d.purpose, d.doc_type, d.language, d.issued_on
        )
        for d in build_corpus(plan.name)
    ]
    return out


def register_pages_for(
    plan: TenantPlan, school: SchoolStudents, *, dataset_version: str, seed: int
) -> list[RegisterPage]:
    layout = PAGES_BY_PROFILE.get(school.profile, PAGES_BY_PROFILE["small"])
    return build_register_pages(
        school,
        dataset_version=dataset_version,
        seed=seed,
        tenant_index=plan.index,
        batches=layout.batches,
        pages_per_batch=layout.per_batch,
    )


def seed_documents(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: UserContext,
    plan: TenantPlan,
    school: SchoolStudents,
    pages: list[RegisterPage],
    uploader: Uploader,
    created: dict[str, int],
) -> None:
    with tenant_session(tenant_id, user_id) as session:
        titles = _existing_titles(session, ctx)
        year_id = load_structure(session, plan).current_year_id
    for item in _uploads(plan, school, pages):
        if item.title in titles:
            continue
        try:
            with tenant_session(tenant_id, user_id) as session:
                presigned = documents.create_upload(
                    session,
                    ctx,
                    UploadCreate.model_validate(
                        {
                            "filename": item.filename,
                            "content_type": item.content_type,
                            "size_bytes": len(item.data),
                            "purpose": item.purpose,
                        }
                    ),
                )
                uploader(presigned, item.data, item.content_type, item.filename)
                documents.register_document(
                    session,
                    ctx,
                    DocumentCreate.model_validate(
                        {
                            "upload_id": presigned.upload_id,
                            "title": item.title,
                            "doc_type": item.doc_type,
                            "issued_on": item.issued_on,
                            "academic_year_id": year_id,
                            "language": item.language,
                        }
                    ),
                )
        except DomainError as exc:
            raise StudentSeedError(
                f"tenant {tenant_id}: storing a synthetic document failed ({exc.code})"
            ) from exc
        key = "register_pages" if item.purpose == "register_scan" else "documents"
        created[key] = created.get(key, 0) + 1
    log.info("devtools.seed_synthetic.documents", tenant_id=str(tenant_id), count=len(pages))
