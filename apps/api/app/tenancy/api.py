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

from fastapi import APIRouter, Depends, Response

from app.authz import scope
from app.authz.catalog import AUTHENTICATED
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag, paginate
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


def _with_etag(response: Response, version: int) -> None:
    response.headers["ETag"] = etag(version)


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
    ctx: Reader, db: TenantDB, limit: Limit = 50, cursor: Cursor = None
) -> Page[AcademicYearOut]:
    """Academic years, newest first (permission ``student.read_basic``)."""
    return paginate(tenancy.list_academic_years(db), key=_year_key, cursor=cursor, limit=limit)


@router.get("/academic-years/{year_id}", response_model=AcademicYearOut)
def get_academic_year(
    ctx: Reader, db: TenantDB, year_id: uuid.UUID, response: Response
) -> AcademicYearOut:
    """One academic year (permission ``student.read_basic``)."""
    year = tenancy.get_academic_year(db, year_id)
    _with_etag(response, year.version)
    return year


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


# --- classes ----------------------------------------------------------------------------------


def _class_key(klass: ClassOut) -> str:
    return f"{klass.sort_order:05d}:{klass.code}"


@router.get("/classes", response_model=Page[ClassOut])
def list_classes(
    ctx: Reader, db: TenantDB, limit: Limit = 50, cursor: Cursor = None
) -> Page[ClassOut]:
    """Classes in display order (permission ``student.read_basic``; scoped holders see only
    their classes)."""
    visible = scope.visible_classes(ctx, READ, tenancy.list_classes(db), tenancy.list_sections(db))
    return paginate(visible, key=_class_key, cursor=cursor, limit=limit)


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
    return klass


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
    return klass


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
) -> Page[SectionOut]:
    """Sections, optionally for one year and/or class (permission ``student.read_basic``;
    class teachers see only their sections)."""
    sections = tenancy.list_sections(db, academic_year_id=academic_year_id, class_id=class_id)
    order = {s.id: i for i, s in enumerate(sections)}
    visible = scope.visible_sections(ctx, READ, sections)
    return paginate(visible, key=lambda s: f"{order[s.id]:06d}", cursor=cursor, limit=limit)


@router.get("/sections/{section_id}", response_model=SectionOut)
def get_section(ctx: Reader, db: TenantDB, section_id: uuid.UUID, response: Response) -> SectionOut:
    """One section (permission ``student.read_basic``; 404 outside the caller's scope)."""
    section = scope.ensure_section_visible(ctx, READ, tenancy.get_section(db, section_id))
    _with_etag(response, section.version)
    return section


@router.post("/sections", response_model=SectionOut, status_code=201)
def create_section(
    ctx: Manager, db: TenantDB, body: SectionCreate, idem: IdempotencyDep
) -> Response:
    """Add a section to a class for an academic year, optionally with its class teacher
    (permission ``tenant.structure.manage``). Accepts ``Idempotency-Key``."""
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
