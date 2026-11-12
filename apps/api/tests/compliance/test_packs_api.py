"""Inspection readiness packs over HTTP (FC-07, FO-12; FR-COMP-024..FR-COMP-045). Real
database (RLS), synthetic data only.

The office sees, per authority, which items SchoolOS holds (certificates from the FC-01
register, registers, the Appendix IX page, the privacy notice, the DPDP checklist) and which it
does not track yet, then makes a pack: the states are frozen, the registers chosen are listed,
the index prints, and making it is audited in the same transaction.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.audit import service as audit_service
from app.compliance import packs
from app.compliance.packs_page import STYLE_CSP

pytestmark = pytest.mark.db
S = sys.modules["sos_test_compliance_support"]
W = S.W
BASE = S.BASE


def _readiness(api: Any, who: Any) -> dict[str, Any]:
    res = api.call(who, "GET", f"{BASE}/inspection-readiness")
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


def _pack(readiness: dict[str, Any], key: str) -> dict[str, Any]:
    (found,) = [p for p in readiness["packs"] if p["key"] == key]
    out: dict[str, Any] = found
    return out


def _items(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["key"]: i for i in pack["items"]}


def _generate(api: Any, who: Any, **body: Any) -> dict[str, Any]:
    res = api.call(who, "POST", f"{BASE}/inspection-packs", json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _codes(res: Any) -> set[str]:
    return {e["code"] for e in res.json().get("errors", [])}


def _pack_rows(admin: Engine, tenant_id: uuid.UUID) -> int:
    with admin.connect() as c:
        n: int = c.execute(
            text("SELECT count(*) FROM ops.inspection_packs WHERE tenant_id = :t"),
            {"t": tenant_id},
        ).scalar_one()
    return n


# --- authorization (FR-COMP-044) --------------------------------------------------------------


def test_FR_COMP_044_read_and_make_by_role(api: Any, school: Any) -> None:
    for role in S.ROLES:
        who = school.people[role]
        res = api.call(who, "GET", f"{BASE}/inspection-readiness")
        assert res.status_code == (200 if role in S.READERS else 403), (role, res.text)
        res = api.call(who, "GET", f"{BASE}/inspection-packs")
        assert res.status_code == (200 if role in S.READERS else 403), (role, res.text)
        res = api.call(
            who, "POST", f"{BASE}/inspection-packs", json={"pack_key": "fire_inspection"}
        )
        assert res.status_code == (201 if role in S.MANAGERS else 403), (role, res.text)


def test_FR_COMP_042_other_school_pack_is_404(api: Any, school: Any, admin_engine: Engine) -> None:
    other = S.make_school(admin_engine)
    made = _generate(api, other.people["owner"], pack_key="fire_inspection")
    owner = school.people["owner"]
    for suffix in ("", "/index"):
        res = api.call(owner, "GET", f"{BASE}/inspection-packs/{made['id']}{suffix}")
        assert res.status_code == 404, (suffix, res.text)
        random = api.call(owner, "GET", f"{BASE}/inspection-packs/{uuid.uuid4()}{suffix}")
        assert random.status_code == 404
    assert api.call(owner, "GET", f"{BASE}/inspection-packs").json() == []


# --- readiness (FR-COMP-025..FR-COMP-031) -------------------------------------------------------


def test_FR_COMP_026_readiness_states_per_source(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    today = S.today()
    S.certificate(admin_engine, school, kind="fire_noc", valid_until=today - dt.timedelta(days=3))
    S.certificate(admin_engine, school, kind="building_safety", valid_until=None)  # no copy
    doc = S.document(admin_engine, school)
    cid = S.certificate(
        admin_engine, school, kind="state_recognition", valid_until=today + dt.timedelta(days=30)
    )
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE ops.compliance_certificates SET document_id = :d WHERE id = :i"),
            {"d": doc, "i": cid},
        )
    out = _readiness(api, school.people["auditor_readonly"])
    assert out["verified"] is False
    keys = {p["key"] for p in out["packs"]}
    assert "cbse_inspection" not in keys  # AP State Board school
    assert {"deo_inspection", "apsermc_inspection", "fire_inspection"} <= keys
    deo = _items(_pack(out, "deo_inspection"))
    assert deo["fire_noc"]["state"] == "expired"
    assert deo["building_safety"]["state"] == "no_copy"
    assert deo["state_recognition"]["state"] == "expiring"
    assert deo["state_recognition"]["has_copy"] is True
    assert deo["state_recognition"]["certificate_id"] == str(cid)
    assert deo["trust_registration"]["state"] == "missing"
    assert deo["admission_register"]["state"] == "ready"
    assert deo["admission_register"]["included"] is None
    assert deo["privacy_notice"]["state"] == "missing"
    assert deo["committees"]["state"] == "not_tracked"
    assert deo["committees"]["feature"] == "FC-05"
    apsermc = _items(_pack(out, "apsermc_inspection"))
    assert "cbse_affiliation" not in apsermc
    assert apsermc["dpdp_readiness"]["state"] == "incomplete"
    assert apsermc["dpdp_readiness"]["applicable"] >= 1
    counts = _pack(out, "deo_inspection")["counts"]
    assert counts["missing"] >= 2  # expired fire NOC + missing papers
    assert counts["attention"] == 2  # no copy + expiring
    assert counts["not_tracked"] == 2


def test_FR_COMP_025_cbse_school_gets_the_cbse_pack(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    S.set_boards(admin_engine, school.tenant_id, ["CBSE"])
    out = _readiness(api, school.people["principal"])
    cbse = _items(_pack(out, "cbse_inspection"))
    assert cbse["disclosure"]["state"] == "missing"  # nothing published yet
    assert cbse["cbse_affiliation"]["state"] == "missing"
    assert cbse["safety_drills"]["state"] == "not_tracked"
    assert "state_recognition" not in _items(_pack(out, "deo_inspection"))


# --- making a pack (FR-COMP-032..FR-COMP-037) ---------------------------------------------------


def test_FR_COMP_032_generate_freezes_states_and_audits(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    office = school.people["office_admin"]
    made = _generate(
        api,
        office,
        pack_key="deo_inspection",
        visit_on="2026-12-01",
        registers=["admission_register"],
    )
    items = {i["key"]: i for i in made["items"]}
    assert items["admission_register"]["state"] == "ready"
    assert items["admission_register"]["included"] is True
    assert items["tc_register"]["state"] == "left_out"
    assert made["counts"]["left_out"] == 2
    assert made["registers"] == ["admission_register"]
    assert made["academic_year_id"] == str(school.ids["year"])  # the current year
    assert made["academic_year_label"] == "2026-27"
    assert made["verified"] is False
    assert made["generated_by_name"]
    # Frozen: a certificate recorded later does not change the pack.
    S.certificate(admin_engine, school, kind="trust_registration")
    again = api.call(office, "GET", f"{BASE}/inspection-packs/{made['id']}").json()
    assert {i["key"]: i for i in again["items"]}["trust_registration"]["state"] == "missing"
    live = _items(_pack(_readiness(api, office), "deo_inspection"))
    assert live["trust_registration"]["state"] == "no_copy"
    (event,) = W.audit_events(admin_engine, school.tenant_id, packs.ACTION)
    assert event["resource_type"] == "inspection_pack"
    assert str(event["resource_id"]) == made["id"]
    summary = event["summary"]
    assert summary["pack_key"] == "deo_inspection"
    assert summary["registers"] == ["admission_register"]
    assert summary["left_out"] == 2
    listed = api.call(school.people["auditor_readonly"], "GET", f"{BASE}/inspection-packs").json()
    assert [p["id"] for p in listed] == [made["id"]]


def test_FR_COMP_035_audit_failure_rolls_back_the_pack(
    api: Any, school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit_service, "record", boom)
    try:
        res = api.call(
            school.people["owner"],
            "POST",
            f"{BASE}/inspection-packs",
            json={"pack_key": "fire_inspection"},
        )
        assert res.status_code >= 500
    except RuntimeError:
        pass
    assert _pack_rows(admin_engine, school.tenant_id) == 0


def test_FR_COMP_033_errors(api: Any, school: Any, admin_engine: Engine) -> None:
    owner = school.people["owner"]
    res = api.call(owner, "POST", f"{BASE}/inspection-packs", json={"pack_key": "cbse_inspection"})
    assert res.status_code == 422  # not a CBSE school
    assert _codes(res) == {"unknown_pack"}
    res = api.call(
        owner,
        "POST",
        f"{BASE}/inspection-packs",
        json={"pack_key": "fire_inspection", "registers": ["admission_register"]},
    )
    assert res.status_code == 422
    assert _codes(res) == {"unknown_register"}
    other = S.make_school(admin_engine)
    with admin_engine.connect() as c:
        year: uuid.UUID = c.execute(
            text("SELECT id FROM core.academic_years WHERE tenant_id = :t LIMIT 1"),
            {"t": other.tenant_id},
        ).scalar_one()
    res = api.call(
        owner,
        "POST",
        f"{BASE}/inspection-packs",
        json={"pack_key": "fire_inspection", "academic_year_id": str(year)},
    )
    assert res.status_code == 422
    assert _codes(res) == {"academic_year_not_found"}
    res = api.call(owner, "POST", f"{BASE}/inspection-packs", json={"pack_key": "x", "extra": 1})
    assert res.status_code == 422
    assert _pack_rows(admin_engine, school.tenant_id) == 0


def test_FR_COMP_034_registers_need_register_read(
    api: Any, school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every manager holds register.read today; a permission no one holds stands in for a
    # manager without it.
    monkeypatch.setattr(packs, "REGISTER_READ", "register.read.nobody")
    office = school.people["office_admin"]
    res = api.call(
        office,
        "POST",
        f"{BASE}/inspection-packs",
        json={"pack_key": "deo_inspection", "registers": ["tc_register"]},
    )
    assert res.status_code == 403, res.text
    assert _generate(api, office, pack_key="deo_inspection")["counts"]["left_out"] == 3


# --- index (FR-COMP-038..FR-COMP-040) -----------------------------------------------------------


def test_FR_COMP_038_index_prints_with_its_own_csp(api: Any, school: Any) -> None:
    made = _generate(api, school.people["principal"], pack_key="fire_inspection")
    res = api.call(
        school.people["auditor_readonly"], "GET", f"{BASE}/inspection-packs/{made['id']}/index"
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["content-security-policy"] == STYLE_CSP
    assert res.headers["cache-control"] == "no-store"
    body = res.text
    assert "Inspection pack: Fire department inspection" in body
    assert "Synthetic Model School" in body
    assert "Not tracked in SchoolOS" in body
    assert "<script" not in body.lower()
    res = api.call(school.people["teacher"], "GET", f"{BASE}/inspection-packs/{made['id']}/index")
    assert res.status_code == 403
