"""FA-04 / FC-11 Ask tools on the real database (D86; docs/06 §7; ADR-0008 as amended by D86;
FR-KB-066..080; invariants 3, 5, 8, 9, 14).

``get_readiness_summary``, ``get_attendance_summary``, ``get_certificates`` and
``list_compliance_status``: a scripted model calls one tool and cites every passage it gets back.
Each test checks what reached the model (the recorded request bodies): offered by permission
only, counts within the caller's scope, no names, one student's certificates only, the
register's school-level facts only; citations that validate, resolve and are audited with the
query; nothing of a record in the logs. Two fresh synthetic schools (the shared world's counts
stay untouched). Synthetic data only.
"""

from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from app.academics import service as academics
from app.core.db import tenant_session
from app.dq import service as dq
from app.knowledge import composition, sources
from app.knowledge.config.tools import load_tools_config
from app.knowledge.tools.attendance import GetAttendanceSummaryTool
from app.knowledge.tools.due import today_ist

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


TESTS = Path(__file__).resolve().parents[1]
K = _load("sos_test_ask_support", TESTS / "knowledge" / "ask_support.py")
R = _load("sos_test_record_tools_db", TESTS / "knowledge" / "test_record_tools_db.py")
CERT = _load("sos_test_certificates_support", TESTS / "certificates" / "support.py")
COMP = _load("sos_test_compliance_support", TESTS / "compliance" / "support.py")
W = K.W
api = W.api
ask_with = R.ask_with

OTHER_LABEL = "Synthetic other-school licence"
NEW_TOOLS = frozenset(
    {
        "get_readiness_summary",
        "get_attendance_summary",
        "get_certificates",
        "list_compliance_status",
    }
)


@dataclass
class Status:
    s: Any
    """The school under test."""
    o: Any
    """Another school."""
    ids: dict[str, uuid.UUID]

    def who(self, role: str) -> Any:
        return self.s.people[role]


def _mark(admin: Engine, school: Any, student_id: uuid.UUID, *, day: dt.date, status: str) -> None:
    with admin.begin() as c:
        section: uuid.UUID = c.execute(
            text(
                "SELECT section_id FROM sis.enrollments WHERE student_id = :s AND status = 'active'"
            ),
            {"s": student_id},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO sis.attendance_marks (id, tenant_id, student_id, section_id, on_date, "
                "status, recorded_by) VALUES (:i, :t, :s, :sec, :d, :st, :u)"
            ),
            {
                "i": uuid.uuid4(),
                "t": school.tenant_id,
                "s": student_id,
                "sec": section,
                "d": day,
                "st": status,
                "u": school.people["owner"].user_id,
            },
        )


@pytest.fixture(scope="module")
def status(admin_engine: Engine) -> Iterator[Status]:
    K.install_runtime()
    composition.set_runtime(None)
    s, o = COMP.make_school(admin_engine), COMP.make_school(admin_engine)
    for school in (s, o):
        K.enable_ai(admin_engine, school.tenant_id)
    today = today_ist()
    yesterday = today - dt.timedelta(days=1)
    nine_a = [CERT.student(s, "section_9a") for _ in range(5)]
    nine_c = CERT.student(s, "section_9c")
    ten = CERT.student(s, "section_10a")
    for i, sid in enumerate(nine_a):  # 9A: 8 of 10 marks present or late over 2 days
        _mark(admin_engine, s, sid, day=today, status="late" if i == 4 else "present")
        _mark(admin_engine, s, sid, day=yesterday, status="absent" if i < 2 else "present")
    _mark(admin_engine, s, nine_c, day=today, status="absent")  # 9C: one student
    bonafide = CERT.issue(s, nine_a[0], "bonafide")
    cancelled = CERT.cancel(s, CERT.issue(s, nine_a[0], "study"))
    other = CERT.issue(s, nine_c, "bonafide")
    pending = CERT.pending_tc(s, ten)
    o_cert = CERT.issue(o, CERT.student(o, "section_9a"), "bonafide")
    COMP.certificate(admin_engine, s, kind="fire_noc", valid_until=today + dt.timedelta(days=100))
    COMP.certificate(admin_engine, o, kind="other", label=OTHER_LABEL, valid_until=today)
    yield Status(
        s=s,
        o=o,
        ids={
            "nine_a": nine_a[0],
            "nine_c": nine_c,
            "ten": ten,
            "bonafide": bonafide.id,
            "cancelled": cancelled.id,
            "other_cert": other.id,
            "pending": pending.id,
            "o_student": o_cert.student_id,
            "o_cert": o_cert.id,
        },
    )
    composition.set_runtime(None)


