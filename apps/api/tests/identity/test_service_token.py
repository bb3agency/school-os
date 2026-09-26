"""BFF -> API service token (contract §1, TB2; SEC-004)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
import redis
from app.core.config import Environment, Settings
from app.core.errors import ServiceUnavailable, Unauthenticated
from app.identity.service_token import (
    REPLAY_WINDOW,
    SERVICE_TOKEN_AUDIENCE,
    SERVICE_TOKEN_ISSUER,
    SERVICE_TOKEN_MAX_TTL,
    InMemoryReplayStore,
    RedisReplayStore,
    ServiceTokenVerifier,
    build_service_token_verifier,
    issue_service_token,
    verify_service_token,
)
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

KEY = "synthetic-service-token-key-0123456789abcdef"
OTHER_KEY = "another-synthetic-service-key-0123456789abcd"


class FakeClock:
    def __init__(self) -> None:
        self.t = 50_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def verifier(store: Any = None) -> ServiceTokenVerifier:
    return ServiceTokenVerifier(KEY, replay_store=store or InMemoryReplayStore())


def raw_token(key: Any = KEY, algorithm: str = "HS256", **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": SERVICE_TOKEN_ISSUER,
        "aud": SERVICE_TOKEN_AUDIENCE,
        "iat": now,
        "exp": now + 60,
        "jti": "synthetic-jti-aaaaaaaaaaaa",
    }
    for name, value in overrides.items():
        if value is None:
            claims.pop(name, None)
        else:
            claims[name] = value
    return jwt.encode(claims, key, algorithm=algorithm)


# --------------------------------------------------------------------------- happy path
def test_SEC_004_service_token_round_trip() -> None:
    v = verifier()
    token = v.issue()
    claims = v.verify(token)
    assert len(claims.token_id) >= 16
    assert claims.expires_at - claims.issued_at <= SERVICE_TOKEN_MAX_TTL
    assert claims.expires_at.tzinfo is not None


def test_SEC_004_token_minted_by_web_side_is_accepted() -> None:
    # Mirrors what the TypeScript BFF produces (uuid jti, 60 s ttl).
    token = raw_token(jti="0192f3c1-7b2a-7c3d-8e4f-0123456789ab")
    assert verifier().verify(token).token_id == "0192f3c1-7b2a-7c3d-8e4f-0123456789ab"


def test_SEC_004_each_issued_token_has_unique_jti() -> None:
    v = verifier()
    ids = {v.verify(v.issue()).token_id for _ in range(50)}
    assert len(ids) == 50


# --------------------------------------------------------------------------- rejections
def test_SEC_004_expired_service_token_is_rejected() -> None:
    v = verifier()
    stale = v.issue(now=datetime.now(UTC) - timedelta(seconds=120))
    with pytest.raises(Unauthenticated):
        v.verify(stale)


def test_SEC_004_future_dated_service_token_is_rejected() -> None:
    now = int(time.time())
    with pytest.raises(Unauthenticated):
        verifier().verify(raw_token(iat=now + 120, exp=now + 180))


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "sos-worker"},
        {"aud": ["sos-api", "sos-other"]},
        {"iss": "sos-evil"},
        {"iss": None},
        {"aud": None},
        {"exp": None},
        {"iat": None},
        {"jti": None},
        {"jti": ""},
        {"jti": "short"},
        {"jti": "has spaces in it 1234"},
        {"jti": 1234567890123456789},
    ],
)
def test_SEC_004_service_token_with_bad_claims_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(Unauthenticated):
        verifier().verify(raw_token(**overrides))


def test_SEC_004_service_token_signed_with_wrong_key_is_rejected() -> None:
    with pytest.raises(Unauthenticated):
        verifier().verify(raw_token(key=OTHER_KEY))


def test_SEC_004_service_token_lifetime_over_60s_is_rejected() -> None:
    now = int(time.time())
    with pytest.raises(Unauthenticated):
        verifier().verify(raw_token(iat=now, exp=now + 300))


def test_SEC_004_replayed_service_token_is_rejected() -> None:
    v = verifier()
    token = v.issue()
    v.verify(token)
    with pytest.raises(Unauthenticated):
        v.verify(token)


def test_SEC_004_replay_is_detected_across_verifiers_sharing_a_store() -> None:
    store = InMemoryReplayStore()
    token = verifier(store).issue()
    verifier(store).verify(token)
    with pytest.raises(Unauthenticated):
        verifier(store).verify(token)


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
@pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
def test_SEC_004_service_token_with_other_hmac_alg_is_rejected(algorithm: str) -> None:
    with pytest.raises(Unauthenticated):
        verifier().verify(raw_token(algorithm=algorithm))


def test_SEC_004_service_token_with_alg_none_is_rejected() -> None:
    token = raw_token()
    header = jwt.utils.base64url_encode(b'{"alg":"none","typ":"JWT"}').decode()
    unsigned = f"{header}.{token.split('.')[1]}."
    with pytest.raises(Unauthenticated):
        verifier().verify(unsigned)


def test_SEC_004_service_token_signed_with_rsa_is_rejected() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(Unauthenticated):
        verifier().verify(raw_token(key=private, algorithm="RS256"))


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "x" * 5000])
def test_SEC_004_malformed_service_token_is_rejected(token: str) -> None:
    with pytest.raises(Unauthenticated):
        verifier().verify(token)


def test_SEC_004_rejected_token_does_not_burn_a_jti() -> None:
    store = InMemoryReplayStore()
    bad = raw_token(aud="sos-worker", jti="synthetic-jti-bbbbbbbbbbbb")
    with pytest.raises(Unauthenticated):
        verifier(store).verify(bad)
    verifier(store).verify(raw_token(jti="synthetic-jti-bbbbbbbbbbbb"))


# --------------------------------------------------------------------------- key handling
@pytest.mark.parametrize("key", ["", "too-short", "x" * 31, b"y" * 31])
def test_SEC_004_short_service_token_key_is_refused(key: str | bytes) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        ServiceTokenVerifier(key)


def test_SEC_004_secretstr_key_is_accepted_and_never_repr_d() -> None:
    v = ServiceTokenVerifier(SecretStr(KEY))
    assert KEY not in repr(v)
    v.verify(v.issue())


def test_SEC_004_issue_refuses_ttl_over_60s() -> None:
    with pytest.raises(ValueError, match="60"):
        verifier().issue(ttl=timedelta(seconds=61))


def test_SEC_004_replay_window_outlives_token_validity() -> None:
    assert timedelta(seconds=120) == REPLAY_WINDOW
    assert REPLAY_WINDOW >= SERVICE_TOKEN_MAX_TTL * 2


# --------------------------------------------------------------------------- replay stores
def test_SEC_004_in_memory_store_forgets_after_window() -> None:
    clock = FakeClock()
    store = InMemoryReplayStore(clock=clock)
    assert store.claim("synthetic-jti-cccccccccccc", REPLAY_WINDOW) is True
    assert store.claim("synthetic-jti-cccccccccccc", REPLAY_WINDOW) is False
    clock.advance(REPLAY_WINDOW.total_seconds() + 1)
    assert store.claim("synthetic-jti-cccccccccccc", REPLAY_WINDOW) is True


def test_SEC_004_in_memory_store_fails_closed_when_full() -> None:
    clock = FakeClock()
    store = InMemoryReplayStore(clock=clock, max_entries=3)
    for i in range(3):
        assert store.claim(f"synthetic-jti-{i:012d}", REPLAY_WINDOW)
    with pytest.raises(ServiceUnavailable):
        store.claim("synthetic-jti-overflowing1", REPLAY_WINDOW)
    clock.advance(REPLAY_WINDOW.total_seconds() + 1)  # expired entries are purged
    assert store.claim("synthetic-jti-overflowing1", REPLAY_WINDOW)


class FakeRedis:
    """Implements the subset of redis-py used: SET key value NX EX seconds."""

    def __init__(self, *, fail: bool = False) -> None:
        self.data: dict[str, tuple[bytes, int]] = {}
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def set(self, name: str, value: bytes, *, nx: bool = False, ex: int | None = None) -> Any:
        self.calls.append({"name": name, "nx": nx, "ex": ex})
        if self.fail:
            raise redis.exceptions.ConnectionError("synthetic valkey outage at 10.0.0.9")
        if nx and name in self.data:
            return None
        self.data[name] = (value, ex or 0)
        return True


def test_SEC_004_redis_store_uses_set_nx_ex() -> None:
    fake = FakeRedis()
    store = RedisReplayStore(fake)
    assert store.claim("synthetic-jti-dddddddddddd", REPLAY_WINDOW) is True
    assert store.claim("synthetic-jti-dddddddddddd", REPLAY_WINDOW) is False
    call = fake.calls[0]
    assert call["nx"] is True
    assert call["ex"] == 120
    assert call["name"].startswith("sos:svc-jti:")


def test_SEC_004_redis_store_rejects_replay_end_to_end() -> None:
    v = verifier(RedisReplayStore(FakeRedis()))
    token = v.issue()
    v.verify(token)
    with pytest.raises(Unauthenticated):
        v.verify(token)


def test_SEC_004_redis_outage_fails_closed_without_details() -> None:
    v = verifier(RedisReplayStore(FakeRedis(fail=True)))
    with pytest.raises(ServiceUnavailable) as exc:
        v.verify(v.issue())
    assert "10.0.0.9" not in f"{exc.value} {exc.value.detail}"


# --------------------------------------------------------------------------- settings wiring
def test_SEC_004_module_functions_use_settings_key() -> None:
    settings = Settings(env=Environment.LOCAL, service_token_key=SecretStr(KEY))
    v = build_service_token_verifier(settings)
    token = issue_service_token(settings=settings)
    assert verify_service_token(token, verifier=v).token_id
    with pytest.raises(Unauthenticated):
        verify_service_token(token, verifier=v)


def test_SEC_004_local_settings_use_in_memory_store() -> None:
    v = build_service_token_verifier(Settings(env=Environment.LOCAL))
    assert isinstance(v.replay_store, InMemoryReplayStore)
