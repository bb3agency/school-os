"""Change-request service (US-601; FR-CR-001..004; SEC-014; BR-01, BR-04; ADR-0010)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import sys
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.changes import service as changes
from app.changes.schemas import ApproveIn, ChangeRequestCreate, RejectIn
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    NotFound,
    PreconditionFailed,
    StepUpRequired,
    ValidationFailed,
)
from app.core.redaction import verhoeff_check_digit
from app.students import service as students

pytestmark = pytest.mark.db
CR = sys.modules["sos_test_changes_objects"]
REASON = CR.REASON


def _create(sid: uuid.UUID, doc: uuid.UUID, **kw: Any) -> ChangeRequestCreate:
    body: dict[str, Any] = {
        "student_id": sid,
        "attribute_key": "dob",
        "new_value": "2012-03-15",
        "reason": REASON,
        "evidence_document_id": doc,
    }
    body.update(kw)
    return ChangeRequestCreate(**body)


def _submit_as(school: Any, who: str, role: str, data: ChangeRequestCreate) -> Any:
    person = school.people[who]
    with tenant_session(school.tenant_id, person.user_id) as s:
        return changes.submit(s, CR.ctx(school, person, role), data)


def _approve(school: Any, who: str, request_id: uuid.UUID, version: int = 1, **kw: Any) -> Any:
    person = school.people[who]
    ctx = kw.pop("ctx", None) or CR.ctx(school, person, "principal")
    with tenant_session(school.tenant_id, person.user_id) as s:
        return changes.approve(s, ctx, request_id, ApproveIn(**kw), expected_version=version)


def _history(school: Any, sid: uuid.UUID, key: str = "dob") -> list[Any]:
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as s:
        return students.value_history(s, CR.ctx(school, owner, "principal"), sid, key)


def _aadhaar() -> str:
    body = "56789012345"
    return body + verhoeff_check_digit(body)


# --- submit (FR-CR-001) --------------------------------------------------------------------------


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_CR_001_submit_snapshots_old_value_and_announces_the_request(
    school: Any, admin_engine: Engine
) -> None:
    sid = CR.student(school)
    old_id = CR.SW.current_value_id(admin_engine, sid, "dob", "admission_register")
    out = CR.submit(
        admin_engine, school, school.people["office_admin"], "office_admin", student_id=sid
    )
    assert out.status == "pending"
    assert (out.old_value_id, out.old_value, out.new_value) == (old_id, "2012-03-14", "2012-03-15")
    assert out.masked is False
    assert (out.attribute_label_en, out.attribute_label_te) == ("Date of birth", "పుట్టిన తేదీ")
    assert out.requested_by == school.people["office_admin"].membership_id
    assert out.expires_at - out.requested_at == dt.timedelta(days=30)
    assert out.can_cancel
    assert not out.can_decide
    events = CR.audit_rows(admin_engine, school.tenant_id, out.id)
    assert [e["action"] for e in events] == ["change_request.submitted"]
    summary = events[0]["summary"]
    assert summary["attribute_key"] == "dob"
    assert "2012" not in str(summary), "no values in audit summaries"
    assert CR.outbox(admin_engine, school.tenant_id, out.id) == [
        (
            "change_request.submitted",
            {"change_request_id": str(out.id), "student_id": str(sid), "attribute_key": "dob"},
        )
    ]
    recipients = {m for k, m in CR.notifications(admin_engine, school.tenant_id, out.id)}
    approvers = {school.people[p].membership_id for p in ("owner", "principal", "dual")}
    assert recipients == approvers


def test_FR_CR_001_date_can_be_sent_as_new_value_date(school: Any, admin_engine: Engine) -> None:
    sid = CR.student(school)
    doc = CR.evidence(admin_engine, school, school.people["office_admin"])
    body = _create(sid, doc, new_value=None, new_value_date=dt.date(2012, 4, 1))
    out = _submit_as(school, "office_admin", "office_admin", body)
    assert out.new_value == "2012-04-01"


def test_FR_CR_001_non_identity_attribute_is_refused(school: Any, admin_engine: Engine) -> None:
    sid = CR.student(school)
    doc = CR.evidence(admin_engine, school, school.people["office_admin"])
    body = _create(sid, doc, attribute_key="mother_tongue", new_value="Telugu")
    with pytest.raises(changes.NotIdentityAttribute) as exc:
        _submit_as(school, "office_admin", "office_admin", body)
    assert exc.value.code == "not_identity_attribute"
    assert exc.value.status == 422


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("missing", "evidence_not_found"),
        ("circular", "evidence_wrong_purpose"),
        ("quarantined", "evidence_not_usable"),
        ("invisible", "evidence_not_found"),
        ("other_school", "evidence_not_found"),
    ],
)
def test_FR_CR_001_evidence_must_exist_be_visible_and_be_evidence(
    school: Any, world: Any, admin_engine: Engine, case: str, code: str
) -> None:
    staff = school.people["office_staff"]
    makers: dict[str, Callable[[], uuid.UUID]] = {
        "missing": uuid.uuid4,
        "circular": lambda: CR.evidence(admin_engine, school, staff, purpose="circular"),
        "quarantined": lambda: CR.evidence(admin_engine, school, staff, status="quarantined"),
        # ACL names only another member: office staff (document.read, no manage) cannot see it.
        "invisible": lambda: CR.evidence(
            admin_engine,
            school,
            school.people["owner"],
            acl=[("membership", str(school.people["principal"].membership_id))],
        ),
        "other_school": lambda: CR.evidence(admin_engine, world.b, world.b.people["owner"]),
    }
    doc = makers[case]()
    sid = CR.student(school)
    with pytest.raises(changes.EvidenceRequired) as exc:
        _submit_as(school, "office_staff", "office_staff", _create(sid, doc))
    assert exc.value.code == "evidence_required"
    assert exc.value.errors[0]["code"] == code


@pytest.mark.parametrize(
    ("changes_", "field", "code"),
    [
        ({"new_value": "2999-01-01"}, "new_value", "date_in_future"),
        ({"new_value": "15/03/2012"}, "new_value", "invalid_date"),
        ({"attribute_key": "gender", "new_value": "unknown"}, "new_value", "not_allowed"),
        (
            {"attribute_key": "full_name", "new_value": "AADHAAR"},
            "new_value",
            "aadhaar_full_number_rejected",
        ),
        ({"reason": "too short"}, "reason", "too_short"),
        (
            {"reason": "Aadhaar AADHAAR printed on the card"},
            "reason",
            "aadhaar_full_number_rejected",
        ),
        ({"target_source": "aadhaar_as_printed"}, "source", "source_not_allowed"),
        ({"attribute_key": "shoe_size", "new_value": "7"}, "attribute_key", "unknown_attribute"),
    ],
)
def test_FR_CR_001_new_value_and_reason_follow_the_rules(
    school: Any, admin_engine: Engine, changes_: dict[str, Any], field: str, code: str
) -> None:
    sid = CR.student(school)
    doc = CR.evidence(admin_engine, school, school.people["office_admin"])
    values = {
        k: (v.replace("AADHAAR", _aadhaar()) if isinstance(v, str) else v)
        for k, v in changes_.items()
    }
    with pytest.raises(ValidationFailed) as exc:
        _submit_as(school, "office_admin", "office_admin", _create(sid, doc, **values))
    assert (exc.value.errors[0]["field"], exc.value.errors[0]["code"]) == (field, code)
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM sis.change_requests WHERE student_id = :s"), {"s": sid}
            ).scalar_one()
            == 0
        )


def test_FR_CR_001_one_pending_request_per_student_attribute_and_source(
    school: Any, admin_engine: Engine
) -> None:
    sid = CR.student(school)
    admin = school.people["office_admin"]
    first = CR.submit(admin_engine, school, admin, "office_admin", student_id=sid)
    with pytest.raises(Conflict) as exc:
        CR.submit(
            admin_engine, school, admin, "office_admin", student_id=sid, new_value="2012-05-05"
        )
    assert exc.value.code == "duplicate_pending_request"
    # Another attribute of the same student is fine; after cancelling, a new request is too.
    CR.submit(
        admin_engine,
        school,
        admin,
        "office_admin",
        student_id=sid,
        attribute_key="gender",
        new_value="female",
    )
    with tenant_session(school.tenant_id, admin.user_id) as s:
        changes.cancel(s, CR.ctx(school, admin, "office_admin"), first.id, expected_version=1)
    again = CR.submit(
        admin_engine, school, admin, "office_admin", student_id=sid, new_value="2012-05-05"
    )
    assert again.status == "pending"


def test_SEC_015_scoped_requester_reaches_only_students_in_scope(
    school: Any, admin_engine: Engine
) -> None:
    clerk = school.people["scoped_clerk"]
    inside, outside = CR.student(school, "section_9a"), CR.student(school, "section_9c")
    section_9a = school.ids["section_9a"]
    base = CR.SW.ctx_for(
        school.tenant_id, clerk, "office_staff", section_ids=frozenset({section_9a})
    )
    # Every grant limited to section 9A (like the custom role in conftest.py).
    scoped = dataclasses.replace(
        base, scoped_permissions=base.permissions, auth_time=dt.datetime.now(dt.UTC)
    )
    doc = CR.evidence(
        admin_engine, school, school.people["owner"], acl=[("section", str(section_9a))]
    )
    with tenant_session(school.tenant_id, clerk.user_id) as s:
        ok = changes.submit(s, scoped, _create(inside, doc))
    assert ok.status == "pending"
    with pytest.raises(NotFound), tenant_session(school.tenant_id, clerk.user_id) as s:
        changes.submit(s, scoped, _create(outside, doc))
    with tenant_session(school.tenant_id, clerk.user_id) as s:
        listed = changes.list_requests(s, scoped, limit=200)
    listed_students = {r.student_id for r in listed.data}
    assert inside in listed_students
    assert {CR.SW.active_section(admin_engine, sid) for sid in listed_students} == {section_9a}


# --- approve (FR-CR-002, FR-CR-003) --------------------------------------------------------------


def test_FR_CR_003_approval_records_a_verified_value_and_keeps_history(
    school: Any, admin_engine: Engine
) -> None:
    sid = CR.student(school)
    old_id = CR.SW.current_value_id(admin_engine, sid, "dob", "admission_register")
    req = CR.submit(
        admin_engine, school, school.people["office_admin"], "office_admin", student_id=sid
    )
    out = _approve(school, "principal", req.id, note="Checked the birth certificate original")
    assert out.status == "approved"
    assert out.decided_by == school.people["principal"].membership_id
    assert out.decision_note == "Checked the birth certificate original"
    new_id = CR.SW.current_value_id(admin_engine, sid, "dob", "admission_register")
    assert out.applied_value_id == new_id != old_id
    history = _history(school, sid)
    by_id = {v.id: v for v in history}
    assert by_id[new_id].value == "2012-03-15"
    assert by_id[new_id].verification_status == "verified"
    assert by_id[new_id].change_request_id == req.id
    assert by_id[new_id].evidence_document_id == req.evidence_document_id
    assert by_id[new_id].verified_by == school.people["principal"].user_id
    assert by_id[old_id].value == "2012-03-14", "old value kept in history"
    assert by_id[old_id].superseded_by == new_id
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as s:
        profile = students.get_profile(s, CR.ctx(school, owner, "principal"), sid)
    assert profile.canonical["dob"].value == "2012-03-15"
    assert profile.canonical["dob"].verified is True
    assert profile.canonical["dob"].provisional is False
    # Audit: exactly one of each, the value event naming the request (FR-CR-003).
    acts = CR.actions(admin_engine, school.tenant_id, req.id)
    assert acts == ["change_request.submitted", "student.value.recorded", "change_request.approved"]
    assert CR.outbox(admin_engine, school.tenant_id, req.id)[-1] == (
        "change_request.approved",
        {"change_request_id": str(req.id), "student_id": str(sid), "attribute_key": "dob"},
    )
    assert ("change_request.approved", school.people["office_admin"].membership_id) in (
        CR.notifications(admin_engine, school.tenant_id, req.id)
    )


def test_SEC_014_requester_cannot_approve_or_reject_own_request(
    school: Any, admin_engine: Engine
) -> None:
    dual = school.people["dual"]
    req = CR.submit(admin_engine, school, dual, "office_admin")
    both = CR.ctx_roles(school, dual, "office_admin", "principal")
    assert both.has(changes.APPROVE)
    with pytest.raises(changes.SelfApprovalForbidden) as exc:
        _approve(school, "dual", req.id, ctx=both)
    assert (exc.value.status, exc.value.code) == (403, "self_approval_forbidden")
    with (
        pytest.raises(changes.SelfApprovalForbidden),
        tenant_session(school.tenant_id, dual.user_id) as s,
    ):
        changes.reject(s, both, req.id, RejectIn(reason="I changed my mind"), expected_version=1)
    assert CR.row(admin_engine, req.id)["status"] == "pending"
    assert CR.actions(admin_engine, school.tenant_id, req.id) == ["change_request.submitted"]


@pytest.mark.parametrize(
    "stale",
    [
        {"auth_time": None},
        {"mfa": False},
        {"auth_time": dt.datetime.now(dt.UTC) - dt.timedelta(minutes=6)},
    ],
)
def test_FR_CR_002_approval_needs_mfa_within_five_minutes(
    school: Any, admin_engine: Engine, stale: dict[str, Any]
) -> None:
    req = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    ctx = dataclasses.replace(CR.ctx(school, school.people["principal"], "principal"), **stale)
    with pytest.raises(StepUpRequired):
        _approve(school, "principal", req.id, ctx=ctx)
    assert CR.row(admin_engine, req.id)["status"] == "pending"


def test_FR_CR_002_stale_version_and_decided_requests_are_refused(
    school: Any, admin_engine: Engine
) -> None:
    req = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    with pytest.raises(PreconditionFailed):
        _approve(school, "principal", req.id, version=7)
    _approve(school, "principal", req.id)
    with pytest.raises(changes.RequestNotPending):
        _approve(school, "owner", req.id, version=2)
    events = CR.actions(admin_engine, school.tenant_id, req.id)
    assert events.count("change_request.approved") == 1


def test_FR_CR_003_request_on_changed_value_is_outdated(school: Any, admin_engine: Engine) -> None:
    """A non-anchor source can change after submission: approving would hide that change."""
    sid = CR.student(school)
    owner = school.people["owner"]
    octx = CR.ctx(school, owner, "office_admin")
    with tenant_session(school.tenant_id, owner.user_id) as s:
        students.record_value(s, octx, sid, "dob", "birth_certificate", "2012-03-20")
    req = CR.submit(
        admin_engine,
        school,
        school.people["office_admin"],
        "office_admin",
        student_id=sid,
        target_source="birth_certificate",
    )
    with tenant_session(school.tenant_id, owner.user_id) as s:
        students.record_value(s, octx, sid, "dob", "birth_certificate", "2012-03-21")
    with pytest.raises(changes.RequestOutdated):
        _approve(school, "principal", req.id)
    assert CR.row(admin_engine, req.id)["status"] == "pending"


def test_FR_CR_004_expired_request_cannot_be_approved_and_expires_daily(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    later = req.expires_at + dt.timedelta(minutes=1)
    monkeypatch.setattr(changes, "_now", lambda session: later)
    with pytest.raises(changes.RequestExpired) as exc:
        _approve(school, "principal", req.id)
    assert (exc.value.status, exc.value.code) == (409, "request_expired")
    with tenant_session(school.tenant_id) as s:
        assert changes.expire_due(s) >= 1
    row = CR.row(admin_engine, req.id)
    assert (row["status"], row["decided_by"], row["decided_at"]) == ("expired", None, later)
    events = CR.audit_rows(admin_engine, school.tenant_id, req.id)
    assert [(e["action"], e["actor_type"]) for e in events][-1] == (
        "change_request.expired",
        "system",
    )
    assert ("change_request.expired", school.people["office_admin"].membership_id) in (
        CR.notifications(admin_engine, school.tenant_id, req.id)
    )
    # DQ findings linked to the request are released (outbox -> dq.unlink_change_request).
    assert CR.outbox(admin_engine, school.tenant_id, req.id)[-1][0] == "change_request.expired"
    with tenant_session(school.tenant_id) as s:
        assert changes.expire_due(s) == 0, "idempotent"


# --- reject and cancel (FR-CR-004) ---------------------------------------------------------------


def test_FR_CR_004_rejection_needs_a_reason(school: Any, admin_engine: Engine) -> None:
    req = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    principal = school.people["principal"]
    pctx = CR.ctx(school, principal, "principal")
    with (
        pytest.raises(ValidationFailed) as exc,
        tenant_session(school.tenant_id, principal.user_id) as s,
    ):
        changes.reject(s, pctx, req.id, RejectIn(reason="no"), expected_version=1)
    assert exc.value.errors[0] == {
        "field": "reason",
        "code": "too_short",
        "message_key": "errors.too_short",
    }
    with tenant_session(school.tenant_id, principal.user_id) as s:
        out = changes.reject(
            s, pctx, req.id, RejectIn(reason="The certificate is not attested"), expected_version=1
        )
    assert (out.status, out.decision_note) == ("rejected", "The certificate is not attested")
    assert out.applied_value_id is None
    assert CR.actions(admin_engine, school.tenant_id, req.id) == [
        "change_request.submitted",
        "change_request.rejected",
    ]
    assert CR.outbox(admin_engine, school.tenant_id, req.id)[-1][0] == "change_request.rejected"
    assert ("change_request.rejected", school.people["office_admin"].membership_id) in (
        CR.notifications(admin_engine, school.tenant_id, req.id)
    )
    assert _history(school, req.student_id)[0].verification_status == "unverified"


def test_FR_CR_004_only_the_requester_cancels(school: Any, admin_engine: Engine) -> None:
    req = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    staff = school.people["office_staff"]
    with pytest.raises(changes.NotRequester), tenant_session(school.tenant_id, staff.user_id) as s:
        changes.cancel(s, CR.ctx(school, staff, "office_staff"), req.id, expected_version=1)
    admin = school.people["office_admin"]
    with tenant_session(school.tenant_id, admin.user_id) as s:
        out = changes.cancel(s, CR.ctx(school, admin, "office_admin"), req.id, expected_version=1)
    assert (out.status, out.decided_by) == ("cancelled", None)
    assert CR.actions(admin_engine, school.tenant_id, req.id)[-1] == "change_request.cancelled"
    assert CR.outbox(admin_engine, school.tenant_id, req.id)[-1] == (
        "change_request.cancelled",
        {
            "change_request_id": str(req.id),
            "student_id": str(req.student_id),
            "attribute_key": req.attribute_key,
        },
    )
    with pytest.raises(changes.RequestNotPending):
        _approve(school, "principal", req.id, version=2)


def test_FR_AUD_failed_decisions_leave_no_audit_event(school: Any, admin_engine: Engine) -> None:
    req = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    before = CR.actions(admin_engine, school.tenant_id, req.id)
    bads: tuple[Callable[[], Any], ...] = (
        lambda: _approve(school, "principal", req.id, version=9),
        lambda: _approve(
            school,
            "office_admin",
            req.id,
            ctx=CR.ctx(school, school.people["office_admin"], "office_admin"),
        ),
    )
    for bad in bads:
        with pytest.raises((PreconditionFailed, NotFound)):
            bad()
    assert CR.actions(admin_engine, school.tenant_id, req.id) == before
    assert _history(school, req.student_id)[0].verification_status == "unverified"


# --- hardening (audit 2026-10-05 "Change requests" and "Certificates") ---------------------------


def _version_of(admin: Engine, document_id: uuid.UUID) -> uuid.UUID:
    with admin.connect() as c:
        value: uuid.UUID = c.execute(
            text(
                "SELECT id FROM kb.document_versions WHERE document_id = :d "
                "ORDER BY version_no DESC LIMIT 1"
            ),
            {"d": document_id},
        ).scalar_one()
    return value


def _set_version_status(admin: Engine, version_id: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE kb.document_versions SET status = :s WHERE id = :v"),
            {"s": status, "v": version_id},
        )


def test_FR_CR_001_submit_pins_the_evidence_version(school: Any, admin_engine: Engine) -> None:
    admin = school.people["office_admin"]
    doc = CR.evidence(admin_engine, school, admin)
    req = CR.submit(admin_engine, school, admin, "office_admin", evidence_id=doc)
    assert CR.row(admin_engine, req.id)["evidence_version_id"] == _version_of(admin_engine, doc)
    # sos_app cannot move the pin afterwards (no UPDATE grant on the column).
    from sqlalchemy.exc import ProgrammingError

    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(school.tenant_id) as s,
    ):
        s.execute(
            text("UPDATE sis.change_requests SET evidence_version_id = NULL WHERE id = :i"),
            {"i": req.id},
        )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("queued", "evidence_not_ready"),
        ("scanning", "evidence_not_ready"),
        ("quarantined", "evidence_not_usable"),
        ("failed", "evidence_not_usable"),
    ],
)
def test_FR_CR_002_approval_needs_the_pinned_evidence_version_ready(
    school: Any, admin_engine: Engine, status: str, code: str
) -> None:
    """Evidence is accepted while its virus scan runs; approval waits until the pinned version
    is clean (``ready``) and refuses one that failed it."""
    admin = school.people["office_admin"]
    doc = CR.evidence(admin_engine, school, admin, status="queued")
    req = CR.submit(admin_engine, school, admin, "office_admin", evidence_id=doc)
    version = _version_of(admin_engine, doc)
    _set_version_status(admin_engine, version, status)
    with pytest.raises(Conflict) as exc:
        _approve(school, "principal", req.id)
    assert exc.value.code == code
    assert CR.row(admin_engine, req.id)["status"] == "pending"
    if status == "queued":
        _set_version_status(admin_engine, version, "ready")
        assert _approve(school, "principal", req.id).status == "approved"


def test_FR_CR_002_a_newer_evidence_version_does_not_replace_the_pinned_one(
    school: Any, admin_engine: Engine
) -> None:
    """The approver decides on the file the requester attached: a later (clean) version of the
    document does not stand in for a pinned version that failed its scan."""
    admin = school.people["office_admin"]
    doc = CR.evidence(admin_engine, school, admin)
    req = CR.submit(admin_engine, school, admin, "office_admin", evidence_id=doc)
    pinned = _version_of(admin_engine, doc)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) SELECT :n, "
                "tenant_id, document_id, 2, replace(object_key, '/v1/', '/v2/'), sha256, "
                "mime_type, size_bytes, "
                "'ready', created_by FROM kb.document_versions WHERE id = :v"
            ),
            {"n": uuid.uuid4(), "v": pinned},
        )
    _set_version_status(admin_engine, pinned, "quarantined")
    with pytest.raises(Conflict) as exc:
        _approve(school, "principal", req.id)
    assert exc.value.code == "evidence_not_usable"


def test_FR_CR_002_approver_must_be_able_to_open_the_evidence(
    school: Any, admin_engine: Engine
) -> None:
    admin = school.people["office_admin"]
    doc = CR.evidence(admin_engine, school, admin, acl=[("membership", str(admin.membership_id))])
    req = CR.submit(admin_engine, school, admin, "office_admin", evidence_id=doc)
    principal = school.people["principal"]
    base = CR.ctx(school, principal, "principal")
    blind = dataclasses.replace(base, permissions=base.permissions - {"document.manage_acl"})
    with pytest.raises(Conflict) as exc:
        _approve(school, "principal", req.id, ctx=blind)
    assert exc.value.code == "evidence_not_visible"
    assert _approve(school, "principal", req.id).status == "approved"


def test_FR_CR_002_approval_locks_the_student_before_comparing_the_old_value(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent value write holds the student row lock: approval takes it first, so the
    old-value check cannot race a write that lands just after it."""
    req = CR.submit(admin_engine, school, school.people["office_admin"], "office_admin")
    calls: list[str] = []
    real_lock = students.lock_student_for_change
    real_current = changes._current_value

    def lock(*args: Any, **kwargs: Any) -> Any:
        calls.append("lock")
        return real_lock(*args, **kwargs)

    def current(*args: Any, **kwargs: Any) -> Any:
        calls.append("compare")
        return real_current(*args, **kwargs)

    monkeypatch.setattr(students, "lock_student_for_change", lock)
    monkeypatch.setattr(changes, "_current_value", current)
    _approve(school, "principal", req.id)
    assert calls[:2] == ["lock", "compare"]


def test_FR_CR_002_requester_must_still_be_an_active_member(
    school: Any, admin_engine: Engine
) -> None:
    maker = CR.W.add_member(admin_engine, school.tenant_id, ["office_admin"])
    req = CR.submit(admin_engine, school, maker, "office_admin")
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET status = 'suspended' WHERE id = :m"),
            {"m": maker.membership_id},
        )
    with pytest.raises(Conflict) as exc:
        _approve(school, "principal", req.id)
    assert exc.value.code == "requester_inactive"
    assert CR.row(admin_engine, req.id)["status"] == "pending"
    # The checker can still clear it from the queue.
    principal = school.people["principal"]
    with tenant_session(school.tenant_id, principal.user_id) as s:
        out = changes.reject(
            s,
            CR.ctx(school, principal, "principal"),
            req.id,
            RejectIn(reason="The person who asked has left the school"),
            expected_version=1,
        )
    assert out.status == "rejected"
