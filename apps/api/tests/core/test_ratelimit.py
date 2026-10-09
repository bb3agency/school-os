"""API rate limiter (audit 2026-10-05 P2-07; OWASP API4:2023; ASVS 2.2.1, 11.1.4; docs/09 §2.7).

The GCRA store is tested twice: in memory (local/CI and the fail-closed fallback) and against a real
Valkey with the Lua script (testcontainers, the dedicated-host image), including parallel callers
that must never be over-admitted. Layer isolation, headers, trusted-proxy handling and failure
modes run against a small app that calls the same ``enforce`` the route guards call. Synthetic IDs
only; no personal data.
"""

from __future__ import annotations

import io
import json
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
import redis
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import ratelimit as rl
from app.core.config import Environment, Settings
from app.core.errors import install_error_handlers
from app.core.health import PUBLIC_PATHS
from app.core.logging import ALLOWED_FIELDS, clear_context, setup_logging
from app.core.middleware import install_middleware

VALKEY_IMAGE = "valkey/valkey:8.1.10-alpine"  # the deploy/dedicated/compose.yaml image
SCHOOL_A = uuid.UUID("0192f000-0000-7000-8000-00000000a001")
SCHOOL_B = uuid.UUID("0192f000-0000-7000-8000-00000000b001")


def policy(name: str = "p", quota: int = 5, window_s: int = 60, **kw: Any) -> rl.Policy:
    values: dict[str, Any] = {
        "per": "user",
        "fail": "open",
        "report": True,
        "weighted": False,
    } | kw
    return rl.Policy(name=name, quota=quota, window_s=window_s, **values)


class Clock:
    def __init__(self) -> None:
        self.ms = 1_000_000.0

    def __call__(self) -> float:
        return self.ms

    def advance(self, seconds: float) -> None:
        self.ms += seconds * 1000


def limiter_with(store: rl.RateLimitStore, **kw: Any) -> rl.RateLimiter:
    return rl.RateLimiter(rl.load_config(), store, hash_key=b"synthetic-test-key", **kw)


# --- stores: in memory and real Valkey -------------------------------------------------------


@pytest.fixture(scope="module")
def valkey_client() -> Iterator[Any]:
    from testcontainers.community.valkey import ValkeyContainer

    with ValkeyContainer(VALKEY_IMAGE) as container:
        client = redis.Redis(
            host=container.get_container_host_ip(),
            port=container.get_exposed_port(),
            socket_timeout=5,
        )
        yield client
        client.close()


@pytest.fixture(params=["memory", "valkey"])
def store(request: pytest.FixtureRequest) -> rl.RateLimitStore:
    if request.param == "memory":
        return rl.InMemoryRateLimitStore()
    client = request.getfixturevalue("valkey_client")
    client.flushdb()
    return rl.ValkeyRateLimitStore(client)


def spec(key: str, quota: int, window_s: float = 60, cost: int = 1) -> rl.BucketSpec:
    return rl.BucketSpec(key, int(window_s * 1000), quota, cost)


def test_P2_07_limit_is_reached_then_refused_with_retry(store: rl.RateLimitStore) -> None:
    results = [store.acquire([], [spec("sos:rl:t:a", 5)]) for _ in range(6)]
    assert [r.allowed for r in results] == [True] * 5 + [False]
    assert [r.buckets[0].remaining for r in results[:5]] == [4, 3, 2, 1, 0]
    refused = results[5].buckets[0]
    assert refused.remaining == 0
    # One unit refills every 60 s / 5 = 12 s.
    assert 11_000 <= refused.retry_ms <= 12_000
    assert 59_000 <= refused.reset_ms <= 60_000


def test_P2_07_window_refills(store: rl.RateLimitStore) -> None:
    assert all(store.acquire([], [spec("sos:rl:t:w", 2, 1)]).allowed for _ in range(2))
    assert not store.acquire([], [spec("sos:rl:t:w", 2, 1)]).allowed
    time.sleep(1.1)
    assert store.acquire([], [spec("sos:rl:t:w", 2, 1)]).allowed