def _offered(transport: Any) -> set[str]:
    return {t["name"] for t in transport.sent[0]["tools"]}


def _text(transport: Any) -> str:
    return " ".join(r["content"][0]["text"] for r in transport.results)


def _cited(events: Any) -> list[str]:
    return [d["source"] for e, d in events if e == "citation"]


def _resolve(api: Any, who: Any, source: str) -> Any:
    return api.call(who, "GET", "/api/v1/knowledge/resolve", params={"source": source})


def _names(st: Status, admin: Engine) -> list[str]:
    with admin.connect() as c:
        found: list[str] = list(
            c.execute(
                text(
                    "SELECT v.value_text FROM sis.attribute_values v WHERE v.tenant_id = :t "
                    "AND v.attribute_key = 'full_name' AND v.value_text IS NOT NULL"
                ),
                {"t": st.s.tenant_id},
            ).scalars()
        )
        return ["Synthetica Certificate Student", *found]


# --- offered by permission (FR-KB-066, FR-KB-070, FR-KB-074, FR-KB-077) ------------------------


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("principal", set(NEW_TOOLS)),
        ("owner", {"get_readiness_summary", "get_certificates", "list_compliance_status"}),
        ("office_staff", {"get_readiness_summary", "get_certificates"}),
        ("class_teacher", {"get_readiness_summary", "get_attendance_summary"}),
        ("accountant", set()),
        ("teacher", set()),
    ],
)
def test_FR_KB_066_the_new_tools_are_offered_by_permission_only(
    api: Any, status: Status, role: str, expected: set[str]
) -> None:
    transport, _ = ask_with(api, status.who(role), "count_students", {})
    assert _offered(transport) & NEW_TOOLS == expected, role


def test_FR_KB_066_a_tool_not_held_is_refused_even_if_called(api: Any, status: Status) -> None:
    transport, events = ask_with(api, status.who("accountant"), "list_compliance_status", {})
    assert transport.results == []  # refused: no passage reaches the model
    assert _cited(events) == []


# --- get_readiness_summary (FR-KB-066..069) ---------------------------------------------------


def test_FR_KB_067_readiness_counts_in_the_callers_scope_without_names(
    api: Any, admin_engine: Engine, status: Status
) -> None:
    s = status.s
    transport, events = ask_with(api, status.who("class_teacher"), "get_readiness_summary", {})
    assert not transport.error
    ctx = K.SW.ctx_for(
        s.tenant_id,
        s.people["class_teacher"],
        "class_teacher",
        section_ids=frozenset({s.ids["section_9a"]}),
    )
    with tenant_session(s.tenant_id, ctx.user_id) as db:
        expected = dq.readiness_for_ask(db, ctx, profile_key=None, limit=5)
    assert expected
    body = _text(transport)
    for profile in expected:
        t = profile.totals
        assert t.students <= 5  # 9A only: never the rest of the school
        assert f"All classes: {t.ready} of {t.students} ready" in body
    sent = str(transport.sent)
    for name in _names(status, admin_engine):
        assert name not in sent
    cited = _cited(events)
    assert cited
    assert {sources.parse(c).kind for c in cited} == {"readiness"}
    res = _resolve(api, status.who("class_teacher"), cited[0])
    assert res.status_code == 200, res.text
    assert res.json()["href"] == "/findings/readiness"
    # Computed over 9A: withheld from someone whose reach differs (W3-09).
    assert _resolve(api, status.who("principal"), cited[0]).status_code == 404


def test_FR_KB_068_an_unknown_profile_is_an_error_result(api: Any, status: Status) -> None:
    transport, _ = ask_with(
        api, status.who("principal"), "get_readiness_summary", {"profile_key": "no-such"}
    )
    assert transport.error


# --- get_attendance_summary (FR-KB-070..073) --------------------------------------------------


