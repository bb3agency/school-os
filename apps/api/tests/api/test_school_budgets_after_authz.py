"""Per-school route budgets are charged only after authorization (owner decision 2026-10-07;
hardening note in docs/security/audit-2026-10-06-api-routes.md; OWASP API4:2023, CWE-400).

A route budget shared by the whole school (``per: school`` under ``routes:`` in
app/core/rate_limits.yaml: ``data_export``, ``heavy_jobs``) used to be charged by the route guard
before the permission check, so a member refused with 403 could spend the school's budget and
lock out the people allowed to use the route. The guards now charge those budgets last
(``ratelimit.charge`` after permission, scope, step-up and break-glass checks), the same as the
``audit_verify`` cool-down (R-19). Per-person and per-IP layers still run before authorization.

For each moved policy: a role without the permission goes over the quota and gets 403 every
time; a permitted member then still succeeds; permitted calls over the quota get 429
``rate_limited`` with Retry-After and the RateLimit headers. The rate-limit clock is frozen.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core import ratelimit as rl

from .conftest import W

pytestmark = pytest.mark.db

EXPORT = "/api/v1/admin/tenant-export"
DQ_RUNS = "/api/v1/dq/runs"


class Clock:
    def __init__(self) -> None:
        self.ms = 9_000_000.0

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


def _quota(name: str) -> int:
    return rl.load_config().policies[name].quota


def _school(admin: Engine, *roles: str) -> tuple[Any, dict[str, Any]]:
    """A fresh school (its own rate-limit partition) with an owner and one member per role."""
    school = W.School(W.provision_school())
    owner = W.add_member(admin, school.tenant_id, ["owner"])
    W.build_structure(school, owner, sections=False)
    people = {"owner": owner}
    people.update({r: W.add_member(admin, school.tenant_id, [r]) for r in roles})
    return school, people


def _assert_rate_limited(res: Any, policy: str) -> None:
    assert (res.status_code, res.json()["code"]) == (429, "rate_limited"), res.text
    assert res.headers["content-type"].startswith("application/problem+json")
    assert int(res.headers["Retry-After"]) > 0
    p = rl.load_config().policies[policy]
    assert f'"{policy}";q={p.quota};w={p.window_s}' in res.headers["RateLimit-Policy"]
    assert "RateLimit" in res.headers


def test_school_budgets_moved_after_authz_are_the_per_school_route_policies() -> None:
    """Every ``per: school`` policy named under ``routes:`` is one the guards charge after
    authorization; ``audit_verify`` is charged by its handler and is not under ``routes:``."""
    config = rl.load_config()
    school_route_policies = {name for names in config.routes.values() for name in names} & {
        name for name, p in config.policies.items() if p.per == "school"
    }
    assert school_route_policies == {"data_export", "heavy_jobs"}
    assert "audit_verify" not in {n for names in config.routes.values() for n in names}


def test_data_export_refused_members_cannot_spend_the_school_budget(
    admin_engine: Engine, api: Any, clock: Clock
) -> None:
    _, people = _school(admin_engine, "principal", "office_admin")
    owner = people["owner"]
    over = _quota("data_export") + 2
    for role in ("principal", "office_admin"):
        for _ in range(over):
            assert api.call(people[role], "POST", EXPORT, json={}).status_code == 403
    # The owner whose MFA sign-in is not recent is refused too (428) and spends nothing.
    for _ in range(over):
        res = api.call(owner, "POST", EXPORT, json={}, auth_age_s=3600)
        assert (res.status_code, res.json()["code"]) == (428, "step_up_required"), res.text

    first = api.call(owner, "POST", EXPORT, json={})
    assert first.status_code == 202, first.text
    assert '"data_export";q=3;w=3600' in first.headers["RateLimit-Policy"]
    # The permitted owner's calls count: one export at a time (409), then over quota (429).
    for _ in range(_quota("data_export") - 1):
        res = api.call(owner, "POST", EXPORT, json={})
        assert (res.status_code, res.json()["code"]) == (409, "tenant_export_in_progress")
    _assert_rate_limited(api.call(owner, "POST", EXPORT, json={}), "data_export")
    # Refused members still get 403 (authorization first), never the school's 429.
    assert api.call(people["principal"], "POST", EXPORT, json={}).status_code == 403


def test_heavy_jobs_dq_runs_refused_members_cannot_spend_the_school_budget(
    admin_engine: Engine, api: Any, clock: Clock
) -> None:
    _, people = _school(admin_engine, "accountant", "teacher", "office_staff")
    over = _quota("heavy_jobs") + 2
    for role in ("accountant", "teacher"):
        for _ in range(over):
            assert api.call(people[role], "POST", DQ_RUNS, json={}).status_code == 403

    clerk = people["office_staff"]  # holds dq.findings.read
    first = api.call(clerk, "POST", DQ_RUNS, json={})
    assert first.status_code == 202, first.text
    assert '"heavy_jobs";q=10;w=600' in first.headers["RateLimit-Policy"]
    for _ in range(_quota("heavy_jobs") - 1):
        assert api.call(people["owner"], "POST", DQ_RUNS, json={}).status_code == 202
    _assert_rate_limited(api.call(clerk, "POST", DQ_RUNS, json={}), "heavy_jobs")
    assert api.call(people["accountant"], "POST", DQ_RUNS, json={}).status_code == 403

    clock.advance(rl.load_config().policies["heavy_jobs"].window_s)
    assert api.call(clerk, "POST", DQ_RUNS, json={}).status_code == 202


def test_heavy_jobs_promotion_preview_refused_members_cannot_spend_the_school_budget(
    admin_engine: Engine, api: Any, clock: Clock
) -> None:
    school, people = _school(admin_engine, "teacher", "office_staff", "accountant")
    url = f"/api/v1/academic-years/{school.ids['old_year']}/promotions:preview"
    body = {"to_academic_year_id": str(school.ids["year"])}
    over = _quota("heavy_jobs") + 2
    for role in ("teacher", "office_staff", "accountant"):
        for _ in range(over):
            assert api.call(people[role], "POST", url, json=body).status_code == 403

    owner = people["owner"]
    for _ in range(_quota("heavy_jobs")):
        res = api.call(owner, "POST", url, json=body)
        assert res.status_code == 200, res.text
    _assert_rate_limited(api.call(owner, "POST", url, json=body), "heavy_jobs")
    assert api.call(people["teacher"], "POST", url, json=body).status_code == 403


def test_heavy_jobs_is_shared_by_the_routes_of_one_school(
    admin_engine: Engine, api: Any, clock: Clock
) -> None:
    """Still one school-wide budget across its routes, and another school is not affected."""
    school, people = _school(admin_engine)
    other, other_people = _school(admin_engine)
    owner = people["owner"]
    url = f"/api/v1/academic-years/{school.ids['old_year']}/promotions:preview"
    body = {"to_academic_year_id": str(school.ids["year"])}
    for _ in range(_quota("heavy_jobs")):
        assert api.call(owner, "POST", DQ_RUNS, json={}).status_code == 202
    _assert_rate_limited(api.call(owner, "POST", url, json=body), "heavy_jobs")
    assert api.call(other_people["owner"], "POST", DQ_RUNS, json={}).status_code == 202
    assert other.tenant_id != school.tenant_id
    assert isinstance(other.tenant_id, uuid.UUID)
