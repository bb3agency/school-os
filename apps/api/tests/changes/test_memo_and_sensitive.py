"""Correction memo (FR-CR-005, US-601 AC4) and C3 identity values (SEC-012) in change requests."""

from __future__ import annotations

import datetime as dt
import html
import re
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.changes import memo as memo_page
from app.changes import service as changes
from app.changes.schemas import ApproveIn
from app.core.db import tenant_session
from app.students import crypto
from app.students import service as students

pytestmark = pytest.mark.db
CR = sys.modules["sos_test_changes_objects"]
BASE = "/api/v1/change-requests"
PLACE_OLD = "Synthetic Guntur Village"
PLACE_NEW = "Synthetic Tenali Town"
XSS_NAME = "<script>alert('x')</script> Synthetica"
XSS_REASON = 'Register says <img src=x onerror="alert(1)"> & birth certificate differs'


def _approve(school: Any, rid: uuid.UUID) -> None:
    principal = school.people["principal"]
    with tenant_session(school.tenant_id, principal.user_id) as s:
        changes.approve(
            s, CR.ctx(school, principal, "principal"), rid, ApproveIn(), expected_version=1
        )


# --- memo ----------------------------------------------------------------------------------------


def test_FR_CR_005_memo_is_a_print_ready_bilingual_page(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    adm_no = f"MEMO/{uuid.uuid4().hex[:5].upper()}"
    sid = CR.student(school, admission_no=adm_no)
    req = CR.submit(
        admin_engine, school, school.people["office_admin"], "office_admin", student_id=sid
    )
    pending = api.call(school.people["office_admin"], "GET", f"{BASE}/{req.id}/memo")
    assert pending.status_code == 200
    assert memo_page.NOT_APPROVED_EN in pending.text
    assert memo_page.INSTRUCTIONS_EN not in pending.text
    _approve(school, req.id)
    res = api.call(school.people["office_admin"], "GET", f"{BASE}/{req.id}/memo")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["content-disposition"].startswith("inline;")
    assert res.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in res.headers["content-security-policy"]
    body = res.text
    for expected in (
        "Synthetic Model School",
        adm_no,
        "IX-A",
        "Date of birth",
        "పుట్టిన తేదీ",
        "Admission register",
        "ప్రవేశ రిజిస్టర్",
        "2012-03-14",
        "2012-03-15",
        html.escape(CR.REASON),
        "Synthetic circular (certificate, ref.",
        "Synthetic Clerk Lakshmi",
        "Synthetic Principal Suresh",
        "Approved",
        "ఆమోదించబడింది",
        memo_page.INSTRUCTIONS_EN,
        memo_page.INSTRUCTIONS_TE,
        f"CR-{str(req.id)[:8].upper()}",
        "@page { size: A4;",
    ):
        assert expected in body, expected
    ist = req.requested_at.astimezone(memo_page.IST).strftime("%d %b %Y, %H:%M IST")
    assert ist in body
    assert "<script" not in body.lower()
    assert not re.search(r"<(a|img|link|iframe|object|form)\b", body)
    actions = CR.audit_rows(admin_engine, school.tenant_id, req.id)
    viewed = [a for a in actions if a["action"] == "change_request.memo_viewed"]
    assert len(viewed) == 2
    assert viewed[-1]["summary"]["sensitive_shown"] is False


def test_FR_CR_005_memo_escapes_every_value(school: Any, api: Any, admin_engine: Engine) -> None:
    sid = CR.SW.create(school, name=XSS_NAME, section_key="section_9a")
    doc = CR.evidence(admin_engine, school, school.people["office_admin"])
    res = api.call(
        school.people["office_admin"],
        "POST",
        BASE,
        json={
            "student_id": str(sid),
            "attribute_key": "full_name",
            "new_value": '"><script>alert(2)</script>',
            "reason": XSS_REASON,
            "evidence_document_id": str(doc),
        },
    )
    assert res.status_code == 201, res.text
    body = api.call(school.people["office_admin"], "GET", f"{BASE}/{res.json()['id']}/memo").text
    assert "<script" not in body.lower()
    assert "<img" not in body.lower()
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; Synthetica" in body
    assert "&quot;&gt;&lt;script&gt;alert(2)&lt;/script&gt;" in body
    assert html.escape(XSS_REASON) in body


def test_FR_CR_005_render_escapes_quotes_and_ampersands() -> None:
    now = dt.datetime(2026, 9, 27, 8, 0, tzinfo=dt.UTC)
    page = memo_page.render(
        memo_page.MemoData(
            school_name='A & B "School"',
            reference="CR-1",
            request_id="r",
            student_name="<b>x</b>",
            admission_no="1",
            class_section="IX-A",
            attribute_en="Name",
            attribute_te="పేరు",
            source="admission_register",
            old_value="'o'",
            new_value="n",
            reason="r",
            evidence="e",
            requested_by="m",
            requested_at=now,
            status="approved",
            decided_by="c",
            decided_at=now,
            decision_note="—",
            printed_at=now,
        )
    )
    assert "A &amp; B &quot;School&quot;" in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page
    assert "&#x27;o&#x27;" in page
    assert "27 Sep 2026, 13:30 IST" in page
    style = page.split("<style>", 1)[1].split("</style>", 1)[0]
    assert style == memo_page.STYLE
    assert "sha256-" in memo_page.STYLE_CSP


# --- C3 identity attributes (SEC-012) -------------------------------------------------------


@pytest.fixture
def c3_request(school: Any, admin_engine: Engine, c3_identity: str) -> tuple[uuid.UUID, Any]:
    sid = CR.student(school)
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as s:
        students.record_value(
            s,
            CR.ctx(school, owner, "office_admin"),
            sid,
            c3_identity,
            "admission_register",
            PLACE_OLD,
        )
    req = CR.submit(
        admin_engine,
        school,
        school.people["office_admin"],
        "office_admin",
        student_id=sid,
        attribute_key=c3_identity,
        new_value=PLACE_NEW,
    )
    return sid, req


def test_SEC_012_c3_values_are_encrypted_and_bound_to_the_row(
    school: Any, admin_engine: Engine, c3_request: tuple[uuid.UUID, Any]
) -> None:
    _, req = c3_request
    assert (req.masked, req.old_value, req.new_value) == (True, "••••", "••••")
    row = CR.row(admin_engine, req.id)
    assert row["new_value_text"] is None
    assert row["old_value_text"] is None
    assert row["new_value_date"] is None
    assert row["key_version"] is not None
    blob = bytes(row["new_value_ciphertext"])
    assert PLACE_NEW.encode() not in blob
    assert PLACE_OLD.encode() not in bytes(row["old_value_ciphertext"])
    with admin_engine.connect() as c:
        dump = c.execute(
            text("SELECT CAST(r AS text) FROM sis.change_requests r WHERE id = :i"), {"i": req.id}
        ).scalar_one()
    assert "Tenali" not in dump
    assert "Guntur" not in dump
    with tenant_session(school.tenant_id) as s:
        assert (
            crypto.decrypt_value(s, blob, table=changes.TABLE, column=changes.NEW_CT, row_id=req.id)
            == PLACE_NEW
        )
        for kwargs in (
            {"column": changes.NEW_CT, "row_id": uuid.uuid4()},
            {"column": changes.OLD_CT, "row_id": req.id},
        ):
            with pytest.raises(crypto.CryptoError):
                crypto.decrypt_value(s, blob, table=changes.TABLE, **kwargs)


def test_SEC_012_c3_values_are_masked_in_the_api_and_the_memo_unless_permitted(
    school: Any, api: Any, admin_engine: Engine, c3_request: tuple[uuid.UUID, Any]
) -> None:
    _, req = c3_request
    for who in ("office_staff", "principal"):
        got = api.call(school.people[who], "GET", f"{BASE}/{req.id}").json()
        assert (got["masked"], got["new_value"], got["old_value"]) == (True, "••••", "••••")
        listed = api.call(school.people[who], "GET", BASE, params={"limit": 200}).json()["data"]
        assert PLACE_NEW not in str(listed)
    staff_memo = api.call(school.people["office_staff"], "GET", f"{BASE}/{req.id}/memo").text
    assert PLACE_NEW not in staff_memo
    assert PLACE_OLD not in staff_memo
    assert "••••" in staff_memo
    principal_memo = api.call(school.people["principal"], "GET", f"{BASE}/{req.id}/memo").text
    assert PLACE_NEW in principal_memo
    assert PLACE_OLD in principal_memo
    viewed = [
        a["summary"]["sensitive_shown"]
        for a in CR.audit_rows(admin_engine, school.tenant_id, req.id)
        if a["action"] == "change_request.memo_viewed"
    ]
    assert viewed == [False, True]


def test_SEC_012_approval_records_an_encrypted_verified_value(
    school: Any, admin_engine: Engine, c3_request: tuple[uuid.UUID, Any], c3_identity: str
) -> None:
    sid, req = c3_request
    _approve(school, req.id)
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT value_text, value_norm, value_ciphertext, verification_status, "
                "change_request_id FROM sis.attribute_values WHERE student_id = :s "
                "AND attribute_key = :k AND superseded_by IS NULL"
            ),
            {"s": sid, "k": c3_identity},
        ).one()
    assert (row.value_text, row.value_norm) == (None, None)
    assert row.verification_status == "verified"
    assert row.change_request_id == req.id
    with tenant_session(school.tenant_id) as s:
        current = students.source_values(s, [sid], [c3_identity], include_sensitive=True)
    assert current[sid][c3_identity]["admission_register"].value == PLACE_NEW
