"""Synthetic year-end promotion scenarios (FR-TEN-011; synthetic data only).

Loaded by path (``--import-mode=importlib``) from ``tests/students`` and the security suites.
Every scenario gets its own pair of academic years (unique labels from 2500 upwards, far from
the labels other suites use), so promotions never touch the shared current-year students of
``student_world.py``. Everything is created through the real services as the school's owner
account with office-admin permissions.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import itertools
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from app.core.db import tenant_session
from app.students import service as students
from app.students.schemas import PromotionCommitIn, StudentCreate, ValueIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearCreate, SectionCreate


def _load_student_world() -> ModuleType:
    name = "sos_test_student_world"
    if name not in sys.modules:
        path = Path(__file__).with_name("student_world.py")
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SW = _load_student_world()
_years = itertools.count(2500, 2)


@dataclass
class Pair:
    """Two consecutive academic years; ``sections[(year, class_code, name)]`` -> section id."""

    school: Any
    from_year: uuid.UUID
    to_year: uuid.UUID
    sections: dict[tuple[str, str, str], uuid.UUID] = field(default_factory=dict)

    def section(self, year: str, class_code: str, name: str = "A") -> uuid.UUID:
        return self.sections[(year, class_code, name)]


def year_pair(
    school: Any,
    *,
    source: dict[str, tuple[str, ...]] | None = None,
    target: dict[str, tuple[str, ...]] | None = None,
) -> Pair:
    """A fresh (from, to) year pair. ``source``/``target`` map class codes to section names;
    by default every class of the school gets a section ``A`` in both years."""
    first = next(_years)
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as db:
        # Active classes only: other tests may archive classes in the shared school.
        classes = {c.code: c.id for c in tenancy.list_classes(db, include_archived=False)}
        years = []
        for y in (first, first + 1):
            out = tenancy.create_academic_year(
                db,
                AcademicYearCreate(
                    label=f"{y}-{(y + 1) % 100:02d}",
                    starts_on=dt.date(y, 6, 1),
                    ends_on=dt.date(y + 1, 3, 31),
                ),
            )
            years.append(out.id)
        pair = Pair(school=school, from_year=years[0], to_year=years[1])
        default = dict.fromkeys(classes, ("A",))
        for key, year_id, layout in (
            ("from", years[0], source or default),
            ("to", years[1], target or default),
        ):
            for code, names in layout.items():
                for name in names:
                    sec = tenancy.create_section(
                        db,
                        SectionCreate(academic_year_id=year_id, class_id=classes[code], name=name),
                    )
                    pair.sections[(key, code, name)] = sec.id
    return pair


def enrolled(school: Any, section_id: uuid.UUID, *, name: str = "Synthetica Promotee") -> uuid.UUID:
    """A student enrolled in ``section_id`` (created with the owner's account)."""
    SW.configure_keyring()
    values = [ValueIn(attribute_key="full_name", source="admission_register", value=name)]
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        out = students.create_student(
            db, SW.admin_ctx(school), StudentCreate(values=values, section_id=section_id)
        )
    return out.id


def body(pair: Pair, **extra: Any) -> dict[str, Any]:
    return {"to_academic_year_id": str(pair.to_year), **extra}


def committed(school: Any, class_code: str = "IX") -> Pair:
    """A pair whose promotion (one student of ``class_code``) is committed."""
    pair = year_pair(school)
    enrolled(school, pair.section("from", class_code))
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        students.commit_promotion(
            db,
            SW.admin_ctx(school),
            pair.from_year,
            PromotionCommitIn(to_academic_year_id=pair.to_year),
        )
    return pair
