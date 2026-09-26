"""The synthetic dataset as pure data: schools, academic structure and staff (docs/12 §3).

SYNTHETIC ONLY (CLAUDE.md invariant 11). :func:`build_plan` is a pure function of
``(dataset_version, seed, tenants, code_prefix)``: the same inputs always give the same plan, so
test expectations can refer to a dataset version and seed. Nothing here touches the database;
:mod:`app.devtools.seed_synthetic` applies a plan through the tenancy and identity services.

Determinism and identity:
- Tenant IDs are UUIDv5 values derived from ``(dataset_version, code)`` under
  :data:`NAMESPACE`, so re-running with the same code finds the same school.
- Staff are keyed by IdP subject ``synthetic|<code>|<role>|<n>`` and e-mail
  ``<role>.<n>@<code>.example.invalid`` (RFC 2606 ``.invalid``: never deliverable).
- Each person's name comes from its own random stream seeded with
  ``(dataset_version, seed, ...)``; changing the number of tenants does not change the names of
  existing people. Streams are independent of ``code_prefix``.
- Overlapping names across schools (docs/12 §4.4): the first person of every role and every
  fifth class teacher draw from a *shared* stream that ignores the tenant, so every school has
  staff with exactly the same names as the other schools.

Dataset ``v1`` (2026-27 current, 2025-26 previous; Nursery-XII; sections A-B for NUR/LKG/UKG and
A-D for I-XII in both years; one class teacher per current-year section; staff for every system
role). Add a new version rather than changing ``v1``.
"""

from __future__ import annotations

import datetime as dt
import random
import re
import uuid
from dataclasses import dataclass
from typing import Final, Literal

from app.authz.catalog import system_roles
from app.devtools.names import generate_name
from app.tenancy import service as tenancy

DATASET_VERSIONS: Final = ("v1",)
DEFAULT_DATASET_VERSION: Final = "v1"
DEFAULT_SEED: Final = 20260926
DEFAULT_TENANTS: Final = 2
DEFAULT_CODE_PREFIX: Final = "synth"
MAX_TENANTS: Final = 26

NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_URL, "https://schoolos.invalid/devtools/synthetic")
EMAIL_DOMAIN: Final = "example.invalid"
_PREFIX_RE: Final = re.compile(r"^[a-z][a-z0-9-]{0,29}$")

Language = Literal["en", "te"]

# Every school name says "Synthetic" so nobody mistakes a seeded school for a real one.
SCHOOL_NAMES_V1: Final[tuple[str, ...]] = (
    "Sri Venkateswara Synthetic High School",
    "Sri Saraswathi Synthetic Vidyalayam",
    "Godavari Synthetic Public School",
    "Krishnaveni Synthetic English Medium School",
    "Nallamala Synthetic Model School",
    "Tungabhadra Synthetic Residential School",
)
BOARDS_V1: Final[tuple[tuple[str, ...], ...]] = (("BSEAP",), ("CISCE",))
TELUGU_PREFERENCE_RATE: Final = 0.35

EARLY_YEARS_CLASSES: Final = frozenset({"NUR", "LKG", "UKG"})
EARLY_YEARS_SECTIONS: Final = ("A", "B")
SCHOOL_SECTIONS: Final = ("A", "B", "C", "D")
# Subject teachers: teacher n is scoped to two consecutive classes from this list.
TEACHER_CLASSES: Final = ("VI", "VII", "VIII", "IX", "X", "XI", "XII")
# Staff per role; class_teacher is one per current-year section; unknown roles get 1.
STAFF_COUNTS_V1: Final[dict[str, int]] = {
    "owner": 1,
    "principal": 1,
    "office_admin": 1,
    "office_staff": 3,
    "accountant": 1,
    "exam_coordinator": 1,
    "teacher": 6,
    "auditor_readonly": 1,
}
CLASS_TEACHER: Final = "class_teacher"
TEACHER: Final = "teacher"
SHARED_CLASS_TEACHER_EVERY: Final = 5


class PlanError(ValueError):
    """The requested plan is invalid (bad version, prefix or tenant count)."""


@dataclass(frozen=True, slots=True)
class YearSpec:
    label: str
    starts_on: dt.date
    ends_on: dt.date
    is_current: bool


YEARS_V1: Final = (
    YearSpec("2026-27", dt.date(2026, 6, 1), dt.date(2027, 4, 30), is_current=True),
    YearSpec("2025-26", dt.date(2025, 6, 1), dt.date(2026, 4, 30), is_current=False),
)


@dataclass(frozen=True, slots=True)
class StaffSpec:
    role: str
    ordinal: int
    subject: str
    email: str
    display_name: str
    preferred_language: Language
    shared_name: bool
    section: tuple[str, str] | None = None
    """``(class_code, section_name)`` in the current year (class teachers only)."""
    classes: tuple[str, ...] = ()
    """Class codes for the class scope (subject teachers only)."""

    @property
    def key(self) -> str:
        return f"{self.role}.{self.ordinal}"


