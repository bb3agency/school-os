"""BFF -> API service token (build contract §1; trust boundary TB2 in docs/07 §3; SEC-004).

The web BFF sends a short-lived HS256 JWT in ``X-Service-Token`` on every call, next to the
user's access token in ``Authorization: Bearer``. It proves the request came through the BFF
(which enforces the session cookie and CSRF) and not from something else on the network.

Token: ``iss = "sos-web"``, ``aud = "sos-api"``, ``iat``, ``exp`` (≤ 60 s after ``iat``), ``jti``.
Key: ``SOS_SERVICE_TOKEN_KEY`` (≥ 32 bytes), shared only by web and api via Secrets Manager.
Replay: each ``jti`` is remembered for 120 s (longer than any token can be valid) and a second use
is refused. Production uses Valkey/Redis ``SET NX EX`` so all API tasks share the memory.

HMAC verification in PyJWT uses ``hmac.compare_digest`` (constant time).
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Final, Protocol

import jwt
import redis
from jwt.exceptions import PyJWTError
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailable, Unauthenticated

logger = logging.getLogger(__name__)

SERVICE_TOKEN_HEADER: Final = "X-Service-Token"  # noqa: S105
SERVICE_TOKEN_ISSUER: Final = "sos-web"  # noqa: S105
SERVICE_TOKEN_AUDIENCE: Final = "sos-api"  # noqa: S105
SERVICE_TOKEN_ALGORITHM: Final = "HS256"  # noqa: S105
SERVICE_TOKEN_MAX_TTL: Final = timedelta(seconds=60)
SERVICE_TOKEN_LEEWAY: Final = timedelta(seconds=5)
REPLAY_WINDOW: Final = timedelta(seconds=120)
MIN_KEY_BYTES: Final = 32
MAX_SERVICE_TOKEN_BYTES: Final = 2048
_JTI_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{16,128}")
_REJECTED = "Internal service authentication failed"

# A jti must be remembered for at least as long as its token could still be accepted.
if REPLAY_WINDOW < SERVICE_TOKEN_MAX_TTL + 2 * SERVICE_TOKEN_LEEWAY:  # pragma: no cover
    raise RuntimeError("REPLAY_WINDOW must outlive service token validity")


class ReplayStore(Protocol):
    def claim(self, jti: str, ttl: timedelta) -> bool:
        """Atomically record ``jti`` for ``ttl``; return False if it was already recorded."""
        ...


class InMemoryReplayStore:
    """Single-process store for local development and tests. Fails closed when full."""

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_entries: int = 100_000
    ) -> None:
        self._clock = clock
        self._max_entries = max_entries
        self._seen: dict[str, float] = {}  # jti -> expiry (monotonic seconds)
        self._lock = threading.Lock()

    def claim(self, jti: str, ttl: timedelta) -> bool:
        with self._lock:
            now = self._clock()
            expiry = self._seen.get(jti)
            if expiry is not None and expiry > now:
                return False
            if len(self._seen) >= self._max_entries:
                self._seen = {k: v for k, v in self._seen.items() if v > now}
                if len(self._seen) >= self._max_entries:
                    logger.error("service_token_replay_store_full")
                    raise ServiceUnavailable()
            self._seen[jti] = now + ttl.total_seconds()
            return True


class _SetNxClient(Protocol):
    def set(self, name: str, value: bytes, *, nx: bool = ..., ex: int | None = ...) -> Any: ...


class RedisReplayStore:
    """Shared store across API tasks: ``SET sos:svc-jti:<jti> 1 NX EX <ttl>``. Fails closed."""

    def __init__(self, client: _SetNxClient, *, prefix: str = "sos:svc-jti:") -> None:
        self._client = client
        self._prefix = prefix

    def claim(self, jti: str, ttl: timedelta) -> bool:
        try:
            result = self._client.set(
                self._prefix + jti, b"1", nx=True, ex=max(1, int(ttl.total_seconds()))
            )
        except redis.exceptions.RedisError:
            logger.error("service_token_replay_store_unavailable")
            raise ServiceUnavailable() from None
        return bool(result)


@dataclass(frozen=True, slots=True)
class ServiceTokenClaims:
    token_id: str
    issued_at: datetime
    expires_at: datetime


def _key_bytes(key: SecretStr | str | bytes) -> bytes:
    if isinstance(key, SecretStr):
        key = key.get_secret_value()
    raw = key.encode("utf-8") if isinstance(key, str) else bytes(key)
    if len(raw) < MIN_KEY_BYTES:
        raise ValueError(f"service token key must be at least {MIN_KEY_BYTES} bytes")
    return raw


class ServiceTokenVerifier:
    """Issues (for tests / Python callers) and verifies BFF service tokens."""

    def __init__(
        self, key: SecretStr | str | bytes, *, replay_store: ReplayStore | None = None
    ) -> None:
        self._key = _key_bytes(key)
        self.replay_store: ReplayStore = replay_store or InMemoryReplayStore()

    def __repr__(self) -> str:
        return f"ServiceTokenVerifier(replay_store={type(self.replay_store).__name__})"

    def issue(
        self,
        now: datetime | None = None,
        *,
        ttl: timedelta = SERVICE_TOKEN_MAX_TTL,
        token_id: str | None = None,
    ) -> str:
        if ttl > SERVICE_TOKEN_MAX_TTL or ttl <= timedelta(0):
            raise ValueError("service token ttl must be between 1 and 60 seconds")
        issued = int((now or datetime.now(UTC)).timestamp())
        claims = {
            "iss": SERVICE_TOKEN_ISSUER,
            "aud": SERVICE_TOKEN_AUDIENCE,
            "iat": issued,
            "exp": issued + int(ttl.total_seconds()),
            "jti": token_id or secrets.token_urlsafe(18),
        }
        return jwt.encode(claims, self._key, algorithm=SERVICE_TOKEN_ALGORITHM)

    def verify(self, token: str) -> ServiceTokenClaims:
        if not isinstance(token, str) or not token or len(token) > MAX_SERVICE_TOKEN_BYTES:
            raise Unauthenticated(_REJECTED)
        try:
            if jwt.get_unverified_header(token).get("alg") != SERVICE_TOKEN_ALGORITHM:
                raise Unauthenticated(_REJECTED)
            claims = jwt.decode(
                token,
                key=self._key,
                algorithms=[SERVICE_TOKEN_ALGORITHM],
                audience=SERVICE_TOKEN_AUDIENCE,
                issuer=SERVICE_TOKEN_ISSUER,
                leeway=SERVICE_TOKEN_LEEWAY,
                options={"require": ["exp", "iat", "iss", "aud", "jti"], "strict_aud": True},
            )
        except (PyJWTError, ValueError, TypeError):
            raise Unauthenticated(_REJECTED) from None

        iat, exp, jti = claims["iat"], claims["exp"], claims["jti"]
        if not isinstance(jti, str) or not _JTI_PATTERN.fullmatch(jti):
            raise Unauthenticated(_REJECTED)
        if not (isinstance(iat, int) and isinstance(exp, int)):
            raise Unauthenticated(_REJECTED)
        if not 0 < exp - iat <= SERVICE_TOKEN_MAX_TTL.total_seconds():
            raise Unauthenticated(_REJECTED)
        # Only fully valid tokens reach the replay store, so junk cannot burn someone's jti.
        if not self.replay_store.claim(jti, REPLAY_WINDOW):
            logger.warning("service_token_replayed")
            raise Unauthenticated(_REJECTED)
        return ServiceTokenClaims(
            token_id=jti,
            issued_at=datetime.fromtimestamp(iat, tz=UTC),
            expires_at=datetime.fromtimestamp(exp, tz=UTC),
        )


def build_service_token_verifier(settings: Settings) -> ServiceTokenVerifier:
    """Staging/prod share replay memory through Valkey; local/CI use the in-memory store."""
    store: ReplayStore
    if settings.is_production_like:
        client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
        )
        store = RedisReplayStore(client)
    else:
        store = InMemoryReplayStore()
    return ServiceTokenVerifier(settings.service_token_key, replay_store=store)


@lru_cache(maxsize=1)
def get_service_token_verifier() -> ServiceTokenVerifier:
    """FastAPI dependency: process-wide verifier."""
    return build_service_token_verifier(get_settings())


def issue_service_token(now: datetime | None = None, *, settings: Settings | None = None) -> str:
    """Mint a token the way the BFF does (tests, scripts, Python-side internal callers)."""
    s = settings or get_settings()
    return ServiceTokenVerifier(s.service_token_key).issue(now)


def verify_service_token(
    token: str, *, verifier: ServiceTokenVerifier | None = None
) -> ServiceTokenClaims:
    return (verifier or get_service_token_verifier()).verify(token)
