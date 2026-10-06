"""``/audit/verify`` serves the stored result; re-verifying is a queued job with a cool-down
(audit 2026-10-06 R-19; US-1001 AC2, FR-AUD-004, SEC-007; OWASP API4, CWE-400).

- ``GET /audit/verify`` no longer re-hashes the chain: it returns the latest stored result of
  the daily job or an on-demand run (``verified_at`` null before the first run).
- ``POST /audit/verify`` queues one run (202) and is limited to 1 per school per 10 minutes
  (``audit_verify`` in app/core/rate_limits.yaml): 429 ``rate_limited`` with Retry-After and the
  RateLimit headers. The rate-limit clock is frozen (time-sensitive).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine

from app.audit import service as audit_service
from app.audit import verification
from app.core import ratelimit as rl

from .conftest import W

pytestmark = pytest.mark.db

URL = "/api/v1/audit/verify"


class Clock:
    def __init__(self) -> None:
        self.ms = 7_000_000.0

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


def _school(admin: Engine) -> tuple[uuid.UUID, Any]:
    """A fresh school with an owner (its own chain and its own rate-limit partition)."""
    tenant_id = W.provision_school()
    owner = W.add_member(admin, tenant_id, ["owner"])
    return tenant_id, owner


def test_R_19_get_serves_the_stored_result_and_post_queues_a_run(
    admin_engine: Engine, api: Any, clock: Clock
) -> None:
    tenant_id, owner = _school(admin_engine)
    before = api.call(owner, "GET", URL)
    assert before.status_code == 200, before.text
    assert (before.json()["verified_at"], before.json()["ok"], before.json()["pending"]) == (
        None,
        None,
        False,
    )

    queued = api.call(owner, "POST", URL, json={})
    assert queued.status_code == 202, queued.text
    assert queued.json()["pending"] is True
    assert queued.headers["Location"] == URL

    # The cool-down: one request per school per 10 minutes, with the RateLimit headers.
    again = api.call(owner, "POST", URL, json={"full": True})
    assert (again.status_code, again.json()["code"]) == (429, "rate_limited")
    assert int(again.headers["Retry-After"]) > 0
    assert '"audit_verify";q=1;w=600' in again.headers["RateLimit-Policy"]
    assert "RateLimit" in again.headers

    # The worker runs the queued job; GET now shows the stored result without re-hashing.
    done = verification.run_requested(tenant_id)
    assert (done.ok, done.pending) == (True, False)
    after = api.call(owner, "GET", URL).json()
    assert after["ok"] is True
    assert after["checked"] > 0
    assert after["first_bad_seq"] is None
    assert after["verified_at"] is not None
    assert after["checkpoint_seq"] > 0
    assert (after["source"], after["pending"]) == ("on_demand", False)

    clock.advance(601)
    assert api.call(owner, "POST", URL, json={"full": True}).status_code == 202


def test_R_19_get_never_rehashes(
    admin_engine: Engine, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, owner = _school(admin_engine)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("GET /audit/verify must not verify the chain")

    monkeypatch.setattr(verification, "run", boom)
    monkeypatch.setattr(audit_service, "verify_chain", boom)
    for _ in range(3):
        assert api.call(owner, "GET", URL).status_code == 200


def test_R_19_post_needs_audit_read_and_rejects_extra_fields(world: Any, api: Any) -> None:
    for role in ("office_staff", "teacher", "accountant"):
        assert api.call(world.person(role), "POST", URL, json={}).status_code == 403
    bad = api.call(world.person("owner"), "POST", URL, json={"full": False, "from_seq": 1})
    assert bad.status_code == 422


def test_R_19_refused_members_cannot_spend_the_school_cool_down(
    admin_engine: Engine, api: Any, clock: Clock
) -> None:
    """The per-school budget is charged after the permission check: staff without audit.read
    get 403 and leave the cool-down to the people who may verify."""
    tenant_id, owner = _school(admin_engine)
    clerk = W.add_member(admin_engine, tenant_id, ["office_staff"])
    for _ in range(3):
        assert api.call(clerk, "POST", URL, json={}).status_code == 403
    assert api.call(owner, "POST", URL, json={}).status_code == 202
