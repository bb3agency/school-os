"""Enrolment statistics sheet (FO-06; FR-EXP-009..FR-EXP-019; invariants 3, 5, 7, 14).

Counts of the students enrolled on one day of an academic year, per class, in the shapes of the
UDISE+ school-level enrolment tables: by gender, by age (as on the day), by social category and
gender, by minority group and gender, children with special needs (CWSN), repeaters, and
admissions and leavers in the year. The same sheet serves management as a "strength register".

Rules:

- **Counts only (FR-EXP-009).** No names, no student ids leave this module; the audit events
  carry the year, the day, the student count and the tables included.
- **The whole school (FR-EXP-010).** The route needs ``export.portal`` school-wide; the service
  also needs school-wide ``student.read_basic`` (403 ``school_wide_read_required``), so a
  scoped holder never learns counts of sections outside their reach.
- **Restricted values (FR-EXP-011).** Category, religion (minority) and disability are C3: their
  tables are counted only for holders of school-wide ``student.read_sensitive``; otherwise they
  are left out (``restricted_hidden``). Values are aggregated, never shown.
- **Unverified shapes (FR-EXP-012).** Reference date, age columns and minority groups live in
  ``enrolment_stats.yaml`` with ``verified: false``; the sheet says so.
- **Audit (FR-EXP-014, invariant 7).** Printing and the XLSX download are audited as exports
  (``export.enrolment_statistics.printed`` / ``.downloaded``) with a recent MFA sign-in (428);
  the on-screen view is audited (``.viewed``) when it includes restricted tables.
- **School only (invariant 14).** Aggregates of the school's own records; never cross-tenant.
- No RTE 12(1)(c) count: the RTE register is not built (D10, D43; FO-13).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core.errors import Forbidden, StepUpRequired, ValidationFailed
from app.core.logging import get_context, get_logger
from app.exports import pack_pages as pages
from app.exports.config import load_config
from app.exports.pack_schemas import (
    EnrolmentStatisticsOut,
    EnrolmentStatsColumnOut,
    EnrolmentStatsGapOut,
    EnrolmentStatsRowOut,
    EnrolmentStatsTableKey,
    EnrolmentStatsTableOut,
    EnrolmentStatsYearOut,
)
from app.exports.tables import Table, write_xlsx
from app.identity.principal import STEP_UP_MAX_AGE
from app.students import service as students
from app.students.schemas import EnrollmentOut
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut

log = get_logger(__name__)

CONFIG_PATH: Final = Path(__file__).with_name("enrolment_stats.yaml")
PORTAL: Final = "export.portal"
STUDENT_READ: Final = "student.read_basic"
SENSITIVE: Final = "student.read_sensitive"
NOT_RECORDED: Final = "not_recorded"
RESTRICTED_TABLES: Final = ("category", "minority", "cwsn")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReferenceDate(_Model):
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)


class MinorityGroup(_Model):
    label: str = Field(min_length=1, max_length=60)
    religions: tuple[str, ...] = Field(min_length=1)


class StatsConfig(_Model):
    version: int = Field(ge=1)
    verified: bool
    source: tuple[str, ...] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=80)
    unverified_note: str = Field(min_length=1, max_length=400)
    reference_date: ReferenceDate
    age_min: int = Field(ge=1, le=10)
    age_max: int = Field(ge=11, le=25)
    minority_groups: dict[str, MinorityGroup] = Field(min_length=1)
    categories: dict[str, str] = Field(min_length=1)
    genders: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def _religions_unique(self) -> StatsConfig:
        seen: set[str] = set()
        for group in self.minority_groups.values():
            for religion in group.religions:
                key = religion.strip().casefold()
                if key in seen:
                    raise ValueError(f"religion {religion!r} is in two minority groups")
                seen.add(key)
        return self

    def minority_of(self, religion: str | None) -> str:
        """Minority group key of a religion value; ``none`` for others, not recorded if empty."""
        if religion is None or not religion.strip():
            return NOT_RECORDED
        key = " ".join(religion.split()).casefold()
        for group_key, group in self.minority_groups.items():
            if key in {r.casefold() for r in group.religions}:
                return group_key
        return "none"


@lru_cache(maxsize=1)
def load_stats_config() -> StatsConfig:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return StatsConfig.model_validate(data)


# --- pure counting ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StudentFacts:
    """One student enrolled on the day: what the tables count (no name, no id)."""

    class_id: uuid.UUID
    gender: str | None
    dob: dt.date | None
    category: str | None = None
    religion: str | None = None
    disability: bool = False
    repeater: bool = False


@dataclass(frozen=True, slots=True)
class ClassRow:
    class_id: uuid.UUID
    label: str


@dataclass(frozen=True, slots=True)
class Movement:
    """Admissions and leavers of the year per class."""

    admitted: Mapping[uuid.UUID, int]
    left: Mapping[uuid.UUID, int]


def age_on(dob: dt.date, day: dt.date) -> int:
    """Completed years on ``day``."""
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def _age_column(cfg: StatsConfig, dob: dt.date | None, day: dt.date) -> str:
    if dob is None or dob > day:
        return NOT_RECORDED
    age = age_on(dob, day)
    if age < cfg.age_min:
        return f"below_{cfg.age_min}"
    if age >= cfg.age_max:
        return f"{cfg.age_max}_plus"
    return f"age_{age}"


def _gender_key(cfg: StatsConfig, value: str | None) -> str:
    return value if value in cfg.genders else NOT_RECORDED


def _split(cfg: StatsConfig, prefix: str, label: str) -> list[tuple[str, str]]:
    """Girls and boys columns of one group (the row total counts every student)."""
    return [
        (f"{prefix}_female", f"{label} {cfg.genders.get('female', 'girls').lower()}"),
        (f"{prefix}_male", f"{label} {cfg.genders.get('male', 'boys').lower()}"),
    ]


def _columns(cfg: StatsConfig, key: EnrolmentStatsTableKey) -> list[tuple[str, str]]:
    genders = [*cfg.genders.items(), (NOT_RECORDED, "Not recorded")]
    if key == "gender":
        return genders
    if key == "age":
        return [
            (f"below_{cfg.age_min}", f"Below {cfg.age_min}"),
            *((f"age_{a}", str(a)) for a in range(cfg.age_min, cfg.age_max)),
            (f"{cfg.age_max}_plus", f"{cfg.age_max} and above"),
            (NOT_RECORDED, "Not recorded"),
        ]
    if key == "category":
        cols = [c for k, label in cfg.categories.items() for c in _split(cfg, k, label)]
        return [*cols, (NOT_RECORDED, "Not recorded")]
    if key == "minority":
        cols = [c for k, g in cfg.minority_groups.items() for c in _split(cfg, k, g.label)]
        return [*cols, ("none", "Not a minority group"), (NOT_RECORDED, "Not recorded")]
    if key in ("cwsn", "repeaters"):
        return list(cfg.genders.items())
    return [("admitted", "Admitted in the year"), ("left", "Left in the year")]


_TITLES: Final[dict[EnrolmentStatsTableKey, tuple[str, str]]] = {
    "gender": ("Enrolment by class and gender", "Total"),
    "age": ("Enrolment by class and age", "Total"),
    "category": ("Enrolment by class, social category and gender", "Total"),
    "minority": ("Enrolment by class, minority group and gender", "Total"),
    "cwsn": ("Children with special needs (a disability is recorded)", "Total CWSN"),
    "repeaters": ("Repeaters (same class as in the previous academic year)", "Total repeaters"),
    "movement": ("Admissions and leavers in the year", "Enrolled on the day"),
}


def _keys_of(  # noqa: PLR0911 - one branch per table
    cfg: StatsConfig, key: EnrolmentStatsTableKey, s: StudentFacts, day: dt.date
) -> tuple[list[str], bool]:
    """(column keys the student counts in, whether the student counts in the row total)."""
    gender = _gender_key(cfg, s.gender)
    if key == "gender":
        return [gender], True
    if key == "age":
        return [_age_column(cfg, s.dob, day)], True
    if key == "category":
        if s.category not in cfg.categories:
            return [NOT_RECORDED], True
        return ([f"{s.category}_{gender}"] if gender in ("female", "male") else []), True
    if key == "minority":
        group = cfg.minority_of(s.religion)
        if group in (NOT_RECORDED, "none"):
            return [group], True
        return ([f"{group}_{gender}"] if gender in ("female", "male") else []), True
    if key == "cwsn":
        return ([gender] if s.disability else []), s.disability
    if key == "repeaters":
        return ([gender] if s.repeater else []), s.repeater
    return [], True  # movement: counted from Movement; the row total is the enrolment


def build_tables(
    cfg: StatsConfig,
    facts: Sequence[StudentFacts],
    classes: Sequence[ClassRow],
    day: dt.date,
    movement: Movement,
    *,
    restricted: bool,
) -> list[EnrolmentStatsTableOut]:
    """Every table of the sheet (restricted tables only when ``restricted``). Pure."""
    by_class: dict[uuid.UUID, list[StudentFacts]] = {c.class_id: [] for c in classes}
    for s in facts:
        by_class.setdefault(s.class_id, []).append(s)
    keys: list[EnrolmentStatsTableKey] = ["gender", "age"]
    if restricted:
        keys += ["category", "minority", "cwsn"]
    keys += ["repeaters", "movement"]
    out: list[EnrolmentStatsTableOut] = []
    for key in keys:
        columns = _columns(cfg, key)
        col_keys = [c for c, _ in columns]
        rows: list[EnrolmentStatsRowOut] = []
        grand: Counter[str] = Counter()
        grand_total = 0
        for c in classes:
            counter: Counter[str] = Counter()
            total = 0
            for s in by_class.get(c.class_id, []):
                hits, counted = _keys_of(cfg, key, s, day)
                counter.update(hits)
                total += int(counted)
            if key == "movement":
                counter["admitted"] = movement.admitted.get(c.class_id, 0)
                counter["left"] = movement.left.get(c.class_id, 0)
            grand.update(counter)
            grand_total += total
            rows.append(
                EnrolmentStatsRowOut(
                    class_id=c.class_id,
                    label=c.label,
                    counts=[counter.get(k, 0) for k in col_keys],
                    total=total,
                )
            )
        title, _ = _TITLES[key]
        out.append(
            EnrolmentStatsTableOut(
                key=key,
                title=title,
                restricted=key in RESTRICTED_TABLES,
                columns=[EnrolmentStatsColumnOut(key=k, label=label) for k, label in columns],
                rows=rows,
                totals=EnrolmentStatsRowOut(
                    class_id=None,
                    label="Total",
                    counts=[grand.get(k, 0) for k in col_keys],
                    total=grand_total,
                ),
            )
        )
    return out


def total_label(key: EnrolmentStatsTableKey) -> str:
    return _TITLES[key][1]


# --- service --------------------------------------------------------------------------------------


def _school_wide(ctx: UserContext, permission: str) -> bool:
    return ctx.has(permission) and ctx.scope_for(permission).school_wide


def _require_step_up(ctx: UserContext) -> None:
    """FR-EXP-014: MFA-backed sign-in within 5 minutes (428 ``step_up_required``)."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def _invalid(field: str, code: str) -> ValidationFailed:
    return ValidationFailed(
        [{"field": field, "code": code, "message_key": f"errors.exports.{code}"}],
        detail="Choose a day inside the academic year.",
    )


