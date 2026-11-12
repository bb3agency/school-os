"""Enrolment statistics sheet (FO-06; FR-EXP-009..FR-EXP-019; invariants 3, 5, 7, 14).
Synthetic data only.

A fresh school per module, so the counts are exactly the students made here. The authorization
matrix (tests/security/test_authz_matrix.py with tests/exports/pack_security_support.py) covers
every role; these tests cover the counts, an allowed and a disallowed role, another school's
year (404), step-up and the audit trail.
"""

from __future__ import annotations

import datetime as dt
import io
import sys
import uuid
from typing import Any

import pytest
from openpyxl import load_workbook
from sqlalchemy import Engine, text

from app.exports import enrolment_stats as stats
from app.exports.enrolment_stats import ClassRow, Movement, StudentFacts
from app.students.schemas import ValueIn

EX = sys.modules["sos_test_exports_objects"]
BASE = "/api/v1/reports/enrolment-statistics"
SRC = "admission_register"


# --- pure ---------------------------------------------------------------------------------------


def test_FR_EXP_012_config_is_unverified_with_sources() -> None:
    cfg = stats.load_stats_config()
    assert cfg.verified is False
    assert cfg.source
    assert (cfg.reference_date.month, cfg.reference_date.day) == (9, 30)
    assert set(cfg.genders) == {"female", "male", "transgender"}
    assert set(cfg.categories) == {"general", "ews", "obc", "sc", "st"}


def test_FR_EXP_012_minority_groups_from_religion_text() -> None:
    cfg = stats.load_stats_config()
    assert cfg.minority_of("  Islam ") == "muslim"
    assert cfg.minority_of("CHRISTIAN") == "christian"
    assert cfg.minority_of("Hindu") == "none"
    assert cfg.minority_of("") == "not_recorded"
    assert cfg.minority_of(None) == "not_recorded"


def test_FR_EXP_015_age_on_the_day_in_completed_years() -> None:
    day = dt.date(2026, 9, 30)
    assert stats.age_on(dt.date(2012, 9, 30), day) == 14
    assert stats.age_on(dt.date(2012, 10, 1), day) == 13
    assert stats.age_on(dt.date(2020, 2, 29), day) == 6


def test_FR_EXP_012_reference_date_is_30_september_of_the_start_year() -> None:
    cfg = stats.load_stats_config()
    year: Any = type("Y", (), {"starts_on": dt.date(2026, 6, 1), "ends_on": dt.date(2027, 3, 31)})()
    assert stats.reference_date(cfg, year) == dt.date(2026, 9, 30)
    late: Any = type(
        "Y", (), {"starts_on": dt.date(2026, 10, 15), "ends_on": dt.date(2027, 9, 1)}
    )()
    assert stats.reference_date(cfg, late) == dt.date(2027, 9, 1)


