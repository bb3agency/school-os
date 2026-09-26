"""Input validation for tenancy schemas (no database)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from app.tenancy.schemas import (
    AcademicYearCreate,
    AcademicYearUpdate,
    ClassCreate,
    SectionCreate,
    SectionUpdate,
    TenantProvisionIn,
)
from app.tenancy.service import default_class_catalog, suggested_section_names
from pydantic import ValidationError


def test_unknown_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError, match="extra"):
        ClassCreate.model_validate(
            {"code": "I", "display_en": "I", "display_te": "I", "sort_order": 1, "tenant_id": "x"}
        )


@pytest.mark.parametrize(
    ("label", "starts", "ends"),
    [
        ("2026-28", dt.date(2026, 6, 1), dt.date(2027, 4, 30)),
        ("2025-26", dt.date(2026, 6, 1), dt.date(2027, 4, 30)),
        ("2026-27", dt.date(2026, 6, 1), dt.date(2026, 6, 1)),
        ("26-27", dt.date(2026, 6, 1), dt.date(2027, 4, 30)),
    ],
)
def test_FR_TEN_010_year_validation(label: str, starts: dt.date, ends: dt.date) -> None:
    with pytest.raises(ValidationError):
        AcademicYearCreate(label=label, starts_on=starts, ends_on=ends)


def test_FR_TEN_010_century_rollover_label() -> None:
    y = AcademicYearCreate(
        label="2099-00", starts_on=dt.date(2099, 6, 1), ends_on=dt.date(2100, 4, 30)
    )
    assert y.label == "2099-00"


def test_year_update_is_partial() -> None:
    assert AcademicYearUpdate(ends_on=dt.date(2027, 1, 1)).model_dump(exclude_unset=True) == {
        "ends_on": dt.date(2027, 1, 1)
    }


def test_section_update_distinguishes_clear_from_omit() -> None:
    assert SectionUpdate().model_fields_set == set()
    assert SectionUpdate(class_teacher_membership_id=None).model_fields_set == {
        "class_teacher_membership_id"
    }


def test_control_characters_rejected() -> None:
    with pytest.raises(ValidationError):
        SectionCreate(academic_year_id=uuid.uuid4(), class_id=uuid.uuid4(), name="A\x00")


def test_tenant_input_normalised() -> None:
    t = TenantProvisionIn(code=" demo-school ", name="  Demo  ", boards=["CISCE", "BSEAP"])
    assert (t.code, t.name, t.boards, t.plan_tier) == (
        "demo-school",
        "Demo",
        ["CISCE", "BSEAP"],
        "shared",
    )


def test_US_202_default_catalogue_is_valid() -> None:
    catalog = default_class_catalog()
    assert len(catalog) == 15
    assert len({c.code for c in catalog}) == 15
    assert [c.sort_order for c in catalog] == sorted(c.sort_order for c in catalog)
    assert all(c.display_te and c.display_en for c in catalog)
    assert suggested_section_names() == ("A", "B", "C", "D")
