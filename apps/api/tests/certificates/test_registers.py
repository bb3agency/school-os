"""Register print views (US-1106; FR-REG-001..005, BR-11): TC register, certificate issue
register, admission and withdrawal register. Synthetic data only."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.certificates import service as certificates
from app.core.errors import ValidationFailed
from app.core.languages import contains_telugu

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]


def _page(school: Any, fn: Any, **kw: Any) -> str:
    page: str = C.call(school, school.people["office_admin"], "office_admin", fn, **kw)
    return page


def _register_audit(admin: Engine, tenant_id: Any) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t "
                "AND action = 'register.viewed' ORDER BY seq DESC LIMIT 1"
            ),
            {"t": tenant_id},
        ).one()
    value: dict[str, Any] = dict(row[0])
    return value


def test_ADR_0036_registers_are_english_while_telugu_is_hidden(school: Any) -> None:
    C.issued_tc(school)
    C.issue(school, C.student(school), "bonafide")
    pages = [
        _page(school, certificates.register_page, kind="transfer", academic_year_id=None),
        _page(school, certificates.register_page, kind="certificates", academic_year_id=None),
        _page(school, certificates.admission_register_page, academic_year_id=None),
    ]
    for page in pages:
        assert "<thead>" in page or 'class="empty"' in page
        assert not contains_telugu(page)
        assert "Noto Sans Telugu" not in page


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_REG_001_tc_register_lists_every_serial_with_cancelled_and_duplicates(
    school: Any, admin_engine: Engine
) -> None:
    first = C.issued_tc(school)
    second = C.issued_tc(school)
    C.cancel(school, second)
    dup = C.approve(school, C.duplicate(school, first.id))
    page = _page(school, certificates.register_page, kind="transfer", academic_year_id=None)
    assert "Transfer certificate register (counterfoil)" in page
    assert "బదిలీ ధృవీకరణ పత్రాల రిజిస్టర్" in page
    assert first.serial in page
    assert second.serial in page
    assert f"{first.serial} (D{dup.duplicate_no})" in page
    assert "CANCELLED" in page
    assert '<tr class="cancelled">' in page
    assert "Synthetic Principal Suresh" in page, "approved by"
    assert "Synthetic Clerk Lakshmi" in page, "prepared by"
    audit = _register_audit(admin_engine, school.tenant_id)
    assert audit["register"] == "transfer"
    assert audit["rows"] >= 3
    assert "full_name" not in str(audit)


def test_FR_REG_002_certificate_register_filters_by_type(school: Any) -> None:
    bonafide = C.issue(school, C.student(school), "bonafide")
    conduct = C.issue(school, C.student(school), "conduct")
    both = _page(school, certificates.register_page, kind="certificates", academic_year_id=None)
    assert bonafide.serial in both
    assert conduct.serial in both
    only = _page(
        school,
        certificates.register_page,
        kind="certificates",
        academic_year_id=None,
        certificate_type="conduct",
    )
    assert conduct.serial in only
    assert bonafide.serial not in only
    with pytest.raises(ValidationFailed):
        _page(
            school,
            certificates.register_page,
            kind="certificates",
            academic_year_id=None,
            certificate_type="transfer",
        )


def test_FR_REG_001_empty_year_prints_a_note(school: Any) -> None:
    page = _page(
        school,
        certificates.register_page,
        kind="transfer",
        academic_year_id=school.ids["old_year"],
    )
    assert "No certificates were issued in this academic year." in page


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_REG_003_admission_and_withdrawal_register(school: Any) -> None:
    staying = C.student(school, admission_no="AW/0009")
    leaving = C.student(school, admission_no="AW/0010")
    tc = C.issued_tc(school, leaving)
    page = _page(school, certificates.admission_register_page, academic_year_id=None)
    assert "Admission and withdrawal register" in page
    assert "ప్రవేశ, నిష్క్రమణ రిజిస్టర్" in page
    assert page.index("AW/0009") < page.index("AW/0010"), "natural admission-number order"
    assert tc.serial in page
    assert "Parent transferred" in page
    assert "14/03/2012" in page
    del staying


def test_FR_REG_003_admission_numbers_sort_naturally_on_every_number() -> None:
    """Admission numbers that carry a year (``2024/15``) sort by year first, then by number:
    every run of digits is compared as a number, not only the last one."""
    given = ["2025/3", "2024/15", "", "2024/2", "2025/10", "A/9", "A/10", "2024/100", "B/1"]
    ordered = sorted(given, key=certificates._admission_sort_key)
    assert ordered == [
        "2024/2",
        "2024/15",
        "2024/100",
        "2025/3",
        "2025/10",
        "A/9",
        "A/10",
        "B/1",
        "",
    ]


def test_FR_REG_004_register_rows_are_limited(school: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    C.issue(school, C.student(school), "study")
    C.issue(school, C.student(school), "study")
    cfg = certificates.settings()
    monkeypatch.setattr(
        certificates, "settings", lambda: cfg.model_copy(update={"register_max_rows": 1})
    )
    with pytest.raises(ValidationFailed) as exc:
        _page(school, certificates.register_page, kind="certificates", academic_year_id=None)
    assert exc.value.errors[0]["code"] == "too_many_rows"
