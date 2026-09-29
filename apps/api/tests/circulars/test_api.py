"""Circulars, tasks and notices over the API (US-1601..US-1606; FR-CIR-*, FR-TASK-*,
FR-NOTICE-*; invariants 3, 7, 8, 9; docs/12 §4).

Synthetic schools A and B (tests/api/world.py). Authorization per role is covered by the generated
matrix (tests/security/test_authz_matrix.py) and cross-school ids by tests/security/test_bola.py;
this module checks behaviour, object visibility, audit and notifications.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.circulars import service
from app.core.db import tenant_session
from app.core.errors import Forbidden

from .conftest import C

pytestmark = pytest.mark.db


def _if(version: int) -> dict[str, str]:
    return {"If-Match": f'W/"{version}"'}


def _notifications(admin: Engine, membership_id: uuid.UUID, template: str) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text(
                    "SELECT count(*) FROM ops.notifications WHERE recipient_membership_id = :m "
                    "AND template_key = :k"
                ),
                {"m": membership_id, "k": template},
            ).scalar_one()
        )


# --- circulars (US-1601, US-1602) -----------------------------------------------------------


def test_US_1601_inbox_and_detail_show_the_reading_with_source_chips(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    document_id = C.read_circular(admin_engine, ai_on.a)
    office = ai_on.person("office_staff")
    listed = api.call(office, "GET", "/api/v1/circulars", params={"limit": 200})
    assert listed.status_code == 200
    item = next(i for i in listed.json()["data"] if i["document_id"] == str(document_id))
    assert item["reading_status"] == "ready"
    assert item["open_suggestions"] == 2
    detail = api.call(office, "GET", f"/api/v1/circulars/{document_id}")
    assert detail.status_code == 200
    reading = detail.json()["reading"]
    assert reading["ai_generated"] is True
    assert reading["summary_sources"]
    assert all(s["citation"]["source"].startswith("sos://doc/") for s in reading["suggestions"])
    assert detail.headers["ETag"] == f'W/"{reading["version"]}"'


def test_FR_CIR_006_reading_follows_document_visibility(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    hidden = C.read_circular(admin_engine, ai_on.a, acl=[("role", "principal")])
    teacher = ai_on.person("teacher")
    assert api.call(teacher, "GET", f"/api/v1/circulars/{hidden}").status_code == 404
    listed = api.call(teacher, "GET", "/api/v1/circulars", params={"limit": 200}).json()["data"]
    assert str(hidden) not in {i["document_id"] for i in listed}
    # A reviewer who cannot see the circular cannot decide its suggestions either.
    (first, *_rest) = C.suggestions(admin_engine, hidden)
    office = ai_on.person("office_staff")
    res = api.call(
        office,
        "POST",
        f"/api/v1/circular-suggestions/{first['id']}/dismiss",
        json={},
        headers=_if(first["version"]),
    )
    assert res.status_code == 404


def _told(admin: Engine, membership_id: uuid.UUID, template: str, document_id: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text(
                    "SELECT count(*) FROM ops.notifications WHERE recipient_membership_id = :m "
                    "AND template_key = :k AND resource_id = :d"
                ),
                {"m": membership_id, "k": template, "d": document_id},
            ).scalar_one()
        )


def test_FR_CIR_006_reading_notifications_go_only_to_reviewers_who_can_see_the_circular(
    ai_on: Any, admin_engine: Engine
) -> None:
    """``circular.read_ready`` and ``circular.needs_review`` name the circular: only
    ``circular.review`` holders whose document visibility reaches it are told (a restrictive
    ACL keeps it from the others, like the inbox does)."""
    school = ai_on.a
    principal = school.people["principal"]  # in the ACL
    office_admin = school.people["office_admin"]  # document.manage_acl: sees every document
    office_staff = school.people["office_staff"]  # circular.review, outside the ACL
    teacher = school.people["teacher"]  # in the ACL, but no circular.review
    acl = [("role", "principal"), ("role", "teacher")]
    hidden = C.read_circular(admin_engine, school, acl=acl)
    ready = "circular.read_ready"
    assert _told(admin_engine, principal.membership_id, ready, hidden) == 1
    assert _told(admin_engine, office_admin.membership_id, ready, hidden) == 1
    assert _told(admin_engine, office_staff.membership_id, ready, hidden) == 0
    assert _told(admin_engine, teacher.membership_id, ready, hidden) == 0
    # A circular every role may see still reaches every reviewer.
    open_to_all = C.read_circular(admin_engine, school)
    assert _told(admin_engine, office_staff.membership_id, ready, open_to_all) == 1

    C.KB.enable_ai(admin_engine, school.tenant_id, enabled=False)
    try:
        failed = C.circular(admin_engine, school, acl=acl)
        assert C.read_now(admin_engine, school, failed) == "ai_disabled"
    finally:
        C.KB.enable_ai(admin_engine, school.tenant_id)
    review = "circular.needs_review"
    assert _told(admin_engine, principal.membership_id, review, failed) == 1
    assert _told(admin_engine, office_staff.membership_id, review, failed) == 0
    assert _told(admin_engine, teacher.membership_id, review, failed) == 0


def test_FR_CIR_004_confirm_creates_one_task_with_the_citation(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    document_id = C.read_circular(admin_engine, ai_on.a)
    first, second = C.suggestions(admin_engine, document_id)
    office = ai_on.person("office_staff")
    teacher = ai_on.person("teacher")
    path = f"/api/v1/circular-suggestions/{first['id']}/confirm"
    body = {"owner_membership_id": str(teacher.membership_id), "title": "Send UDISE+ sheets"}
    res = api.call(office, "POST", path, json=body, headers=_if(first["version"]))
    assert res.status_code == 201, res.text
    task = res.json()
    assert task["title"] == "Send UDISE+ sheets"
    assert task["due_on"] == "2026-10-15"
    assert task["source"] == "circular"
    assert task["citation"]["quote"] == first["citation"]["quote"]
    # Decided once: a second decision is refused.
    again = api.call(office, "POST", path, json=body, headers=_if(first["version"] + 1))
    assert again.status_code == 409
    assert again.json()["code"] == "suggestion_decided"
    # The owner is told in the app; the decision and the task are audited (ids only).
    assert _notifications(admin_engine, teacher.membership_id, "task.assigned") >= 1
    events = C.W.audit_events(admin_engine, ai_on.a.tenant_id, "circular.suggestion_confirmed")
    confirmed = [e for e in events if e["summary"]["suggestion_id"] == str(first["id"])]
    assert confirmed
    assert confirmed[0]["summary"]["edited"] == ["title"]
    # Review needs every suggestion decided.
    detail = api.call(office, "GET", f"/api/v1/circulars/{document_id}")
    review = f"/api/v1/circulars/{document_id}/review"
    early = api.call(office, "POST", review, json={}, headers={"If-Match": detail.headers["ETag"]})
    assert early.status_code == 409
    assert early.json()["code"] == "suggestions_open"
    dismissed = api.call(
        office,
        "POST",
        f"/api/v1/circular-suggestions/{second['id']}/dismiss",
        json={},
        headers=_if(second["version"]),
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    detail = api.call(office, "GET", f"/api/v1/circulars/{document_id}")
    done = api.call(office, "POST", review, json={}, headers={"If-Match": detail.headers["ETag"]})
    assert done.status_code == 200, done.text
    assert done.json()["reviewed"] is True


def test_FR_CIR_004_owner_must_be_an_active_member_of_this_school(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    document_id = C.read_circular(admin_engine, ai_on.a)
    first = C.suggestions(admin_engine, document_id)[0]
    other_school = ai_on.b.people["owner"].membership_id
    res = api.call(
        ai_on.person("office_admin"),
        "POST",
        f"/api/v1/circular-suggestions/{first['id']}/confirm",
        json={"owner_membership_id": str(other_school)},
        headers=_if(first["version"]),
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "owner_not_active"


def test_FR_CIR_001_read_again_after_manual_review(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    school = ai_on.a
    C.KB.enable_ai(admin_engine, school.tenant_id, enabled=False)
    try:
        document_id = C.circular(admin_engine, school)
        assert C.read_now(admin_engine, school, document_id) == "ai_disabled"
    finally:
        C.KB.enable_ai(admin_engine, school.tenant_id)
    office = ai_on.person("office_staff")
    detail = api.call(office, "GET", f"/api/v1/circulars/{document_id}").json()
    assert detail["reading"]["can_retry"] is True
    res = api.call(office, "POST", f"/api/v1/circulars/{document_id}/read", json={})
    assert res.status_code == 202, res.text
    assert res.json()["reading_status"] == "queued"
    assert C.read_now(admin_engine, school, document_id) == "ready"
    done = api.call(office, "POST", f"/api/v1/circulars/{document_id}/read", json={})
    assert done.status_code == 409
    assert done.json()["code"] == "reading_done"


# --- tasks (US-1603, US-1604) ---------------------------------------------------------------


def test_US_1603_my_tasks_and_the_school_view(ai_on: Any, api: Any) -> None:
    school = ai_on.a
    teacher = school.people["teacher"]
    mine = C.task(school, owner=teacher, by="principal", title="Collect exam fees")
    others = C.task(school, owner=school.people["accountant"], by="principal")
    res = api.call(teacher, "GET", "/api/v1/tasks", params={"limit": 200})
    ids = {t["id"] for t in res.json()["data"]}
    assert str(mine) in ids
    assert str(others) not in ids
    # Another person's task is 404 without task.read_all (never 403: existence hidden).
    assert api.call(teacher, "GET", f"/api/v1/tasks/{others}").status_code == 404
    refused = api.call(teacher, "GET", "/api/v1/tasks", params={"view": "all"})
    assert refused.status_code == 403
    everything = api.call(
        school.people["principal"], "GET", "/api/v1/tasks", params={"view": "all", "limit": 200}
    )
    assert {str(mine), str(others)} <= {t["id"] for t in everything.json()["data"]}


def test_FR_TASK_004_owner_moves_their_task_and_cannot_cancel_it(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    school = ai_on.a
    teacher = school.people["teacher"]
    task_id = C.task(school, owner=teacher, by="principal")
    path = f"/api/v1/tasks/{task_id}/status"
    started = api.call(teacher, "POST", path, json={"status": "in_progress"}, headers=_if(1))
    assert started.status_code == 200, started.text
    cancel = api.call(teacher, "POST", path, json={"status": "cancelled"}, headers=_if(2))
    assert cancel.status_code == 409
    assert cancel.json()["code"] == "task_cancel_not_allowed"
    done = api.call(teacher, "POST", path, json={"status": "done"}, headers=_if(2))
    assert done.status_code == 200
    assert done.json()["completed_at"] is not None
    assert C.W.audit_events(admin_engine, school.tenant_id, "task.completed")
    stale = api.call(teacher, "POST", path, json={"status": "open"}, headers=_if(1))
    assert stale.status_code == 412


def test_FR_TASK_003_manager_reassigns_and_cancels(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    school = ai_on.a
    task_id = C.task(school, by="office_admin")
    admin_person = school.people["office_admin"]
    new_owner = school.people["exam_coordinator"]
    res = api.call(
        admin_person,
        "PATCH",
        f"/api/v1/tasks/{task_id}",
        json={"owner_membership_id": str(new_owner.membership_id), "due_on": "2026-12-01"},
        headers=_if(1),
    )
    assert res.status_code == 200, res.text
    assert res.json()["owner"]["membership_id"] == str(new_owner.membership_id)
    assert _notifications(admin_engine, new_owner.membership_id, "task.assigned") >= 1
    cancel = api.call(
        admin_person,
        "POST",
        f"/api/v1/tasks/{task_id}/status",
        json={"status": "cancelled"},
        headers=_if(2),
    )
    assert cancel.status_code == 200
    closed = api.call(
        admin_person, "PATCH", f"/api/v1/tasks/{task_id}", json={"title": "x"}, headers=_if(3)
    )
    assert closed.status_code == 409
    assert closed.json()["code"] == "task_closed"


def test_FR_TASK_007_reminders_are_sent_once(ai_on: Any, admin_engine: Engine) -> None:
    school = ai_on.b
    owner = school.people["owner"]
    today = service.today_ist()
    soon = C.task(school, due_on=today + dt.timedelta(days=1), title="Soon")
    late = C.task(school, due_on=today - dt.timedelta(days=2), title="Late")
    later = C.task(school, due_on=today + dt.timedelta(days=30), title="Later")
    with tenant_session(school.tenant_id) as db:
        first = service.send_reminders(db, today=today)
    with tenant_session(school.tenant_id) as db:
        second = service.send_reminders(db, today=today)
    assert first >= 2
    assert second == 0
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT template_key, resource_id, params FROM ops.notifications "
                "WHERE recipient_membership_id = :m AND template_key LIKE 'task.%'"
            ),
            {"m": owner.membership_id},
        ).all()
    keys = {(r.template_key, r.resource_id) for r in rows}
    assert ("task.due_soon", soon) in keys
    assert ("task.overdue", late) in keys
    assert not any(r.resource_id == later for r in rows)
    for r in rows:
        assert set(r.params) <= {"task_id", "days"}  # ids and counts only


# --- notices (US-1605, US-1606) -------------------------------------------------------------


def test_US_1605_notice_from_a_circular_is_bilingual_and_never_sees_students(
    ai_on: Any, api: Any, admin_engine: Engine, installed: Any
) -> None:
    _store, transport, _pdf = installed
    document_id = C.read_circular(admin_engine, ai_on.a)
    office = ai_on.person("office_staff")
    res = api.call(
        office,
        "POST",
        "/api/v1/notices",
        json={"source": "circular", "document_id": str(document_id)},
    )
    assert res.status_code == 201, res.text
    notice = res.json()
    assert notice["ai_drafted"] is True
    assert notice["title_te"]
    assert notice["body_te"]
    assert notice["status"] == "draft"
    sent = str(transport.sent[-1])
    assert "Synthetic Student" not in sent
    assert "admission" not in sent.lower()
    assert C.W.audit_events(admin_engine, ai_on.a.tenant_id, "notice.drafted")
    assert (
        C.db_value(
            admin_engine,
            "SELECT count(*) FROM kb.llm_calls WHERE tenant_id = :t AND feature = 'notices'",
            t=ai_on.a.tenant_id,
        )
        >= 1
    )


def test_FR_NOTICE_003_the_ai_draft_runs_with_no_database_transaction_open(
    ai_on: Any, api: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model call may take longer than ``idle_in_transaction_session_timeout`` (30 s for
    ``sos_app``, docs/05 §3.1): drafting must not hold a transaction open, or the database ends
    the connection and the request fails. Like the circular reading, the draft runs between two
    short transactions."""
    from app.knowledge import service as knowledge

    original = knowledge.draft_notice
    seen: list[int] = []

    def watching(tenant_id: Any, source: Any) -> Any:
        with admin_engine.connect() as c:
            seen.append(
                int(
                    c.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE usename = 'sos_app' "
                            "AND datname = current_database() "
                            "AND state LIKE 'idle in transaction%'"
                        )
                    ).scalar_one()
                )
            )
        return original(tenant_id, source)

    monkeypatch.setattr(knowledge, "draft_notice", watching)
    office = ai_on.person("office_staff")
    headers = {"Idempotency-Key": f"notice-draft-{uuid.uuid4().hex}"}
    body = {"source": "staff_text", "text": "Sports day is on 14/11/2026 at the school ground."}
    res = api.call(office, "POST", "/api/v1/notices", json=body, headers=headers)
    assert res.status_code == 201, res.text
    assert res.json()["ai_drafted"] is True
    assert seen == [0]
    # A retry with the same key replays the first answer without drafting again.
    again = api.call(office, "POST", "/api/v1/notices", json=body, headers=headers)
    assert again.status_code == 201
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == res.json()["id"]
    assert seen == [0]