def reference_date(cfg: StatsConfig, year: AcademicYearOut) -> dt.date:
    """The configured day (30 September) of the year's starting calendar year, inside the year."""
    try:
        day = dt.date(year.starts_on.year, cfg.reference_date.month, cfg.reference_date.day)
    except ValueError:  # e.g. 31 September in a bad config: the last day of that month
        day = dt.date(year.starts_on.year, cfg.reference_date.month, 28)
    if day < year.starts_on:
        day = dt.date(year.starts_on.year + 1, day.month, day.day)
    return min(max(day, year.starts_on), year.ends_on)


def _year(session: Session, academic_year_id: uuid.UUID | None) -> AcademicYearOut:
    if academic_year_id is None:
        year = tenancy.get_current_academic_year(session)
        if year is None:
            raise _invalid("academic_year_id", "no_current_academic_year")
        return year
    return tenancy.get_academic_year(session, academic_year_id)  # 404 for another school's


def _start(e: EnrollmentOut, year: AcademicYearOut) -> dt.date:
    """The enrolment's real start: an implicit start (the day the record was keyed in, FT-09)
    is not a fact, so the year start stands for it."""
    if e.started_on_explicit and e.started_on is not None:
        return max(e.started_on, year.starts_on)
    return year.starts_on


def _on_day(e: EnrollmentOut, year: AcademicYearOut, day: dt.date) -> bool:
    return _start(e, year) <= day and (e.ended_on is None or e.ended_on >= day)


