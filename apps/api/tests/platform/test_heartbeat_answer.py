"""The signed heartbeat answer and what a dedicated host applies from it (ADR-0058, W3-05;
docs/16 §12, §19 Q5, Q7, Q19; FR-PLT-022, FR-PLT-024, FR-PLT-030, FR-KB-011, SEC-028)."""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from app.core.config import DeploymentMode, Settings
from app.core.db import platform_session
from app.core.errors import Unauthenticated
from app.core.logging import ALLOWED_FIELDS
from app.platform import billing, flags, fleet, heartbeat_apply, heartbeat_client, tenant_api
from app.platform.announcements import InMemoryAnnouncementCache
from app.platform.common import SYSTEM
from app.platform.schemas import FlagIn, HbAiAllowance, HeartbeatIn, HeartbeatOut

from .conftest import Api, MakeOperator, Operator, billing_account_payload, provision_payload
from .test_heartbeat import URL, _next_minute, _payload, _signed

pytestmark = pytest.mark.db


def _school(api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]) -> dict[str, Any]:
    body = provision_payload(
        make_plan(tier="dedicated"), tier="dedicated", owner=None, start_as="active"
    )
    body["billing_account"] = billing_account_payload(state_code="37")
    res = api.call("POST", "/tenants", owner, json=body)
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


@pytest.fixture
def school(api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]) -> dict[str, Any]:
    return _school(api, owner, make_plan)


def _host(dep: dict[str, Any], **kw: Any) -> Settings:
    return Settings(
        deployment_mode=DeploymentMode.DEDICATED,
        version="2026.11.1",
        control_plane_url="http://testserver",
        deployment_id=dep["deployment_id"],
        dedicated_tenant_id=dep["tenant_id"],
        heartbeat_key_id=dep["heartbeat_key_id"],
        heartbeat_key=SecretStr(dep["heartbeat_key"]),
        **kw,
    )


def _beat(api: Api, dep: dict[str, Any]) -> tuple[httpx.Response, uuid.UUID]:
    body = _payload(dep)
    raw, headers = _signed(dep, body)
    res = api.client.post(URL, content=raw, headers=headers)
    assert res.status_code == 200, res.text
    return res, uuid.UUID(body["nonce"])


def _bundle_id(code: str) -> uuid.UUID:
    with platform_session() as s:
        return uuid.UUID(
            str(
                s.execute(
                    text(
                        "SELECT id FROM platform.ai_bundles WHERE code = :c AND status = "
                        "'published' ORDER BY version DESC LIMIT 1"
                    ),
                    {"c": code},
                ).scalar_one()
            )
        )


def _flag_key() -> str:
    return f"d79.f{uuid.uuid4().hex[:10]}"


# --- signing (W3-05) ---------------------------------------------------------------------------


def test_ADR_0058_answer_is_signed_with_the_deployment_key_and_verifies_on_the_host(
    api: Api, school: dict[str, Any]
) -> None:
    res, nonce = _beat(api, school)
    assert res.headers[fleet.HEADER_KEY_ID] == school["heartbeat_key_id"]
    assert res.headers[fleet.HEADER_SIGNATURE].startswith("v1=")
    answer = heartbeat_client.verify_answer(
        _host(school), res.headers, res.content, request_nonce=nonce
    )
    assert answer.request_nonce == nonce


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("unsigned", "unsigned"),
        ("tampered", "bad_signature"),
        ("other_key", "bad_signature"),
        ("key_id", "unknown_key"),
        ("stale", "stale"),
        ("bad_timestamp", "bad_timestamp"),
        ("nonce", "nonce_mismatch"),
    ],
)
def test_ADR_0058_host_refuses_unsigned_wrong_or_stale_answers(
    api: Api, school: dict[str, Any], change: str, reason: str
) -> None:
    res, nonce = _beat(api, school)
    headers = dict(res.headers)
    body = res.content
    at: float | None = None
    if change == "unsigned":
        headers.pop(fleet.HEADER_SIGNATURE.lower(), None)
        headers.pop(fleet.HEADER_SIGNATURE_V2.lower(), None)
    elif change == "tampered":
        data = json.loads(body)
        data["feature_flags"] = {"ask.enabled": True}
        body = json.dumps(data).encode()
    elif change == "other_key":
        other = heartbeat_client.decode_key("A" * 43)
        stamp = headers[fleet.HEADER_TIMESTAMP.lower()]
        headers[fleet.HEADER_SIGNATURE.lower()] = fleet.sign(other, stamp, body)
        headers[fleet.HEADER_SIGNATURE_V2.lower()] = fleet.sign_v2(other, fleet.ANSWER, stamp, body)
    elif change == "key_id":
        headers[fleet.HEADER_KEY_ID.lower()] = "hk_other"
    elif change == "stale":
        at = time.time() + 3600
    elif change == "bad_timestamp":
        headers[fleet.HEADER_TIMESTAMP.lower()] = "-1"
    else:
        nonce = uuid.uuid4()
    with pytest.raises(heartbeat_client.AnswerRejected) as caught:
        heartbeat_client.verify_answer(_host(school), headers, body, request_nonce=nonce, now=at)
    assert caught.value.reason == reason


