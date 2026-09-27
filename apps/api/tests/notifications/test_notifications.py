"""In-app notifications: schema, notify(), the reader API and retention (FR-NOT-001, SEC-001,
SEC-015, invariant 5). Synthetic schools only."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.audit.schemas import SummaryError
from app.core.db import tenant_session
from app.notifications import service
from app.notifications.tasks import purge_all
from app.notifications.templates import TemplateError

from .conftest import Staff, W

pytestmark = pytest.mark.db


def _notify(
    tenant_id: uuid.UUID, recipients: Any, key: str = "import.committed", **params: Any
) -> int:
    with tenant_session(tenant_id) as s:
        return service.notify(
            s,
            tenant_id=tenant_id,
            recipients=recipients,
            template_key=key,
            params=params or {"import_id": str(uuid.uuid4()), "rows": 3},
        )


def _rows(admin: Engine, membership_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT * FROM ops.notifications WHERE recipient_membership_id = :m "
                    "ORDER BY created_at, id"
                ),
                {"m": membership_id},
            )
        ]


# --- schema ------------------------------------------------------------------------------


def test_SEC_001_notifications_are_tenant_isolated(school: Staff, admin_engine: Engine) -> None:
    _notify(school.tenant_id, [school.teacher.membership_id])  # type: ignore[attr-defined]
    other = W.provision_school()
    with tenant_session(other) as s:
        assert s.execute(text("SELECT count(*) FROM ops.notifications")).scalar_one() == 0
    with tenant_session(school.tenant_id) as s:
        assert s.execute(text("SELECT count(*) FROM ops.notifications")).scalar_one() >= 1


def test_SEC_001_recipient_must_be_a_member_of_the_same_school(school: Staff, world: Any) -> None:
    with pytest.raises(IntegrityError, match="notifications_recipient_fk"):
        _notify(school.tenant_id, [world.b.people["owner"].membership_id])


def test_FR_NOT_001_app_may_only_change_read_at(school: Staff, admin_engine: Engine) -> None:
    member = school.teacher.membership_id  # type: ignore[attr-defined]
    _notify(school.tenant_id, [member])
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(school.tenant_id) as s,
    ):
        s.execute(text("UPDATE ops.notifications SET template_key = 'x.y'"))


# --- notify() ----------------------------------------------------------------------------


def test_FR_NOT_001_notify_permission_selector_reaches_only_holders(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine
) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    principal = W.add_member(admin_engine, tid, ["principal"])
    staff = W.add_member(admin_engine, tid, ["office_staff"])
    suspended = W.add_member(admin_engine, tid, ["principal"], status="suspended")
    expired = W.add_member(
        admin_engine,
        tid,
        ["owner"],
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1),
    )
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.memberships SET expires_at = created_at + interval '1 ms' "
                "WHERE id = :m"
            ),
            {"m": expired.membership_id},
        )
    created = _notify(
        tid,
        service.PermissionSelector("breakglass.approve"),
        "breakglass.expired",
        grant_id=str(uuid.uuid4()),
    )
    assert created == 2
    assert len(_rows(admin_engine, owner.membership_id)) == 1
    assert len(_rows(admin_engine, principal.membership_id)) == 1
    for other in (staff, suspended, expired):
        assert _rows(admin_engine, other.membership_id) == []


def test_FR_NOT_001_dedupe_key_notifies_once(school: Staff, admin_engine: Engine) -> None:
    member = school.office_staff.membership_id  # type: ignore[attr-defined]
    key = f"import.committed:{uuid.uuid4()}"
    before = len(_rows(admin_engine, member))
    for _ in range(3):
        with tenant_session(school.tenant_id) as s:
            service.notify(
                s,
                tenant_id=school.tenant_id,
                recipients=[member, member],
                template_key="import.committed",
                params={"import_id": str(uuid.uuid4()), "rows": 1},
                dedupe_key=key,
            )
    assert len(_rows(admin_engine, member)) == before + 1


def test_FR_NOT_001_notify_validates_template_params_and_tenant(school: Staff) -> None:
    member = [school.teacher.membership_id]  # type: ignore[attr-defined]
    with pytest.raises(TemplateError):
        _notify(school.tenant_id, member, "import.committed", import_id="i")
    with pytest.raises(TemplateError):
        _notify(school.tenant_id, member, "no.such_template", x="y")
    # Personal data never goes into params (same rules as audit summaries).
    with pytest.raises(SummaryError):
        _notify(
            school.tenant_id,
            member,
            "import.committed",
            import_id="call 9876543210",
            rows=1,
        )
    with pytest.raises(SummaryError):
        _notify(
            school.tenant_id,
            member,
            "import.committed",
            import_id="lakshmi.synthetica@example.test",
            rows=1,
        )
    other = W.provision_school()
    with pytest.raises(RuntimeError), tenant_session(other) as s:
        service.notify(
            s,
            tenant_id=school.tenant_id,
            recipients=member,
            template_key="import.committed",
            params={"import_id": "i", "rows": 1},
        )


# --- reader API ----------------------------------------------------------------------------


def test_FR_NOT_001_list_count_read_and_read_all(
    school: Staff, api: Any, admin_engine: Engine
) -> None:
    me = W.add_member(admin_engine, school.tenant_id, ["office_staff"])
    other = W.add_member(admin_engine, school.tenant_id, ["office_staff"])
    for rows in (1, 2, 3):
        _notify(school.tenant_id, [me.membership_id], import_id=str(uuid.uuid4()), rows=rows)
    _notify(school.tenant_id, [other.membership_id])

    assert api.call(me, "GET", "/api/v1/notifications/unread-count").json() == {"count": 3}
    page = api.call(me, "GET", "/api/v1/notifications", params={"limit": 2})
    assert page.status_code == 200, page.text
    body = page.json()
    assert len(body["data"]) == 2
    assert body["data"][0]["body"] == "3 rows were added to the student records."
    assert page.headers["content-language"] == "en"
    rest = api.call(me, "GET", "/api/v1/notifications", params={"cursor": body["next_cursor"]})
    assert [n["params"]["rows"] for n in rest.json()["data"]] == [1]
    assert rest.json()["next_cursor"] is None

    first = body["data"][0]["id"]
    read = api.call(me, "POST", f"/api/v1/notifications/{first}/read")
    assert read.status_code == 200, read.text
    read_at = read.json()["read_at"]
    assert read_at is not None
    again = api.call(me, "POST", f"/api/v1/notifications/{first}/read")
    assert again.json()["read_at"] == read_at, "marking read twice keeps the first time"
    assert api.call(me, "GET", "/api/v1/notifications/unread-count").json() == {"count": 2}
    unread = api.call(me, "GET", "/api/v1/notifications", params={"unread": "true"}).json()
    assert first not in {n["id"] for n in unread["data"]}

    done = api.call(me, "POST", "/api/v1/notifications/read-all")
    assert done.json() == {"updated": 2}
    assert api.call(me, "GET", "/api/v1/notifications/unread-count").json() == {"count": 0}
    # The other member's notification is untouched.
    assert api.call(other, "GET", "/api/v1/notifications/unread-count").json() == {"count": 1}


def test_FR_NOT_001_rendered_in_telugu_for_accept_language_te(
    school: Staff, api: Any, admin_engine: Engine
) -> None:
    me = W.add_member(admin_engine, school.tenant_id, ["teacher"])
    _notify(school.tenant_id, [me.membership_id], import_id="i", rows=4)
    res = api.call(
        me, "GET", "/api/v1/notifications", headers={"Accept-Language": "te-IN,te;q=0.9"}
    )
    assert res.headers["content-language"] == "te"
    item = res.json()["data"][0]
    assert item["language"] == "te"
    assert item["title"] == "రికార్డులు జోడించబడ్డాయి"
    assert item["body"] == "4 వరుసలు విద్యార్థి రికార్డులకు జోడించబడ్డాయి."


def test_SEC_015_cannot_read_or_mark_someone_elses_notification(
    school: Staff, api: Any, admin_engine: Engine
) -> None:
    owner_of = W.add_member(admin_engine, school.tenant_id, ["teacher"])
    intruder = W.add_member(admin_engine, school.tenant_id, ["principal"])
    _notify(school.tenant_id, [owner_of.membership_id])
    target = _rows(admin_engine, owner_of.membership_id)[0]["id"]
    res = api.call(intruder, "POST", f"/api/v1/notifications/{target}/read")
    assert res.status_code == 404
    random = api.call(intruder, "POST", f"/api/v1/notifications/{uuid.uuid4()}/read")
    assert {k: res.json()[k] for k in ("status", "title")} == {
        k: random.json()[k] for k in ("status", "title")
    }
    listed = api.call(intruder, "GET", "/api/v1/notifications").json()["data"]
    assert str(target) not in {n["id"] for n in listed}
    assert _rows(admin_engine, owner_of.membership_id)[0]["read_at"] is None


def test_FR_NOT_001_bad_cursor_is_422(school: Staff, api: Any) -> None:
    res = api.call(school.teacher, "GET", "/api/v1/notifications", params={"cursor": "e30"})
    assert res.status_code == 422


# --- retention -----------------------------------------------------------------------------


def test_FR_ADM_002_purge_removes_only_old_read_notifications(
    school: Staff, admin_engine: Engine
) -> None:
    member = W.add_member(admin_engine, school.tenant_id, ["teacher"])
    for _ in range(3):
        _notify(school.tenant_id, [member.membership_id])
    ids = [r["id"] for r in _rows(admin_engine, member.membership_id)]
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.notifications SET created_at = now() - interval '200 days', "
                "read_at = now() - interval '91 days' WHERE id = :i"
            ),
            {"i": ids[0]},
        )
        c.execute(
            text(
                "UPDATE ops.notifications SET created_at = now() - interval '200 days', "
                "read_at = now() - interval '10 days' WHERE id = :i"
            ),
            {"i": ids[1]},
        )
        c.execute(
            text(
                "UPDATE ops.notifications SET created_at = now() - interval '300 days' "
                "WHERE id = :i"
            ),
            {"i": ids[2]},
        )
    result = purge_all()
    assert result["purged"] >= 1
    assert {r["id"] for r in _rows(admin_engine, member.membership_id)} == set(ids[1:])