def _value(values: Mapping[str, students.CanonicalValue], key: str) -> str | None:
    v = values.get(key)
    return v.value if v is not None else None


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _movement(
    session: Session,
    year: AcademicYearOut,
    in_year: Mapping[uuid.UUID, Sequence[EnrollmentOut]],
    values: Mapping[uuid.UUID, Mapping[str, students.CanonicalValue]],
    section_class: Mapping[uuid.UUID, uuid.UUID],
) -> tuple[Counter[uuid.UUID], Counter[uuid.UUID]]:
    """Admissions: admission date inside the year (class of the first enrolment of the year).
    Leavers: record status ``left`` with the leaving date inside the year (last enrolment)."""
    admitted: Counter[uuid.UUID] = Counter()
    for sid, es in in_year.items():
        adm = _date(_value(values.get(sid, {}), "admission_date"))
        if adm is not None and year.starts_on <= adm <= year.ends_on:
            first = min(es, key=lambda e: _start(e, year))
            admitted[section_class[first.section_id]] += 1
    statuses = students.record_statuses(session, list(in_year))
    gone = [sid for sid, st in statuses.items() if st == "left"]
    left: Counter[uuid.UUID] = Counter()
    for sid, when in students.leaving_dates(session, gone).items():
        if year.starts_on <= when <= year.ends_on:
            last = max(in_year[sid], key=lambda e: _start(e, year))
            left[section_class[last.section_id]] += 1
    return admitted, left