def test_P2_07_weighted_cost_and_all_or_nothing(store: rl.RateLimitStore) -> None:
    big, small = spec("sos:rl:t:big", 10, cost=3), spec("sos:rl:t:small", 1)
    assert store.acquire([], [big, small]).allowed  # big 7 left, small 0 left
    refused = store.acquire([], [big, small])
    assert not refused.allowed
    # The refused call charged nothing: big still has 7 units for three writes (3 + 3 + 1 left).
    assert [store.acquire([], [big]).allowed for _ in range(3)] == [True, True, False]


def test_P2_07_parallel_callers_are_never_over_admitted(store: rl.RateLimitStore) -> None:
    def one(_: int) -> bool:
        return store.acquire([], [spec("sos:rl:t:race", 20, 3600)]).allowed

    with ThreadPoolExecutor(max_workers=16) as pool:
        admitted = sum(pool.map(one, range(200)))
    assert admitted == 20


def test_P2_07_parallel_multi_bucket_callers_charge_together(store: rl.RateLimitStore) -> None:
    """Two layers (person and school): the school budget is never overshot by parallel people."""

    def one(i: int) -> bool:
        user = spec(f"sos:rl:t:u{i % 4}", 100, 3600)
        school = spec("sos:rl:t:school", 30, 3600)
        return store.acquire([], [user, school]).allowed

    with ThreadPoolExecutor(max_workers=16) as pool:
        admitted = sum(pool.map(one, range(120)))
    assert admitted == 30


def test_ASVS_2_2_1_failures_back_off_exponentially_and_clear(store: rl.RateLimitStore) -> None:
    backoff = rl.Backoff(free_failures=2, base_delay_s=1, max_delay_s=4, reset_after_s=60)
    delays = [store.fail("sos:rl:t:f:n", "sos:rl:t:f:block", backoff)[1] for _ in range(6)]
    assert delays == [0, 0, 1000, 2000, 4000, 4000]
    blocked = store.acquire(["sos:rl:t:f:block"], [spec("sos:rl:t:f:any", 100)])
    assert not blocked.allowed
    assert 3000 < blocked.block_ms <= 4000
    store.clear("sos:rl:t:f:n", "sos:rl:t:f:block")
    assert store.acquire(["sos:rl:t:f:block"], [spec("sos:rl:t:f:any", 100)]).allowed
    assert store.fail("sos:rl:t:f:n", "sos:rl:t:f:block", backoff) == (1, 0)


def test_P2_07_in_memory_window_refills_on_the_clock() -> None:
    clock = Clock()
    store = rl.InMemoryRateLimitStore(clock=clock)
    assert all(store.acquire([], [spec("k", 3)]).allowed for _ in range(3))
    assert not store.acquire([], [spec("k", 3)]).allowed
    clock.advance(20)  # one unit per 20 s
    assert store.acquire([], [spec("k", 3)]).allowed
    assert not store.acquire([], [spec("k", 3)]).allowed
    clock.advance(60)
    assert all(store.acquire([], [spec("k", 3)]).allowed for _ in range(3))


# --- configuration --------------------------------------------------------------------------


def test_P2_07_config_loads_with_every_layer_and_exempts_only_health() -> None:
    config = rl.load_config()
    for name in ("ip", "machine_ip", "user", "school", "operator"):
        assert name in config.policies
    assert set(config.exempt_paths) == set(PUBLIC_PATHS)
    assert config.key_prefix.startswith("sos:rl:")
    assert config.cost.write > config.cost.read
    # Sign-in and credential paths fail closed (ASVS 2.2.1).
    assert config.policies["machine_ip"].fail == "closed"
    assert config.policies["login_event"].fail == "closed"
    assert config.policies["invitations"].fail == "closed"