def test_FR_KB_070_attendance_by_section_counts_only_small_sections_hidden(
    api: Any, admin_engine: Engine, status: Status
) -> None:
    transport, events = ask_with(api, status.who("principal"), "get_attendance_summary", {})
    body = _text(transport)
    assert "80.0% present or late (5 students, 2 days marked)" in body
    assert "not shown (fewer than 5 students)" in body  # 9C: one child
    assert "Class IX C: 0.0%" not in body
    assert "Total over the 1 section(s) with a figure: 80.0%." in body
    sent = str(transport.sent)
    for name in _names(status, admin_engine):
        assert name not in sent
    cited = _cited(events)
    assert [sources.parse(c).kind for c in cited] == ["attendance"]
    res = _resolve(api, status.who("principal"), cited[0])
    assert res.status_code == 200, res.text
    assert res.json()["href"] == "/attendance"


def _attendance(st: Status, role: str, **scopes: Any) -> str:
    s = st.s
    ctx = K.SW.ctx_for(s.tenant_id, s.people[role], role, **scopes)
    config = load_tools_config().tools["get_attendance_summary"]
    tool = GetAttendanceSummaryTool(config.model_copy(update={"small_cell_min": 1}))
    with tenant_session(s.tenant_id, ctx.user_id) as db:
        out = tool.run(db, ctx, "call-1", {})
    assert not out.is_error
    return out.blocks[0].text


def test_FR_KB_071_class_teachers_get_their_own_sections_only(status: Status) -> None:
    s = status.s
    teacher = _attendance(status, "class_teacher", section_ids=frozenset({s.ids["section_9a"]}))
    assert "80.0% present or late" in teacher
    assert "Class IX C" not in teacher  # 9C is outside the teacher's section
    principal = _attendance(status, "principal")
    assert "Class IX C: 0.0% present or late (1 students, 1 days marked)" in principal


def test_FR_KB_072_a_range_is_cut_to_today_and_to_31_days(status: Status) -> None:
    s = status.s
    ctx = K.SW.ctx_for(s.tenant_id, s.people["principal"], "principal")
    tool = GetAttendanceSummaryTool(load_tools_config().tools["get_attendance_summary"])
    today = today_ist()
    with tenant_session(s.tenant_id, ctx.user_id) as db:
        out = tool.run(
            db,
            ctx,
            "call-1",
            {
                "from_date": (today - dt.timedelta(days=90)).isoformat(),
                "to_date": (today + dt.timedelta(days=5)).isoformat(),
            },
        )
        bad = tool.run(db, ctx, "call-2", {"from_date": "2026-12-10", "to_date": "2026-12-01"})
    assert f"to {today.strftime('%d/%m/%Y')}" in out.blocks[0].title
    assert "Only 31 days at a time are read." in out.blocks[0].text
    assert bad.is_error


def test_FR_KB_073_attendance_is_never_read_without_the_permission(status: Status) -> None:
    s = status.s
    ctx = K.SW.ctx_for(s.tenant_id, s.people["owner"], "owner")  # no attendance.read
    with tenant_session(s.tenant_id, ctx.user_id) as db:
        found = academics.attendance_rates_for_ask(db, ctx, start=today_ist(), end=today_ist())
    assert found.sections == ()


# --- get_certificates (FR-KB-074..076) --------------------------------------------------------


def test_FR_KB_074_one_students_certificates_type_serial_date_status_only(
    api: Any, status: Status
) -> None:
    ids = status.ids
    transport, events = ask_with(
        api, status.who("office_staff"), "get_certificates", {"student_id": str(ids["nine_a"])}
    )
    body = _text(transport)
    today = CERT.today().strftime("%d/%m/%Y")
    assert "Bonafide certificate" in body
    assert f"Issued on {today}. Status: issued." in body
    assert "Study certificate" in body
    assert f"cancelled on {today}" in body
    assert set(_cited(events)) == {
        f"sos://certificate/{ids['bonafide']}",
        f"sos://certificate/{ids['cancelled']}",
    }
    # Another student's certificate never reaches the model; nor do inputs or reasons.
    sent = str(transport.sent)
    assert str(ids["other_cert"]) not in sent
    assert "bus_pass" not in sent
    assert "wrong purpose" not in sent
    res = _resolve(api, status.who("office_staff"), f"sos://certificate/{ids['bonafide']}")
    assert res.status_code == 200, res.text
    assert res.json()["href"] == f"/certificates/{ids['bonafide']}"


