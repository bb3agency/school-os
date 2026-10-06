"""Fleet heartbeat protocol (FR-PLT-023..025, SEC-028; docs/12 §4.13, docs/16 §12)."""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import SecretStr, ValidationError
from sqlalchemy import text

from app.core.config import DeploymentMode, Settings
from app.core.db import platform_session
from app.core.errors import Unauthenticated
from app.identity.service_token import InMemoryReplayStore
from app.platform import fleet, heartbeat_client
from app.platform.common import fleet_cfg, today_ist
from app.platform.schemas import HeartbeatIn

from .conftest import Api, Operator, provision_payload

pytestmark = pytest.mark.db

URL = "/api/v1/fleet/heartbeat"


def _dedicated(api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]) -> dict[str, Any]:
    body = provision_payload(make_plan(tier="dedicated"), tier="dedicated", owner=None)
    res = api.call("POST", "/tenants", owner, json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def _payload(dep: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "deployment_id": dep["deployment_id"],
        "tenant_id": dep["tenant_id"],
        "sent_at": dt.datetime.now(dt.UTC).isoformat(),
        "nonce": str(uuid.uuid4()),
        "app_version": "2026.10.1",
        "git_sha": "3f2c1ab",
        "db_revision": "0006_ops",
        "health": {
            "api": "ok",
            "worker": "ok",
            "beat": "ok",
            "db": "ok",
            "valkey": "ok",
            "s3": "ok",
        },
        "queues": {"ingest": {"depth": 0, "oldest_s": 0}},
        "usage": {
            "date": str(today_ist()),
            "active_users": 3,
            "staff_users": 7,
            "students_active": 120,
            "storage_bytes": 10_000,
            "documents": 4,
            "ai_queries": 2,
            "ai_input_tokens": 100,
            "ai_output_tokens": 50,
            "ai_cost_usd": "0.0100",
        },
    }
    body.update(extra)
    return body


def _signed(
    dep: dict[str, Any],
    body: dict[str, Any] | bytes,
    *,
    key: str | None = None,
    key_id: str | None = None,
    ts: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    stamp = str(ts if ts is not None else int(time.time()))
    secret = heartbeat_client.decode_key(key or dep["heartbeat_key"])
    return raw, {
        "Content-Type": "application/json",
        fleet.HEADER_DEPLOYMENT: dep["deployment_id"],
        fleet.HEADER_KEY_ID: key_id or dep["heartbeat_key_id"],
        fleet.HEADER_TIMESTAMP: stamp,
        fleet.HEADER_SIGNATURE: fleet.sign(secret, stamp, raw),
    }


@pytest.fixture
def dep(api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]) -> dict[str, Any]:
    return _dedicated(api, owner, make_plan)


def test_SEC_028_valid_heartbeat_is_accepted_and_recorded(api: Api, dep: dict[str, Any]) -> None:
    raw, headers = _signed(dep, _payload(dep))
    res = api.client.post(URL, content=raw, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["announcements"] == []
    with platform_session() as s:
        row = s.execute(
            text(
                "SELECT status, app_version, last_heartbeat FROM platform.deployments WHERE id = :d"
            ),
            {"d": dep["deployment_id"]},
        ).one()
        usage = s.execute(
            text("SELECT source, students_active FROM platform.usage_daily WHERE tenant_id = :t"),
            {"t": dep["tenant_id"]},
        ).one()
        actions: Any = list(
            s.execute(
                text(
                    "SELECT action FROM platform.audit_events "
                    "WHERE subject_tenant_id = :t ORDER BY seq"
                ),
                {"t": dep["tenant_id"]},
            ).scalars()
        )
    assert (row.status, row.app_version) == ("healthy", "2026.10.1")
    assert row.last_heartbeat["usage"]["documents"] == 4
    assert tuple(usage) == ("heartbeat", 120)
    assert "deployment.first_heartbeat" in actions
    assert "deployment.status_changed" in actions


@pytest.mark.parametrize(
    "tamper",
    ["wrong_key", "unknown_key_id", "altered_body", "stale", "future", "unknown_deployment"],
)
def test_SEC_028_bad_signatures_are_401_and_store_nothing(
    api: Api, dep: dict[str, Any], tamper: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _payload(dep)
    now = int(time.time())
    # The server clock is pinned to the same whole second: with the real clock, "future"
    # (now + 301) fell inside the 300 s window whenever the request reached the server a
    # fraction of a second later (it passed or failed with load and test order).
    pinned = dt.datetime.fromtimestamp(now, dt.UTC)
    monkeypatch.setattr(fleet, "now", lambda: pinned)
    if tamper == "wrong_key":
        raw, headers = _signed(dep, body, key="A" * 43)
    elif tamper == "unknown_key_id":
        raw, headers = _signed(dep, body, key_id="hb-nosuchkeyatall")
    elif tamper == "altered_body":
        raw, headers = _signed(dep, body)
        raw = raw.replace(b"2026.10.1", b"2026.10.2")
    elif tamper == "stale":
        raw, headers = _signed(dep, body, ts=now - 301)
    elif tamper == "future":
        raw, headers = _signed(dep, body, ts=now + 301)
    else:
        raw, headers = _signed(dep, body)
        headers[fleet.HEADER_DEPLOYMENT] = str(uuid.uuid4())
    res = api.client.post(URL, content=raw, headers=headers)
    assert res.status_code == 401, (tamper, res.text)
    assert "detail" not in res.json() or res.json()["detail"] == "Heartbeat rejected"
    with platform_session() as s:
        seen: Any = s.execute(
            text("SELECT last_heartbeat_at FROM platform.deployments WHERE id = :d"),
            {"d": dep["deployment_id"]},
        ).scalar_one()
    assert seen is None


def test_SEC_028_replay_extra_fields_size_mismatch_and_rate_limit(
    api: Api,
    dep: dict[str, Any],
    owner: Operator,
    make_plan: Callable[..., uuid.UUID],
    fleet_stores: fleet.FleetStores,
) -> None:
    body = _payload(dep)
    raw, headers = _signed(dep, body)
    assert api.client.post(URL, content=raw, headers=headers).status_code == 200
    replay = api.client.post(URL, content=raw, headers=headers)
    assert (replay.status_code, replay.json()["code"]) == (409, "replay")
    again = _post(api, dep, _payload(dep))
    assert again.status_code == 429
    _next_minute(fleet_stores)
    extra = _post(api, dep, _payload(dep, school_name="Leak"))
    assert extra.status_code == 422
    nested = _payload(dep)
    nested["health"] = {**nested["health"], "note": "free text"}
    assert _post(api, dep, nested).status_code == 422
    big = _payload(dep, queues={f"q{i:04d}"[:32]: {"depth": 1, "oldest_s": 1} for i in range(16)})
    big_raw = json.dumps(big).encode() + b" " * 17000
    assert _post(api, dep, big_raw).status_code == 422
    other = _dedicated(api, owner, make_plan)
    mismatch = _payload(dep, tenant_id=other["tenant_id"])
    assert _post(api, dep, mismatch).status_code == 401


def _next_minute(stores: fleet.FleetStores) -> None:
    """Forget the per-deployment rate-limit marks (as if a minute had passed)."""
    rate = stores.rate
    assert isinstance(rate, InMemoryReplayStore)
    rate._seen.clear()


def _post(api: Api, dep: dict[str, Any], body: dict[str, Any] | bytes) -> Any:
    raw, headers = _signed(dep, body)
    return api.client.post(URL, content=raw, headers=headers)


def test_SEC_028_key_rotation_overlap(
    api: Api, dep: dict[str, Any], owner: Operator, wrapper: Any
) -> None:
    res = api.call("POST", f"/deployments/{dep['deployment_id']}/heartbeat-key:rotate", owner)
    assert res.status_code == 200, res.text
    new = res.json()
    stores = fleet.FleetStores(InMemoryReplayStore(), InMemoryReplayStore())
    for key, key_id in (
        (dep["heartbeat_key"], dep["heartbeat_key_id"]),
        (new["heartbeat_key"], new["heartbeat_key_id"]),
    ):
        raw, headers = _signed(dep, _payload(dep), key=key, key_id=key_id)
        fleet.verify_heartbeat(headers, raw, wrapper=wrapper, stores=stores)
        _next_minute(stores)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=8)
    old_raw, old_headers = _signed(dep, _payload(dep), ts=int(later.timestamp()))
    with pytest.raises(Unauthenticated):
        fleet.verify_heartbeat(old_headers, old_raw, wrapper=wrapper, stores=stores, at=later)
    new_raw, new_headers = _signed(
        dep,
        _payload(dep),
        key=new["heartbeat_key"],
        key_id=new["heartbeat_key_id"],
        ts=int(later.timestamp()),
    )
    fleet.verify_heartbeat(new_headers, new_raw, wrapper=wrapper, stores=stores, at=later)


def test_R_15_second_rotation_while_one_is_pending_is_409_and_keeps_both_keys(
    api: Api, dep: dict[str, Any], owner: Operator, wrapper: Any
) -> None:
    """Audit 2026-10-06 R-15: a second rotation (double click, retry) used to promote the
    pending key and drop the one the host still signs with. Now it answers 409
    ``rotation_pending`` until the overlap (billing.yaml fleet.key_rotation_overlap_days, 7)
    has passed; the old key keeps working during the overlap and stops after it."""
    rotate = f"/deployments/{dep['deployment_id']}/heartbeat-key:rotate"
    first = api.call("POST", rotate, owner)
    assert first.status_code == 200, first.text
    new = first.json()
    second = api.call("POST", rotate, owner)
    assert (second.status_code, second.json()["code"]) == (409, "rotation_pending")

    stores = fleet.FleetStores(InMemoryReplayStore(), InMemoryReplayStore())
    overlap = dt.timedelta(days=int(fleet_cfg()["key_rotation_overlap_days"]))
    assert overlap == dt.timedelta(days=7)
    inside = dt.datetime.now(dt.UTC) + overlap - dt.timedelta(hours=1)
    for key, key_id in (
        (dep["heartbeat_key"], dep["heartbeat_key_id"]),
        (new["heartbeat_key"], new["heartbeat_key_id"]),
    ):
        raw, headers = _signed(
            dep, _payload(dep), key=key, key_id=key_id, ts=int(inside.timestamp())
        )
        fleet.verify_heartbeat(headers, raw, wrapper=wrapper, stores=stores, at=inside)
        _next_minute(stores)
    after = dt.datetime.now(dt.UTC) + overlap + dt.timedelta(hours=1)
    old_raw, old_headers = _signed(dep, _payload(dep), ts=int(after.timestamp()))
    with pytest.raises(Unauthenticated):
        fleet.verify_heartbeat(old_headers, old_raw, wrapper=wrapper, stores=stores, at=after)

    # Once the overlap has passed, the next rotation is accepted again (the pending key is
    # promoted first, so the key the host now uses stays valid during the new overlap).
    with platform_session() as s:
        s.execute(
            text(
                "UPDATE platform.deployments SET heartbeat_rotation_started_at = "
                "heartbeat_rotation_started_at - interval '8 days' WHERE id = :d"
            ),
            {"d": dep["deployment_id"]},
        )
    third = api.call("POST", rotate, owner)
    assert third.status_code == 200, third.text
    raw, headers = _signed(
        dep, _payload(dep), key=new["heartbeat_key"], key_id=new["heartbeat_key_id"]
    )
    fleet.verify_heartbeat(headers, raw, wrapper=wrapper, stores=stores)


def test_FR_PLT_025_staleness_marks_unreachable(api: Api, dep: dict[str, Any]) -> None:
    raw, headers = _signed(dep, _payload(dep))
    assert api.client.post(URL, content=raw, headers=headers).status_code == 200
    assert fleet.check_staleness(at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=21)) >= 1
    with platform_session() as s:
        status: Any = s.execute(
            text("SELECT status FROM platform.deployments WHERE id = :d"),
            {"d": dep["deployment_id"]},
        ).scalar_one()
    assert status == "unreachable"


def test_FR_PLT_024_degraded_when_backup_is_stale(api: Api, dep: dict[str, Any]) -> None:
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=30)).isoformat()
    body = _payload(dep, backup={"last_base_backup_at": old, "status": "ok"})
    raw, headers = _signed(dep, body)
    assert api.client.post(URL, content=raw, headers=headers).status_code == 200
    assert fleet.get_deployment(uuid.UUID(dep["deployment_id"])).status == "degraded"


