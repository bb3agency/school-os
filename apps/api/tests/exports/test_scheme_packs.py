"""Government scheme verification pack (FO-11; FR-EXP-020..FR-EXP-030; invariants 3, 5, 7, 14).
Synthetic data only.

A fresh school per module. The authorization matrix (tests/security/test_authz_matrix.py with
tests/exports/pack_security_support.py) covers every role; these tests cover the lists, the
bulk certificates, an allowed and a disallowed role, reach (404), another school's student,
step-up and the audit trail.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.academics import service as academics
from app.academics.schemas import AttendanceEntryIn, AttendanceWrite
from app.core.db import tenant_session
from app.exports import scheme_packs as schemes

EX = sys.modules["sos_test_exports_objects"]
SW = EX.SW
BASE = "/api/v1/scheme-packs"
PERIOD = {"from_date": "2026-07-01", "to_date": "2026-07-31"}


def test_FR_EXP_027_config_is_unverified_and_makes_no_eligibility_decision() -> None:
    cfg = schemes.load_scheme_config()
    assert cfg.verified is False
    assert "thalliki_vandanam" in cfg.schemes
    assert all(not s.verified and s.source for s in cfg.schemes.values())
    assert cfg.attendance.present_statuses == ("present", "late")
    assert cfg.attendance.excluded_statuses == ("leave",)
    raw = schemes.CONFIG_PATH.read_text(encoding="utf-8")
    assert "threshold:" not in raw
    assert "eligible:" not in raw


def test_FR_EXP_021_percent_one_decimal() -> None:
    assert schemes.percent(4, 5) == 80.0
    assert schemes.percent(2, 3) == 66.7
    assert schemes.percent(0, 0) is None


@pytest.fixture(scope="module")
def pack_school(world: Any, admin_engine: Engine) -> Any:
    s = EX.build_school(admin_engine)
    s.people["class_teacher_9a"] = s.people["class_teacher"]
    marked = EX.student(s, roll_no="1")
    unmarked = EX.student(s, roll_no="2")
    s.ids["marked"], s.ids["unmarked"] = marked, unmarked
    s.ids["x_student"] = EX.student(s, section_key="section_10a")
    statuses = ["present", "present", "present", "late", "absent", "leave"]
    entries = [
        AttendanceEntryIn(student_id=marked, on_date=dt.date(2026, 7, 6 + i), status=st)
        for i, st in enumerate(statuses)
    ]
    with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
        academics.record_attendance(
            db, SW.admin_ctx(s), s.ids["section_9a"], AttendanceWrite(entries=entries)
        )
    return s


def _params(school: Any, section: str = "section_9a", **kw: str) -> dict[str, str]:
    return {
        "scheme_key": "thalliki_vandanam",
        "section_id": str(school.ids[section]),
        **PERIOD,
        **kw,
    }


def _audit(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows: Any = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t AND action = :a ORDER BY seq"
            ),
            {"t": tenant_id, "a": action},
        ).scalars()
        return list(rows)


@pytest.mark.db
def test_FR_EXP_028_schemes_listed(pack_school: Any, api: Any) -> None:
    res = api.call(pack_school.people["office_staff"], "GET", BASE)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verified"] is False
    assert {s["key"] for s in body["schemes"]} >= {"thalliki_vandanam", "scholarship"}
    assert api.call(pack_school.people["accountant"], "GET", BASE).status_code == 403


@pytest.mark.db
def test_FR_EXP_020_021_attendance_list(pack_school: Any, api: Any) -> None:
    office = pack_school.people["office_admin"]
    res = api.call(office, "GET", f"{BASE}/attendance", params=_params(pack_school))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["section_label"] == "IX-A"
    rows = {r["student_id"]: r for r in body["students"]}
    marked = rows[str(pack_school.ids["marked"])]
    assert (marked["days_counted"], marked["days_present"], marked["leave_days"]) == (5, 4, 1)
    assert marked["percent"] == 80.0
    assert rows[str(pack_school.ids["unmarked"])]["percent"] is None
    assert "eligible" not in res.text
    assert [r["roll_no"] for r in body["students"]] == ["1", "2"]

    # A role without attendance.read: 403. A class teacher of 9A: 9A yes, 10A 404.
    denied = api.call(
        pack_school.people["office_staff"], "GET", f"{BASE}/attendance", params=_params(pack_school)
    )
    assert denied.status_code == 403
    teacher = pack_school.people["class_teacher"]
    own = api.call(teacher, "GET", f"{BASE}/attendance", params=_params(pack_school))
    assert own.status_code == 200, own.text
    other = api.call(
        teacher, "GET", f"{BASE}/attendance", params=_params(pack_school, "section_10a")
    )
    assert other.status_code == 404


@pytest.mark.db
def test_FR_EXP_020_period_and_scheme_errors(pack_school: Any, world: Any, api: Any) -> None:
    office = pack_school.people["office_admin"]
    bad = api.call(
        office,
        "GET",
        f"{BASE}/attendance",
        params=_params(pack_school, from_date="2026-08-01", to_date="2026-07-01"),
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["code"] == "invalid_period"
    future = api.call(
        office,
        "GET",
        f"{BASE}/attendance",
        params=_params(pack_school, to_date="2027-03-01"),
    )
    assert future.status_code == 422
    unknown = api.call(
        office, "GET", f"{BASE}/attendance", params=_params(pack_school, scheme_key="nope")
    )
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["code"] == "unknown_scheme"
    foreign = dict(_params(pack_school), section_id=str(world.b.ids["section_9a"]))
    assert api.call(office, "GET", f"{BASE}/attendance", params=foreign).status_code == 404


@pytest.mark.db
def test_FR_EXP_022_print_and_xlsx_audited_with_step_up(
    pack_school: Any, api: Any, admin_engine: Engine
) -> None:
    office = pack_school.people["office_admin"]
    stale = api.call(
        office, "GET", f"{BASE}/attendance/print", params=_params(pack_school), auth_age_s=3600
    )
    assert stale.status_code == 428
    check = api.call(
        office, "GET", f"{BASE}/attendance/print", params=_params(pack_school, check="true")
    )
    assert check.status_code == 204
    assert _audit(admin_engine, pack_school.tenant_id, "export.scheme_attendance.printed") == []
    page = api.call(office, "GET", f"{BASE}/attendance/print", params=_params(pack_school))
    assert page.status_code == 200, page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert "80.0%" in page.text
    assert "Head of the institution" in page.text
    (printed,) = _audit(admin_engine, pack_school.tenant_id, "export.scheme_attendance.printed")
    assert printed["students"] == 2
    assert printed["scheme_key"] == "thalliki_vandanam"
    assert "Synthetica" not in str(printed), "no names in the audit log"

    xlsx = api.call(office, "GET", f"{BASE}/attendance/xlsx", params=_params(pack_school))
    assert xlsx.status_code == 200, xlsx.text
    assert xlsx.content[:2] == b"PK"
    assert _audit(admin_engine, pack_school.tenant_id, "export.scheme_attendance.downloaded")


@pytest.mark.db
def test_FR_EXP_024_026_bulk_certificates(
    pack_school: Any, world: Any, api: Any, admin_engine: Engine
) -> None:
    clerk = pack_school.people["office_staff"]
    foreign = uuid.uuid4()
    body = {
        "scheme_key": "thalliki_vandanam",
        "student_ids": [
            str(pack_school.ids["marked"]),
            str(pack_school.ids["unmarked"]),
            str(pack_school.ids["marked"]),  # duplicates are issued once
            str(foreign),
        ],
    }
    res = api.call(
        clerk,
        "POST",
        f"{BASE}/certificates",
        json=body,
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["certificate_type"] == "study"
    assert (out["issued"], out["refused"]) == (2, 1)
    results = {r["student_id"]: r for r in out["results"]}
    serials = {results[str(pack_school.ids[k])]["serial"] for k in ("marked", "unmarked")}
    assert len(serials) == 2, "each certificate has its own serial"
    assert None not in serials
    assert results[str(foreign)] == {
        "student_id": str(foreign),
        "status": "refused",
        "certificate_id": None,
        "serial": None,
        "error_code": "not_found",
    }
    (batch,) = _audit(admin_engine, pack_school.tenant_id, "export.scheme_certificates.issued")
    assert (batch["issued"], batch["refused"], batch["requested"]) == (2, 1, 3)
    issued = _audit(admin_engine, pack_school.tenant_id, "certificate.issued")
    assert len(issued) >= 2

    # The certificates carry the scheme's purpose.
    with admin_engine.connect() as c:
        inputs: Any = c.execute(
            text("SELECT inputs FROM sis.certificates WHERE id = :i"),
            {"i": results[str(pack_school.ids["marked"])]["certificate_id"]},
        ).scalar_one()
    assert inputs == {"purpose_note": "Thalliki Vandanam scheme verification"}

    # Bonafide for the scholarship scheme: purpose "scholarship".
    res = api.call(
        clerk,
        "POST",
        f"{BASE}/certificates",
        json={"scheme_key": "scholarship", "student_ids": [str(pack_school.ids["x_student"])]},
    )
    assert res.status_code == 201, res.text
    assert res.json()["certificate_type"] == "bonafide"
    assert res.json()["issued"] == 1

    # A role without certificate.issue: 403. Another school's student: refused, not found.
    denied = api.call(pack_school.people["teacher"], "POST", f"{BASE}/certificates", json=body)
    assert denied.status_code == 403
    other = api.call(
        clerk,
        "POST",
        f"{BASE}/certificates",
        json={"scheme_key": "scholarship", "student_ids": [str(world.b.ids["year"])]},
    )
    assert other.status_code == 201
    assert other.json()["results"][0]["error_code"] == "not_found"