@dataclass(frozen=True, slots=True)
class _Counted:
    out: EnrolmentStatisticsOut
    restricted_tables: list[str]


def _count(
    session: Session,
    ctx: UserContext,
    academic_year_id: uuid.UUID | None,
    as_of: dt.date | None,
) -> _Counted:
    if not _school_wide(ctx, STUDENT_READ):
        raise Forbidden(
            "The enrolment statistics count the whole school: you need to see every "
            "student's basic record.",
            code="school_wide_read_required",
        )
    cfg = load_stats_config()
    year = _year(session, academic_year_id)
    default_day = reference_date(cfg, year)
    day = as_of or default_day
    if not year.starts_on <= day <= year.ends_on:
        raise _invalid("as_of", "as_of_out_of_year")
    restricted = _school_wide(ctx, SENSITIVE)

    years = sorted(tenancy.list_academic_years(session), key=lambda y: y.starts_on)
    previous = next((y for y in reversed(years) if y.starts_on < year.starts_on), None)
    section_class = {s.id: s.class_id for s in tenancy.list_sections(session)}
    year_classes = {s.class_id for s in tenancy.list_sections(session, academic_year_id=year.id)}

    ids = students.list_students_in_scope(session, ctx)  # school-wide: every student
    histories = students.enrolment_histories(session, ids)
    in_year = {
        sid: [e for e in hist if e.academic_year_id == year.id] for sid, hist in histories.items()
    }
    in_year = {sid: es for sid, es in in_year.items() if es}
    enrolled: dict[uuid.UUID, uuid.UUID] = {}
    for sid, es in in_year.items():
        current = [e for e in es if _on_day(e, year, day)]
        if current:
            latest = max(current, key=lambda e: _start(e, year))
            enrolled[sid] = section_class[latest.section_id]

    wanted = list(in_year)
    keys = ["gender", "dob", "admission_date"]
    values = students.canonical_values(session, wanted, keys)
    sensitive: dict[uuid.UUID, dict[str, students.CanonicalValue]] = {}
    if restricted and enrolled:
        sensitive = students.canonical_values(
            session, list(enrolled), ["category", "religion", "disability"], include_sensitive=True
        )

    facts: list[StudentFacts] = []
    for sid, class_id in enrolled.items():
        v = values.get(sid, {})
        r = sensitive.get(sid, {})
        repeater = previous is not None and any(
            e.academic_year_id == previous.id and section_class.get(e.section_id) == class_id
            for e in histories.get(sid, [])
        )
        disability = _value(r, "disability")
        facts.append(
            StudentFacts(
                class_id=class_id,
                gender=_value(v, "gender"),
                dob=_date(_value(v, "dob")),
                category=_value(r, "category"),
                religion=_value(r, "religion"),
                disability=bool(disability and disability.strip()),
                repeater=repeater,
            )
        )

    admitted, left = _movement(session, year, in_year, values, section_class)
    class_ids = year_classes | set(enrolled.values()) | set(admitted) | set(left)
    classes = [
        ClassRow(class_id=c.id, label=c.display_en)
        for c in sorted(tenancy.list_classes(session), key=lambda c: (c.sort_order, c.code))
        if c.id in class_ids
    ]
    tables = build_tables(
        cfg, facts, classes, day, Movement(admitted=admitted, left=left), restricted=restricted
    )
    gap_specs: list[tuple[str, str, Callable[[StudentFacts], bool]]] = [
        ("gender", "Gender", lambda s: s.gender not in cfg.genders),
        ("dob", "Date of birth", lambda s: s.dob is None),
    ]
    if restricted:
        gap_specs += [
            ("category", "Social category", lambda s: s.category not in cfg.categories),
            ("religion", "Religion", lambda s: not (s.religion and s.religion.strip())),
        ]
    gaps = [
        EnrolmentStatsGapOut(attribute_key=k, label=label, count=n)
        for k, label, missing in gap_specs
        if (n := sum(1 for s in facts if missing(s)))
    ]
    out = EnrolmentStatisticsOut(
        year=EnrolmentStatsYearOut(
            id=year.id, label=year.label, starts_on=year.starts_on, ends_on=year.ends_on
        ),
        as_of=day,
        reference_date=default_day,
        students=len(facts),
        verified=cfg.verified,
        source=list(cfg.source),
        restricted_hidden=not restricted,
        tables=tables,
        gaps=gaps,
    )
    return _Counted(out=out, restricted_tables=list(RESTRICTED_TABLES) if restricted else [])


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(session: Session, action: str, counted: _Counted, **extra: Any) -> None:
    out = counted.out
    audit.record(
        session,
        action=action,
        resource_type="export",
        resource_id=None,
        summary={
            "kind": "enrolment_statistics",
            "academic_year_id": out.year.id,
            "as_of": out.as_of.isoformat(),
            "students": out.students,
            "restricted_tables": counted.restricted_tables,
            "layout_version": load_stats_config().version,
            **extra,
        },
        request_id=_request_id(),
    )
    log.info("exports.enrolment_statistics", action=action, resource_type="export")