def test_FR_EXP_009_build_tables_counts_per_class() -> None:
    cfg = stats.load_stats_config()
    ix, x = uuid.uuid4(), uuid.uuid4()
    day = dt.date(2026, 9, 30)
    facts = [
        StudentFacts(ix, "female", dt.date(2012, 3, 14), "sc", "Islam", True, False),
        StudentFacts(ix, "male", dt.date(2012, 3, 14), "general", "Hindu", False, True),
        StudentFacts(ix, "transgender", None, None, None, False, False),
        StudentFacts(x, None, dt.date(2008, 1, 1), "obc", "", False, False),
    ]
    classes = [ClassRow(ix, "Class IX"), ClassRow(x, "Class X")]
    tables: dict[str, Any] = {
        t.key: t
        for t in stats.build_tables(
            cfg, facts, classes, day, Movement({ix: 2}, {x: 1}), restricted=True
        )
    }
    assert list(tables) == [
        "gender",
        "age",
        "category",
        "minority",
        "cwsn",
        "repeaters",
        "movement",
    ]

    def row(key: str, label: str) -> dict[str, int]:
        t = tables[key]
        r = next(r for r in [*t.rows, t.totals] if r.label == label)
        return {c.key: n for c, n in zip(t.columns, r.counts, strict=True)} | {"_": r.total}

    assert row("gender", "Class IX") == {
        "female": 1,
        "male": 1,
        "transgender": 1,
        "not_recorded": 0,
        "_": 3,
    }
    assert row("gender", "Total")["_"] == 4
    assert row("age", "Class IX")["age_14"] == 2
    assert row("age", "Class IX")["not_recorded"] == 1
    assert row("age", "Class X")["18_plus"] == 1
    assert row("category", "Class IX")["sc_female"] == 1
    assert row("category", "Class IX")["general_male"] == 1
    assert row("category", "Class IX")["not_recorded"] == 1
    assert row("minority", "Class IX")["muslim_female"] == 1
    assert row("minority", "Class IX")["none"] == 1
    assert row("minority", "Class X")["not_recorded"] == 1
    assert row("cwsn", "Class IX") == {"female": 1, "male": 0, "transgender": 0, "_": 1}
    assert row("repeaters", "Total") == {"female": 0, "male": 1, "transgender": 0, "_": 1}
    assert row("movement", "Class IX") == {"admitted": 2, "left": 0, "_": 3}
    assert row("movement", "Total") == {"admitted": 2, "left": 1, "_": 4}

    hidden = stats.build_tables(cfg, facts, classes, day, Movement({}, {}), restricted=False)
    assert [t.key for t in hidden] == ["gender", "age", "repeaters", "movement"]
    assert not any(t.restricted for t in hidden)


# --- API ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stats_school(world: Any, admin_engine: Engine) -> Any:
    """A fresh school: Class IX (9A, 9C) and Class X (10A) of 2026-27."""
    s = EX.build_school(admin_engine)
    # 9A: a girl (SC, Muslim, CWSN), a boy (no category); 10A: a girl admitted in the year.
    EX.student(
        s,
        dob="2012-03-14",
        extra=[
            ValueIn(attribute_key="admission_date", source=SRC, value="2019-06-12"),
            ValueIn(attribute_key="category", source=SRC, value="sc"),
            ValueIn(attribute_key="religion", source=SRC, value="Islam"),
            ValueIn(attribute_key="disability", source="parent_form", value="Low vision"),
        ],
    )
    EX.student(
        s,
        dob="2011-11-02",
        extra=[
            ValueIn(attribute_key="admission_date", source=SRC, value="2018-06-11"),
            ValueIn(attribute_key="gender", source="manual_entry", value="male"),
        ],
    )
    EX.student(
        s,
        section_key="section_10a",
        dob="2011-05-20",
        extra=[ValueIn(attribute_key="admission_date", source=SRC, value="2026-07-01")],
    )
    return s


def _audit(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows: Any = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t AND action = :a ORDER BY seq"
            ),
            {"t": tenant_id, "a": action},
        ).scalars()
        return list(rows)


def _counts(body: dict[str, Any], key: str, label: str) -> dict[str, int]:
    table = next(t for t in body["tables"] if t["key"] == key)
    row = next(r for r in [*table["rows"], table["totals"]] if r["label"] == label)
    cols = [c["key"] for c in table["columns"]]
    return dict(zip(cols, row["counts"], strict=True)) | {"_": row["total"]}


