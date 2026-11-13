"""FT-11 attendance that survives a bad network (lead decision D87; FR-ATT-026..FR-ATT-035).

The teacher's unsent day is kept on the device and sent again later. These tests pin what the
API guarantees for such a retry: the same ``Idempotency-Key`` replays the first answer (one save,
one audit event), a replay never overwrites a newer save, a draft read before someone else saved
is refused (412 ``attendance_changed``) and nothing is stored, and the day tells the screen how
long a draft may be kept (``draft_keep_hours`` from ``app/academics/config.yaml``).
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text

from app.academics.config import AttendanceRules, load_config

pytestmark = pytest.mark.db

S = sys.modules["sos_test_insights_support"]
W = S.W


@pytest.fixture(scope="module")
def ds(world: Any, admin_engine: Engine) -> Any:
    """A school of its own (a1, a2 in 9A with class teacher ``ct``) plus an accountant."""
    s = S.insights_school(admin_engine)
    s.people["accountant"] = W.add_member(admin_engine, s.tenant_id, ["accountant"])
    return s


def _day(back: int) -> str:
    """A distinct past school day per test (no two tests touch the same day)."""
    return str(S.school_days(1, end=S.school_days(back)[0])[0].isoformat())


def _path(s: Any) -> str:
    return f"/api/v1/sections/{s.ids['section_9a']}/attendance"


def _entry(sid: uuid.UUID, day: str, status: str, version: int | None = 0) -> dict[str, Any]:
    out: dict[str, Any] = {"student_id": str(sid), "on_date": day, "status": status}
    if version is not None:
        out["expected_version"] = version
    return out


def _status(admin: Engine, s: Any, sid: uuid.UUID, day: str) -> str | None:
    with admin.connect() as c:
        row = c.execute(
            text(
                "SELECT status FROM sis.attendance_marks "
                "WHERE tenant_id = :t AND student_id = :s AND on_date = :d"
            ),
            {"t": s.tenant_id, "s": sid, "d": day},
        ).first()
    return None if row is None else str(row[0])


def _recorded(admin: Engine, s: Any, day: str) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text(
                    "SELECT count(*) FROM audit.events WHERE tenant_id = :t "
                    "AND action = 'attendance.recorded' AND summary->>'first_date' = :d"
                ),
                {"t": s.tenant_id, "d": day},
            ).scalar_one()
        )


def test_FR_ATT_026_the_day_says_how_long_a_draft_is_kept(ds: Any, api: Any) -> None:
    got = api.call(ds.people["ct"], "GET", _path(ds), params={"date": _day(70)})
    assert got.status_code == 200, got.text
    assert got.json()["draft_keep_hours"] == load_config().attendance.draft_keep_hours == 24


def test_FR_ATT_026_draft_keep_hours_is_bounded() -> None:
    base = load_config().attendance.model_dump()
    for bad in (0, 73):
        with pytest.raises(ValidationError, match="draft_keep_hours"):
            AttendanceRules.model_validate({**base, "draft_keep_hours": bad})


def test_FR_ATT_027_a_retried_save_is_replayed_once(
    ds: Any, api: Any, admin_engine: Engine
) -> None:
    day, ct = _day(71), ds.people["ct"]
    body = {"entries": [_entry(ds.ids["a1"], day, "absent")], "source": "mark"}
    key = {"Idempotency-Key": f"att-{uuid.uuid4()}"}
    first = api.call(ct, "POST", _path(ds), json=body, headers=key)
    assert first.status_code == 200, first.text
    again = api.call(ct, "POST", _path(ds), json=body, headers=key)
    assert again.status_code == 200, again.text
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json() == first.json()
    assert first.json()["written"] == 1
    # One save, one audit event (written in the save's transaction).
    assert _recorded(admin_engine, ds, day) == 1
    assert _status(admin_engine, ds, ds.ids["a1"], day) == "absent"


def test_FR_ATT_028_a_replay_never_overwrites_a_newer_save(
    ds: Any, api: Any, admin_engine: Engine
) -> None:
    """The teacher's first save went through but the answer was lost; the principal corrected
    the day; the teacher's phone then retries: the replay answers without saving again."""
    day, ct, principal = _day(72), ds.people["ct"], ds.people["principal"]
    sid = ds.ids["a1"]
    body = {"entries": [_entry(sid, day, "absent")], "source": "mark"}
    key = {"Idempotency-Key": f"att-{uuid.uuid4()}"}
    assert api.call(ct, "POST", _path(ds), json=body, headers=key).status_code == 200
    fix = api.call(
        principal,
        "POST",
        _path(ds),
        json={"entries": [_entry(sid, day, "present", 1)], "source": "mark"},
    )
    assert fix.status_code == 200, fix.text
    retry = api.call(ct, "POST", _path(ds), json=body, headers=key)
    assert retry.status_code == 200
    assert retry.headers.get("Idempotent-Replayed") == "true"
    assert _status(admin_engine, ds, sid, day) == "present"


