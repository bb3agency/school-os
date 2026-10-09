"""Rate limits and sign-in backoff on the real API routes (P2-07; ASVS 2.2.1; docs/09 §2.7).

The tests swap the process-wide limiter for an in-memory one on a test clock, so the backoff can be
waited out without sleeping. Limiting stays on for the whole suite (tests/core/test_ratelimit.py).
Synthetic schools and people only.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core import ratelimit as rl
from app.core.config import Environment, Settings
from app.core.logging import ALLOWED_FIELDS, clear_context, setup_logging

W = sys.modules["sos_test_api_world"]


class Clock:
    def __init__(self) -> None:
        self.ms = 5_000_000.0

    def __call__(self) -> float:
        return self.ms

    def advance(self, seconds: float) -> None:
        self.ms += seconds * 1000


@pytest.fixture
def clock() -> Iterator[Clock]:
    now = Clock()
    rl.set_rate_limiter(
        rl.RateLimiter(
            rl.load_config(), rl.InMemoryRateLimitStore(clock=now), hash_key=b"synthetic-key"
        )
    )
    yield now
    rl.set_rate_limiter(None)


@pytest.fixture
def lines() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    setup_logging(Settings(env=Environment.CI, log_level="INFO"), stream=stream)
    clear_context()
    yield stream
    clear_context()
    setup_logging(Settings(env=Environment.CI, log_level="INFO"))


def events(stream: io.StringIO, name: str) -> list[dict[str, Any]]:
    out = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    return [line for line in out if line.get("event") == name]


def test_P2_07_tenant_routes_carry_ratelimit_headers(world: Any, api: Any) -> None:
    res = api.call(world.person("owner"), "GET", "/api/v1/me")
    assert res.status_code == 200
    assert res.headers["RateLimit-Policy"] == '"user";q=600;w=60'
    assert res.headers["RateLimit"].startswith('"user";r=599;t=')
    assert "school" not in res.headers["RateLimit"]


def test_P2_07_route_budget_refuses_with_problem_and_retry_after(
    world: Any, api: Any, clock: Clock
) -> None:
    who = world.person("owner")
    body = {"query": "synthetic"}
    codes = [
        api.call(who, "POST", "/api/v1/students/search", json=body).status_code for _ in range(61)
    ]
    assert codes[-1] == 429
    assert 429 not in codes[:-1]
    refused = api.call(who, "POST", "/api/v1/students/search", json=body)
    assert refused.status_code == 429
    assert refused.json()["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1
    assert '"search";q=60;w=60' in refused.headers["RateLimit-Policy"]
    # Other people in the same school keep their own search budget.
    other = api.call(world.person("principal"), "POST", "/api/v1/students/search", json=body)
    assert other.status_code != 429
    clock.advance(2)
    assert api.call(who, "POST", "/api/v1/students/search", json=body).status_code != 429


def test_ASVS_2_2_1_refused_sign_ins_back_off_then_a_correct_sign_in_works(
    world: Any, api: Any, admin_engine: Engine, clock: Clock, lines: io.StringIO
) -> None:
    """A principal without MFA is refused at login-event (auth.login.denied). After the free
    failures the person waits from this address with a growing delay (soft lockout, no hard
    lockout); once the delay is over a correct sign-in succeeds and clears the count."""
    person = W.add_member(admin_engine, world.a.tenant_id, ["principal"])
    backoff = rl.load_config().auth_failures.subject_ip
    refused = [
        api.call(person, "POST", "/api/v1/me/login-event", mfa=False).status_code
        for _ in range(backoff.free_failures + 1)
    ]
    assert refused == [403] * (backoff.free_failures + 1)
    waiting = api.call(person, "POST", "/api/v1/me/login-event", mfa=True)
    assert waiting.status_code == 429
    assert waiting.headers["Retry-After"] == str(backoff.base_delay_s)
    assert waiting.json()["code"] == "rate_limited"

    failed = events(lines, "security.auth.failed")
    assert len(failed) == backoff.free_failures + 1
    assert {line["error_code"] for line in failed} == {"mfa_required"}
    assert failed[-1]["retry_after_s"] == backoff.base_delay_s
    for line in failed:
        assert set(line) <= set(ALLOWED_FIELDS)
        assert len(line["ip_hash"]) == 16
    assert events(lines, "security.rate_limited")[-1]["policy"] == "auth_backoff"

    clock.advance(backoff.base_delay_s + 1)
    ok = api.call(person, "POST", "/api/v1/me/login-event", mfa=True)
    assert ok.status_code == 200, ok.text
    # Cleared: the next refusal starts again from the first free failure.
    assert api.call(person, "POST", "/api/v1/me/login-event", mfa=False).status_code == 403
    assert api.call(person, "POST", "/api/v1/me/login-event", mfa=True).status_code == 200


def test_ASVS_2_2_1_backoff_is_per_person_not_a_lockout_of_others(
    world: Any, api: Any, admin_engine: Engine, clock: Clock
) -> None:
    victim = W.add_member(admin_engine, world.a.tenant_id, ["principal"])
    other = W.add_member(admin_engine, world.a.tenant_id, ["principal"])
    free = rl.load_config().auth_failures.subject_ip.free_failures
    for _ in range(free + 1):
        api.call(victim, "POST", "/api/v1/me/login-event", mfa=False)
    assert api.call(victim, "POST", "/api/v1/me/login-event").status_code == 429
    assert api.call(other, "POST", "/api/v1/me/login-event").status_code == 200
