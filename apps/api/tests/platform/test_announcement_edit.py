"""Editing a platform announcement (FR-PLT-026; docs/16 §5.13).

Owner decision 2026-10-04: an announcement that has ended is read-only. "Ended" is the same
rule the banner feed uses (``starts_at <= now < ends_at`` is active, so ``ends_at <= now`` has
ended), evaluated on the server in UTC. A cancelled one stays read-only too (409
``invalid_state``). The server clock is pinned through ``announcements.now``. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from app.platform import announcements
from app.platform.announcements import InMemoryAnnouncementCache

from .conftest import Api, MakeOperator, Operator

pytestmark = pytest.mark.db

T0 = dt.datetime(2030, 3, 4, 4, 30, tzinfo=dt.UTC)  # starts_at of every announcement here
T1 = T0 + dt.timedelta(hours=2)  # ends_at


@pytest.fixture(autouse=True)
def _cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = InMemoryAnnouncementCache()
    monkeypatch.setattr(announcements, "get_cache", lambda: cache)


@pytest.fixture
def agent(make_operator: MakeOperator) -> Operator:
    return make_operator("support_agent")


def _body(**overrides: Any) -> dict[str, Any]:
    return {
        "title_en": "Maintenance on Sunday",
        "body_en": "SchoolOS is unavailable from 10:00 to 12:00 IST.",
        "severity": "maintenance",
        "starts_at": T0.isoformat(),
        "ends_at": T1.isoformat(),
        **overrides,
    }


def _create(api: Api, op: Operator, **overrides: Any) -> dict[str, Any]:
    res = api.call("POST", "/announcements", op, json=_body(**overrides))
    assert res.status_code == 201, res.text
    created: dict[str, Any] = res.json()
    return created


def _at(monkeypatch: pytest.MonkeyPatch, moment: dt.datetime) -> None:
    monkeypatch.setattr(announcements, "now", lambda: moment)


def _patch(api: Api, op: Operator, row: dict[str, Any], **overrides: Any) -> Any:
    return api.call(
        "PATCH",
        f"/announcements/{row['id']}",
        op,
        json=_body(**overrides),
        headers={"If-Match": f'"{row["version"]}"'},
    )


@pytest.mark.parametrize(
    ("status", "moment"),
    [
        ("draft", T0 - dt.timedelta(days=1)),
        ("scheduled", T0 - dt.timedelta(days=1)),  # not started yet
        ("scheduled", T0 + dt.timedelta(minutes=30)),  # live
        ("draft", T0 + dt.timedelta(minutes=30)),
        ("scheduled", T1 - dt.timedelta(microseconds=1)),  # the last moment it is shown
    ],
)
def test_FR_PLT_026_draft_scheduled_and_live_announcements_can_be_edited(
    api: Api, agent: Operator, monkeypatch: pytest.MonkeyPatch, status: str, moment: dt.datetime
) -> None:
    row = _create(api, agent, status=status)
    _at(monkeypatch, moment)
    res = _patch(api, agent, row, status=status, title_en="Maintenance moved")
    assert res.status_code == 200, res.text
    assert (res.json()["title_en"], res.json()["version"]) == (
        "Maintenance moved",
        row["version"] + 1,
    )


@pytest.mark.parametrize("status", ["draft", "scheduled"])
@pytest.mark.parametrize("after", [dt.timedelta(0), dt.timedelta(days=30)])
def test_FR_PLT_026_an_ended_announcement_is_read_only(
    api: Api,
    agent: Operator,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    after: dt.timedelta,
) -> None:
    """At exactly ``ends_at`` it has ended (the feed stops showing it then); later too. A new
    window does not reopen it."""
    row = _create(api, agent, status=status)
    _at(monkeypatch, T1 + after)
    later = T1 + after + dt.timedelta(days=7)
    res = _patch(
        api,
        agent,
        row,
        status=status,
        starts_at=later.isoformat(),
        ends_at=(later + dt.timedelta(hours=1)).isoformat(),
    )
    assert (res.status_code, res.json()["code"]) == (409, "invalid_state"), res.text
    assert "ended" in res.json()["detail"]
    stored = announcements.get(uuid.UUID(row["id"]))
    assert (stored.version, stored.starts_at, stored.ends_at) == (row["version"], T0, T1)


def test_FR_PLT_026_an_edit_that_moves_the_end_into_the_past_ends_it_now(
    api: Api, agent: Operator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create accepts any window whose end is after its start, past or not; an edit follows
    the same rule, so moving the end of a live banner to now ends it ("end now"). From then
    on it is read-only."""
    row = _create(api, agent)
    moment = T0 + dt.timedelta(minutes=30)
    _at(monkeypatch, moment)
    res = _patch(api, agent, row, ends_at=moment.isoformat())
    assert res.status_code == 200, res.text
    shown = announcements.active_announcements(uuid.uuid4(), "shared", at=moment)
    assert row["id"] not in {str(a.id) for a in shown}
    again = _patch(api, agent, res.json(), title_en="Too late")
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")


