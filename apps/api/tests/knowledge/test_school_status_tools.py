"""FA-04 / FC-11 Ask tools without a database (D86; docs/06 §7; ADR-0008 as amended by D86;
FR-KB-066..079; invariants 8, 9, 13).

The whitelist and ``tools.yaml`` entries, the date range of ``get_attendance_summary``, the small
section rule, what one certificate and one register line say, the source URIs, and that every
new tool refuses a caller without its permission before reading anything.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from app.academics.service import AttendanceRates, SectionAttendance
from app.knowledge import sources
from app.knowledge.config.tools import WHITELIST, load_tools_config
from app.knowledge.tools.attendance import (
    AttendanceArgs,
    GetAttendanceSummaryTool,
    attendance_range,
)
from app.knowledge.tools.certificates import GetCertificatesTool
from app.knowledge.tools.certificates import describe as describe_certificate
from app.knowledge.tools.compliance import ListComplianceStatusTool
from app.knowledge.tools.compliance import describe as describe_line
from app.knowledge.tools.readiness import GetReadinessSummaryTool

NEW = ("get_readiness_summary", "get_attendance_summary", "get_certificates")
NEW_ALL = (*NEW, "list_compliance_status")
TODAY = dt.date(2026, 11, 12)


def test_D86_the_four_tools_are_whitelisted_described_and_permissioned() -> None:
    tools = load_tools_config().tools
    expected = {
        "get_readiness_summary": "dq.readiness.read",
        "get_attendance_summary": "attendance.read",
        "get_certificates": "certificate.read",
        "list_compliance_status": "compliance.read",
    }
    for name, permission in expected.items():
        assert name in WHITELIST
        assert tools[name].description
        assert tools[name].permission == permission
    assert tools["get_attendance_summary"].small_cell_min == 5
    assert (
        tools["get_attendance_summary"].default_days,
        tools["get_attendance_summary"].max_days,
    ) == (
        7,
        31,
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({}, (dt.date(2026, 11, 6), TODAY, False)),
        ({"to_date": "2026-11-30"}, (dt.date(2026, 11, 6), TODAY, False)),  # never after today
        ({"from_date": "2026-11-10"}, (dt.date(2026, 11, 10), TODAY, False)),
        ({"from_date": "2026-08-01"}, (dt.date(2026, 10, 13), TODAY, True)),  # 31 days kept
        (
            {"from_date": "2026-10-01", "to_date": "2026-10-31"},
            (dt.date(2026, 10, 1), dt.date(2026, 10, 31), False),
        ),
    ],
)
def test_FR_KB_072_attendance_range(args: dict[str, str], expected: tuple[Any, ...]) -> None:
    got = attendance_range(
        AttendanceArgs.model_validate(args), today=TODAY, default_days=7, max_days=31
    )
    assert got == expected


def test_FR_KB_072_an_end_before_the_start_is_refused() -> None:
    with pytest.raises(ValueError, match="before"):
        attendance_range(
            AttendanceArgs(from_date="2026-11-10", to_date="2026-11-01"),
            today=TODAY,
            default_days=7,
            max_days=31,
        )


def _section(label: str, students: int, present: int, absent: int) -> SectionAttendance:
    return SectionAttendance(
        section_id=uuid.uuid4(),
        class_id=uuid.uuid4(),
        label=label,
        students=students,
        days_marked=1,
        present=present,
        late=0,
        absent=absent,
        leave=0,
    )


def test_FR_KB_071_a_small_section_is_hidden_and_left_out_of_the_total() -> None:
    tool = GetAttendanceSummaryTool(load_tools_config().tools["get_attendance_summary"])
    found = AttendanceRates(
        year_label="2026-27",
        start=TODAY,
        end=TODAY,
        sections=(_section("Class IX A", 40, 30, 10), _section("Class IX B", 3, 0, 3)),
    )
    text = tool._text(found, cut=False)
    assert "Class IX A: 75.0% present or late (40 students, 1 days marked)" in text
    assert "Class IX B: not shown (fewer than 5 students)" in text
    assert "Total over the 1 section(s) with a figure: 75.0%." in text
    assert "0.0%" not in text.replace("75.0%", "")


def _cert(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "certificate_type": "transfer",
        "status": "issued",
        "duplicate_no": None,
        "serial": "TC/2026/0012",
        "requested_at": dt.datetime(2026, 6, 10, 4, 0, tzinfo=dt.UTC),
        "issued_at": dt.datetime(2026, 6, 11, 20, 0, tzinfo=dt.UTC),
        "cancelled_at": None,
        "cancel_reason": "Synthetic reason that never reaches Ask",
        "inputs": {"leaving_reason": "parent_transferred"},
    }
    base.update(kw)
    return type("Cert", (), base)()


def test_FR_KB_074_a_certificate_says_type_serial_date_and_status_only() -> None:
    title, text = describe_certificate(_cert())
    assert title == "Certificate · Transfer certificate (TC) · TC/2026/0012"
    assert "Issued on 12/06/2026. Status: issued." in text  # IST date
    assert "reason" not in text.lower()
    assert "parent_transferred" not in text
    _, cancelled = describe_certificate(
        _cert(status="cancelled", cancelled_at=dt.datetime(2026, 7, 1, 6, 0, tzinfo=dt.UTC))
    )
    assert "cancelled on 01/07/2026; it is no longer valid" in cancelled
    _, pending = describe_certificate(_cert(status="pending", serial=None, issued_at=None))
    assert "no serial number yet" in pending
    assert "waiting for approval, not issued yet" in pending
    _, duplicate = describe_certificate(_cert(certificate_type="bonafide", duplicate_no=2))
    assert duplicate.startswith("Bonafide certificate, duplicate copy 2.")


def test_FR_KB_077_a_register_line_says_state_and_dates_only() -> None:
    document = type("Doc", (), {"title": "Synthetic NOC scan"})()
    cert = type(
        "Cert",
        (),
        {
            "valid_until": dt.date(2027, 3, 31),
            "valid_from": dt.date(2022, 4, 1),
            "document": document,
            "notes": "Synthetic note",
            "reference_no": "SYN/123",
        },
    )()
    row = type("Row", (), {"kind_label": "Fire NOC", "state": "valid", "certificate": cert})()
    text = describe_line(row, TODAY)
    assert text.startswith("Fire NOC: valid.")
    assert "Valid until 31/03/2027 (139 days left)." in text
    assert "A copy is in Documents." in text
    assert "Synthetic note" not in text
    assert "SYN/123" not in text
    missing = type(
        "Row", (), {"kind_label": "Building safety", "state": "missing", "certificate": None}
    )()
    assert describe_line(missing, TODAY) == "Building safety: missing (no certificate recorded)."


def test_D86_sources_name_ids_only_and_parse_back() -> None:
    i = uuid.uuid4()
    scope = "a" * 16
    for uri, kind in (
        (sources.readiness(i, scope=scope), "readiness"),
        (sources.attendance(i, scope=scope), "attendance"),
        (sources.compliance(i, scope=scope), "compliance"),
        (sources.certificate(i), "certificate"),
    ):
        ref = sources.parse(uri)
        assert (ref.kind, ref.object_id) == (kind, i)
    assert sources.parse(sources.readiness(i, scope=scope)).scope == scope


class _NoPermission:
    tenant_id = uuid.uuid4()

    def has(self, permission: str) -> bool:
        return False


@pytest.mark.parametrize("name", NEW_ALL)
def test_invariant_9_every_new_tool_refuses_without_its_permission_before_reading(
    name: str,
) -> None:
    config = load_tools_config().tools[name]
    tool: Any = {
        "get_readiness_summary": GetReadinessSummaryTool,
        "get_attendance_summary": GetAttendanceSummaryTool,
        "get_certificates": GetCertificatesTool,
        "list_compliance_status": ListComplianceStatusTool,
    }[name](config)
    session: Any = object()  # any read would fail on this
    out = tool.run(session, _NoPermission(), "call-1", {})
    assert out.is_error
    assert out.blocks == ()
    assert tool.spec.input_schema["additionalProperties"] is False


STAFF_ROLES = frozenset(
    {
        "owner",
        "principal",
        "office_admin",
        "office_staff",
        "accountant",
        "exam_coordinator",
        "class_teacher",
        "teacher",
    }
)


def test_FR_KB_079_the_eval_role_table_is_the_role_matrix() -> None:
    """``sos_evals.status.GRANTS`` (who may use each tool, and how far) equals ``roles.yaml``:
    the tool's permission together with ``kb.ask``, scoped or school-wide."""
    from app.authz.catalog import system_roles
    from sos_evals.status import GRANTS

    tools = load_tools_config().tools
    for tool, grants in GRANTS.items():
        permission = tools[tool].permission
        actual: dict[str, str] = {}
        for role, template in system_roles().items():
            grant, ask = template.grant(permission), template.grant("kb.ask")
            if role in STAFF_ROLES and grant is not None and ask is not None:
                actual[role] = "scoped" if grant.scoped else "school"
        assert actual == dict(grants), tool