def test_SEC_020_knowledge_search_has_a_per_user_limit() -> None:
    """Audit 2026-10-04 hardening: /knowledge/search (search-only Ask, an embedding per call)
    carries a per-person route budget; with a frozen clock the next call past it is refused."""
    config = rl.load_config()
    names = config.routes["POST /api/v1/knowledge/search"]
    per_user = [config.policies[n] for n in names if config.policies[n].per == "user"]
    assert per_user, names
    p = per_user[0]
    assert p.quota <= 60
    clock = Clock()
    store = rl.InMemoryRateLimitStore(clock=clock)
    key = "sos:rl:test:knowledge-search:user-1"
    results = [store.acquire([], [spec(key, p.quota, p.window_s)]) for _ in range(p.quota + 1)]
    assert [r.allowed for r in results] == [True] * p.quota + [False]
    assert results[-1].buckets[0].retry_ms > 0


def test_P2_07_settings_guard_keeps_limiting_on_outside_local_and_ci() -> None:
    with pytest.raises(ValidationError, match="SOS_RATE_LIMIT_ENABLED"):
        Settings(
            env=Environment.STAGING,
            rate_limit_enabled=False,
            key_wrapper="kms",
            database_url="postgresql+psycopg://sos_app:x@db/schoolos?sslmode=verify-full",
            platform_database_url="postgresql+psycopg://sos_platform:x@db/schoolos?sslmode=verify-full",
            service_token_key="synthetic-staging-service-token-key-0123456789",
        )
    assert Settings(env=Environment.CI).rate_limit_enabled is True


@pytest.mark.parametrize("value", ["0.0.0.0/0", "::/0", "not-an-ip", "10.0.0.0/8, nope"])
def test_P2_07_trusted_proxies_must_be_specific_networks(value: str) -> None:
    with pytest.raises(ValidationError, match="SOS_TRUSTED_PROXIES"):
        Settings(env=Environment.CI, trusted_proxies=value)


# --- client IP from trusted proxies only ----------------------------------------------------


def scope_for(peer: str, xff: str | None = None) -> dict[str, Any]:
    headers = [] if xff is None else [(b"x-forwarded-for", xff.encode())]
    return {"type": "http", "client": (peer, 40000), "headers": headers}


TRUSTED = Settings(env=Environment.CI, trusted_proxies="10.0.0.0/16").trusted_proxy_networks


@pytest.mark.parametrize(
    ("peer", "xff", "expected"),
    [
        # An untrusted peer's header is ignored: a client cannot choose its own bucket.
        ("203.0.113.9", "198.51.100.1", ("203.0.113.9", False)),
        # Trusted ALB/BFF: the right-most untrusted hop is the client; spoofed left parts are not.
        ("10.0.1.5", "198.51.100.7, 203.0.113.20", ("203.0.113.20", False)),
        ("10.0.1.5", "203.0.113.20, 10.0.2.9", ("203.0.113.20", False)),
        # Trusted peer that names no client: the BFF's own server-side call (internal).
        ("10.0.1.5", None, ("10.0.1.5", True)),
        # Malformed chain from a trusted peer: fall back to the peer, never to the junk.
        ("10.0.1.5", "evil, 203.0.113.20", ("203.0.113.20", False)),
        ("10.0.1.5", "203.0.113.20, junk", ("10.0.1.5", False)),
        ("::ffff:10.0.1.5", "203.0.113.30", ("203.0.113.30", False)),
    ],
)
def test_P2_07_x_forwarded_for_only_from_trusted_proxies(
    peer: str, xff: str | None, expected: tuple[str, bool]
) -> None:
    assert rl.client_ip(scope_for(peer, xff), TRUSTED) == expected


def test_P2_07_without_trusted_proxies_the_peer_is_the_client() -> None:
    assert rl.client_ip(scope_for("10.0.1.5", "198.51.100.1"), ()) == ("10.0.1.5", False)


# --- a small app with the real middleware and enforce() ---------------------------------------