def test_FR_PLT_026_a_cancelled_announcement_stays_read_only(
    api: Api, agent: Operator, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _create(api, agent)
    _at(monkeypatch, T0 - dt.timedelta(days=1))
    cancelled = api.call("POST", f"/announcements/{row['id']}/cancel", agent).json()
    res = _patch(api, agent, cancelled)
    assert (res.status_code, res.json()["code"]) == (409, "invalid_state")
    assert "cancelled" in res.json()["detail"]


@pytest.mark.parametrize("ended", [False, True])
def test_FR_PLT_026_edit_needs_announcements_manage(
    api: Api,
    agent: Operator,
    make_operator: MakeOperator,
    monkeypatch: pytest.MonkeyPatch,
    ended: bool,
) -> None:
    """Without the permission the answer is 403 whether or not the announcement has ended
    (the state is not disclosed)."""
    row = _create(api, agent)
    _at(monkeypatch, T1 if ended else T0 - dt.timedelta(days=1))
    for role in ("billing_admin", "platform_engineer", "platform_viewer"):
        res = _patch(api, make_operator(role), row)
        assert (res.status_code, res.json()["code"]) == (403, "forbidden"), role


# --- cancel (owner decision 2026-10-04: an ended announcement is fully read-only) ----------


def _cancel(api: Api, op: Operator, row: dict[str, Any]) -> Any:
    return api.call("POST", f"/announcements/{row['id']}/cancel", op)


@pytest.mark.parametrize(
    ("status", "moment"),
    [
        ("draft", T0 - dt.timedelta(days=1)),
        ("scheduled", T0 - dt.timedelta(days=1)),
        ("scheduled", T0 + dt.timedelta(minutes=30)),  # live
        ("scheduled", T1 - dt.timedelta(microseconds=1)),
    ],
)
def test_FR_PLT_026_draft_scheduled_and_live_announcements_can_be_cancelled(
    api: Api, agent: Operator, monkeypatch: pytest.MonkeyPatch, status: str, moment: dt.datetime
) -> None:
    row = _create(api, agent, status=status)
    _at(monkeypatch, moment)
    res = _cancel(api, agent, row)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "cancelled"


@pytest.mark.parametrize("status", ["draft", "scheduled"])
@pytest.mark.parametrize("after", [dt.timedelta(0), dt.timedelta(days=30)])
def test_FR_PLT_026_an_ended_announcement_cannot_be_cancelled(
    api: Api,
    agent: Operator,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    after: dt.timedelta,
) -> None:
    row = _create(api, agent, status=status)
    _at(monkeypatch, T1 + after)
    res = _cancel(api, agent, row)
    assert (res.status_code, res.json()["code"]) == (409, "invalid_state"), res.text
    assert res.json()["detail"] == "An announcement that has ended cannot change."
    stored = announcements.get(uuid.UUID(row["id"]))
    assert (stored.status, stored.version) == (status, row["version"])


def test_FR_PLT_026_cancelling_twice_keeps_its_answer_even_after_the_end(
    api: Api, agent: Operator, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _create(api, agent)
    _at(monkeypatch, T0 - dt.timedelta(days=1))
    first = _cancel(api, agent, row)
    assert first.status_code == 200, first.text
    again = _cancel(api, agent, row)
    assert (again.status_code, again.json()) == (200, first.json())
    _at(monkeypatch, T1 + dt.timedelta(days=1))
    later = _cancel(api, agent, row)
    assert (later.status_code, later.json()) == (200, first.json())


@pytest.mark.parametrize("ended", [False, True])
def test_FR_PLT_026_cancel_needs_announcements_manage(
    api: Api,
    agent: Operator,
    make_operator: MakeOperator,
    monkeypatch: pytest.MonkeyPatch,
    ended: bool,
) -> None:
    row = _create(api, agent)
    _at(monkeypatch, T1 if ended else T0 - dt.timedelta(days=1))
    for role in ("billing_admin", "platform_engineer", "platform_viewer"):
        res = _cancel(api, make_operator(role), row)
        assert (res.status_code, res.json()["code"]) == (403, "forbidden"), role
    assert announcements.get(uuid.UUID(row["id"])).status == "scheduled"