def test_SEC_028_CP_03_requests_and_answers_carry_a_direction_labelled_v2_signature(
    api: Api, school: dict[str, Any], fleet_stores: fleet.FleetStores, wrapper: Any
) -> None:
    """ADR-0058 amendment (audit 2026-11-08 CP-03): both directions send ``X-SOS-Signature-V2``
    (``req.`` / ``ans.`` label) next to v1. A verifier checks v2 when present and never falls
    back to v1 when v2 is wrong; v1-only counts only while
    ``SOS_HEARTBEAT_ACCEPT_V1_SIGNATURE`` (default true). A signed request reflected as an
    answer, or the reverse, fails whatever the schemas allow."""
    host = _host(school)
    key = heartbeat_client.decode_key(school["heartbeat_key"])
    payload = HeartbeatIn.model_validate(_payload(school))
    headers, raw = heartbeat_client.signed_request(host, payload)
    stamp = headers[fleet.HEADER_TIMESTAMP]
    assert headers[fleet.HEADER_SIGNATURE] == fleet.sign(key, stamp, raw)
    assert headers[fleet.HEADER_SIGNATURE_V2] == fleet.sign_v2(key, fleet.REQUEST, stamp, raw)
    # A request signed with the answer label is refused, even with a valid v1 next to it.
    wrong = {**headers, fleet.HEADER_SIGNATURE_V2: fleet.sign_v2(key, fleet.ANSWER, stamp, raw)}
    with pytest.raises(Unauthenticated):
        fleet.verify_heartbeat(wrong, raw, wrapper=wrapper, stores=fleet_stores)
    # v1 only: accepted during the rollout, refused once v1 is switched off.
    v1_only = {k: v for k, v in headers.items() if k != fleet.HEADER_SIGNATURE_V2}
    with pytest.raises(Unauthenticated):
        fleet.verify_heartbeat(v1_only, raw, wrapper=wrapper, stores=fleet_stores, accept_v1=False)
    fleet.verify_heartbeat(v1_only, raw, wrapper=wrapper, stores=fleet_stores)
    _next_minute(fleet_stores)

    res, nonce = _beat(api, school)
    stamp = res.headers[fleet.HEADER_TIMESTAMP]
    assert res.headers[fleet.HEADER_SIGNATURE_V2] == fleet.sign_v2(
        key, fleet.ANSWER, stamp, res.content
    )
    strict = _host(school, heartbeat_accept_v1_signature=False)
    assert heartbeat_client.verify_answer(strict, res.headers, res.content, request_nonce=nonce)
    # An answer signed with the request label (a reflected request) is refused.
    reflected = dict(res.headers)
    reflected[fleet.HEADER_SIGNATURE_V2.lower()] = fleet.sign_v2(
        key, fleet.REQUEST, stamp, res.content
    )
    with pytest.raises(heartbeat_client.AnswerRejected) as caught:
        heartbeat_client.verify_answer(host, reflected, res.content, request_nonce=nonce)
    assert caught.value.reason == "bad_signature"
    # An answer from a control plane on the older release (v1 only).
    old = {k: v for k, v in res.headers.items() if k.lower() != fleet.HEADER_SIGNATURE_V2.lower()}
    assert heartbeat_client.verify_answer(host, old, res.content, request_nonce=nonce)
    with pytest.raises(heartbeat_client.AnswerRejected) as caught:
        heartbeat_client.verify_answer(strict, old, res.content, request_nonce=nonce)
    assert caught.value.reason == "bad_signature"