@dataclass(frozen=True, slots=True)
class TenantPlan:
    index: int
    code: str
    tenant_id: uuid.UUID
    name: str
    boards: tuple[str, ...]
    years: tuple[YearSpec, ...]
    sections: tuple[tuple[str, str], ...]
    """``(class_code, section_name)`` created in every year."""
    staff: tuple[StaffSpec, ...]

    @property
    def current_year(self) -> YearSpec:
        return next(y for y in self.years if y.is_current)

    @property
    def owner(self) -> StaffSpec:
        return next(s for s in self.staff if s.role == "owner")


@dataclass(frozen=True, slots=True)
class DatasetPlan:
    dataset_version: str
    seed: int
    tenants: tuple[TenantPlan, ...]


def tenant_id_for(code: str, dataset_version: str = DEFAULT_DATASET_VERSION) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{dataset_version}/tenant/{code}")


def subject_for(code: str, role: str, ordinal: int) -> str:
    return f"synthetic|{code}|{role}|{ordinal}"


def email_for(code: str, role: str, ordinal: int) -> str:
    return f"{role.replace('_', '-')}.{ordinal}@{code}.{EMAIL_DOMAIN}"


def _section_layout() -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for klass in tenancy.default_class_catalog():
        names = EARLY_YEARS_SECTIONS if klass.code in EARLY_YEARS_CLASSES else SCHOOL_SECTIONS
        out.extend((klass.code, n) for n in names)
    return tuple(out)


def _person_rng(
    version: str, seed: int, tenant: int | None, role: str, ordinal: int
) -> random.Random:
    stream = "shared" if tenant is None else f"tenant:{tenant}"
    return random.Random(f"{version}:{seed}:{stream}:{role}:{ordinal}")  # noqa: S311 - test data


def _is_shared(role: str, ordinal: int) -> bool:
    if role == CLASS_TEACHER:
        return ordinal % SHARED_CLASS_TEACHER_EVERY == 1
    return ordinal == 1


def _staff_for(
    version: str, seed: int, index: int, code: str, sections: tuple[tuple[str, str], ...]
) -> tuple[StaffSpec, ...]:
    staff: list[StaffSpec] = []
    for role in system_roles():
        count = len(sections) if role == CLASS_TEACHER else STAFF_COUNTS_V1.get(role, 1)
        for n in range(1, count + 1):
            shared = _is_shared(role, n)
            rng = _person_rng(version, seed, None if shared else index, role, n)
            name = generate_name(rng)
            language: Language = "te" if rng.random() < TELUGU_PREFERENCE_RATE else "en"
            section = sections[n - 1] if role == CLASS_TEACHER else None
            classes: tuple[str, ...] = ()
            if role == TEACHER:
                start = (n - 1) % (len(TEACHER_CLASSES) - 1)
                classes = TEACHER_CLASSES[start : start + 2]
            staff.append(
                StaffSpec(
                    role=role,
                    ordinal=n,
                    subject=subject_for(code, role, n),
                    email=email_for(code, role, n),
                    display_name=name.canonical,
                    preferred_language=language,
                    shared_name=shared,
                    section=section,
                    classes=classes,
                )
            )
    return tuple(staff)


def build_plan(
    *,
    seed: int = DEFAULT_SEED,
    tenants: int = DEFAULT_TENANTS,
    dataset_version: str = DEFAULT_DATASET_VERSION,
    code_prefix: str = DEFAULT_CODE_PREFIX,
) -> DatasetPlan:
    """The complete synthetic dataset for these inputs (pure and deterministic)."""
    if dataset_version not in DATASET_VERSIONS:
        raise PlanError(f"unknown dataset version {dataset_version!r}; known: {DATASET_VERSIONS}")
    if not 1 <= tenants <= MAX_TENANTS:
        raise PlanError(f"tenants must be between 1 and {MAX_TENANTS}")
    if not _PREFIX_RE.match(code_prefix):
        raise PlanError("code prefix must be lower-case letters, digits or '-' (max 30)")
    sections = _section_layout()
    plans: list[TenantPlan] = []
    for index in range(tenants):
        code = f"{code_prefix}-{chr(ord('a') + index)}"
        base = SCHOOL_NAMES_V1[index % len(SCHOOL_NAMES_V1)]
        cycle = index // len(SCHOOL_NAMES_V1)
        plans.append(
            TenantPlan(
                index=index,
                code=code,
                tenant_id=tenant_id_for(code, dataset_version),
                name=base if cycle == 0 else f"{base} {cycle + 1}",
                boards=BOARDS_V1[index % len(BOARDS_V1)],
                years=YEARS_V1,
                sections=sections,
                staff=_staff_for(dataset_version, seed, index, code, sections),
            )
        )
    return DatasetPlan(dataset_version=dataset_version, seed=seed, tenants=tuple(plans))