def test_FR_NOTICE_002_personal_circulars_and_numbers_are_refused(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    personal = C.circular(admin_engine, ai_on.a, sensitivity="C2")
    office = ai_on.person("office_staff")
    res = api.call(
        office, "POST", "/api/v1/notices", json={"source": "circular", "document_id": str(personal)}
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "notice_source_personal"
    phone = api.call(
        office,
        "POST",
        "/api/v1/notices",
        json={"source": "staff_text", "text": "Call 9876543210 for the fee receipt."},
    )
    assert phone.status_code == 422
    assert phone.json()["errors"][0]["code"] == "notice_personal_data"


def test_FR_NOTICE_005_approve_render_and_download(
    ai_on: Any, api: Any, admin_engine: Engine, installed: Any
) -> None:
    school = ai_on.a
    _store, _transport, fake_pdf = installed
    office = school.people["office_staff"]
    blank = api.call(office, "POST", "/api/v1/notices", json={"source": "blank"}).json()
    path = f"/api/v1/notices/{blank['id']}"
    incomplete = api.call(
        school.people["principal"], "POST", f"{path}/approve", json={}, headers=_if(1)
    )
    assert incomplete.status_code == 422
    edited = api.call(
        office,
        "PATCH",
        path,
        json={
            "title_en": "Sports day",
            "body_en": "Sports day is on 14/11/2026 at 9:00.",
            "title_te": "క్రీడా దినోత్సవం",
            "body_te": "క్రీడా దినోత్సవం 14/11/2026 ఉదయం 9:00కు.",
        },
        headers=_if(1),
    )
    assert edited.status_code == 200, edited.text
    # office_staff may draft but not approve (notice.approve).
    assert api.call(office, "POST", f"{path}/approve", json={}, headers=_if(2)).status_code == 403
    approved = api.call(
        school.people["principal"], "POST", f"{path}/approve", json={}, headers=_if(2)
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["render_status"] == "queued"
    locked = api.call(office, "PATCH", path, json={"title_en": "Changed"}, headers=_if(3))
    assert locked.status_code == 409
    early = api.call(office, "GET", f"{path}/download-url", params={"format": "pdf"})
    assert early.status_code == 409
    assert C.render_all(school, uuid.UUID(blank["id"])) == "ready"
    page = fake_pdf.pages[-1]
    assert "క్రీడా దినోత్సవం" in page
    assert "Synthetic Model School" in page
    for fmt in ("pdf", "png"):
        link = api.call(office, "GET", f"{path}/download-url", params={"format": fmt})
        assert link.status_code == 200, link.text
        assert link.json()["filename"].endswith(f".{fmt}")
    downloads = C.W.audit_events(admin_engine, school.tenant_id, "notice.downloaded")
    assert {e["summary"]["format"] for e in downloads} >= {"pdf", "png"}


def test_FR_NOTICE_006_rendered_html_escapes_everything() -> None:
    from app.circulars.rendering import notice_html

    page = notice_html(
        school_name="<b>School</b>",
        approved_on=dt.date(2026, 11, 1),
        title_en="<script>alert(1)</script>",
        body_en="a & b",
        title_te="క",
        body_te="<img src=x>",
    )
    assert "<script>" not in page
    assert "<img" not in page
    assert "&lt;script&gt;" in page
    assert "01/11/2026" in page


# --- isolation and logs (SEC-001, invariant 5) ----------------------------------------------


def test_SEC_001_lists_never_show_the_other_school(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    b_circular = C.read_circular(admin_engine, ai_on.b)
    b_task = C.task(ai_on.b)
    b_notice = C.notice(ai_on.b)
    owner = ai_on.a.people["owner"]
    circulars = api.call(owner, "GET", "/api/v1/circulars", params={"limit": 200}).json()["data"]
    tasks = api.call(owner, "GET", "/api/v1/tasks", params={"view": "all", "limit": 200}).json()[
        "data"
    ]
    notices = api.call(owner, "GET", "/api/v1/notices", params={"limit": 200}).json()["data"]
    assert str(b_circular) not in {c["document_id"] for c in circulars}
    assert str(b_task) not in {t["id"] for t in tasks}
    assert str(b_notice) not in {n["id"] for n in notices}
    assignees = api.call(owner, "GET", "/api/v1/task-assignees").json()
    b_members = {str(p.membership_id) for p in ai_on.b.people.values()}
    assert not {a["membership_id"] for a in assignees} & b_members


def test_invariant_5_no_circular_or_notice_text_in_logs(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    from structlog.testing import capture_logs

    school = ai_on.a
    office = school.people["office_staff"]
    with capture_logs() as logs:
        document_id = C.read_circular(admin_engine, school)
        first = C.suggestions(admin_engine, document_id)[0]
        api.call(
            office,
            "POST",
            f"/api/v1/circular-suggestions/{first['id']}/confirm",
            json={"owner_membership_id": str(office.membership_id), "title": "Secret title ZQX"},
            headers=_if(first["version"]),
        )
        api.call(
            office,
            "POST",
            "/api/v1/notices",
            json={"source": "circular", "document_id": str(document_id)},
        )
        notice_id = C.notice(school, approved=True)
        C.render_all(school, notice_id)
    text_ = str(logs)
    for secret in ("Headmasters", "UDISE", "Secret title ZQX", "Sports day", "క్రీడా", "Guntur"):
        assert secret not in text_, secret


def test_FR_TASK_002_assignees_need_a_school_wide_grant(ai_on: Any) -> None:
    """``GET /task-assignees`` is a ``require_any`` read: the service, not the guard, checks
    the reach. Every other ``task.manage`` / ``circular.review`` route is school-wide, so a
    section-scoped grant (a custom role) does not open the staff list either (SEC-003)."""
    school = ai_on.a
    base = C.ctx(school, "office_admin")
    scoped = dataclasses.replace(
        base,
        scopes=dataclasses.replace(
            base.scopes, school=False, section_ids=frozenset({school.ids["section_9a"]})
        ),
        scoped_permissions=frozenset({"task.manage", "circular.review"}),
    )
    with tenant_session(school.tenant_id, base.user_id) as db:
        assert service.assignees(db, base)
        with pytest.raises(Forbidden):
            service.assignees(db, scoped)


def test_invariant_4_tasks_and_notice_drafts_refuse_full_aadhaar_numbers(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    """No Aadhaar number is ever stored (invariant 4): typed task titles and details are refused
    with ``aadhaar_full_number_rejected`` (like reasons, notes and cell edits elsewhere), and a
    notice draft's texts with ``notice_personal_data`` when saved (docs/09), not only when the
    notice is approved."""
    from app.core.redaction import verhoeff_check_digit

    digits = "73920184556"
    aadhaar = digits + verhoeff_check_digit(digits)
    spaced = f"{aadhaar[:4]} {aadhaar[4:8]} {aadhaar[8:]}"
    school = ai_on.a
    owner = school.people["owner"]
    teacher = school.people["class_teacher"]
    due = (service.today_ist() + dt.timedelta(days=5)).isoformat()
    created = api.call(
        owner,
        "POST",
        "/api/v1/tasks",
        json={
            "title": "Collect the forms",
            "details": f"Student Aadhaar {spaced} is missing a photo",
            "owner_membership_id": str(teacher.membership_id),
            "due_on": due,
        },
    )
    assert created.status_code == 422, created.text
    assert [(e["field"], e["code"]) for e in created.json()["errors"]] == [
        ("details", "aadhaar_full_number_rejected")
    ]
    task = C.task(school, owner=teacher)
    edited = api.call(
        owner, "PATCH", f"/api/v1/tasks/{task}", json={"title": f"Check {aadhaar}"}, headers=_if(1)
    )
    assert edited.status_code == 422, edited.text
    assert edited.json()["errors"][0]["code"] == "aadhaar_full_number_rejected"
    office = school.people["office_staff"]
    blank = api.call(office, "POST", "/api/v1/notices", json={"source": "blank"}).json()
    draft = api.call(
        office,
        "PATCH",
        f"/api/v1/notices/{blank['id']}",
        json={"body_te": f"ఆధార్ {spaced}"},
        headers=_if(blank["version"]),
    )
    assert draft.status_code == 422, draft.text
    assert draft.json()["errors"][0]["field"] == "body_te"
    # docs/09: PATCH /notices refuses personal numbers (Aadhaar-like, phones, emails) at once.
    assert draft.json()["errors"][0]["code"] == "notice_personal_data"
    for sql in (
        "SELECT count(*) FROM ops.tasks WHERE tenant_id = :t "
        "AND coalesce(title, '') || coalesce(details, '') LIKE :p",
        "SELECT count(*) FROM ops.parent_notices WHERE tenant_id = :t "
        "AND title_en || body_en || title_te || body_te LIKE :p",
    ):
        assert C.db_value(admin_engine, sql, t=school.tenant_id, p=f"%{aadhaar[8:]}%") == 0