def test_ADR_0058_a_refused_answer_applies_nothing_and_keeps_the_last_good_state(
    api: Api, school: dict[str, Any], fleet_stores: fleet.FleetStores
) -> None:
    host = _host(school)
    store = heartbeat_apply.InMemoryBillingSummaryStore()
    cache = InMemoryAnnouncementCache()
    res, nonce = _beat(api, school)
    good = heartbeat_client.handle_answer(
        host, res.headers, res.content, request_nonce=nonce, store=store, cache=cache
    )
    assert good["answer"] == "verified"
    before = (store.get(), cache.get())
    _next_minute(fleet_stores)
    res, nonce = _beat(api, school)
    headers = dict(res.headers)
    headers[fleet.HEADER_SIGNATURE.lower()] = "v1=" + "0" * 64
    headers[fleet.HEADER_SIGNATURE_V2.lower()] = "v2=" + "0" * 64
    with capture_logs() as logs:
        out = heartbeat_client.handle_answer(
            host, headers, res.content, request_nonce=nonce, store=store, cache=cache
        )
    assert out == {"status": 200, "answer": "rejected", "reason": "bad_signature"}
    assert (store.get(), cache.get()) == before
    (event,) = [e for e in logs if e["event"] == "fleet.heartbeat.answer_rejected"]
    assert event["error_code"] == "bad_signature"
    assert set(event) - {"event", "log_level"} <= set(ALLOWED_FIELDS)
    # Nothing secret reaches the log line.
    assert school["heartbeat_key"] not in json.dumps(logs, default=str)


def test_ADR_0058_unsigned_answer_only_with_the_one_release_setting_and_announcements_only(
    api: Api, school: dict[str, Any]
) -> None:
    res, nonce = _beat(api, school)
    headers = {k: v for k, v in res.headers.items() if not k.lower().startswith("x-sos-signature")}
    store = heartbeat_apply.InMemoryBillingSummaryStore()
    cache = InMemoryAnnouncementCache()
    strict = heartbeat_client.handle_answer(
        _host(school), headers, res.content, request_nonce=nonce, store=store, cache=cache
    )
    assert strict["answer"] == "rejected"
    assert cache.get() is None
    legacy = heartbeat_client.handle_answer(
        _host(school, heartbeat_answer_unsigned_ok=True),
        headers,
        res.content,
        request_nonce=nonce,
        store=store,
        cache=cache,
    )
    assert legacy["answer"] == "unsigned"
    assert cache.get() is not None
    assert store.get() is None  # no billing summary, flags or allowance from an unsigned answer


# --- what the answer carries ----------------------------------------------------------------


def test_ADR_0058_answer_carries_flags_allowance_and_billing_summary(
    api: Api, school: dict[str, Any], make_operator: MakeOperator
) -> None:
    tid = uuid.UUID(school["tenant_id"])
    on, off = _flag_key(), _flag_key()
    flags.set_global(SYSTEM, on, FlagIn(enabled=True))
    flags.set_global(SYSTEM, off, FlagIn(enabled=True))
    flags.set_override(SYSTEM, off, tid, False)
    sub = uuid.UUID(school["subscription_id"])
    billing.set_ai_bundle(make_operator("billing_admin").actor, sub, _bundle_id("ai-lite"))
    res, nonce = _beat(api, school)
    answer = heartbeat_client.verify_answer(
        _host(school), res.headers, res.content, request_nonce=nonce
    )
    assert answer.feature_flags[on] is True
    assert answer.feature_flags[off] is False
    with platform_session() as s:
        included, price = s.execute(
            text("SELECT included_answers, price_inr FROM platform.ai_bundles WHERE id = :i"),
            {"i": _bundle_id("ai-lite")},
        ).one()
    assert answer.ai_allowance == HbAiAllowance(included_answers=included)
    summary = answer.billing
    assert summary is not None
    assert (summary.plan_name, summary.status, summary.tier) == (
        "Synthetic Standard",
        "active",
        "dedicated",
    )
    assert summary.next_invoice_on == summary.current_period_end
    # Intra-state (37 -> 37): plan 5,000 + bundle, 18 % GST.
    expected = ((Decimal("5000.00") + Decimal(price)) * Decimal("1.18")).quantize(Decimal("0.01"))
    assert summary.next_invoice_amount_inr == expected


FORBIDDEN = ("student", "guardian", "aadhaar", "dob", "birth", "phone", "email", "address")


def _field_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for node in [schema, *schema.get("$defs", {}).values()]:
        names |= set(node.get("properties", {}))
    return names


def test_ADR_0058_answer_has_no_student_or_personal_data_fields() -> None:
    names = _field_names(HeartbeatOut.model_json_schema())
    assert not [n for n in names if any(word in n.lower() for word in FORBIDDEN)]
    assert {"feature_flags", "ai_allowance", "billing", "request_nonce"} <= names


# --- applying it on the host ------------------------------------------------------------------


def _audit_actions(tid: uuid.UUID, key: str) -> list[tuple[str, str | None]]:
    with platform_session() as s:
        rows = s.execute(
            text(
                "SELECT action, summary->>'source' FROM platform.audit_events "
                "WHERE subject_tenant_id = :t AND summary->>'key' = :k ORDER BY seq"
            ),
            {"t": tid, "k": key},
        ).all()
    return [(r[0], r[1]) for r in rows]


