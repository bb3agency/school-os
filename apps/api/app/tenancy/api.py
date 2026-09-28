"""School profile, settings and academic structure routes (US-202, FR-TEN-010, FR-TEN-012).

Reads of the structure need ``student.read_basic``; holders whose grant is scoped (class and
subject teachers) see only their classes and sections, and other IDs answer 404. Writes need
``tenant.structure.manage`` (owner, principal, office_admin; no step-up). Settings changes need
``tenant.settings.manage`` with step-up.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.authz import scope
from app.authz.catalog import AUTHENTICATED
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag, paginate
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import (
    AcademicYearCreate,
    AcademicYearOut,
    AcademicYearUpdate,
    ClassCreate,
    ClassOut,
    ClassUpdate,
    SectionCreate,
    SectionOut,
    SectionUpdate,
    TenantOut,
    TenantSettingsPatch,
)

router = APIRouter(prefix="/api/v1", tags=["school"])

READ = "student.read_basic"
Member = Annotated[UserContext, Depends(require(AUTHENTICATED))]
SettingsManager = Annotated[UserContext, Depends(require("tenant.settings.manage", step_up=True))]
Reader = Annotated[UserContext, Depends(require(READ))]
Manager = Annotated[UserContext, Depends(require("tenant.structure.manage", scope="school"))]
IncludeArchived = Annotated[
    bool,
    Query(description="Also list archived rows (hidden by default; US-202, FR-TEN-010)."),
]


def _with_etag(response: Response, version: int) -> None:
    response.headers["ETag"] = etag(version)


def _years_in_use(db: Session, years: list[AcademicYearOut]) -> list[AcademicYearOut]:
    used = students.structure_in_use(db).year_ids
    return [y.model_copy(update={"in_use": y.id in used}) for y in years]


def _classes_in_use(db: Session, classes: list[ClassOut]) -> list[ClassOut]:
    used = students.structure_in_use(db).class_ids
    return [c.model_copy(update={"in_use": c.id in used}) for c in classes]


def _sections_in_use(db: Session, sections: list[SectionOut]) -> list[SectionOut]:
    used = students.structure_in_use(db).section_ids
    return [s.model_copy(update={"in_use": s.id in used}) for s in sections]


class _Created(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def version(self) -> int: ...


def _created(prefix: str) -> Callable[[_Created], dict[str, str]]:
    def headers(item: _Created) -> dict[str, str]:
        return {"Location": f"/api/v1/{prefix}/{item.id}", "ETag": etag(item.version)}

    return headers


# --- school profile and settings --------------------------------------------------------------


@router.get("/tenant", response_model=TenantOut)
def get_tenant(ctx: Member, db: TenantDB, response: Response) -> TenantOut:
    """This school's profile and settings (permission: any active member)."""
    tenant = tenancy.get_tenant(db)
    _with_etag(response, tenant.version)
    return tenant


@router.patch("/tenant", response_model=TenantOut)
def update_tenant_settings(
    ctx: SettingsManager,
    db: TenantDB,
    body: TenantSettingsPatch,
    version: IfMatch,
    response: Response,
) -> TenantOut:
    """Change languages, date format, idle timeout (5-30 min), AI features and the monthly AI
    budget (permission ``tenant.settings.manage``, step-up; ``If-Match`` required)."""
    tenant = tenancy.update_tenant_settings(db, body, expected_version=version)
    _with_etag(response, tenant.version)
    return tenant


# --- academic years ---------------------------------------------------------------------------


def _year_key(year: AcademicYearOut) -> str:
    # Newest first: invert the start date so a plain string sort puts later years first.
    return f"{99991231 - int(year.starts_on.strftime('%Y%m%d')):08d}:{year.id}"


@router.get("/academic-years", response_model=Page[AcademicYearOut])
def list_academic_years(
    ctx: Reader,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    include_archived: IncludeArchived = False,
) -> Page[AcademicYearOut]:
    """Academic years, newest first; archived years only with ``include_archived=true``
    (permission ``student.read_basic``)."""
    years = tenancy.list_academic_years(db, include_archived=include_archived)
    return paginate(_years_in_use(db, years), key=_year_key, cursor=cursor, limit=limit)


@router.get("/academic-years/{year_id}", response_model=AcademicYearOut)
def get_academic_year(
    ctx: Reader, db: TenantDB, year_id: uuid.UUID, response: Response
) -> AcademicYearOut:
    """One academic year (permission ``student.read_basic``)."""
    year = tenancy.get_academic_year(db, year_id)
    _with_etag(response, year.version)
    return _years_in_use(db, [year])[0]


