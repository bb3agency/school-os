"""Academic structure services (US-202, FR-TEN-010)."""

from __future__ import annotations

import datetime as dt
import threading
import unicodedata
import uuid
from collections.abc import Callable

import pytest
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.tenancy import service
from app.tenancy.schemas import (
    AcademicYearCreate,
    AcademicYearUpdate,
    ClassCreate,
    ClassUpdate,
    SectionCreate,
    SectionUpdate,
)

pytestmark = pytest.mark.db

MakeTenant = Callable[..., uuid.UUID]
MakeMember = Callable[..., tuple[uuid.UUID, uuid.UUID]]


def year(label: str, *, current: bool = False) -> AcademicYearCreate:
    first = int(label[:4])
    return AcademicYearCreate(
        label=label,
        starts_on=dt.date(first, 6, 1),
        ends_on=dt.date(first + 1, 4, 30),
        is_current=current,
    )


def test_US_202_AC1_year_with_nursery_to_xii_and_sections_a_to_d(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        y = service.create_academic_year(s, year("2026-27", current=True))
        classes = service.ensure_default_classes(s)
        assert [c.code for c in classes] == [
            "NUR", "LKG", "UKG", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
            "XI", "XII",
        ]  # fmt: skip
        assert classes[0].display_te == "నర్సరీ"
        for klass in classes:
            for name in service.suggested_section_names():
                service.create_section(
                    s, SectionCreate(academic_year_id=y.id, class_id=klass.id, name=name)
                )
    with tenant_session(tid) as s:
        sections = service.list_sections(s, academic_year_id=y.id)
        assert len(sections) == 15 * 4
        assert [x.name for x in sections[:4]] == ["A", "B", "C", "D"]
        assert sections[0].class_id == classes[0].id
        current = service.get_current_academic_year(s)
        assert current is not None
        assert current.id == y.id


def test_FR_TEN_010_default_classes_are_idempotent(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        custom = service.create_class(
            s, ClassCreate(code="PP1", display_en="Pre-primary 1", display_te="పీపీ 1", sort_order=5)
        )
        first = service.ensure_default_classes(s)
        second = service.ensure_default_classes(s)
    assert len(first) == len(second) == 16
    assert first[0].id == custom.id


def test_FR_TEN_010_set_current_year_switches_atomically(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        old = service.create_academic_year(s, year("2025-26", current=True))
        new = service.create_academic_year(s, year("2026-27"))
    with tenant_session(tid) as s:
        switched = service.set_current_academic_year(s, new.id, expected_version=new.version)
    assert switched.is_current
    assert switched.version == new.version + 1
    with tenant_session(tid) as s:
        years = {y.id: y for y in service.list_academic_years(s)}
    assert years[new.id].is_current
    assert not years[old.id].is_current
    assert years[old.id].version == old.version + 1  # ETag of the demoted year changes too


def test_FR_TEN_010_creating_a_current_year_demotes_the_previous_one(
    make_tenant: MakeTenant,
) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        old = service.create_academic_year(s, year("2025-26", current=True))
        new = service.create_academic_year(s, year("2026-27", current=True))
        assert service.get_academic_year(s, old.id).is_current is False
        assert service.get_academic_year(s, new.id).is_current is True


def test_FR_TEN_010_concurrent_switches_leave_exactly_one_current_year(
    make_tenant: MakeTenant,
) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        years = [service.create_academic_year(s, year(f"{2020 + i}-{21 + i}")) for i in range(4)]
    errors: list[BaseException] = []

    def switch(target_index: int) -> None:
        target = years[target_index]
        for _ in range(5):
            try:
                with tenant_session(tid) as s:
                    fresh = service.get_academic_year(s, target.id)
                    service.set_current_academic_year(s, target.id, expected_version=fresh.version)
            except PreconditionFailed:
                continue
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

    threads = [threading.Thread(target=switch, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with tenant_session(tid) as s:
        assert sum(y.is_current for y in service.list_academic_years(s)) == 1


def test_FR_TEN_010_optimistic_locking_on_year_update(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        y = service.create_academic_year(s, year("2026-27"))
    with tenant_session(tid) as s:
        updated = service.update_academic_year(
            s, y.id, AcademicYearUpdate(ends_on=dt.date(2027, 5, 31)), expected_version=y.version
        )
    assert updated.ends_on == dt.date(2027, 5, 31)
    assert updated.version == y.version + 1
    with pytest.raises(PreconditionFailed), tenant_session(tid) as s:
        service.update_academic_year(
            s, y.id, AcademicYearUpdate(ends_on=dt.date(2027, 4, 1)), expected_version=y.version
        )
    with pytest.raises(PreconditionFailed), tenant_session(tid) as s:
        service.set_current_academic_year(s, y.id, expected_version=y.version)


def test_FR_TEN_010_year_update_rejects_inverted_dates(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        y = service.create_academic_year(s, year("2026-27"))
    with pytest.raises(ValidationFailed), tenant_session(tid) as s:
        service.update_academic_year(
            s, y.id, AcademicYearUpdate(ends_on=dt.date(2026, 1, 1)), expected_version=y.version
        )


def test_FR_TEN_010_duplicate_and_overlapping_years_rejected(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        service.create_academic_year(s, year("2026-27"))
    with pytest.raises(Conflict, match="overlap"), tenant_session(tid) as s:
        service.create_academic_year(
            s,
            AcademicYearCreate(
                label="2027-28", starts_on=dt.date(2027, 4, 1), ends_on=dt.date(2028, 3, 31)
            ),
        )
    with pytest.raises(Conflict) as info, tenant_session(tid) as s:
        service.create_academic_year(
            s,
            AcademicYearCreate(
                label="2026-27", starts_on=dt.date(2026, 5, 1), ends_on=dt.date(2026, 5, 2)
            ),
        )
    assert info.value.code in {"duplicate", "academic_year_overlap"}


def test_FR_TEN_002_other_tenants_year_is_not_found(make_tenant: MakeTenant) -> None:
    a, b = make_tenant(), make_tenant()
    with tenant_session(b) as s:
        y = service.create_academic_year(s, year("2026-27"))
    with pytest.raises(NotFound), tenant_session(a) as s:
        service.get_academic_year(s, y.id)
    with pytest.raises(NotFound), tenant_session(a) as s:
        service.set_current_academic_year(s, y.id, expected_version=y.version)
    with tenant_session(a) as s:
        assert service.list_academic_years(s) == []


def test_FR_TEN_010_class_update_and_duplicate(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    with tenant_session(tid) as s:
        c = service.create_class(
            s, ClassCreate(code="IX", display_en="Class IX", display_te="9వ తరగతి", sort_order=120)
        )
        renamed = service.update_class(
            s, c.id, ClassUpdate(display_en="Grade IX"), expected_version=c.version
        )
        assert (renamed.display_en, renamed.display_te, renamed.version) == (
            "Grade IX",
            "9వ తరగతి",
            c.version + 1,
        )
    with pytest.raises(PreconditionFailed), tenant_session(tid) as s:
        service.update_class(s, c.id, ClassUpdate(sort_order=1), expected_version=c.version)
    with pytest.raises(NotFound), tenant_session(tid) as s:
        service.update_class(s, uuid.uuid4(), ClassUpdate(sort_order=1), expected_version=1)
    with pytest.raises(Conflict, match="class with this code"), tenant_session(tid) as s:
        service.create_class(
            s, ClassCreate(code="IX", display_en="Again", display_te="మళ్ళీ", sort_order=1)
        )


def _year_and_class(tid: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    with tenant_session(tid) as s:
        y = service.create_academic_year(s, year("2026-27", current=True))
        c = service.create_class(
            s, ClassCreate(code="X", display_en="Class X", display_te="10వ తరగతి", sort_order=130)
        )
    return y.id, c.id


def test_FR_TEN_010_section_class_teacher_assign_and_clear(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    tid = make_tenant()
    year_id, class_id = _year_and_class(tid)
    _, membership = make_member(tid)
    with tenant_session(tid) as s:
        sec = service.create_section(
            s,
            SectionCreate(
                academic_year_id=year_id,
                class_id=class_id,
                name="A",
                class_teacher_membership_id=membership,
            ),
        )
        assert sec.class_teacher_membership_id == membership
        renamed = service.update_section(s, sec.id, SectionUpdate(name="Rose"), expected_version=1)
        assert (renamed.name, renamed.class_teacher_membership_id) == ("Rose", membership)
        cleared = service.update_section(
            s,
            sec.id,
            SectionUpdate(class_teacher_membership_id=None),
            expected_version=renamed.version,
        )
        assert (cleared.name, cleared.class_teacher_membership_id) == ("Rose", None)
    with pytest.raises(ValidationFailed), tenant_session(tid) as s:
        service.update_section(
            s, sec.id, SectionUpdate(name=None), expected_version=cleared.version
        )


def test_FR_TEN_002_section_cannot_use_other_tenants_ids(
    make_tenant: MakeTenant, make_member: MakeMember
) -> None:
    a, b = make_tenant(), make_tenant()
    a_year, a_class = _year_and_class(a)
    b_year, b_class = _year_and_class(b)
    _, b_membership = make_member(b)
    cases = {
        "class_id": SectionCreate(academic_year_id=a_year, class_id=b_class, name="A"),
        "academic_year_id": SectionCreate(academic_year_id=b_year, class_id=a_class, name="A"),
        "class_teacher_membership_id": SectionCreate(
            academic_year_id=a_year,
            class_id=a_class,
            name="A",
            class_teacher_membership_id=b_membership,
        ),
    }
    for field, data in cases.items():
        with pytest.raises(ValidationFailed) as info, tenant_session(a) as s:
            service.create_section(s, data)
        assert info.value.errors[0]["field"] == field


def test_FR_TEN_010_duplicate_section_name_conflicts(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    year_id, class_id = _year_and_class(tid)
    data = SectionCreate(academic_year_id=year_id, class_id=class_id, name="B")
    with tenant_session(tid) as s:
        service.create_section(s, data)
    with pytest.raises(Conflict, match="section with this name"), tenant_session(tid) as s:
        service.create_section(s, data)


def test_text_inputs_are_nfc_normalised(make_tenant: MakeTenant) -> None:
    tid = make_tenant()
    decomposed = unicodedata.normalize("NFD", "  Clàss Ⅸ  ")
    with tenant_session(tid) as s:
        c = service.create_class(
            s, ClassCreate(code="IXB", display_en=decomposed, display_te="తరగతి", sort_order=1)
        )
    assert c.display_en == unicodedata.normalize("NFC", "Clàss Ⅸ")


def test_services_require_tenant_context(app_engine: object) -> None:
    from app.core.db import context_free_session

    with pytest.raises(RuntimeError, match="tenant context"), context_free_session() as s:
        service.create_class(s, ClassCreate(code="Z", display_en="Z", display_te="Z", sort_order=1))
