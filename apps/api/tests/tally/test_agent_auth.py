"""Edge-agent request signing and the two guards (ADR-0032 §3; FR-TALLY-002; SEC-003).

The signing vector below is ALSO pinned in the agent package
(``apps/edge-agent/tests/test_signing.py``): server and agent build the same canonical string.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac

import pytest

from app.core.errors import Unauthenticated
from app.tally import agent_auth
from app.tally.api import agent_router, router

SECRET = bytes(range(32))
TIMESTAMP = "1790000000"
NONCE = "nonce-0123456789abcdef"
SYNC_BODY = b'{"batch_id":"x"}'
VECTORS = (
    (
        "POST",
        "/api/v1/edge/tally/syncs",
        SYNC_BODY,
        "v1=9d04c53f43df1df7bcf0e13794a2cde9105e51c739d538917d55cfa59403b8fd",
    ),
    (
        "GET",
        "/api/v1/edge/tally/config",
        b"",
        "v1=f701e185f20469cc0b9f65d4bceb6c68fc73e7c322ded938f475bc2c0b024dbd",
    ),
)


@pytest.mark.parametrize(("method", "path", "body", "expected"), VECTORS)
def test_ADR_0032_signing_vector_is_pinned(
    method: str, path: str, body: bytes, expected: str
) -> None:
    got = agent_auth.sign(
        SECRET, method=method, path=path, timestamp=TIMESTAMP, nonce=NONCE, body=body
    )
    assert got == expected


def test_ADR_0032_canonical_string_is_scheme_method_path_time_nonce_body_digest() -> None:
    digest = hashlib.sha256(SYNC_BODY).hexdigest()
    assert (
        agent_auth.canonical("post", "/api/v1/edge/tally/syncs", TIMESTAMP, NONCE, SYNC_BODY)
        == (
            f"SOS-EDGE-HMAC-SHA256\nPOST\n/api/v1/edge/tally/syncs\n{TIMESTAMP}\n{NONCE}\n{digest}"
        ).encode()
    )
    manual = hmac.new(
        SECRET,
        agent_auth.canonical("POST", "/api/v1/edge/tally/syncs", TIMESTAMP, NONCE, SYNC_BODY),
        hashlib.sha256,
    ).hexdigest()
    assert VECTORS[0][3] == f"v1={manual}"


def test_ADR_0032_every_part_of_the_request_is_signed() -> None:
    base = {
        "method": "POST",
        "path": "/api/v1/edge/tally/syncs",
        "timestamp": TIMESTAMP,
        "nonce": NONCE,
        "body": SYNC_BODY,
    }
    reference = agent_auth.sign(SECRET, **base)  # type: ignore[arg-type]
    changes = {
        "method": "PUT",
        "path": "/api/v1/edge/tally/catalog",
        "timestamp": "1790000001",
        "nonce": "nonce-0123456789abcdeX",
        "body": b'{"batch_id":"y"}',
    }
    for key, value in changes.items():
        assert agent_auth.sign(SECRET, **{**base, key: value}) != reference, key  # type: ignore[arg-type]
    assert agent_auth.sign(bytes(32), **base) != reference  # type: ignore[arg-type]


def test_SEC_003_agent_guards_carry_a_pseudo_permission_and_their_kind() -> None:
    enrol = agent_auth.require_edge_agent_enrolment()
    signed = agent_auth.require_edge_agent_signature("sync")
    assert (enrol.sos_permission, enrol.sos_edge_agent) == ("tally.agent", "enrolment")
    assert (signed.sos_permission, signed.sos_edge_agent) == ("tally.agent", "signature")
    assert enrol.sos_step_up is False
    assert signed.sos_step_up is False


def test_SEC_003_staff_and_agent_routers_are_separate() -> None:
    assert all(r.path.startswith("/api/v1/tally/") for r in router.routes)  # type: ignore[attr-defined]
    assert all(r.path.startswith("/api/v1/edge/tally/") for r in agent_router.routes)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({}, "bad_timestamp"),
        ({"X-SOS-Timestamp": "abc"}, "bad_timestamp"),
        ({"X-SOS-Timestamp": "1000"}, "clock_skew"),
        ({"X-SOS-Nonce": "short"}, "bad_nonce"),
        ({"X-SOS-Nonce": "has spaces in the nonce!!"}, "bad_nonce"),
    ],
)
def test_FR_TALLY_002_time_and_nonce_are_checked(headers: dict[str, str], reason: str) -> None:
    now = dt.datetime.fromtimestamp(int(TIMESTAMP), dt.UTC)
    full = {"X-SOS-Timestamp": TIMESTAMP, "X-SOS-Nonce": NONCE}
    if headers == {}:
        full = {}
    else:
        full.update(headers)
    with pytest.raises(Unauthenticated):
        agent_auth._check_time_and_nonce(full, now)
    assert reason  # the reason code is logged, never returned


def test_FR_TALLY_002_the_skew_window_is_inclusive_of_five_minutes() -> None:
    now = dt.datetime.fromtimestamp(int(TIMESTAMP) + 300, dt.UTC)
    ts, nonce = agent_auth._check_time_and_nonce(
        {"X-SOS-Timestamp": TIMESTAMP, "X-SOS-Nonce": NONCE}, now
    )
    assert (ts, nonce) == (TIMESTAMP, NONCE)
    with pytest.raises(Unauthenticated):
        agent_auth._check_time_and_nonce(
            {"X-SOS-Timestamp": TIMESTAMP, "X-SOS-Nonce": NONCE}, now + dt.timedelta(seconds=1)
        )