def test_FR_KB_074_a_pending_tc_says_it_is_not_issued_yet(api: Any, status: Status) -> None:
    transport, events = ask_with(
        api, status.who("principal"), "get_certificates", {"student_id": str(status.ids["ten"])}
    )
    body = _text(transport)
    assert "Transfer certificate (TC)" in body
    assert "waiting for approval, not issued yet" in body
    assert _cited(events) == [f"sos://certificate/{status.ids['pending']}"]


def test_FR_KB_075_another_schools_student_is_an_error_and_never_resolves(
    api: Any, status: Status
) -> None:
    transport, events = ask_with(
        api,
        status.who("principal"),
        "get_certificates",
        {"student_id": str(status.ids["o_student"])},
    )
    assert transport.error
    assert _cited(events) == []
    source = f"sos://certificate/{status.ids['o_cert']}"
    assert _resolve(api, status.who("principal"), source).status_code == 404


def test_FR_KB_076_a_caller_without_certificate_read_cannot_resolve_a_certificate(
    api: Any, status: Status
) -> None:
    source = f"sos://certificate/{status.ids['bonafide']}"
    assert _resolve(api, status.who("class_teacher"), source).status_code == 404


# --- list_compliance_status (FR-KB-077..079) --------------------------------------------------


def test_FR_KB_077_the_compliance_register_school_level_facts_only(
    api: Any, status: Status
) -> None:
    transport, events = ask_with(api, status.who("principal"), "list_compliance_status", {})
    body = _text(transport)
    until = (today_ist() + dt.timedelta(days=100)).strftime("%d/%m/%Y")
    assert f"Valid until {until} (100 days left)" in body
    assert "missing (no certificate recorded)" in body
    sent = str(transport.sent)
    assert OTHER_LABEL not in sent  # another school's register
    assert status.who("owner").display_name not in sent  # who recorded it: never
    cited = _cited(events)
    assert cited
    assert {sources.parse(c).kind for c in cited} == {"compliance"}
    res = _resolve(api, status.who("principal"), cited[0])
    assert res.status_code == 200, res.text
    assert res.json()["href"] == "/compliance"
    assert _resolve(api, status.who("office_staff"), cited[0]).status_code == 404


# --- audit and logs (FR-KB-080; invariants 5, 7) -----------------------------------------------


def test_FR_KB_080_every_call_is_recorded_with_the_query_and_audited(
    api: Any, admin_engine: Engine, status: Status
) -> None:
    _, events = ask_with(api, status.who("principal"), "list_compliance_status", {})
    query_id = events[0][1]["query_id"]
    with admin_engine.connect() as c:
        tools: list[dict[str, Any]] = c.execute(
            text("SELECT tools FROM kb.queries WHERE id = :i"), {"i": query_id}
        ).scalar_one()
    assert [t["tool"] for t in tools] == ["list_compliance_status"]
    completed = [
        e
        for e in W.audit_events(admin_engine, status.s.tenant_id, "kb.query.completed")
        if str(e["resource_id"]) == str(query_id)
    ]
    assert len(completed) == 1
    assert completed[0]["summary"]["citations"] >= 1


def test_FR_KB_080_invariant_5_no_record_text_in_logs(
    api: Any, admin_engine: Engine, status: Status
) -> None:
    principal = status.who("principal")
    with capture_logs() as logs:
        ask_with(api, principal, "get_certificates", {"student_id": str(status.ids["nine_a"])})
        ask_with(api, principal, "get_attendance_summary", {})
        ask_with(api, principal, "list_compliance_status", {})
        ask_with(api, principal, "get_readiness_summary", {})
    flat = repr(logs)
    secrets = ("Bonafide certificate", "present or late", "Valid until", "waiting for the parent")
    for secret in (*_names(status, admin_engine), *secrets):
        assert secret not in flat


def test_invariant_14_the_tools_never_read_early_warning_data() -> None:
    root = Path(__file__).resolve().parents[2] / "app" / "knowledge" / "tools"
    for name in ("readiness.py", "attendance.py", "certificates.py", "compliance.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any(m.startswith("app.insights") for m in imported), name