@router.post("/academic-years", response_model=AcademicYearOut, status_code=201)
def create_academic_year(
    ctx: Manager, db: TenantDB, body: AcademicYearCreate, idem: IdempotencyDep
) -> Response:
    """Create an academic year such as 2026-27; ``is_current`` makes it the only current year
    (permission ``tenant.structure.manage``). Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: tenancy.create_academic_year(db, body),
        headers=_created("academic-years"),
    )


@router.patch("/academic-years/{year_id}", response_model=AcademicYearOut)
def update_academic_year(
    *,
    ctx: Manager,
    db: TenantDB,
    year_id: uuid.UUID,
    body: AcademicYearUpdate,
    version: IfMatch,
    response: Response,
) -> AcademicYearOut:
    """Change a year's label or dates (permission ``tenant.structure.manage``; ``If-Match``)."""
    year = tenancy.update_academic_year(db, year_id, body, expected_version=version)
    _with_etag(response, year.version)
    return year


@router.post("/academic-years/{year_id}/make-current", response_model=AcademicYearOut)
def make_academic_year_current(
    ctx: Manager, db: TenantDB, year_id: uuid.UUID, version: IfMatch, response: Response
) -> AcademicYearOut:
    """Make this the one current academic year (FR-TEN-010; permission
    ``tenant.structure.manage``; ``If-Match``)."""
    year = tenancy.set_current_academic_year(db, year_id, expected_version=version)
    _with_etag(response, year.version)
    return year


@router.post("/academic-years/{year_id}/archive", response_model=AcademicYearOut)
def archive_academic_year(
    ctx: Manager, db: TenantDB, year_id: uuid.UUID, version: IfMatch, response: Response
) -> AcademicYearOut:
    """Archive a year: it is hidden from lists but kept for old records (permission
    ``tenant.structure.manage``; ``If-Match``). The current year answers 409
    ``academic_year_current``; a year with active enrolments 409 ``structure_in_use``."""
    year = tenancy.archive_academic_year(db, year_id, archived=True, expected_version=version)
    _with_etag(response, year.version)
    return year


@router.post("/academic-years/{year_id}/unarchive", response_model=AcademicYearOut)
def unarchive_academic_year(
    ctx: Manager, db: TenantDB, year_id: uuid.UUID, version: IfMatch, response: Response
) -> AcademicYearOut:
    """Bring an archived year back into the lists (permission ``tenant.structure.manage``;
    ``If-Match``)."""
    year = tenancy.archive_academic_year(db, year_id, archived=False, expected_version=version)
    _with_etag(response, year.version)
    return year


# --- classes ----------------------------------------------------------------------------------


def _class_key(klass: ClassOut) -> str:
    return f"{klass.sort_order:05d}:{klass.code}"


@router.get("/classes", response_model=Page[ClassOut])
def list_classes(
    ctx: Reader,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    include_archived: IncludeArchived = False,
) -> Page[ClassOut]:
    """Classes in display order; archived classes only with ``include_archived=true``
    (permission ``student.read_basic``; scoped holders see only their classes)."""
    classes = tenancy.list_classes(db, include_archived=include_archived)
    visible = scope.visible_classes(ctx, READ, classes, tenancy.list_sections(db))
    return paginate(_classes_in_use(db, visible), key=_class_key, cursor=cursor, limit=limit)


@router.post("/classes/defaults", response_model=Page[ClassOut])
def add_default_classes(ctx: Manager, db: TenantDB) -> Page[ClassOut]:
    """Add any missing classes from Nursery to XII with English and Telugu names; existing
    classes are kept (permission ``tenant.structure.manage``)."""
    classes = tenancy.ensure_default_classes(db)
    return Page[ClassOut](data=classes, next_cursor=None)


@router.get("/classes/{class_id}", response_model=ClassOut)
def get_class(ctx: Reader, db: TenantDB, class_id: uuid.UUID, response: Response) -> ClassOut:
    """One class (permission ``student.read_basic``; 404 outside the caller's scope)."""
    klass = scope.ensure_class_visible(
        ctx, READ, tenancy.get_class(db, class_id), tenancy.list_sections(db)
    )
    _with_etag(response, klass.version)
    return _classes_in_use(db, [klass])[0]


