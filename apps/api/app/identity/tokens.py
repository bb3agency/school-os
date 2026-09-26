"""OIDC access-token verification for the API (FR-IAM-001, FR-IAM-004, SEC-004; threat T2).

The API trusts nothing from the BFF except a valid user access token plus the internal service
token (docs/09 §1, TB2). This module verifies the access token:

* signature against the issuer's JWKS (fetched with httpx, explicit timeouts, cached 1 h,
  docs/04 §10), refetched at most once per 60 s when an unknown ``kid`` appears so that random
  ``kid`` values cannot amplify into JWKS fetches;
* algorithm allowlist RS256/ES256 (never ``none``/HS*), and the header ``alg`` must match the
  JWK, which rules out key-confusion attacks;
* required claims ``exp``, ``iat``, ``iss``, ``sub``; issuer equals the configured issuer;
* audience: standard ``aud`` **or** Amazon Cognito access tokens, which carry ``client_id`` and
  ``token_use == "access"`` instead of ``aud``; Cognito ID tokens (``token_use == "id"``) are
  refused even though their ``aud`` equals the app client id;
* 30 s clock leeway; a token whose ``exp - iat`` exceeds 15 min is refused as an IdP
  misconfiguration (FR-IAM-004 requires ≤ 10 min);
* IdP/JWKS failures surface as ``ServiceUnavailable`` (503), never as 401 and never with
  upstream details.

See ``app/identity/README.md`` for the Cognito research behind these rules.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
import jwt
from jwt.exceptions import ExpiredSignatureError, PyJWTError

from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailable, Unauthenticated

logger = logging.getLogger(__name__)

ALLOWED_ALGORITHMS: Final[frozenset[str]] = frozenset({"RS256", "ES256"})
REQUIRED_CLAIMS: Final[tuple[str, ...]] = ("exp", "iat", "iss", "sub")
DEFAULT_LEEWAY: Final = timedelta(seconds=30)
# FR-IAM-004 says ≤ 10 min; anything above 15 min means the IdP app client is misconfigured.
DEFAULT_MAX_LIFETIME: Final = timedelta(minutes=15)
JWKS_TTL: Final = timedelta(hours=1)
JWKS_REFETCH_MIN_INTERVAL: Final = timedelta(seconds=60)
# If a scheduled refresh fails, keep using the last good key set for this long (stale-if-error).
JWKS_STALE_GRACE: Final = timedelta(hours=1)
# Before the first successful fetch, retry at most this often (seconds) while the IdP is down.
COLD_START_RETRY_S: Final = 5.0
MAX_TOKEN_BYTES: Final = 8 * 1024
MAX_DOCUMENT_BYTES: Final = 64 * 1024
MIN_RSA_BITS: Final = 2048
ACCEPTED_TYP: Final[frozenset[str]] = frozenset({"jwt", "at+jwt", "application/at+jwt"})
DEV_ISSUER_HOSTS: Final[frozenset[str]] = frozenset(
    {"localhost", "127.0.0.1", "::1", "0.0.0.0", "mock-oauth2-server", "oidc"}  # noqa: S104
)
HTTP_TIMEOUT: Final = httpx.Timeout(3.0, connect=2.0)
# Cognito marks access tokens token_use="access" and ID tokens token_use="id".
ACCESS_TOKEN_USE: Final = "access"  # noqa: S105 (claim value, not a secret)

_GENERIC_REJECTION = "The access token is not valid. Sign in again."
_IDP_UNAVAILABLE = "Sign-in could not be checked right now. Try again in a minute."


def is_dev_issuer(issuer: str) -> bool:
    """True for issuers that must never be trusted in staging/prod (dev stubs, loopback, http)."""
    parts = urlsplit(issuer)
    host = (parts.hostname or "").lower()
    return (
        parts.scheme != "https"
        or host in DEV_ISSUER_HOSTS
        or host.endswith(".localhost")
        or host.startswith("127.")
    )


def default_http_client() -> httpx.Client:
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": "schoolos-api/jwks"},
    )


@dataclass(frozen=True, slots=True)
class MfaClaimPolicy:
    """How MFA is read from a token (SEC-005).

    ``amr_claim`` (RFC 8176 ``amr``) containing one of ``amr_values``, or ``flag_claim`` equal to
    true / "true". Cognito never emits ``amr`` on user-pool tokens and does not let a
    pre-token-generation trigger set it, so the Cognito setup adds ``sos:mfa`` (README).
    """

    amr_claim: str = "amr"
    amr_values: frozenset[str] = frozenset({"mfa"})
    flag_claim: str = "sos:mfa"

    def evaluate(self, claims: Mapping[str, Any]) -> bool:
        amr = claims.get(self.amr_claim)
        if isinstance(amr, str):
            amr = [amr]
        if isinstance(amr, list) and any(v in self.amr_values for v in amr if isinstance(v, str)):
            return True
        flag = claims.get(self.flag_claim)
        return flag is True or flag == "true"


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    subject: str
    issuer: str
    issued_at: datetime
    expires_at: datetime
    auth_time: datetime | None
    mfa: bool
    session_id: str | None
    token_id: str | None
    # Read-only view of every claim; excluded from repr so tokens never end up in logs.
    claims: Mapping[str, Any] = field(repr=False, compare=False)


def _utc(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class _JwksUnavailable(Exception):
    """Internal: the IdP could not be reached or returned something unusable."""


class JwksCache:
    """Thread-safe in-process JWKS cache for one issuer."""

    def __init__(
        self,
        *,
        issuer: str,
        http_client: httpx.Client,
        jwks_uri: str | None,
        production_like: bool,
        ttl: timedelta = JWKS_TTL,
        refetch_min_interval: timedelta = JWKS_REFETCH_MIN_INTERVAL,
        stale_grace: timedelta = JWKS_STALE_GRACE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer
        self._http = http_client
        self._jwks_uri = jwks_uri
        self._production_like = production_like
        self._ttl = ttl.total_seconds()
        self._min_interval = refetch_min_interval.total_seconds()
        self._stale_grace = stale_grace.total_seconds()
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None  # last successful fetch
        self._attempted_at: float | None = None  # last attempt (success or failure)

    def get(self, kid: str) -> jwt.PyJWK:
        # The lock is held across the (bounded-timeout) fetch on purpose: concurrent requests
        # wait for one fetch instead of stampeding the IdP.
        with self._lock:
            now = self._clock()
            if self._fetched_at is None or now - self._fetched_at >= self._ttl:
                self._scheduled_refresh(now)
            key = self._keys.get(kid)
            if key is not None:
                return key
            # Unknown kid: maybe the IdP rotated keys. Refetch, but at most once per interval.
            if self._attempted_at is not None and now - self._attempted_at < self._min_interval:
                raise Unauthenticated(_GENERIC_REJECTION)
            try:
                self._refresh(now)
            except _JwksUnavailable:
                raise ServiceUnavailable(_IDP_UNAVAILABLE) from None
            key = self._keys.get(kid)
            if key is None:
                raise Unauthenticated(_GENERIC_REJECTION)
            return key

    def _scheduled_refresh(self, now: float) -> None:
        # Back off after a failed attempt so an IdP outage is not multiplied by request volume.
        backoff = self._min_interval if self._fetched_at is not None else COLD_START_RETRY_S
        recently_failed = (
            self._attempted_at is not None
            and (self._fetched_at is None or self._attempted_at > self._fetched_at)
            and now - self._attempted_at < backoff
        )
        if not recently_failed:
            try:
                self._refresh(now)
                return
            except _JwksUnavailable:
                pass
        if self._fetched_at is None or now - self._fetched_at >= self._ttl + self._stale_grace:
            raise ServiceUnavailable(_IDP_UNAVAILABLE)
        # stale-if-error: keep serving the last good key set for a bounded time.

    def _refresh(self, now: float) -> None:
        self._attempted_at = now
        try:
            uri = self._jwks_uri or self._discover()
            document = self._get_json(uri)
        except _JwksUnavailable:
            logger.warning("jwks_fetch_failed", extra={"issuer_host": self._issuer_host})
            raise
        keys = self._parse(document)
        if not keys:
            logger.warning("jwks_no_usable_keys", extra={"issuer_host": self._issuer_host})
            raise _JwksUnavailable
        self._keys = keys
        self._fetched_at = now

    @property
    def _issuer_host(self) -> str:
        return urlsplit(self._issuer).hostname or ""

    def _discover(self) -> str:
        document = self._get_json(self._issuer.rstrip("/") + "/.well-known/openid-configuration")
        # OpenID Connect Discovery 1.0 §4.3: the issuer in the document MUST match exactly.
        if document.get("issuer") != self._issuer:
            logger.error("oidc_discovery_issuer_mismatch", extra={"issuer_host": self._issuer_host})
            raise _JwksUnavailable
        uri = document.get("jwks_uri")
        if not isinstance(uri, str) or (self._production_like and urlsplit(uri).scheme != "https"):
            raise _JwksUnavailable
        self._jwks_uri = uri
        return uri

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            with self._http.stream("GET", url) as response:
                if response.status_code != 200:
                    raise _JwksUnavailable
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DOCUMENT_BYTES:
                        raise _JwksUnavailable
            document = json.loads(bytes(body))
        except (httpx.HTTPError, ValueError) as exc:
            raise _JwksUnavailable from exc
        if not isinstance(document, dict):
            raise _JwksUnavailable
        return document

    @staticmethod
    def _parse(document: Mapping[str, Any]) -> dict[str, jwt.PyJWK]:
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list):
            return {}
        keys: dict[str, jwt.PyJWK] = {}
        for entry in raw_keys:
            if not isinstance(entry, dict):
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str) or not kid or entry.get("use", "sig") != "sig":
                continue
            if entry.get("kty") not in ("RSA", "EC"):
                continue  # never accept symmetric ("oct") keys from a JWKS
            if "alg" in entry and entry["alg"] not in ALLOWED_ALGORITHMS:
                continue
            try:
                key = jwt.PyJWK(entry)
            except (PyJWTError, ValueError, TypeError, KeyError):
                continue
            if key.algorithm_name not in ALLOWED_ALGORITHMS:
                continue
            if entry["kty"] == "RSA" and getattr(key.key, "key_size", 0) < MIN_RSA_BITS:
                continue
            keys[kid] = key
        return keys


class TokenVerifier:
    """Verifies access tokens from one OIDC issuer for one audience/app client."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        production_like: bool,
        http_client: httpx.Client | None = None,
        jwks_uri: str | None = None,
        mfa_policy: MfaClaimPolicy | None = None,
        leeway: timedelta = DEFAULT_LEEWAY,
        max_lifetime: timedelta = DEFAULT_MAX_LIFETIME,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not issuer.strip():
            raise ValueError("OIDC issuer must be set")
        if not audience.strip():
            raise ValueError("OIDC audience must be set")
        if production_like and is_dev_issuer(issuer):
            raise ValueError(
                "OIDC issuer points at a development identity provider or is not https; "
                "refusing to trust it in staging/prod"
            )
        if production_like and jwks_uri is not None and is_dev_issuer(jwks_uri):
            raise ValueError("JWKS URI for the OIDC issuer must be https in staging/prod")
        self.issuer = issuer
        self.audience = audience
        self._mfa = mfa_policy or MfaClaimPolicy()
        self._leeway = leeway
        self._max_lifetime = max_lifetime.total_seconds()
        self._jwks = JwksCache(
            issuer=issuer,
            http_client=http_client or default_http_client(),
            jwks_uri=jwks_uri,
            production_like=production_like,
            clock=clock,
        )

    def __repr__(self) -> str:
        return f"TokenVerifier(issuer={self.issuer!r}, audience={self.audience!r})"

    def verify(self, token: str) -> VerifiedToken:
        """Return the verified token or raise ``Unauthenticated`` / ``ServiceUnavailable``."""
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES:
            raise Unauthenticated(_GENERIC_REJECTION)
        try:
            header = jwt.get_unverified_header(token)
        except PyJWTError:
            raise Unauthenticated(_GENERIC_REJECTION) from None
        alg = header.get("alg")
        kid = header.get("kid")
        typ = header.get("typ")
        # Cheap structural checks first: nothing below may trigger a JWKS fetch for junk.
        if alg not in ALLOWED_ALGORITHMS or not isinstance(kid, str) or not kid:
            raise Unauthenticated(_GENERIC_REJECTION)
        if typ is not None and (not isinstance(typ, str) or typ.lower() not in ACCEPTED_TYP):
            raise Unauthenticated(_GENERIC_REJECTION)

        jwk = self._jwks.get(kid)
        if jwk.algorithm_name != alg:
            raise Unauthenticated(_GENERIC_REJECTION)
        try:
            claims = jwt.decode(
                token,
                key=jwk.key,
                algorithms=[alg],
                issuer=self.issuer,
                leeway=self._leeway,
                options={"require": list(REQUIRED_CLAIMS), "verify_aud": False},
            )
        except ExpiredSignatureError:
            raise Unauthenticated(_GENERIC_REJECTION, code="token_expired") from None
        except (PyJWTError, ValueError, TypeError):
            raise Unauthenticated(_GENERIC_REJECTION) from None
        return self._build(claims)

    def _check_audience(self, claims: Mapping[str, Any]) -> None:
        token_use = claims.get("token_use")
        if token_use is not None and token_use != ACCESS_TOKEN_USE:
            raise Unauthenticated(_GENERIC_REJECTION)  # e.g. a Cognito ID token
        aud = claims.get("aud")
        if aud is not None:
            audiences = [aud] if isinstance(aud, str) else aud
            if not isinstance(audiences, list) or self.audience not in audiences:
                raise Unauthenticated(_GENERIC_REJECTION)
            return
        # Amazon Cognito access tokens: no `aud`; `client_id` names the app client.
        if token_use == ACCESS_TOKEN_USE and claims.get("client_id") == self.audience:
            return
        raise Unauthenticated(_GENERIC_REJECTION)

    def _build(self, claims: dict[str, Any]) -> VerifiedToken:
        self._check_audience(claims)
        sub, iat, exp = claims["sub"], claims["iat"], claims["exp"]
        if not isinstance(sub, str) or not sub.strip():
            raise Unauthenticated(_GENERIC_REJECTION)
        if not (_is_number(iat) and _is_number(exp)) or exp <= iat:
            raise Unauthenticated(_GENERIC_REJECTION)
        if exp - iat > self._max_lifetime:
            logger.warning("access_token_lifetime_too_long", extra={"lifetime_s": exp - iat})
            raise Unauthenticated(_GENERIC_REJECTION)

        auth_time: datetime | None = None
        raw_auth_time = claims.get("auth_time")
        if raw_auth_time is not None:
            if not _is_number(raw_auth_time):
                raise Unauthenticated(_GENERIC_REJECTION)
            if raw_auth_time > time.time() + self._leeway.total_seconds():
                raise Unauthenticated(_GENERIC_REJECTION)
            auth_time = _utc(raw_auth_time)

        session_id = claims.get("sid") or claims.get("origin_jti")
        token_id = claims.get("jti")
        return VerifiedToken(
            subject=sub,
            issuer=self.issuer,
            issued_at=_utc(iat),
            expires_at=_utc(exp),
            auth_time=auth_time,
            mfa=self._mfa.evaluate(claims),
            session_id=session_id if isinstance(session_id, str) else None,
            token_id=token_id if isinstance(token_id, str) else None,
            claims=MappingProxyType(claims),
        )


def build_tenant_verifier(settings: Settings) -> TokenVerifier:
    """Verifier for school staff (SOS_OIDC_ISSUER / SOS_OIDC_AUDIENCE)."""
    return TokenVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        production_like=settings.is_production_like,
        jwks_uri=settings.oidc_jwks_uri,
    )


def build_platform_verifier(settings: Settings) -> TokenVerifier:
    """Verifier for platform operators (SOS_PLATFORM_OIDC_*): separate pool/app client."""
    return TokenVerifier(
        issuer=settings.platform_oidc_issuer,
        audience=settings.platform_oidc_audience,
        production_like=settings.is_production_like,
        jwks_uri=settings.platform_oidc_jwks_uri,
    )


@lru_cache(maxsize=1)
def get_tenant_token_verifier() -> TokenVerifier:
    """FastAPI dependency: process-wide verifier (the JWKS cache lives inside it)."""
    return build_tenant_verifier(get_settings())


@lru_cache(maxsize=1)
def get_platform_token_verifier() -> TokenVerifier:
    return build_platform_verifier(get_settings())