def test_FR_PLT_024_client_builds_a_payload_the_control_plane_accepts(
    api: Api, dep: dict[str, Any]
) -> None:
    host = Settings(
        deployment_mode=DeploymentMode.DEDICATED,
        version="2026.10.3",
        control_plane_url="https://admin.example.test",
        deployment_id=dep["deployment_id"],
        dedicated_tenant_id=dep["tenant_id"],
        heartbeat_key_id=dep["heartbeat_key_id"],
        heartbeat_key=SecretStr(dep["heartbeat_key"]),
    )
    payload = heartbeat_client.build_payload(
        host, checks={"database": lambda: True, "redis": lambda: False}
    )
    assert payload.health.valkey == "down"
    headers, body = heartbeat_client.signed_request(host, payload)
    res = api.client.post(URL, content=body, headers=headers)
    assert res.status_code == 200, res.text
    assert fleet.get_deployment(uuid.UUID(dep["deployment_id"])).app_version == "2026.10.3"


def _string_fields_are_constrained(schema: dict[str, Any], defs: dict[str, Any]) -> list[str]:
    loose: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                walk(defs[node["$ref"].split("/")[-1]], path)
                return
            if node.get("type") == "string" and not (
                {"pattern", "enum", "const", "format"} & set(node)
            ):
                loose.append(path)
            for key in ("properties",):
                for name, sub in node.get(key, {}).items():
                    walk(sub, f"{path}.{name}")
            for key in ("anyOf", "allOf", "oneOf"):
                for sub in node.get(key, []):
                    walk(sub, path)
            for key in ("additionalProperties", "items"):
                if isinstance(node.get(key), dict):
                    walk(node[key], f"{path}[]")

    walk(schema, "heartbeat")
    return loose


