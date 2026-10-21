"""Values the disclosure fills from SchoolOS records (ADR-0051 decision 1; FR-DISC-003).

Computed in the school's ``tenant_session`` through other modules' public services only
(CLAUDE.md §4): ``tenancy`` (school name and letterhead address, the current academic year and
its sections), ``identity`` (active staff by role: counts and the principal's display name, no
contact details) and ``fees`` (the active general fee plans per class: heads and annual amounts,
only when fees are on). School-level facts and aggregates only: nothing here reads a student.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping
from typing import Final

from sqlalchemy.orm import Session

from app.disclosure.values import Derived, Table, format_date, rupees
from app.fees import service as fees
from app.identity import service as identity
from app.tenancy import service as tenancy

PRINCIPAL_ROLE: Final = "principal"
TEACHER_ROLES: Final = frozenset({"teacher", "class_teacher"})


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
        "fee_structure": _fee_structure(session, year.id if year is not None else None),
    }
    return out


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
