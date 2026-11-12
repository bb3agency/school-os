"""Fix-owner letters from readiness differences (FA-08; FR-DQ-049..FR-DQ-060; invariants 3-7).
Synthetic data only.

The authz matrix (tests/security/test_authz_matrix.py, tests/dq/fix_letter_security_support.py)
covers every role; these tests cover the behaviour, an allowed and a disallowed role, another
school's student, the audit trail, the follow-up step and the logs.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from app.dq import fix_letter_page as page
from app.dq.fix_letters import load_fix_letter_config
from app.dq.followup_config import load_followup_config
from app.students import service as students
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
SSC = "bseap-ssc-2027"
BASE = f"/api/v1/dq/readiness/{SSC}"
# A 12-digit number that passes the Verhoeff check (synthetic, not a real Aadhaar number).
VERHOEFF_12 = "234123412346"


# --- config and page (no database) --------------------------------------------------------------


def test_FR_DQ_049_config_is_versioned_unverified_and_follow_ups_exist() -> None:
    cfg = load_fix_letter_config()
    assert cfg.version >= 1
    assert cfg.verified is False
    assert cfg.source
    parent, board = cfg.letters.parent_aadhaar, cfg.letters.board_registration
    assert (parent.owner, parent.source, parent.audience) == (
        "parent_aadhaar",
        "aadhaar_as_printed",
        "parent",
    )
    assert (board.owner, board.source, board.audience) == (
        "school_udise",
        "board_registration",
        "office",
    )
    spec = load_followup_config().followups
    assert parent.followup.event in spec.owners["parent_aadhaar"].events
    assert (board.followup.event, board.followup.channel) == ("request_sent", "letter")
    # Recipients are roles, never people.
    assert all("Mr" not in v and "Mrs" not in v for v in board.recipients.values())


def _parent_data(value: str) -> page.ParentData:
    words = load_fix_letter_config().letters.parent_aadhaar
    return page.ParentData(
        head=page.Letterhead("Synthetic School", "", "", ""),
        words=page.ParentWords(
            title=words.title,
            salutation=words.salutation,
            intro=words.intro,
            steps=words.steps,
            reply_by=words.reply_by,
            privacy_note=words.privacy_note,
            wrong_record_note=words.wrong_record_note,
            signature=words.signature,
            acknowledgement=words.acknowledgement,
        ),
        profile="AP SSC",
        verified=False,
        reply_on=dt.date(2026, 11, 26),
        letters=[
            page.ParentLetter(
                student="<b>Synthetic</b>",
                admission_no="A-1",
                class_section="IX A",
                reference="REF",
                lines=[page.ParentLine("Full name", value, "Admission register", "Letters differ")],
            )
        ],
        printed_at=dt.datetime(2026, 11, 12, 5, 0, tzinfo=dt.UTC),
    )


def test_FR_DQ_058_pages_escape_mask_and_allow_only_the_hashed_style() -> None:
    html = page.render_parent(_parent_data(f"Name {VERHOEFF_12}"))
    assert VERHOEFF_12 not in html
    assert "<b>Synthetic</b>" not in html
    assert "&lt;b&gt;" in html
    assert "<script" not in html
    assert "26 Nov 2026" in html
    assert "not yet confirmed" in html
    assert "style-src 'sha256-" in page.STYLE_CSP
    assert "default-src 'none'" in page.STYLE_CSP


# --- API ----------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _keys(keyring: None) -> None:
    return None


def _student(
    school: Any,
    *,
    aadhaar_name: str | None = None,
    board_name: str | None = None,
) -> tuple[uuid.UUID, str]:
    """A Class IX student of 9A: register and UDISE+ agree; Aadhaar or the board may not."""
    name = str(DS.unique_name("Kommineni Venkata")) + " Sai Kumar"
    extra = [
        *DS.aadhaar(name=aadhaar_name or name, dob="2012-03-14", gender="male", last4="4821"),
        ValueIn(attribute_key="full_name", source="udise_plus", value=name),
        ValueIn(attribute_key="dob", source="udise_plus", value="2012-03-14"),
        ValueIn(attribute_key="gender", source="udise_plus", value="male"),
    ]
    if board_name is not None:
        extra.append(
            ValueIn(attribute_key="full_name", source="board_registration", value=board_name)
        )
    sid = DS.student(school, name=name, section_key="section_9a", extra=extra)
    return sid, name


def _audits(admin: Engine, action: str, since: dt.datetime) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows: Any = c.execute(
            text(
                "SELECT resource_type, resource_id, summary FROM audit.events "
                "WHERE action = :a AND occurred_at >= :s ORDER BY occurred_at"
            ),
            {"a": action, "s": since},
        ).mappings()
        return [dict(r) for r in rows]


def _now(admin: Engine) -> dt.datetime:
    with admin.connect() as c:
        value: dt.datetime = c.execute(text("SELECT now()")).scalar_one()
        return value


def _letter(body: dict[str, Any], key: str) -> dict[str, Any]:
    return next(x for x in body["letters"] if x["key"] == key)


def test_FR_DQ_050_to_053_parent_letters_print_audit_and_scope(  # noqa: PLR0915 - one story
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid, name = _student(world.a, aadhaar_name="")
    aadhaar_name = name.replace(" Sai Kumar", " Saikumar")
    DS.call(
        world.a,
        students.record_value,
        sid,
        "aadhaar_name_as_printed",
        "aadhaar_as_printed",
        aadhaar_name,
    )
    office = world.person("office_admin")

    # The overview names the student and the field, never a value.
    listed = api.call(office, "GET", f"{BASE}/fix-letters")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["verified"] is False
    parent = _letter(body, "parent_aadhaar")
    assert (parent["fix_owner"], parent["followup_event"], parent["followup_channel"]) == (
        "parent_aadhaar",
        "parent_contacted",
        "letter",
    )
    row = next(s for s in parent["students"] if s["student"]["id"] == str(sid))
    assert len(row["fields"]) == 1
    assert row["last_sent_on"] is None
    assert aadhaar_name not in listed.text
    assert _letter(body, "board_registration")["step_up"] is True

    # One student's letter: the register value, what differs; never the Aadhaar value.
    since = _now(admin_engine)
    with capture_logs() as logs:
        printed = api.call(office, "GET", f"{BASE}/parent-letters", params={"student_id": str(sid)})
    assert printed.status_code == 200, printed.text
    html = printed.text
    assert "Please update your child&#x27;s Aadhaar details" in html
    assert name in html
    assert aadhaar_name not in html
    assert "4821" not in html
    assert not re.search(r"\d{4}\s?\d{4}\s?\d{4}", html)
    assert "default-src 'none'" in printed.headers["Content-Security-Policy"]
    assert printed.headers["Cache-Control"] == "no-store"
    assert name not in str(logs)
    (event,) = _audits(admin_engine, "dq.fix_letter.printed", since)
    assert (event["resource_type"], event["resource_id"]) == ("student", sid)
    summary = event["summary"]
    assert summary["letter"] == "parent_aadhaar"
    assert summary["template_version"] == load_fix_letter_config().version
    assert (summary["students"], summary["lines"], summary["fields"]) == (1, 1, ["full_name"])
    assert summary["student_ids"] == [str(sid)]
    assert name not in str(summary)

    # check=true: every check, no page, no audit.
    check = api.call(
        office, "GET", f"{BASE}/parent-letters", params={"student_id": str(sid), "check": "true"}
    )
    assert check.status_code == 204
    assert len(_audits(admin_engine, "dq.fix_letter.printed", since)) == 1

    # A whole section in bulk.
    section = world.a.ids["section_9a"]
    bulk = api.call(office, "GET", f"{BASE}/parent-letters", params={"section_id": str(section)})
    assert bulk.status_code == 200, bulk.text
    assert name in bulk.text
    events = _audits(admin_engine, "dq.fix_letter.printed", since)
    assert events[-1]["resource_type"] == "section"
    assert str(sid) in events[-1]["summary"]["student_ids"]

    # One stored finding (after a readiness run).
    DS.run(world.a, sid, profile_key=SSC)
    with admin_engine.connect() as c:
        finding_id: uuid.UUID = c.execute(
            text(
                "SELECT id FROM sis.dq_findings WHERE student_id = :s AND profile_key = :p "
                "AND rule_id = 'DQ-031' AND attribute_key = 'full_name'"
            ),
            {"s": sid, "p": SSC},
        ).scalar_one()
    one = api.call(office, "GET", f"{BASE}/parent-letters", params={"finding_id": str(finding_id)})
    assert one.status_code == 200, one.text
    assert _audits(admin_engine, "dq.fix_letter.printed", since)[-1]["resource_type"] == (
        "dq_finding"
    )

    # Exactly one target.
    none = api.call(office, "GET", f"{BASE}/parent-letters")
    assert none.status_code == 422
    # A student whose records agree: nothing to print.
    fine, _ = _student(world.a)
    nothing = api.call(office, "GET", f"{BASE}/parent-letters", params={"student_id": str(fine)})
    assert nothing.status_code == 409
    assert nothing.json()["code"] == "nothing_to_print"
    # A role without dq.readiness.manage: 403.
    teacher = api.call(
        world.person("teacher"), "GET", f"{BASE}/parent-letters", params={"student_id": str(sid)}
    )
    assert teacher.status_code == 403
    # Another school's student, and a random one: 404.
    other, _ = _student(world.b, aadhaar_name="Other Synthetic Name")
    for target in (other, uuid.uuid4()):
        res = api.call(office, "GET", f"{BASE}/parent-letters", params={"student_id": str(target)})
        assert res.status_code == 404
    # A profile the letter is not offered for: 404.
    assert (
        api.call(
            office,
            "GET",
            "/api/v1/dq/readiness/udise-plus/parent-letters",
            params={"student_id": str(sid)},
        ).status_code
        == 404
    )


def test_FR_DQ_057_letters_given_record_the_follow_up_step(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid, name = _student(world.a, aadhaar_name="")
    DS.call(
        world.a,
        students.record_value,
        sid,
        "aadhaar_name_as_printed",
        "aadhaar_as_printed",
        name.replace(" Sai Kumar", " Saikumar"),
    )
    fine, _ = _student(world.a)
    office = world.person("office_admin")
    url = f"{BASE}/fix-letters/parent_aadhaar/sent"
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    made = api.call(office, "POST", url, json={"student_ids": [str(sid)]}, headers=headers)
    assert made.status_code == 201, made.text
    assert made.json() == {
        "recorded": 1,
        "fix_owner": "parent_aadhaar",
        "event": "parent_contacted",
        "channel": "letter",
    }
    with admin_engine.connect() as c:
        steps = c.execute(
            text(
                "SELECT fix_owner, event, channel FROM sis.dq_followups "
                "WHERE student_id = :s AND profile_key = :p"
            ),
            {"s": sid, "p": SSC},
        ).all()
    assert [tuple(r) for r in steps] == [("parent_aadhaar", "parent_contacted", "letter")]
    listed = api.call(office, "GET", f"{BASE}/fix-letters")
    row = next(
        s
        for s in _letter(listed.json(), "parent_aadhaar")["students"]
        if s["student"]["id"] == str(sid)
    )
    assert row["last_sent_on"] is not None

    # A student without an open Aadhaar difference: 422; outside the reach: 404.
    no_fix = api.call(
        office,
        "POST",
        url,
        json={"student_ids": [str(fine)]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert no_fix.status_code == 422
    assert no_fix.json()["errors"][0]["code"] == "no_open_fix"
    missing = api.call(
        office,
        "POST",
        url,
        json={"student_ids": [str(uuid.uuid4())]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert missing.status_code == 404
    teacher = api.call(
        world.person("teacher"),
        "POST",
        url,
        json={"student_ids": [str(sid)]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert teacher.status_code == 403


def test_FR_DQ_054_055_board_letter_needs_export_portal_and_records_request_sent(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid, name = _student(world.a)
    board_name = name.replace(" Sai Kumar", " Saikumar")
    DS.call(world.a, students.record_value, sid, "full_name", "board_registration", board_name)
    principal = world.person("principal")
    since = _now(admin_engine)
    printed = api.call(principal, "GET", f"{BASE}/board-letter", params={"recipient": "deo"})
    assert printed.status_code == 200, printed.text
    html = printed.text
    assert "Request to correct student details in the board registration" in html
    assert "District Educational Officer (DEO)" in html
    assert name in html
    assert board_name in html
    events = _audits(admin_engine, "dq.fix_letter.printed", since)
    assert events[-1]["summary"]["letter"] == "board_registration"
    assert events[-1]["summary"]["recipient"] == "deo"
    assert str(sid) in events[-1]["summary"]["student_ids"]
    assert board_name not in str(events[-1]["summary"])
    bad = api.call(principal, "GET", f"{BASE}/board-letter", params={"recipient": "someone"})
    assert bad.status_code == 422
    # office_staff holds dq.readiness.manage but not export.portal: no board letter.
    staff = api.call(world.person("office_staff"), "GET", f"{BASE}/board-letter")
    assert staff.status_code == 403
    # The board letter is offered only where the profile compares the board registration.
    apaar = api.call(principal, "GET", "/api/v1/dq/readiness/apaar/board-letter")
    assert apaar.status_code == 404

    sent = api.call(
        world.person("office_admin"),
        "POST",
        f"{BASE}/fix-letters/board_registration/sent",
        json={"student_ids": [str(sid)]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert sent.status_code == 201, sent.text
    with admin_engine.connect() as c:
        steps = c.execute(
            text(
                "SELECT fix_owner, event, channel FROM sis.dq_followups "
                "WHERE student_id = :s AND profile_key = :p"
            ),
            {"s": sid, "p": SSC},
        ).all()
    assert [tuple(r) for r in steps] == [("school_udise", "request_sent", "letter")]