def demo_app(
    limiter: rl.RateLimiter, *, trusted_proxies: str = "", operator: bool = False
) -> FastAPI:
    rl.set_rate_limiter(limiter)
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app, Settings(env=Environment.CI, trusted_proxies=trusted_proxies))

    def guard(request: Request) -> None:
        tenant = request.headers.get("x-test-school")
        rl.enforce(
            request,
            principal=rl.principal_key(
                "user", "https://idp.invalid", request.headers["x-test-sub"]
            ),
            tenant_id=uuid.UUID(tenant) if tenant else None,
            layer="operator" if operator else "user",
        )

    @app.get("/api/v1/things", dependencies=[Depends(guard)])
    def things() -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/api/v1/students/search", dependencies=[Depends(guard)])
    def search() -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/api/v1/fleet/heartbeat")
    def heartbeat() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def memory_limiter() -> Iterator[rl.RateLimiter]:
    # A frozen clock: quota + 1 calls must not see a refill however slow the machine is
    # (machine_ip refills every 0.5 s); refill itself is proven with Clock.advance above.
    limiter = limiter_with(rl.InMemoryRateLimitStore(clock=Clock()))
    yield limiter
    rl.set_rate_limiter(None)


def call(
    client: TestClient,
    method: str,
    path: str,
    *,
    sub: str = "synthetic-subject-a",
    school: uuid.UUID | None = SCHOOL_A,
    xff: str | None = None,
) -> httpx.Response:
    h = {"x-test-sub": sub}
    if xff is not None:
        h["X-Forwarded-For"] = xff
    if school is not None:
        h["x-test-school"] = str(school)
    response: httpx.Response = client.request(method, path, headers=h)
    return response


def exhaust(client: TestClient, method: str, path: str, n: int, **kw: Any) -> list[httpx.Response]:
    return [call(client, method, path, **kw) for _ in range(n)]


def test_P2_07_headers_on_success_and_429_with_retry_after(
    memory_limiter: rl.RateLimiter,
) -> None:
    client = TestClient(demo_app(memory_limiter))
    first = call(client, "POST", "/api/v1/students/search")
    assert first.status_code == 200
    policies = first.headers["RateLimit-Policy"]
    assert '"user";q=600;w=60' in policies
    assert '"search";q=60;w=60' in policies
    # The school aggregate and the shared per-IP layer are not shown on success.
    assert '"school"' not in policies
    assert '"ip"' not in policies
    assert '"search";r=59;t=1' in first.headers["RateLimit"]
    assert '"user";r=597;t=' in first.headers["RateLimit"]  # a write costs 3 units

    rest = exhaust(client, "POST", "/api/v1/students/search", 60)
    assert [r.status_code for r in rest] == [200] * 59 + [429]
    refused = rest[-1]
    assert refused.headers["content-type"].startswith("application/problem+json")
    body = refused.json()
    assert body["code"] == "rate_limited"
    retry = int(refused.headers["Retry-After"])
    assert retry >= 1
    assert body["retry_after"] == retry
    assert body["detail"] == f"Too many requests. Wait {retry} seconds and try again."
    assert '"search";r=0;t=' in refused.headers["RateLimit"]
    assert refused.headers["RateLimit-Policy"].startswith('"user";q=600;w=60')


def test_P2_07_people_and_schools_are_isolated(memory_limiter: rl.RateLimiter) -> None:
    client = TestClient(demo_app(memory_limiter))
    assert exhaust(client, "POST", "/api/v1/students/search", 61)[-1].status_code == 429
    # Another person in the same school still has their own search budget.
    assert call(client, "POST", "/api/v1/students/search", sub="synthetic-subject-b").is_success
    # Another person in another school is untouched too.
    other = call(client, "POST", "/api/v1/students/search", sub="synthetic-c", school=SCHOOL_B)
    assert other.is_success


