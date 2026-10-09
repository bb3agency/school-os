"""A per-school budget on staff invitation emails (hardening note in
docs/security/audit-2026-10-04-api-auth.md; SEC-007, OWASP API4:2023, CWE-799).

``POST /users/{id}/invitation-email`` had only a per-membership cool-down that fails open
without Valkey, and nothing capped a whole school: one insider holding ``user.manage`` (with
step-up) could invite or re-invite many addresses. Both invitation routes now share the
``school_invitations`` budget (``per: school``, ``fail: closed``): it is charged by the route
guard only after authorization (``ratelimit.charge_school_routes``), so members refused with 403
or 428 spend nothing; calls over it get 429 ``rate_limited`` with Retry-After; while Valkey is
unreachable the per-process fallback keeps counting. The rate-limit clock is frozen.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core import ratelimit as rl

from .conftest import W

pytestmark = pytest.mark.db

USERS = "/api/v1/users"
POLICY = "school_invitations"
SMALL = 3  # a small school quota for the test; the shipped value is pinned below


class Clock:
    def __init__(self) -> None:
        self.ms = 7_000_000.0

    def __call__(self) -> float:
        return self.ms

    def advance(self, seconds: float) -> None:
        self.ms += seconds * 1000


class DownStore:
    """Valkey unreachable."""

    def acquire(self, blocks: Sequence[str], buckets: Sequence[rl.BucketSpec]) -> rl.RawDecision:
        raise rl.StoreUnavailable("down")

    def fail(self, count_key: str, block_key: str, backoff: rl.Backoff) -> tuple[int, int]:
        raise rl.StoreUnavailable("down")

    def clear(self, *keys: str) -> None:
        raise rl.StoreUnavailable("down")


def _config(quota: int = SMALL) -> rl.RateLimitConfig:
    config = rl.load_config()
    policies = dict(config.policies)
    policies[POLICY] = policies[POLICY].model_copy(update={"quota": quota})
    return config.model_copy(update={"policies": policies})


@pytest.fixture
def clock() -> Iterator[Clock]:
    now = Clock()
    rl.set_rate_limiter(
        rl.RateLimiter(_config(), rl.InMemoryRateLimitStore(clock=now), hash_key=b"synthetic-key")
    )
    yield now
    rl.set_rate_limiter(None)


def _school(admin: Engine, *roles: str) -> dict[str, Any]:
    school = W.School(W.provision_school())
    people = {"owner": W.add_member(admin, school.tenant_id, ["owner"])}
    people.update({r: W.add_member(admin, school.tenant_id, [r]) for r in roles})
    return people


def _body() -> dict[str, Any]:
    return {
        "idp_subject": f"sub-{uuid.uuid4().hex}",
        "display_name": "Synthetic Invitee",
        "email": f"invitee.{uuid.uuid4().hex[:10]}@example.test",
        "roles": ["teacher"],
        "scopes": [],
    }


def _assert_rate_limited(res: Any) -> None:
    assert (res.status_code, res.json()["code"]) == (429, "rate_limited"), res.text
    assert res.headers["content-type"].startswith("application/problem+json")
    assert int(res.headers["Retry-After"]) > 0
    assert f'"{POLICY}";q={SMALL};' in res.headers["RateLimit-Policy"]


def test_SEC_007_school_invitations_budget_is_versioned_and_on_both_invitation_routes() -> None:
    config = rl.load_config()
    policy = config.policies[POLICY]
    assert (policy.per, policy.fail, policy.quota, policy.window_s) == (
        "school",
        "closed",
        200,
        86_400,
    )
    for route in (f"POST {USERS}", f"POST {USERS}/{{user_id}}/invitation-email"):
        assert POLICY in config.routes[route], route
        assert "invitations" in config.routes[route], "the per-person budget stays"


def test_SEC_007_refused_members_cannot_spend_the_schools_invitation_budget(
    admin_engine: Engine, api: Any, clock: Clock
) -> None:
    people = _school(admin_engine, "teacher", "office_admin")
    owner, office_admin = people["owner"], people["office_admin"]
    for _ in range(SMALL + 2):
        assert api.call(people["teacher"], "POST", USERS, json=_body()).status_code == 403
        res = api.call(owner, "POST", USERS, json=_body(), auth_age_s=3600)
        assert (res.status_code, res.json()["code"]) == (428, "step_up_required"), res.text

    first = api.call(owner, "POST", USERS, json=_body())
    assert first.status_code == 201, first.text
    assert f'"{POLICY}";q={SMALL};w=86400' in first.headers["RateLimit-Policy"]
    # Shared by the school's admins and by both routes (invite and resend).
    assert api.call(office_admin, "POST", USERS, json=_body()).status_code == 201
    resend = api.call(owner, "POST", f"{USERS}/{first.json()['id']}/invitation-email")
    assert resend.status_code != 429, resend.text  # 409 email_disabled here; it still counts
    _assert_rate_limited(api.call(office_admin, "POST", USERS, json=_body()))
    _assert_rate_limited(api.call(owner, "POST", f"{USERS}/{first.json()['id']}/invitation-email"))
    # Authorization still comes first.
    assert api.call(people["teacher"], "POST", USERS, json=_body()).status_code == 403

    # Another school is not affected; the budget refills over its window.
    other = _school(admin_engine)
    assert api.call(other["owner"], "POST", USERS, json=_body()).status_code == 201
    clock.advance(rl.load_config().policies[POLICY].window_s)
    assert api.call(owner, "POST", USERS, json=_body()).status_code == 201


def test_SEC_007_school_invitations_budget_fails_closed_while_valkey_is_down(
    admin_engine: Engine, api: Any
) -> None:
    rl.set_rate_limiter(
        rl.RateLimiter(
            _config(),
            DownStore(),
            fallback=rl.InMemoryRateLimitStore(clock=Clock()),
            hash_key=b"synthetic-key",
        )
    )
    try:
        people = _school(admin_engine, "office_admin")
        codes = [
            api.call(people[who], "POST", USERS, json=_body()).status_code
            for who in ("owner", "office_admin", "owner", "office_admin")
        ]
    finally:
        rl.set_rate_limiter(None)
    assert codes == [201] * SMALL + [429]