def test_SEC_028_heartbeat_schema_has_no_free_text() -> None:
    schema = HeartbeatIn.model_json_schema()
    assert _string_fields_are_constrained(schema, schema.get("$defs", {})) == []


@settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    extra=st.dictionaries(
        st.text(min_size=1, max_size=20).filter(lambda k: k not in HeartbeatIn.model_fields),
        st.one_of(st.text(max_size=30), st.integers(), st.booleans()),
        min_size=1,
        max_size=3,
    )
)
def test_SEC_028_fuzzed_unknown_fields_are_rejected(extra: dict[str, Any]) -> None:
    base = {
        "schema_version": 1,
        "deployment_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "sent_at": "2026-09-26T04:35:00Z",
        "nonce": str(uuid.uuid4()),
        "app_version": "2026.10.1",
        "health": {
            "api": "ok",
            "worker": "ok",
            "beat": "ok",
            "db": "ok",
            "valkey": "ok",
            "s3": "ok",
        },
    }
    HeartbeatIn.model_validate(base)
    with pytest.raises(ValidationError):
        HeartbeatIn.model_validate({**base, **extra})


def _usage_dates(tenant_id: str) -> list[dt.date]:
    with platform_session() as s:
        return list(
            s.execute(
                text("SELECT usage_date FROM platform.usage_daily WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).scalars()
        )


def _with_usage_date(dep: dict[str, Any], day: dt.date) -> dict[str, Any]:
    body = _payload(dep)
    body["usage"]["date"] = day.isoformat()
    return body


def test_AA_09_usage_for_today_and_yesterday_ist_is_recorded(
    api: Api, dep: dict[str, Any], fleet_stores: fleet.FleetStores
) -> None:
    today = today_ist()
    for day in (today - dt.timedelta(days=1), today):
        _next_minute(fleet_stores)
        raw, headers = _signed(dep, _with_usage_date(dep, day))
        assert api.client.post(URL, content=raw, headers=headers).status_code == 200
    assert sorted(_usage_dates(dep["tenant_id"])) == [today - dt.timedelta(days=1), today]


def test_AA_09_older_usage_is_ignored_but_the_heartbeat_counts(
    api: Api, dep: dict[str, Any]
) -> None:
    """A signed heartbeat cannot rewrite usage of days already counted or invoiced."""
    old = today_ist() - dt.timedelta(days=2)
    raw, headers = _signed(dep, _with_usage_date(dep, old))
    res = api.client.post(URL, content=raw, headers=headers)
    assert res.status_code == 200, res.text
    assert _usage_dates(dep["tenant_id"]) == []
    with platform_session() as s:
        seen: dt.datetime | None = s.execute(
            text("SELECT last_heartbeat_at FROM platform.deployments WHERE id = :d"),
            {"d": dep["deployment_id"]},
        ).scalar_one()
    assert seen is not None


def test_AA_09_future_usage_is_refused(api: Api, dep: dict[str, Any]) -> None:
    future = today_ist() + dt.timedelta(days=1)
    raw, headers = _signed(dep, _with_usage_date(dep, future))
    res = api.client.post(URL, content=raw, headers=headers)
    assert (res.status_code, res.json()["code"]) == (422, "usage_date_in_future")
    assert _usage_dates(dep["tenant_id"]) == []