@pytest.mark.db
def test_FR_EXP_009_to_011_counts_for_the_office(stats_school: Any, api: Any) -> None:
    office = stats_school.people["office_admin"]
    res = api.call(office, "GET", BASE)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["as_of"] == "2026-09-30"
    assert body["reference_date"] == "2026-09-30"
    assert body["verified"] is False
    assert body["restricted_hidden"] is False
    # The student admitted on 2026-07-01 is enrolled on 30 September; all three are counted.
    assert body["students"] == 3
    # The second student's register gender is female; manual entry does not outrank it.
    assert _counts(body, "gender", "Class IX")["female"] == 2
    assert _counts(body, "gender", "Total")["_"] == 3
    assert _counts(body, "age", "Class IX")["age_14"] == 2
    assert _counts(body, "age", "Class X")["age_15"] == 1
    assert _counts(body, "category", "Class IX")["sc_female"] == 1
    assert _counts(body, "category", "Total")["not_recorded"] == 2
    assert _counts(body, "minority", "Class IX")["muslim_female"] == 1
    assert _counts(body, "cwsn", "Total")["_"] == 1
    assert _counts(body, "movement", "Class X") == {"admitted": 1, "left": 0, "_": 1}
    gaps = {g["attribute_key"]: g["count"] for g in body["gaps"]}
    assert gaps == {"category": 2, "religion": 2}
    text_body = res.text
    assert "Synthetica" not in text_body, "counts only, never names"
    assert "student_id" not in text_body


@pytest.mark.db
def test_FR_EXP_011_restricted_tables_need_sensitive(stats_school: Any, api: Any) -> None:
    # exam_coordinator: export.portal and school-wide student.read_basic, no read_sensitive.
    res = api.call(stats_school.people["exam_coordinator"], "GET", BASE)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["restricted_hidden"] is True
    assert [t["key"] for t in body["tables"]] == ["gender", "age", "repeaters", "movement"]
    assert {g["attribute_key"] for g in body["gaps"]} <= {"gender", "dob"}


@pytest.mark.db
def test_FR_EXP_010_disallowed_role_and_other_school(
    stats_school: Any, world: Any, api: Any
) -> None:
    res = api.call(stats_school.people["class_teacher"], "GET", BASE)
    assert res.status_code == 403
    res = api.call(stats_school.people["office_staff"], "GET", BASE)
    assert res.status_code == 403
    other = api.call(
        stats_school.people["office_admin"],
        "GET",
        BASE,
        params={"academic_year_id": str(world.b.ids["year"])},
    )
    assert other.status_code == 404
    out = api.call(stats_school.people["office_admin"], "GET", BASE, params={"as_of": "2027-05-01"})
    assert out.status_code == 422
    assert out.json()["errors"][0]["code"] == "as_of_out_of_year"


@pytest.mark.db
def test_FR_EXP_013_014_print_and_xlsx_are_audited_with_step_up(
    stats_school: Any, api: Any, admin_engine: Engine
) -> None:
    office = stats_school.people["office_admin"]
    stale = api.call(office, "GET", f"{BASE}/print", auth_age_s=3600)
    assert stale.status_code == 428
    check = api.call(office, "GET", f"{BASE}/print", params={"check": "true"})
    assert check.status_code == 204
    assert _audit(admin_engine, stats_school.tenant_id, "export.enrolment_statistics.printed") == []

    page = api.call(office, "GET", f"{BASE}/print")
    assert page.status_code == 200, page.text
    assert page.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert "Enrolment by class and gender" in page.text
    assert "Synthetica" not in page.text
    assert "not yet verified" in page.text
    (printed,) = _audit(admin_engine, stats_school.tenant_id, "export.enrolment_statistics.printed")
    assert printed["students"] == 3
    assert printed["as_of"] == "2026-09-30"
    assert printed["restricted_tables"] == ["category", "minority", "cwsn"]

    xlsx = api.call(office, "GET", f"{BASE}/xlsx")
    assert xlsx.status_code == 200, xlsx.text
    assert xlsx.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = load_workbook(io.BytesIO(xlsx.content))
    assert wb.sheetnames[:3] == ["About", "Gender", "Age"]
    (downloaded,) = _audit(
        admin_engine, stats_school.tenant_id, "export.enrolment_statistics.downloaded"
    )
    assert downloaded["format"] == "xlsx"
    viewed = _audit(admin_engine, stats_school.tenant_id, "export.enrolment_statistics.viewed")
    assert viewed, "the screen view with restricted tables is audited"