def enrolment_statistics(
    session: Session,
    ctx: UserContext,
    *,
    academic_year_id: uuid.UUID | None = None,
    as_of: dt.date | None = None,
) -> EnrolmentStatisticsOut:
    """The sheet's counts for the screen (FR-EXP-009..012). Audit ``.viewed`` only when the
    restricted tables are included."""
    counted = _count(session, ctx, academic_year_id, as_of)
    if counted.restricted_tables:
        _audit(session, "export.enrolment_statistics.viewed", counted, format="screen")
    return counted.out


def _page_tables(out: EnrolmentStatisticsOut) -> list[pages.PageTable]:
    return [
        pages.PageTable(
            caption=t.title,
            header=["Class", *(c.label for c in t.columns), total_label(t.key)],
            rows=[[r.label, *r.counts, r.total] for r in t.rows],
            total=[t.totals.label, *t.totals.counts, t.totals.total],
        )
        for t in out.tables
    ]


def print_page(
    session: Session,
    ctx: UserContext,
    *,
    academic_year_id: uuid.UUID | None = None,
    as_of: dt.date | None = None,
    check: bool = False,
) -> str | None:
    """The A4 sheet (FR-EXP-013, FR-EXP-014): recent MFA sign-in (428). ``check``: every check
    runs, nothing is printed or audited (204). Audit ``export.enrolment_statistics.printed``."""
    _require_step_up(ctx)
    counted = _count(session, ctx, academic_year_id, as_of)
    if check:
        return None
    cfg = load_stats_config()
    out = counted.out
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    now = dt.datetime.now(dt.UTC)
    meta = [
        ("Academic year", out.year.label),
        ("Counted on", out.as_of.strftime("%d/%m/%Y")),
        ("Students enrolled", str(out.students)),
    ]
    notes = [
        "Counts only. Age is in completed years on the day counted. A row total counts every "
        "student of the class; girls and boys columns leave out other or unrecorded genders.",
    ]
    if out.restricted_hidden:
        notes.append(
            "The social category, minority and CWSN tables are left out: they need permission "
            "to see restricted student details."
        )
    page = pages.render(
        pages.PageData(
            school_name=tenant.name,
            school_address=head.address_en,
            school_affiliation=head.affiliation,
            place=head.place,
            title=f"{cfg.title} {out.year.label}",
            meta=meta,
            verified=cfg.verified,
            unverified_note=cfg.unverified_note,
            tables=_page_tables(out),
            gaps=[f"{g.count} students have no {g.label.lower()} recorded." for g in out.gaps],
            notes=notes,
            printed_at=now,
            reference=f"ENR-{out.year.label}-{out.as_of.strftime('%Y%m%d')}",
        )
    )
    _audit(session, "export.enrolment_statistics.printed", counted, format="html")
    return page


