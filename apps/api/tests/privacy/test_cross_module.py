"""Access-report completeness and erasure across modules on a populated synthetic school
(R3-2F; NFR-PRV-007, FR-DPDP-008, FR-DPDP-009, FR-DPDP-012..014). Synthetic data only.

School A (``world.a``) gets two students with the same kinds of records in several modules
(student details, a guardian, a privacy request, a consent decision, a breach listing them,
behaviour notes and an early-warning flag); school B holds a third student. Every registered
contributor is called for every student: each returns only its own classes, only rows about
the student asked for, and nothing for another school's student.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject
from app.privacy import config as cfg
from app.privacy import service

pytestmark = pytest.mark.db
P = sys.modules["sos_test_privacy_support"]
CP = sys.modules["sos_test_consent_support"]
BASE = P.BASE
BREACHES = "/api/v1/privacy/breaches"


def _populate(world: Any, api: Any, admin: Engine, tag: str) -> dict[str, uuid.UUID]:
    office, principal = world.person("office_admin"), world.person("principal")
    name = f"Synthetica Crossmod {tag}"
    sid = CP.student(world.a, name=name)
    gid = P.guardian(world.a, sid, name=f"Synthetica Crossguard {tag}")
    pid = CP.shared_purpose(api, world.a, admin)
    doc = CP.signed_form(admin, world.a, office)
    assert CP.record(api, office, sid, CP.given(pid, doc)).status_code == 201
    req = P.logged(api, office, P.body("access", student_id=sid))
    note = api.call(
        principal,
        "POST",
        f"/api/v1/students/{sid}/behaviour-notes",
        json={"category": "observation", "text": f"Synthetic note {tag}"},
    )
    assert note.status_code == 201, note.text
    flag = api.call(
        principal, "POST", f"/api/v1/students/{sid}/flags", json={"indicator": "behaviour"}
    )
    assert flag.status_code == 201, flag.text
    breach = CP.create_breach(api, world.person("owner"))
    put = api.call(
        world.person("owner"),
        "PUT",
        f"{BREACHES}/{breach['id']}/affected",
        json={"student_ids": [str(sid)], "guardian_ids": [str(gid)]},
    )
    assert put.status_code == 200, put.text
    return {"student": sid, "guardian": gid, "request": uuid.UUID(req["id"])}


_POPULATED: dict[str, Any] = {}


@pytest.fixture
def populated(world: Any, api: Any, admin_engine: Engine) -> dict[str, Any]:
    """Built once per process (the shared world outlives this module)."""
    if not _POPULATED:
        _POPULATED.update(
            {
                "one": _populate(world, api, admin_engine, "One"),
                "two": _populate(world, api, admin_engine, "Two"),
                "other_school": CP.SW.ensure_students(world)["b_sb"],
            }
        )
    return _POPULATED


def _sections(world: Any, subject: Subject) -> dict[str, list[ReportSection]]:
    out: dict[str, list[ReportSection]] = {}
    with tenant_session(world.a.tenant_id, world.person("office_admin").user_id) as db:
        for contributor in service.contributors():
            out[contributor.module] = contributor.report(db, subject)
    return out


def _flat(sections: list[ReportSection]) -> str:
    return " ".join(" ".join(" ".join(r) for r in s.rows) + (s.note or "") for s in sections)


def test_NFR_PRV_007_every_contributor_answers_for_the_student_only(
    world: Any, populated: dict[str, Any]
) -> None:
    registry = cfg.subjects_registry().contributors
    one = Subject("student", populated["one"]["student"])
    got = _sections(world, one)
    assert set(got) == set(registry), "every registered contributor is called"
    for module, sections in got.items():
        for s in sections:
            assert s.class_key in registry[module].classes, (module, s.class_key)
        if sections:
            assert "student" in registry[module].subjects, module
    # The modules populated above each contribute a section about student One.
    filled = {m for m, s in got.items() if s}
    assert {"students", "privacy", "privacy_consents", "privacy_breach", "insights"} <= filled
    text_one = " ".join(_flat(s) for s in got.values())
    assert "Crossmod One" in text_one
    assert "Crossmod Two" not in text_one
    assert "Crossguard Two" not in text_one


def test_NFR_PRV_007_report_holds_a_block_per_filled_class(
    world: Any, populated: dict[str, Any]
) -> None:
    one = Subject("student", populated["one"]["student"])
    with tenant_session(world.a.tenant_id, world.person("office_admin").user_id) as db:
        blocks = service._report_blocks(db, one)
    labels = {b.label for b in blocks}
    classes = cfg.record_classes().classes
    for key in ("student_identity", "privacy_requests", "consents", "breach_records"):
        assert classes[key].label in labels, key
    assert classes["behaviour_notes"].label in labels or classes["early_warning"].label in labels


def test_NFR_PRV_007_another_schools_student_gets_nothing(
    world: Any, populated: dict[str, Any]
) -> None:
    other = Subject("student", populated["other_school"])
    got = _sections(world, other)
    assert {m: s for m, s in got.items() if s} == {}
    guardian = Subject("guardian", uuid.uuid4())
    assert {m: s for m, s in _sections(world, guardian).items() if s} == {}


def test_NFR_PRV_007_guardian_report_is_their_own(world: Any, populated: dict[str, Any]) -> None:
    g = Subject("guardian", populated["one"]["guardian"])
    got = _sections(world, g)
    registry = cfg.subjects_registry().contributors
    for module, sections in got.items():
        if sections:
            assert "guardian" in registry[module].subjects, module
    flat = " ".join(_flat(s) for s in got.values())
    assert "Crossguard Two" not in flat
    assert got["privacy_breach"], "the breach lists the guardian"


# --- erasure (FR-DPDP-012..014) -----------------------------------------------------------------


def _count(admin: Engine, sql: str, **params: Any) -> int:
    with admin.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


def test_FR_DPDP_013_erasure_removes_exactly_the_erasable_classes(
    world: Any, api: Any, admin_engine: Engine, populated: dict[str, Any]
) -> None:
    sid = populated["one"]["student"]
    other = populated["two"]["student"]
    office, principal = world.person("office_admin"), world.person("principal")
    classes = cfg.record_classes().classes
    registry = cfg.subjects_registry().contributors
    req = P.logged(api, office, P.body("erasure", student_id=sid))
    plan = api.call(office, "GET", f"{BASE}/{req['id']}/erasure-plan")
    assert plan.status_code == 200, plan.text
    rows = {r["class_key"]: r for r in plan.json()["rows"]}
    expected_erasable = {
        k
        for k, c in classes.items()
        if c.erasure == "allowed"
        and (c.owner not in registry or "student" in registry[c.owner].subjects)
    }
    erasable = {k for k, r in rows.items() if r["outcome"] == "erasable"}
    # WhatsApp opt-ins are a guardian's: never offered on a student's plan.
    assert erasable == expected_erasable - {"message_optins"}
    assert "message_optins" not in rows
    for key in erasable:
        assert rows[key]["count"] is not None, f"{key} is offered but no eraser counted it"
    assert rows["behaviour_notes"]["count"] == 1
    assert rows["early_warning"]["count"] >= 1
    for key in ("student_identity", "guardians", "certificates", "attendance_and_marks"):
        assert rows[key]["outcome"] == "refused_official_record", key
    for key in ("financial_records", "audit", "consents", "privacy_requests"):
        assert rows[key]["outcome"] == "refused_legal_obligation", key
    for r in rows.values():
        if r["outcome"] != "erasable":
            assert r["reason"], r["class_key"]

    audit_before = _count(
        admin_engine, "SELECT count(*) FROM audit.events WHERE tenant_id = :t", t=world.a.tenant_id
    )
    res = api.call(
        principal, "POST", f"{BASE}/{req['id']}/erasure", json={"class_keys": sorted(erasable)}
    )
    assert res.status_code == 200, res.text
    counts = {r["class_key"]: r["count"] for r in res.json()["erased"]}
    assert set(counts) == erasable
    for key in erasable:
        assert counts[key] == rows[key]["count"], key
    # Erased: the student's notes and flags; the other student's stay.
    notes = "SELECT count(*) FROM sis.behaviour_notes WHERE student_id = :s"
    flags = "SELECT count(*) FROM sis.insight_flags WHERE student_id = :s"
    assert _count(admin_engine, notes, s=sid) == 0
    assert _count(admin_engine, flags, s=sid) == 0
    assert _count(admin_engine, notes, s=other) == 1
    assert _count(admin_engine, flags, s=other) >= 1
    # Kept: the admission record, the consent register, the privacy requests and the audit log.
    assert _count(admin_engine, "SELECT count(*) FROM sis.students WHERE id = :s", s=sid) == 1
    assert (
        _count(admin_engine, "SELECT count(*) FROM sis.consents WHERE student_id = :s", s=sid) >= 1
    )
    assert (
        _count(
            admin_engine, "SELECT count(*) FROM ops.privacy_requests WHERE student_id = :s", s=sid
        )
        >= 2
    )
    assert (
        _count(
            admin_engine,
            "SELECT count(*) FROM audit.events WHERE tenant_id = :t",
            t=world.a.tenant_id,
        )
        > audit_before
    )
    (event,) = [
        e
        for e in sys.modules["sos_test_api_world"].audit_events(
            admin_engine, world.a.tenant_id, "privacy.erasure.executed"
        )
        if str(e["resource_id"]) == req["id"]
    ]
    assert set(event["summary"]["refused"]) == {
        k for k, r in rows.items() if r["outcome"] != "erasable"
    }
    assert event["summary"]["counts"] == counts