def test_FR_ATT_029_a_stale_draft_is_refused_and_saves_nothing(
    ds: Any, api: Any, admin_engine: Engine
) -> None:
    """A draft read at version 0 meets the principal's newer save: 412 and nothing stored, also
    when the phone retries the same draft (a failure is not kept for replay)."""
    day, ct, principal = _day(73), ds.people["ct"], ds.people["principal"]
    a1, a2 = ds.ids["a1"], ds.ids["a2"]
    saved = api.call(
        principal,
        "POST",
        _path(ds),
        json={"entries": [_entry(a1, day, "present")], "source": "mark"},
    )
    assert saved.status_code == 200
    draft = {"entries": [_entry(a1, day, "absent"), _entry(a2, day, "late")], "source": "mark"}
    key = {"Idempotency-Key": f"att-{uuid.uuid4()}"}
    for _ in range(2):
        stale = api.call(ct, "POST", _path(ds), json=draft, headers=key)
        assert stale.status_code == 412, stale.text
        assert stale.json()["code"] == "attendance_changed"
        assert stale.headers.get("Idempotent-Replayed") is None
    assert _status(admin_engine, ds, a1, day) == "present"
    assert _status(admin_engine, ds, a2, day) is None


def test_FR_ATT_030_a_changed_draft_needs_a_new_key(ds: Any, api: Any) -> None:
    day, ct = _day(74), ds.people["ct"]
    key = {"Idempotency-Key": f"att-{uuid.uuid4()}"}
    first = {"entries": [_entry(ds.ids["a1"], day, "absent")], "source": "mark"}
    assert api.call(ct, "POST", _path(ds), json=first, headers=key).status_code == 200
    changed = {"entries": [_entry(ds.ids["a1"], day, "late", 1)], "source": "mark"}
    reused = api.call(ct, "POST", _path(ds), json=changed, headers=key)
    assert reused.status_code == 422
    assert reused.json()["code"] == "idempotency_key_reused"
    bad = api.call(ct, "POST", _path(ds), json=changed, headers={"Idempotency-Key": "short"})
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_idempotency_key"


def test_FR_ATT_031_authz_with_a_key(ds: Any, api: Any, world: Any) -> None:
    """Allowed role 200 (above); a role without ``attendance.record`` 403; a class teacher of
    another section and another school's principal 404 (a replay key changes none of this)."""
    day = _day(75)
    body = {"entries": [_entry(ds.ids["a1"], day, "absent")], "source": "mark"}
    key = {"Idempotency-Key": f"att-{uuid.uuid4()}"}
    denied = api.call(ds.people["accountant"], "POST", _path(ds), json=body, headers=key)
    assert denied.status_code == 403
    other_section = api.call(ds.people["ct9c"], "POST", _path(ds), json=body, headers=key)
    assert other_section.status_code == 404
    other_school = api.call(world.a.people["principal"], "POST", _path(ds), json=body, headers=key)
    assert other_school.status_code == 404