def test_P2_07_one_school_cannot_starve_another(memory_limiter: rl.RateLimiter) -> None:
    config = memory_limiter.config
    tight = config.model_copy(
        update={
            "policies": config.policies
            | {"school": config.policies["school"].model_copy(update={"quota": 4})}
        }
    )
    limiter = limiter_with(rl.InMemoryRateLimitStore())
    limiter.config = tight
    client = TestClient(demo_app(limiter))
    codes = [call(client, "GET", "/api/v1/things", sub=f"s{i}").status_code for i in range(5)]
    assert codes == [200, 200, 200, 200, 429]
    refused = call(client, "GET", "/api/v1/things", sub="s9")
    assert refused.status_code == 429
    # The aggregate that refused is named on its own 429, with the caller's school only.
    assert '"school";q=4;w=60' in refused.headers["RateLimit-Policy"]
    assert call(client, "GET", "/api/v1/things", sub="s1", school=SCHOOL_B).status_code == 200


def test_P2_07_per_ip_layer_and_health_and_preflight_exemptions(
    memory_limiter: rl.RateLimiter,
) -> None:
    config = memory_limiter.config
    memory_limiter.config = config.model_copy(
        update={
            "policies": config.policies
            | {"ip": config.policies["ip"].model_copy(update={"quota": 3})}
        }
    )
    client = TestClient(demo_app(memory_limiter))
    codes = [call(client, "GET", "/api/v1/things", sub=f"p{i}").status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]
    assert all(client.get("/healthz").status_code == 200 for _ in range(10))
    preflight = client.options("/api/v1/things", headers={"Origin": "https://app.invalid"})
    assert preflight.status_code != 429


def test_P2_07_spoofed_forwarded_for_from_an_untrusted_peer_is_ignored(
    memory_limiter: rl.RateLimiter,
) -> None:
    config = memory_limiter.config
    memory_limiter.config = config.model_copy(
        update={
            "policies": config.policies
            | {"ip": config.policies["ip"].model_copy(update={"quota": 2})}
        }
    )
    app = demo_app(memory_limiter, trusted_proxies="10.0.0.0/16")

    def from_peer(peer: str) -> TestClient:
        return TestClient(app, client=(peer, 50000))

    attacker = from_peer("203.0.113.66")
    codes = [
        call(attacker, "GET", "/api/v1/things", sub=f"a{i}", xff=f"192.0.2.{i}").status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429], "a new X-Forwarded-For per request must not buy a new bucket"
    # Behind the trusted ALB/BFF, the forwarded client is used: different offices, own buckets.
    proxy = from_peer("10.0.3.4")
    for office in ("198.51.100.10", "198.51.100.11"):
        codes = [
            call(proxy, "GET", "/api/v1/things", sub=f"o{i}", xff=office).status_code
            for i in range(2)
        ]
        assert codes == [200, 200]
    # The BFF's own server-side calls (trusted peer, no client named) skip the per-IP layer.
    assert all(
        call(proxy, "GET", "/api/v1/things", sub=f"i{i}").status_code == 200 for i in range(5)
    )


def test_P2_07_machine_paths_get_the_closed_per_ip_layer(memory_limiter: rl.RateLimiter) -> None:
    client = TestClient(demo_app(memory_limiter))
    quota = memory_limiter.config.policies["machine_ip"].quota
    codes = [client.post("/api/v1/fleet/heartbeat").status_code for _ in range(quota + 1)]
    assert codes[-1] == 429
    assert codes[:-1] == [200] * quota
    assert '"machine_ip"' in client.post("/api/v1/fleet/heartbeat").headers["RateLimit-Policy"]


# --- failure modes ---------------------------------------------------------------------------


class DownStore:
    """Valkey unreachable."""

    def acquire(self, blocks: Sequence[str], buckets: Sequence[rl.BucketSpec]) -> rl.RawDecision:
        raise rl.StoreUnavailable("down")

    def fail(self, count_key: str, block_key: str, backoff: rl.Backoff) -> tuple[int, int]:
        raise rl.StoreUnavailable("down")

    def clear(self, *keys: str) -> None:
        raise rl.StoreUnavailable("down")


