"""Tally connector API: flag, enrolment, signed agent calls, snapshots, links, dues, revocation,
rotation, silence notices, audit and cross-tenant denial (M6; ADR-0032 Proposed;
FR-TALLY-001..010; SEC-003, SEC-005, SEC-008).

Each test that changes connector state uses its own synthetic school (``T.fresh_school``), so
device limits, groups and ledgers never meet another test's. The in-memory nonce and rate-limit
stores are reset before every test. Role x route authorization is in the generated matrix
(tests/security/test_authz_matrix.py); cross-school ids also in tests/security/test_bola.py.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.tally import agent_auth, service

from .conftest import T

pytestmark = pytest.mark.db

GROUPS = [
    ("Sundry Debtors", "Current Assets"),
    ("Class IX Fees", "Sundry Debtors"),
    ("Staff Advances", "Loans and Advances"),
]


@pytest.fixture(autouse=True)
def fresh_stores() -> Iterator[None]:
    agent_auth.get_agent_stores.cache_clear()
    yield
    agent_auth.get_agent_stores.cache_clear()


def _new_window() -> None:
    """The next request starts a new rate-limit window (a new in-memory store)."""
    agent_auth.get_agent_stores.cache_clear()


def _if(version: int) -> dict[str, str]:
    return {"If-Match": f'W/"{version}"'}


def _ready(api: Any, admin: Engine) -> tuple[Any, Any]:
    """A fresh school with an enrolled agent, a catalog and the two fee groups selected."""
    school = T.fresh_school(admin)
    agent = T.enrol(api, school)
    assert T.catalog(api, agent, GROUPS).status_code == 200
    T.select(api, school, ["Sundry Debtors", "Class IX Fees"])
    return school, agent


def _scalar(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar_one()


# --- the flag (ADR-0032 §8) -----------------------------------------------------------------------


def test_FR_TALLY_010_everything_is_404_while_the_flag_is_off(
    api: Any, admin_engine: Engine
) -> None:
    school = T.fresh_school(admin_engine, flag=False)
    owner, accountant = school.people["owner"], school.people["accountant"]
    for who, method, path in (
        (owner, "GET", "/api/v1/tally/status"),
        (owner, "GET", "/api/v1/tally/devices"),
        (owner, "POST", "/api/v1/tally/enrolment-codes"),
        (accountant, "GET", "/api/v1/tally/groups"),
        (accountant, "GET", "/api/v1/tally/parties"),
        (accountant, "GET", "/api/v1/tally/dues"),
    ):
        res = api.call(who, method, path, json={} if method == "POST" else None)
        assert res.status_code == 404, (path, res.text)
    # An unauthenticated enrolment attempt cannot tell a switched-off school from a bad code.
    res = T.enrol_with(api, school.tenant_id, "ABCD-EFGH-JKLM")
    assert res.status_code == 401
    with tenant_session(school.tenant_id) as db:
        assert service.connector_enabled(db, school.tenant_id) is False


# --- enrolment (FR-TALLY-001) ---------------------------------------------------------------------


def test_FR_TALLY_001_code_needs_recent_mfa_and_is_shown_once_and_stored_hashed(
    api: Any, admin_engine: Engine
) -> None:
    school = T.fresh_school(admin_engine)
    owner = school.people["owner"]
    stale = api.call(owner, "POST", "/api/v1/tally/enrolment-codes", json={}, auth_age_s=301)
    assert stale.status_code == 428
    out = T.new_code(api, school, name="Accounts PC")
    code = out["code"]
    assert len(code) == 14
    assert code.count("-") == 2
    assert set(code.replace("-", "")) <= set(service.CODE_ALPHABET)
    stored = _scalar(
        admin_engine,
        "SELECT code_hash FROM ops.tally_enrolment_codes WHERE id = :i",
        i=uuid.UUID(out["id"]),
    )
    assert bytes(stored) == service.code_hash(code)
    assert code.replace("-", "").encode() not in bytes(stored)
    events = T.audit_actions(admin_engine, school.tenant_id)
    assert [e["action"] for e in events] == ["tally.enrolment_code.created"]
    assert code not in str(events)


def test_FR_TALLY_001_a_code_enrols_once_and_expires(api: Any, admin_engine: Engine) -> None:
    school = T.fresh_school(admin_engine)
    code = T.new_code(api, school)["code"]
    first = T.enrol_with(api, school.tenant_id, code.lower())  # case and dashes do not matter
    assert first.status_code == 201, first.text
    out = first.json()
    assert out["key_id"].startswith("tdk-")
    assert len(T.b64(out["secret"])) == 32
    assert out["config"]["groups"] == []
    again = T.enrol_with(api, school.tenant_id, code)
    assert again.status_code == 401
    assert again.json()["code"] == "unauthenticated"

    late = T.new_code(api, school)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.tally_enrolment_codes SET created_at = now() - interval '2 hours', "
                "expires_at = now() - interval '1 hour' WHERE id = :i"
            ),
            {"i": uuid.UUID(late["id"])},
        )
    assert T.enrol_with(api, school.tenant_id, late["code"]).status_code == 401
    # The device's secret is stored wrapped, never in plaintext.
    wrapped = _scalar(
        admin_engine,
        "SELECT key_ciphertext FROM ops.tally_devices WHERE id = :i",
        i=uuid.UUID(out["device_id"]),
    )
    assert T.b64(out["secret"]) not in bytes(wrapped)
    enrolled = [
        e
        for e in T.audit_actions(admin_engine, school.tenant_id)
        if e["action"] == "tally.device.enrolled"
    ]
    assert len(enrolled) == 1
    assert enrolled[0]["actor_type"] == "system"
    assert out["secret"] not in str(enrolled)


def test_FR_TALLY_001_enrolment_attempts_are_limited_per_school(
    api: Any, admin_engine: Engine
) -> None:
    school = T.fresh_school(admin_engine)
    codes = [T.enrol_with(api, school.tenant_id, "ZZZZ-ZZZZ-ZZZZ").status_code for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_FR_TALLY_001_at_most_two_active_agents(api: Any, admin_engine: Engine) -> None:
    school = T.fresh_school(admin_engine)
    T.enrol(api, school)
    T.enrol(api, school, name="Spare PC")
    res = api.call(school.people["owner"], "POST", "/api/v1/tally/enrolment-codes", json={})
    assert res.status_code == 409
    assert res.json()["code"] == "too_many_devices"


# --- signed requests (FR-TALLY-002) ---------------------------------------------------------------


def test_FR_TALLY_002_signed_calls_are_verified(api: Any, admin_engine: Engine) -> None:
    school = T.fresh_school(admin_engine)
    agent = T.enrol(api, school)
    ok = agent.call(api, "GET", "/api/v1/edge/tally/config")
    assert ok.status_code == 200, ok.text
    assert ok.json()["min_agent_version"] == "0.1.0"
    path = "/api/v1/edge/tally/config"
    bad: dict[str, dict[str, Any]] = {
        "wrong secret": {"secret": bytes(32)},
        "unknown key": {"key_id": "tdk-aaaaaaaaaaaaaaaaaaaa"},
        "old timestamp": {"timestamp": 1_000_000},
        "other school": {"tenant_id": uuid.uuid4()},
    }
    for label, kw in bad.items():
        _new_window()
        res = agent.call(api, "GET", path, **kw)
        assert res.status_code == 401, (label, res.text)
        assert res.json()["title"] == "Sign in required"
    _new_window()
    # A body changed after signing fails.
    headers = agent.headers("PUT", "/api/v1/edge/tally/catalog", b'{"company":"X","groups":[]}')
    res = api.client.put(
        "/api/v1/edge/tally/catalog",
        content=b'{"company":"Y","groups":[]}',
        headers=headers,
    )
    assert res.status_code == 401
    # No headers at all.
    assert api.client.get(path).status_code == 401


def test_FR_TALLY_002_a_signature_with_non_ascii_bytes_is_a_401_not_an_error(
    api: Any, admin_engine: Engine
) -> None:
    # hmac.compare_digest refuses str with non-ASCII characters (TypeError -> 500); headers are
    # decoded as latin-1, so any byte above 0x7f in X-SOS-Signature reached it.
    school = T.fresh_school(admin_engine)
    agent = T.enrol(api, school)
    path = "/api/v1/edge/tally/config"
    good = agent.headers("GET", path, b"")
    signature = good["X-SOS-Signature"][:-1].encode("ascii") + b"\xe9"
    sent: dict[str, str | bytes] = {**good, "X-SOS-Signature": signature}
    res = api.client.get(path, headers=sent)
    assert res.status_code == 401, res.text
    assert res.json()["title"] == "Sign in required"


def test_FR_TALLY_002_nonces_are_used_once_and_calls_are_rate_limited(
    api: Any, admin_engine: Engine
) -> None:
    school = T.fresh_school(admin_engine)
    agent = T.enrol(api, school)
    path = "/api/v1/edge/tally/config"
    assert agent.call(api, "GET", path, nonce="nonce-aaaaaaaaaaaaaaaa").status_code == 200
    replay = agent.call(api, "GET", path, nonce="nonce-aaaaaaaaaaaaaaaa")
    assert replay.status_code == 409
    assert replay.json()["code"] == "replay"
    limited = agent.call(api, "GET", path)
    assert limited.status_code == 429


def test_FR_TALLY_002_revoked_agents_are_refused_at_once(api: Any, admin_engine: Engine) -> None:
    school = T.fresh_school(admin_engine)
    owner = school.people["owner"]
    agent = T.enrol(api, school)
    devices = api.call(owner, "GET", "/api/v1/tally/devices").json()
    assert [d["status"] for d in devices] == ["active"]
    device = devices[0]
    stale = api.call(
        owner,
        "POST",
        f"/api/v1/tally/devices/{device['id']}/revoke",
        headers=_if(device["version"]),
        auth_age_s=301,
    )
    assert stale.status_code == 428
    wrong = api.call(owner, "POST", f"/api/v1/tally/devices/{device['id']}/revoke", headers=_if(99))
    assert wrong.status_code == 412
    res = api.call(
        owner,
        "POST",
        f"/api/v1/tally/devices/{device['id']}/revoke",
        headers=_if(device["version"]),
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "revoked"
    assert _scalar(
        admin_engine,
        "SELECT key_ciphertext IS NULL AND key_id IS NULL FROM ops.tally_devices WHERE id = :i",
        i=uuid.UUID(device["id"]),
    )
    assert agent.call(api, "GET", "/api/v1/edge/tally/config").status_code == 401
    again = api.call(
        owner,
        "POST",
        f"/api/v1/tally/devices/{device['id']}/revoke",
        headers=_if(res.json()["version"]),
    )
    assert again.status_code == 409


def test_FR_TALLY_002_key_rotation_retires_the_old_key_on_first_use(
    api: Any, admin_engine: Engine
) -> None:
    school = T.fresh_school(admin_engine)
    agent = T.enrol(api, school)
    res = agent.call(api, "POST", "/api/v1/edge/tally/key-rotation")
    assert res.status_code == 200, res.text
    new_key, new_secret = res.json()["key_id"], T.b64(res.json()["secret"])
    assert new_key != agent.key_id
    # Both keys work until the new one is used.
    assert agent.call(api, "GET", "/api/v1/edge/tally/config").status_code == 200
    _new_window()
    assert (
        agent.call(
            api, "GET", "/api/v1/edge/tally/config", key_id=new_key, secret=new_secret
        ).status_code
        == 200
    )
    _new_window()
    assert agent.call(api, "GET", "/api/v1/edge/tally/config").status_code == 401
    actions = [e["action"] for e in T.audit_actions(admin_engine, school.tenant_id)]
    assert "tally.device.key_rotated" in actions
    assert "tally.device.key_promoted" in actions


# --- catalog, selection and snapshots (FR-TALLY-004, FR-TALLY-005) ------------------------------


def test_FR_TALLY_004_the_accountant_selects_groups_and_the_agent_sees_them(
    api: Any, admin_engine: Engine
) -> None:
    school, agent = _ready(api, admin_engine)
    accountant = school.people["accountant"]
    groups = api.call(accountant, "GET", "/api/v1/tally/groups").json()
    assert {(g["name"], g["selected"]) for g in groups} == {
        ("Sundry Debtors", True),
        ("Class IX Fees", True),
        ("Staff Advances", False),
    }
    _new_window()
    config = agent.call(api, "GET", "/api/v1/edge/tally/config").json()
    assert config["company"] == T.COMPANY
    assert sorted(config["groups"]) == ["Class IX Fees", "Sundry Debtors"]
    # A group of another company or an unknown id is refused.
    res = api.call(
        accountant,
        "PUT",
        "/api/v1/tally/groups/selection",
        json={"company": "Another Company", "group_ids": [groups[0]["id"]]},
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "unknown_group"


def test_FR_TALLY_004_snapshots_with_unselected_groups_are_refused(
    api: Any, admin_engine: Engine
) -> None:
    school, agent = _ready(api, admin_engine)
    res = T.snapshot(
        api, agent, [T.party("Synthetic Staff Ledger", "500.00", group="Staff Advances")]
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "group_not_selected"
    assert "Synthetic Staff Ledger" not in res.text
    _new_window()
    res = T.snapshot(api, agent, [T.party("Synthetic Party A", "1.00")], company="Other Co")
    assert res.status_code == 422
    assert (
        _scalar(
            admin_engine,
            "SELECT count(*) FROM ops.tally_parties WHERE tenant_id = :t",
            t=school.tenant_id,
        )
        == 0
    )


def test_FR_TALLY_005_a_snapshot_is_applied_once_and_marks_missing_ledgers(
    api: Any, admin_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    school, agent = _ready(api, admin_engine)
    batch = uuid.uuid4()
    parties = [
        T.party("Synthetic Party Ravi 9A", "15000.00", guid="guid-ravi"),
        T.party("Synthetic Party Sita", "-200.50", group="Class IX Fees"),
        T.party("Synthetic Party Old", "700.00"),
    ]
    with caplog.at_level(logging.INFO):
        res = T.snapshot(api, agent, parties, batch_id=batch)
    assert res.status_code == 200, res.text
    first = res.json()
    assert (first["created"], first["updated"], first["missing"], first["repeat"]) == (
        3,
        0,
        0,
        False,
    )
    # Invariant 5: no ledger names or amounts in the logs.
    logged = caplog.text
    assert "Synthetic Party" not in logged
    assert "15000" not in logged

    _new_window()
    repeat = T.snapshot(api, agent, parties, batch_id=batch)
    assert repeat.status_code == 200
    assert repeat.json()["repeat"] is True
    assert repeat.json()["sync_id"] == first["sync_id"]
    assert (
        _scalar(
            admin_engine,
            "SELECT count(*) FROM ops.tally_syncs WHERE tenant_id = :t",
            t=school.tenant_id,
        )
        == 1
    )

    _new_window()
    renamed = [
        T.party("Synthetic Party Ravi Kumar 9A", "12000.00", guid="guid-ravi"),
        T.party("Synthetic Party Sita", "-200.50", group="Class IX Fees"),
    ]
    res = T.snapshot(api, agent, renamed, as_of=dt.date(2026, 9, 29))
    out = res.json()
    assert (out["created"], out["updated"], out["missing"]) == (0, 1, 1)
    with admin_engine.connect() as c:
        rows = {
            r.ledger_name: (str(r.closing_balance), r.present, r.as_of)
            for r in c.execute(
                text(
                    "SELECT ledger_name, closing_balance, present, as_of FROM ops.tally_parties "
                    "WHERE tenant_id = :t"
                ),
                {"t": school.tenant_id},
            )
        }
    assert rows["Synthetic Party Ravi Kumar 9A"] == ("12000.00", True, dt.date(2026, 9, 29))
    assert rows["Synthetic Party Old"][1] is False
    events = [
        e
        for e in T.audit_actions(admin_engine, school.tenant_id)
        if e["action"].startswith("tally.sync")
    ]
    assert [e["action"] for e in events] == [
        "tally.sync.received",
        "tally.sync.repeated",
        "tally.sync.received",
    ]
    assert "Synthetic" not in str(events)


def test_FR_TALLY_005_duplicate_ledgers_in_one_snapshot_are_refused(
    api: Any, admin_engine: Engine
) -> None:
    _, agent = _ready(api, admin_engine)
    res = T.snapshot(
        api, agent, [T.party("Synthetic Dup", "1.00"), T.party("Synthetic Dup", "2.00")]
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "duplicate_party"


# --- links and dues (FR-TALLY-006, FR-TALLY-007) --------------------------------------------------


def test_FR_TALLY_006_a_person_links_ledgers_and_dues_follow_the_links(
    api: Any, admin_engine: Engine
) -> None:
    school, agent = _ready(api, admin_engine)
    ravi = T.SW.create(
        school, name="Synthetica Ravi Kumar", section_key="section_9a", admission_no="T-901"
    )
    sita = T.SW.create(
        school, name="Synthetica Sita Devi", section_key="section_9c", admission_no="T-902"
    )
    res = T.snapshot(
        api,
        agent,
        [
            T.party("Synthetica Ravi Kumar 9A", "15000.00"),
            T.party("Synthetica Sita Devi T-902", "4500.25", group="Class IX Fees"),
            T.party("Synthetic Family Ledger", "1000.00"),
        ],
    )
    assert res.status_code == 200, res.text
    accountant = school.people["accountant"]
    unlinked = api.call(accountant, "GET", "/api/v1/tally/parties?link=unlinked").json()["data"]
    assert len(unlinked) == 3
    by_name = {p["ledger_name"]: p for p in unlinked}
    # Suggestions only: nothing is linked until a person links.
    detail = api.call(
        accountant, "GET", f"/api/v1/tally/parties/{by_name['Synthetica Ravi Kumar 9A']['id']}"
    ).json()
    assert ravi in {uuid.UUID(c["student_id"]) for c in detail["candidates"]}
    assert detail["links"] == []
    assert (
        _scalar(
            admin_engine,
            "SELECT count(*) FROM ops.tally_party_links WHERE tenant_id = :t",
            t=school.tenant_id,
        )
        == 0
    )

    for ledger, student in (
        ("Synthetica Ravi Kumar 9A", ravi),
        ("Synthetica Sita Devi T-902", sita),
        ("Synthetic Family Ledger", ravi),
    ):
        res = api.call(
            accountant,
            "POST",
            f"/api/v1/tally/parties/{by_name[ledger]['id']}/links",
            json={"student_id": str(student)},
        )
        assert res.status_code == 201, res.text
    dues = api.call(accountant, "GET", "/api/v1/tally/dues").json()
    assert [(uuid.UUID(d["student_id"]), d["total_due"], d["ledgers"]) for d in dues["data"]] == [
        (ravi, "16000.00", 2),
        (sita, "4500.25", 1),
    ]
    assert dues["totals"]["total_due"] == "20500.25"
    assert dues["totals"]["unlinked_parties"] == 0
    status = api.call(accountant, "GET", "/api/v1/tally/status").json()
    assert (status["parties"], status["parties_linked"], status["total_due"]) == (3, 3, "20500.25")

    res = api.call(
        accountant,
        "DELETE",
        f"/api/v1/tally/parties/{by_name['Synthetic Family Ledger']['id']}/links/{ravi}",
    )
    assert res.status_code == 204
    dues = api.call(accountant, "GET", "/api/v1/tally/dues").json()
    assert dues["data"][0]["total_due"] == "15000.00"
    assert dues["totals"]["unlinked_parties"] == 1
    assert dues["totals"]["unlinked_due"] == "1000.00"
    actions = [e["action"] for e in T.audit_actions(admin_engine, school.tenant_id)]
    assert actions.count("tally.party.linked") == 3
    assert actions.count("tally.party.unlinked") == 1

    # Search by name is a POST (no names in URLs).
    found = api.call(
        accountant, "POST", "/api/v1/tally/parties/search", json={"query": "sita", "link": "linked"}
    ).json()["data"]
    assert [p["ledger_name"] for p in found] == ["Synthetica Sita Devi T-902"]


def test_FR_TALLY_007_status_totals_only_for_finance_readers(
    api: Any, admin_engine: Engine
) -> None:
    school, _ = _ready(api, admin_engine)
    owner = api.call(school.people["owner"], "GET", "/api/v1/tally/status").json()
    assert owner["total_due"] == "0.00"
    assert owner["devices_active"] == 1
    assert owner["groups_selected"] == 2


def test_FR_TALLY_006_links_only_to_students_in_this_school(
    api: Any, admin_engine: Engine, world: Any
) -> None:
    school, agent = _ready(api, admin_engine)
    T.snapshot(api, agent, [T.party("Synthetic Party X", "10.00")])
    accountant = school.people["accountant"]
    party_id = api.call(accountant, "GET", "/api/v1/tally/parties").json()["data"][0]["id"]
    other = T.SW.ensure_students(world)["b_sb"]
    res = api.call(
        accountant,
        "POST",
        f"/api/v1/tally/parties/{party_id}/links",
        json={"student_id": str(other)},
    )
    assert res.status_code == 422
    # Another school's owner sees none of it (404, never 403).
    b_owner = world.b.people["owner"]
    assert api.call(b_owner, "GET", f"/api/v1/tally/parties/{party_id}").status_code in (403, 404)
    device_id = api.call(school.people["owner"], "GET", "/api/v1/tally/devices").json()[0]["id"]
    T.set_flag(admin_engine, world.b.tenant_id, enabled=True)
    res = api.call(b_owner, "POST", f"/api/v1/tally/devices/{device_id}/revoke", headers=_if(1))
    assert res.status_code == 404


# --- silence (FR-TALLY-009) -----------------------------------------------------------------------


def test_FR_TALLY_009_a_silent_agent_is_reported_once(api: Any, admin_engine: Engine) -> None:
    school = T.fresh_school(admin_engine)
    agent = T.enrol(api, school)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.tally_devices SET last_seen_at = now() - interval '3 days' "
                "WHERE id = :i"
            ),
            {"i": agent.device_id},
        )
    with tenant_session(school.tenant_id) as db:
        sent = service.notify_silent_devices(db)
    # owner (device manager and finance reader, once), principal, accountant, auditor.
    assert sent == 4
    with tenant_session(school.tenant_id) as db:
        assert service.notify_silent_devices(db) == 0
    devices = api.call(school.people["owner"], "GET", "/api/v1/tally/devices").json()
    assert devices[0]["silent"] is True
    with admin_engine.connect() as c:
        params: list[str] = list(
            c.execute(
                text(
                    "SELECT DISTINCT params::text FROM ops.notifications WHERE tenant_id = :t "
                    "AND template_key = 'tally.agent_silent'"
                ),
                {"t": school.tenant_id},
            )
            .scalars()
            .all()
        )
    assert params
    assert all("hours" in p for p in params)
    # A call ends the silence; the next silence is reported again.
    assert agent.call(api, "GET", "/api/v1/edge/tally/config").status_code == 200
    assert (
        api.call(school.people["owner"], "GET", "/api/v1/tally/devices").json()[0]["silent"]
        is False
    )


def test_FR_TALLY_009_no_notice_while_the_flag_is_off(api: Any, admin_engine: Engine) -> None:
    school = T.fresh_school(admin_engine)
    agent = T.enrol(api, school)
    T.set_flag(admin_engine, school.tenant_id, enabled=False)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.tally_devices SET last_seen_at = now() - interval '3 days' "
                "WHERE id = :i"
            ),
            {"i": agent.device_id},
        )
    with tenant_session(school.tenant_id) as db:
        assert service.notify_silent_devices(db) == 0
    # A signed call from an enrolled agent of a switched-off school is 404 (after authentication).
    assert agent.call(api, "GET", "/api/v1/edge/tally/config").status_code == 404


def test_FR_TALLY_005_old_sync_records_are_deleted_unless_a_ledger_points_to_them(
    api: Any, admin_engine: Engine
) -> None:
    school, agent = _ready(api, admin_engine)
    T.snapshot(api, agent, [T.party("Synthetic Party Keep", "1.00")])
    _new_window()
    T.snapshot(api, agent, [T.party("Synthetic Party Keep", "2.00")])
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.tally_syncs SET received_at = now() - interval '500 days' "
                "WHERE tenant_id = :t"
            ),
            {"t": school.tenant_id},
        )
    with tenant_session(school.tenant_id) as db:
        assert service.purge_old_syncs(db) == 1
    assert (
        _scalar(
            admin_engine,
            "SELECT count(*) FROM ops.tally_syncs WHERE tenant_id = :t",
            t=school.tenant_id,
        )
        == 1
    )