def xlsx_file(
    session: Session,
    ctx: UserContext,
    *,
    academic_year_id: uuid.UUID | None = None,
    as_of: dt.date | None = None,
) -> tuple[bytes, str]:
    """The sheet as XLSX, one worksheet per table (FR-EXP-013, FR-EXP-014): recent MFA sign-in
    (428); every cell passes ``safe_cell``. Audit ``export.enrolment_statistics.downloaded``."""
    _require_step_up(ctx)
    counted = _count(session, ctx, academic_year_id, as_of)
    out = counted.out
    cfg = load_stats_config()
    about = Table(
        name="About",
        header=("Item", "Value"),
        rows=(
            ("Sheet", cfg.title),
            ("Academic year", out.year.label),
            ("Counted on", out.as_of.strftime("%d/%m/%Y")),
            ("Students enrolled", out.students),
            ("Verified against the official form", "Yes" if cfg.verified else "No"),
            ("Note", cfg.unverified_note),
            *((f"Missing: {g.label}", g.count) for g in out.gaps),
        ),
    )
    tables = [about] + [
        Table(
            name=t.key.capitalize(),
            header=("Class", *(c.label for c in t.columns), total_label(t.key)),
            rows=tuple((r.label, *r.counts, r.total) for r in [*t.rows, t.totals]),
        )
        for t in out.tables
    ]
    data = write_xlsx(
        tables, watermark=load_config().watermark_text(), title=f"{cfg.title} {out.year.label}"
    )
    _audit(session, "export.enrolment_statistics.downloaded", counted, format="xlsx")
    return data, f"enrolment-statistics-{out.year.label}-{out.as_of.isoformat()}.xlsx"


__all__ = [
    "StatsConfig",
    "StudentFacts",
    "age_on",
    "build_tables",
    "enrolment_statistics",
    "load_stats_config",
    "print_page",
    "reference_date",
    "xlsx_file",
]