def _school_allowance_audits(admin: Engine, tid: uuid.UUID) -> list[Any]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT summary FROM audit.events WHERE tenant_id = :t "
                    "AND action = 'tenant.ai_allowance_set' ORDER BY occurred_at, id"
                ),
                {"t": tid},
            ).scalars()
        )


@pytest.fixture
def local_school(api: Api, owner: Operator, make_plan: Callable[..., uuid.UUID]) -> uuid.UUID:
    """A school with a local ``core.tenants`` row: the database plays the dedicated host here
    (a dedicated school provisioned on the control plane has no tenant row there)."""
    res = api.call("POST", "/tenants", owner, json=provision_payload(make_plan()))
    assert res.status_code == 201, res.text
    return uuid.UUID(res.json()["tenant_id"])


def test_ADR_0058_apply_is_idempotent_and_audited(
    local_school: uuid.UUID, admin_engine: Engine
) -> None:
    tid = local_school
    key, gone = _flag_key(), _flag_key()
    flags.apply_deployment_flags(tid, {gone: True})
    answer = HeartbeatOut(
        received_at=dt.datetime.now(dt.UTC),
        target_version=None,
        min_supported_version="2026.1.0",
        announcements=[],
        request_nonce=uuid.uuid4(),
        feature_flags={key: True},
        ai_allowance=HbAiAllowance(included_answers=250),
        billing=None,
    )
    store = heartbeat_apply.InMemoryBillingSummaryStore()
    first = heartbeat_apply.apply_answer(tid, answer, store=store)
    second = heartbeat_apply.apply_answer(tid, answer, store=store)
    assert (first["flags_changed"], first["allowance_changed"]) == (2, True)
    assert (second["flags_changed"], second["allowance_changed"]) == (0, False)
    assert flags.is_enabled(key, tid) is True
    assert flags.is_enabled(gone, tid) is False
    assert _audit_actions(tid, key) == [("flag.override_set", "heartbeat")]
    assert _audit_actions(tid, gone) == [
        ("flag.override_set", "heartbeat"),
        ("flag.override_removed", "heartbeat"),
    ]
    assert [dict(s)["included_answers"] for s in _school_allowance_audits(admin_engine, tid)] == [
        250
    ]
    assert store.get() == {"as_of": answer.received_at.isoformat(), "summary": None}


def test_ADR_0058_one_failing_step_keeps_the_others(
    school: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tenancy import service as tenancy_service

    def down(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("synthetic outage")

    monkeypatch.setattr(tenancy_service, "set_ai_answer_allowance", down)
    tid = uuid.UUID(school["tenant_id"])
    key = _flag_key()
    answer = HeartbeatOut(
        received_at=dt.datetime.now(dt.UTC),
        target_version=None,
        min_supported_version="2026.1.0",
        announcements=[],
        feature_flags={key: True},
        ai_allowance=HbAiAllowance(included_answers=100),
    )
    with capture_logs() as logs:
        out = heartbeat_apply.apply_answer(
            tid, answer, store=heartbeat_apply.InMemoryBillingSummaryStore()
        )
    assert out == {"flags_changed": 1, "allowance_changed": None, "billing": "stored"}
    (failed,) = [e for e in logs if e["event"] == "fleet.heartbeat.apply_failed"]
    assert failed["action"] == "ai_allowance"
    assert set(failed) - {"event", "log_level"} <= set(ALLOWED_FIELDS)


def test_ADR_0058_send_round_trip_applies_the_answer_and_plan_and_billing_shows_it(
    api: Api, school: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host's send() against the control plane, then GET /tenant/billing's dedicated path."""
    tid = uuid.UUID(school["tenant_id"])
    key = _flag_key()
    flags.set_global(SYSTEM, key, FlagIn(enabled=True))
    store = heartbeat_apply.InMemoryBillingSummaryStore()
    monkeypatch.setattr(heartbeat_apply, "get_billing_store", lambda: store)
    host = _host(school)
    out = heartbeat_client.send(host, client=api.client, include_usage=False)
    assert out["answer"] == "verified"
    with platform_session() as s:
        override: Any = s.execute(
            text("SELECT enabled FROM platform.feature_flags WHERE key = :k AND tenant_id = :t"),
            {"k": key, "t": tid},
        ).scalar_one()
    assert override is True
    monkeypatch.setattr(tenant_api, "get_settings", lambda: host)
    shown = tenant_api._dedicated_billing()
    assert shown is not None
    assert shown.available is True
    assert shown.plan_name == "Synthetic Standard"
    assert shown.summary_as_of is not None
    assert shown.next_invoice_on is not None
    assert shown.usage == []