@router.post("/classes", response_model=ClassOut, status_code=201)
def create_class(ctx: Manager, db: TenantDB, body: ClassCreate, idem: IdempotencyDep) -> Response:
    """Add a class (permission ``tenant.structure.manage``). Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: tenancy.create_class(db, body),
        headers=_created("classes"),
    )


@router.patch("/classes/{class_id}", response_model=ClassOut)
def update_class(
    *,
    ctx: Manager,
    db: TenantDB,
    class_id: uuid.UUID,
    body: ClassUpdate,
    version: IfMatch,
    response: Response,
) -> ClassOut:
    """Rename or reorder a class; the code cannot change (permission
    ``tenant.structure.manage``; ``If-Match``)."""
    klass = tenancy.update_class(db, class_id, body, expected_version=version)
    _with_etag(response, klass.version)
    return _classes_in_use(db, [klass])[0]


@router.post("/classes/{class_id}/archive", response_model=ClassOut)
def archive_class(
    ctx: Manager, db: TenantDB, class_id: uuid.UUID, version: IfMatch, response: Response
) -> ClassOut:
    """Archive a class: hidden from lists, kept for old records (permission
    ``tenant.structure.manage``; ``If-Match``). A class with active enrolments in any of its
    sections answers 409 ``structure_in_use``."""
    klass = tenancy.archive_class(db, class_id, archived=True, expected_version=version)
    _with_etag(response, klass.version)
    return _classes_in_use(db, [klass])[0]


@router.post("/classes/{class_id}/unarchive", response_model=ClassOut)
def unarchive_class(
    ctx: Manager, db: TenantDB, class_id: uuid.UUID, version: IfMatch, response: Response
) -> ClassOut:
    """Bring an archived class back into the lists (permission ``tenant.structure.manage``;
    ``If-Match``)."""
    klass = tenancy.archive_class(db, class_id, archived=False, expected_version=version)
    _with_etag(response, klass.version)
    return _classes_in_use(db, [klass])[0]


# --- sections ---------------------------------------------------------------------------------


@router.get("/sections", response_model=Page[SectionOut])
def list_sections(
    *,
    ctx: Reader,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    academic_year_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    include_archived: IncludeArchived = False,
) -> Page[SectionOut]:
    """Sections, optionally for one year and/or class; archived sections only with
    ``include_archived=true`` (permission ``student.read_basic``; class teachers see only
    their sections)."""
    sections = tenancy.list_sections(
        db,
        academic_year_id=academic_year_id,
        class_id=class_id,
        include_archived=include_archived,
    )
    order = {s.id: i for i, s in enumerate(sections)}
    visible = _sections_in_use(db, scope.visible_sections(ctx, READ, sections))
    return paginate(visible, key=lambda s: f"{order[s.id]:06d}", cursor=cursor, limit=limit)


@router.get("/sections/{section_id}", response_model=SectionOut)
def get_section(ctx: Reader, db: TenantDB, section_id: uuid.UUID, response: Response) -> SectionOut:
    """One section (permission ``student.read_basic``; 404 outside the caller's scope)."""
    section = scope.ensure_section_visible(ctx, READ, tenancy.get_section(db, section_id))
    _with_etag(response, section.version)
    return _sections_in_use(db, [section])[0]


@router.post("/sections", response_model=SectionOut, status_code=201)
def create_section(
    ctx: Manager, db: TenantDB, body: SectionCreate, idem: IdempotencyDep
) -> Response:
    """Add a section to a class for an academic year, optionally with its class teacher
    (permission ``tenant.structure.manage``). An archived year or class answers 409
    ``structure_archived``. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: tenancy.create_section(db, body),
        headers=_created("sections"),
    )


@router.patch("/sections/{section_id}", response_model=SectionOut)
def update_section(
    *,
    ctx: Manager,
    db: TenantDB,
    section_id: uuid.UUID,
    body: SectionUpdate,
    version: IfMatch,
    response: Response,
) -> SectionOut:
    """Rename a section or assign/clear its class teacher (permission
    ``tenant.structure.manage``; ``If-Match``)."""
    section = tenancy.update_section(db, section_id, body, expected_version=version)
    _with_etag(response, section.version)
    return section


@router.post("/sections/{section_id}/archive", response_model=SectionOut)
def archive_section(
    ctx: Manager, db: TenantDB, section_id: uuid.UUID, version: IfMatch, response: Response
) -> SectionOut:
    """Archive a section: hidden from lists, kept for old records (permission
    ``tenant.structure.manage``; ``If-Match``). A section with active enrolments answers 409
    ``structure_in_use``."""
    section = tenancy.archive_section(db, section_id, archived=True, expected_version=version)
    _with_etag(response, section.version)
    return section


@router.post("/sections/{section_id}/unarchive", response_model=SectionOut)
def unarchive_section(
    ctx: Manager, db: TenantDB, section_id: uuid.UUID, version: IfMatch, response: Response
) -> SectionOut:
    """Bring an archived section back into the lists (permission ``tenant.structure.manage``;
    ``If-Match``)."""
    section = tenancy.archive_section(db, section_id, archived=False, expected_version=version)
    _with_etag(response, section.version)
    return section
