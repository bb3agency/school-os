"""Values the disclosure fills from SchoolOS records (ADR-0051 decision 1; FR-DISC-003).

Computed in the school's ``tenant_session`` through other modules' public services only
(CLAUDE.md §4): ``tenancy`` (school name and letterhead address, the current academic year, its
classes and sections), ``identity`` (active staff by role and by category: counts and the
principal's display name, no contact details), ``students`` (format version 2: the number of
active enrolments per section of the current academic year, summed per class; FR-DISC-015) and
``fees`` (the active general fee plans per class: heads and annual amounts, only when fees are
on). School-level facts and aggregates only: no student is named or listed, and a count of
students below 10 is published as "fewer than 10" (NFR-PRV-006, D42).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping
from typing import Final

from sqlalchemy.orm import Session

from app.disclosure.values import Derived, Table, count_text, format_date, rupees
from app.fees import service as fees
from app.identity import service as identity
from app.students import service as students
from app.tenancy import service as tenancy

PRINCIPAL_ROLE: Final = "principal"
TEACHER_ROLES: Final = frozenset({"teacher", "class_teacher"})
# Staff strength by category (FR-DISC-016): each active member counted once, in the first
# category whose roles they hold. Counts only (lead decision D84).
CATEGORIES: Final = (
    ("Principal", frozenset({PRINCIPAL_ROLE})),
    ("Teachers", TEACHER_ROLES),
)
OTHER_STAFF: Final = "Other staff"


def school_values(session: Session) -> tuple[str, dict[str, str | None]]:
    """The school's name and the ``school.*`` sources (empty settings are ``None``)."""
    tenant = tenancy.get_tenant(session)
    address = tenant.settings.certificate_letterhead.address_en.strip() or None
    return tenant.name, {"school.name": tenant.name, "school.address": address}


def is_cbse(session: Session) -> bool:
    """Whether the school's boards include CBSE (FR-TEN-020; FR-DISC-001)."""
    boards, _ = tenancy.school_boards(session)
    return "CBSE" in boards


def derived_values(session: Session) -> dict[str, Derived]:
    """Every derived source as it is now (FR-DISC-003)."""
    members = identity.active_members(session)
    roles = Counter(role for m in members for role in m.roles)
    teachers = sum(1 for m in members if TEACHER_ROLES & set(m.roles))
    principals = sorted(m.display_name for m in members if PRINCIPAL_ROLE in m.roles)
    names = {r.key: r.name_en for r in identity.list_roles(session)}
    staff_rows = tuple(
        (names.get(key, key), str(count))
        for key, count in sorted(roles.items(), key=lambda kv: names.get(kv[0], kv[0]))
        if count > 0
    )
    year = tenancy.get_current_academic_year(session)
    sections = (
        tenancy.list_sections(session, academic_year_id=year.id, include_archived=False)
        if year is not None
        else []
    )
    out: dict[str, Derived] = {
        "principal": Derived(value=", ".join(principals) or None),
        "teachers_total": Derived(value=str(teachers)),
        "sections_count": Derived(value=str(len(sections)) if year is not None else None),
        "teacher_section_ratio": Derived(
            value=f"{teachers / len(sections):.2f}" if sections else None
        ),
        "staff_by_role": Derived(
            table=Table(columns=("Role", "Number"), rows=staff_rows) if staff_rows else None
        ),
        "academic_year": Derived(
            value=(
                f"{year.label} ({format_date(year.starts_on)} to {format_date(year.ends_on)})"
                if year is not None
                else None
            )
        ),
        "staff_by_category": _staff_by_category([set(m.roles) for m in members]),
        "registered_students": _registered_students(session, year.id if year else None),
        "fee_structure": _fee_structure(session, year.id if year is not None else None),
    }
    return out


def _staff_by_category(member_roles: list[set[str]]) -> Derived:
    counts: Counter[str] = Counter()
    for roles in member_roles:
        label = next((name for name, keys in CATEGORIES if keys & roles), OTHER_STAFF)
        counts[label] += 1
    order = [name for name, _keys in CATEGORIES] + [OTHER_STAFF]
    rows = tuple((name, str(counts[name])) for name in order if counts[name] > 0)
    if not rows:
        return Derived()
    total = sum(counts.values())
    return Derived(table=Table(columns=("Category", "Number"), rows=(*rows, ("Total", str(total)))))


def _registered_students(session: Session, year_id: uuid.UUID | None) -> Derived:
    """Active enrolments of the current academic year per class (FR-DISC-015): classes with at
    least one, in the school's class order, and the total; below 10 is "fewer than 10"."""
    if year_id is None:
        return Derived()
    placements = students.current_placements(session)
    if not placements:
        return Derived()
    per_section = Counter(placements.values())
    sections = tenancy.list_sections(session, academic_year_id=year_id, include_archived=True)
    class_of = {s.id: s.class_id for s in sections}
    per_class: Counter[uuid.UUID] = Counter()
    for section_id, count in per_section.items():
        class_id = class_of.get(section_id)
        if class_id is not None:
            per_class[class_id] += count
    classes = {c.id: c for c in tenancy.list_classes(session, include_archived=True)}
    ordered = sorted(
        per_class.items(),
        key=lambda kv: (
            classes[kv[0]].sort_order if kv[0] in classes else 10_000,
            classes[kv[0]].display_en if kv[0] in classes else "",
        ),
    )
    rows = tuple(
        (classes[cid].display_en if cid in classes else "Other", count_text(n))
        for cid, n in ordered
    )
    total = sum(per_class.values())
    return Derived(
        table=Table(
            columns=("Class", "Registered students"),
            rows=(*rows, ("Total", count_text(total))),
        )
    )


def _fee_structure(session: Session, year_id: uuid.UUID | None) -> Derived:
    tenant = tenancy.get_tenant(session)
    if year_id is None or not fees.enabled(session, tenant.id):
        return Derived()
    classes: Mapping[uuid.UUID, tuple[int, str]] = {
        c.id: (c.sort_order, c.display_en) for c in tenancy.list_classes(session)
    }
    rows = sorted(
        fees.fee_structure(session, year_id),
        key=lambda r: classes.get(r.class_id, (10_000, ""))[0],
    )
    if not rows:
        return Derived()
    table_rows = tuple(
        (
            classes.get(r.class_id, (0, ""))[1],
            "; ".join(f"{h.name} {rupees(h.annual_amount)}" for h in r.heads),
            rupees(r.annual_total),
        )
        for r in rows
    )
    return Derived(
        table=Table(columns=("Class", "Fee heads (per year)", "Total per year"), rows=table_rows)
    )


__all__ = ["derived_values", "is_cbse", "school_values"]