def test_P2_07_valkey_down_fails_open_for_normal_traffic() -> None:
    limiter = limiter_with(DownStore())
    before = rl.unavailable_total()
    client = TestClient(demo_app(limiter))
    try:
        codes = {call(client, "POST", "/api/v1/students/search").status_code for _ in range(80)}
    finally:
        rl.set_rate_limiter(None)
    assert codes == {200}
    assert rl.unavailable_total() > before


def test_P2_07_valkey_down_fails_closed_for_sign_in_paths() -> None:
    """Closed policies and the authentication backoff fall back to a per-process limiter."""
    # Frozen fallback clock: quota + 1 calls must not see a refill on a slow machine.
    limiter = limiter_with(DownStore(), fallback=rl.InMemoryRateLimitStore(clock=Clock()))
    login = limiter.config.policy("login_event")
    decisions = [limiter.check([rl.Bucket(login, "person")]) for _ in range(login.quota + 1)]
    assert [d.allowed for d in decisions] == [True] * login.quota + [False]
    assert decisions[-1].degraded
    assert decisions[-1].retry_after_s >= 1
    # Machine paths too.
    client = TestClient(demo_app(limiter))
    try:
        quota = limiter.config.policies["machine_ip"].quota
        codes = [client.post("/api/v1/fleet/heartbeat").status_code for _ in range(quota + 1)]
    finally:
        rl.set_rate_limiter(None)
    assert codes[-1] == 429
    # Backoff blocks are kept per process while Valkey is down.
    backoff = limiter.config.auth_failures.subject_ip
    delays = [
        limiter.record_failure("subject_ip", "p:ip") for _ in range(backoff.free_failures + 1)
    ]
    assert delays[-1] == backoff.base_delay_s
    blocked = limiter.check([], [limiter.block_key("subject_ip", "p:ip")])
    assert not blocked.allowed
    assert blocked.blocked


def test_P2_07_disabled_limiter_allows_everything() -> None:
    limiter = limiter_with(rl.InMemoryRateLimitStore(), enabled=False)
    login = limiter.config.policy("login_event")
    assert all(limiter.check([rl.Bucket(login, "p")]).allowed for _ in range(login.quota * 3))


# --- security events --------------------------------------------------------------------------


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


def test_P2_07_rate_limit_trips_log_ids_and_ip_hash_only(
    memory_limiter: rl.RateLimiter, lines: io.StringIO
) -> None:
    client = TestClient(demo_app(memory_limiter), client=("198.51.100.200", 1))
    exhaust(client, "POST", "/api/v1/students/search", 61)
    (line,) = events(lines, "security.rate_limited")
    assert line["policy"] == "search"
    assert line["route"] == "POST /api/v1/students/search"
    assert line["retry_after_s"] >= 1
    assert len(line["ip_hash"]) == 16
    assert line["ip_hash"] != "198.51.100.200"
    assert set(line) <= set(ALLOWED_FIELDS)
    assert "198.51.100.200" not in lines.getvalue()


def test_P2_07_ip_hash_is_keyed_and_stable() -> None:
    a = limiter_with(rl.InMemoryRateLimitStore())
    b = rl.RateLimiter(rl.load_config(), rl.InMemoryRateLimitStore(), hash_key=b"other-key")
    assert a.ip_hash("198.51.100.1") == a.ip_hash("198.51.100.1")
    assert a.ip_hash("198.51.100.1") != b.ip_hash("198.51.100.1")
    assert a.ip_hash("198.51.100.1") != a.ip_hash("198.51.100.2")


def test_P2_07_threads_share_one_in_memory_budget() -> None:
    store = rl.InMemoryRateLimitStore()
    admitted: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(25):
            ok = store.acquire([], [spec("shared", 40, 3600)]).allowed
            with lock:
                admitted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(admitted) == 40
