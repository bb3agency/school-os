"""OIDC access-token verification (FR-IAM-001, FR-IAM-003, FR-IAM-004, SEC-004, SEC-005; threat T2).

All keys are generated per test session; the JWKS and discovery documents are served through
``httpx.MockTransport`` so no network is used. Subjects and claims are synthetic.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from pydantic import SecretStr

from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.errors import ServiceUnavailable, Unauthenticated
from app.identity.tokens import (
    JWKS_REFETCH_MIN_INTERVAL,
    JWKS_TTL,
    MfaClaimPolicy,
    TokenVerifier,
    build_platform_verifier,
    build_tenant_verifier,
    is_dev_issuer,
)

ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHETIC"
AUDIENCE = "synthclient0001"
DISCOVERY_URL = ISSUER + "/.well-known/openid-configuration"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
SUBJECT = "00000000-0000-4000-8000-000000000001"


# --------------------------------------------------------------------------- helpers
@dataclass(frozen=True)
class SigningKey:
    kid: str
    alg: str
    private_key: Any
    public_jwk: dict[str, Any]

    @property
    def public_pem(self) -> bytes:
        return bytes(
            self.private_key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )


def make_rsa_key(kid: str, bits: int = 2048) -> SigningKey:
    private = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return SigningKey(kid, "RS256", private, jwk)


def make_ec_key(kid: str) -> SigningKey:
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(ECAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
    return SigningKey(kid, "ES256", private, jwk)


@pytest.fixture(scope="module")
def rsa_key() -> SigningKey:
    return make_rsa_key("rsa-2026-09")


@pytest.fixture(scope="module")
def ec_key() -> SigningKey:
    return make_ec_key("ec-2026-09")


@dataclass
class FakeIdp:
    """Serves discovery + JWKS; counts fetches; can be switched off."""

    keys: list[dict[str, Any]]
    issuer: str = ISSUER
    jwks_fetches: int = 0
    discovery_fetches: int = 0
    mode: str = "up"  # up | http500 | connect_error | garbage
    extra_keys: list[dict[str, Any]] = field(default_factory=list)

    requests: int = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.mode == "connect_error":
            raise httpx.ConnectError(
                "synthetic connect failure to internal-idp.example", request=request
            )
        if self.mode == "http500":
            return httpx.Response(500, text="upstream exploded at internal-idp.example")
        if self.mode == "garbage":
            return httpx.Response(200, text="<html>not json</html>")
        url = str(request.url)
        if url == DISCOVERY_URL:
            self.discovery_fetches += 1
            return httpx.Response(200, json={"issuer": self.issuer, "jwks_uri": JWKS_URL})
        if url == JWKS_URL:
            self.jwks_fetches += 1
            return httpx.Response(200, json={"keys": [*self.keys, *self.extra_keys]})
        return httpx.Response(404)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_verifier(
    idp: FakeIdp,
    clock: FakeClock | None = None,
    **kwargs: Any,
) -> TokenVerifier:
    return TokenVerifier(
        issuer=kwargs.pop("issuer", ISSUER),
        audience=kwargs.pop("audience", AUDIENCE),
        production_like=kwargs.pop("production_like", False),
        http_client=httpx.Client(transport=httpx.MockTransport(idp.handler)),
        clock=clock or FakeClock(),
        **kwargs,
    )


def mint(key: SigningKey, headers: dict[str, Any] | None = None, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 600,
        "auth_time": now - 60,
        "jti": "synthetic-jti-0001",
    }
    for name, value in overrides.items():
        if value is None:
            claims.pop(name, None)
        else:
            claims[name] = value
    # RFC 9068 access tokens say so in the header (audit 2026-10-05: a token must be marked as an
    # access token, by ``typ`` or by Cognito's ``token_use``); tests override it explicitly.
    hdr = {"kid": key.kid, "typ": "at+jwt", **(headers or {})}
    return jwt.encode(claims, key.private_key, algorithm=key.alg, headers=hdr)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forge_hs256(secret: bytes, header: dict[str, Any], claims: dict[str, Any]) -> str:
    signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
    sig = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(sig)}"


@pytest.fixture
def idp(rsa_key: SigningKey, ec_key: SigningKey) -> FakeIdp:
    return FakeIdp(keys=[rsa_key.public_jwk, ec_key.public_jwk])


# --------------------------------------------------------------------------- happy paths
def test_FR_IAM_001_valid_rs256_access_token_is_accepted(idp: FakeIdp, rsa_key: SigningKey) -> None:
    token = make_verifier(idp).verify(mint(rsa_key, sid="synthetic-session-1"))
    assert token.subject == SUBJECT
    assert token.issuer == ISSUER
    assert token.session_id == "synthetic-session-1"
    assert token.token_id == "synthetic-jti-0001"
    assert token.auth_time is not None
    assert token.auth_time.tzinfo is not None
    assert token.expires_at > token.issued_at
    assert token.claims["aud"] == AUDIENCE


def test_FR_IAM_001_valid_es256_access_token_is_accepted(idp: FakeIdp, ec_key: SigningKey) -> None:
    assert make_verifier(idp).verify(mint(ec_key)).subject == SUBJECT


def test_FR_IAM_001_cognito_access_token_with_client_id_is_accepted(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    raw = mint(
        rsa_key,
        aud=None,
        client_id=AUDIENCE,
        token_use="access",
        origin_jti="synthetic-origin-jti",
        scope="openid",
    )
    token = make_verifier(idp).verify(raw)
    assert token.subject == SUBJECT
    # Cognito access tokens have no `sid`; origin_jti is stable across refreshes of one sign-in.
    assert token.session_id == "synthetic-origin-jti"


def test_FR_IAM_001_cognito_id_token_presented_as_access_token_is_rejected(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    # A Cognito ID token has aud == app client id, so only token_use tells them apart.
    raw = mint(rsa_key, aud=AUDIENCE, token_use="id")
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)


def test_FR_IAM_001_client_id_without_access_token_use_is_rejected(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(rsa_key, aud=None, client_id=AUDIENCE))


def test_FR_IAM_001_cognito_access_token_for_other_client_is_rejected(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    raw = mint(rsa_key, aud=None, client_id="someotherclient", token_use="access")
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)


def test_FR_IAM_001_audience_list_containing_ours_is_accepted(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    raw = mint(rsa_key, aud=["another-api", AUDIENCE])
    assert make_verifier(idp).verify(raw).subject == SUBJECT


def test_FR_IAM_001_id_token_typ_header_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    raw = mint(rsa_key, headers={"typ": "id_token+jwt"})
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)


def test_FR_IAM_001_rfc9068_at_jwt_typ_is_accepted(idp: FakeIdp, rsa_key: SigningKey) -> None:
    raw = mint(rsa_key, headers={"typ": "at+jwt"})
    assert make_verifier(idp).verify(raw).subject == SUBJECT


@pytest.mark.parametrize("typ", ["JWT", "jwt", None])
def test_FR_IAM_001_a_token_not_marked_as_access_token_is_rejected(
    idp: FakeIdp, rsa_key: SigningKey, typ: str | None
) -> None:
    """Audit 2026-10-05 (platform, hardening "ID tokens with non-Cognito IdPs"): an IdP that
    puts the client id in ``aud`` and sets no ``token_use`` issues ID tokens that look like
    access tokens. A token must say it is an access token: header ``typ`` ``at+jwt`` (RFC 9068)
    or the claim ``token_use = access`` (Cognito, the dev stub)."""
    raw = mint(rsa_key, headers={"typ": typ})
    assert jwt.get_unverified_header(raw).get("typ") == typ
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)


@pytest.mark.parametrize(
    ("typ", "token_use"),
    [("JWT", "access"), (None, "access"), ("application/at+jwt", None), ("AT+JWT", None)],
)
def test_FR_IAM_001_access_token_marked_by_typ_or_token_use_is_accepted(
    idp: FakeIdp, rsa_key: SigningKey, typ: str | None, token_use: str | None
) -> None:
    raw = mint(rsa_key, headers={"typ": typ}, token_use=token_use)
    assert make_verifier(idp).verify(raw).subject == SUBJECT


# --------------------------------------------------------------------------- claim checks
def test_FR_IAM_004_expired_token_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    now = int(time.time())
    raw = mint(rsa_key, iat=now - 700, exp=now - 100, auth_time=now - 700)
    with pytest.raises(Unauthenticated) as exc:
        make_verifier(idp).verify(raw)
    assert exc.value.code == "token_expired"


def test_FR_IAM_004_expiry_within_30s_leeway_is_accepted(idp: FakeIdp, rsa_key: SigningKey) -> None:
    now = int(time.time())
    raw = mint(rsa_key, iat=now - 590, exp=now - 10, auth_time=now - 600)
    assert make_verifier(idp).verify(raw).subject == SUBJECT


def test_FR_IAM_004_not_yet_valid_token_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    now = int(time.time())
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(rsa_key, nbf=now + 300))


def test_FR_IAM_004_token_issued_in_the_future_is_rejected(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    now = int(time.time())
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(rsa_key, iat=now + 300, exp=now + 800))


def test_FR_IAM_001_wrong_issuer_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    raw = mint(rsa_key, iss="https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_OTHER")
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)


def test_FR_IAM_001_wrong_audience_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(rsa_key, aud="schoolos-platform"))


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "sub"])
def test_FR_IAM_004_missing_required_claim_is_rejected(
    idp: FakeIdp, rsa_key: SigningKey, claim: str
) -> None:
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(rsa_key, **{claim: None}))


def test_FR_IAM_004_empty_subject_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(rsa_key, sub=""))


def test_FR_IAM_004_excessive_lifetime_is_rejected_as_misconfigured(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    now = int(time.time())
    raw = mint(rsa_key, iat=now, exp=now + 3600)  # IdP default of 1 h violates FR-IAM-004
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)


def test_FR_IAM_004_lifetime_of_exactly_15_minutes_is_accepted(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    now = int(time.time())
    raw = mint(rsa_key, iat=now, exp=now + 900)
    assert make_verifier(idp).verify(raw).subject == SUBJECT


def test_FR_IAM_003_auth_time_in_the_future_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    now = int(time.time())
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(rsa_key, auth_time=now + 600))


def test_FR_IAM_003_non_numeric_auth_time_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(rsa_key, auth_time="yesterday"))


def test_FR_IAM_003_missing_auth_time_gives_none(idp: FakeIdp, rsa_key: SigningKey) -> None:
    assert make_verifier(idp).verify(mint(rsa_key, auth_time=None)).auth_time is None


# --------------------------------------------------------------------------- algorithms / keys
def test_SEC_004_alg_none_is_rejected_without_fetching_jwks(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": SUBJECT, "aud": AUDIENCE, "iat": now, "exp": now + 300}
    header = b64url(json.dumps({"alg": "none", "kid": rsa_key.kid}).encode())
    raw = f"{header}.{b64url(json.dumps(claims).encode())}."
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)
    assert idp.jwks_fetches == 0


def test_SEC_004_hs256_signed_with_public_key_is_rejected(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    """Classic key-confusion: HMAC keyed with the RSA public key the attacker can download."""
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": SUBJECT, "aud": AUDIENCE, "iat": now, "exp": now + 300}
    raw = forge_hs256(
        rsa_key.public_pem, {"alg": "HS256", "kid": rsa_key.kid, "typ": "JWT"}, claims
    )
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)
    assert idp.jwks_fetches == 0


@pytest.mark.parametrize("alg", ["HS256", "HS512", "RS512", "PS256", "ES384", "EdDSA"])
def test_SEC_004_algorithms_outside_allowlist_are_rejected(
    idp: FakeIdp, rsa_key: SigningKey, alg: str
) -> None:
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": SUBJECT, "aud": AUDIENCE, "iat": now, "exp": now + 300}
    raw = forge_hs256(b"x" * 32, {"alg": alg, "kid": rsa_key.kid}, claims)
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)
    assert idp.jwks_fetches == 0


def test_SEC_004_header_alg_must_match_jwk(
    idp: FakeIdp, rsa_key: SigningKey, ec_key: SigningKey
) -> None:
    # Signed with the EC key but pointing at the RSA kid.
    confused = SigningKey(rsa_key.kid, "ES256", ec_key.private_key, ec_key.public_jwk)
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(confused))


def test_SEC_004_token_signed_by_unknown_key_with_known_kid_is_rejected(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    attacker = make_rsa_key(rsa_key.kid)
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(mint(attacker))


def test_SEC_004_token_without_kid_is_rejected(idp: FakeIdp, rsa_key: SigningKey) -> None:
    now = int(time.time())
    raw = jwt.encode(
        {"iss": ISSUER, "sub": SUBJECT, "aud": AUDIENCE, "iat": now, "exp": now + 300},
        rsa_key.private_key,
        algorithm="RS256",
    )
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
def test_SEC_004_weak_and_symmetric_jwks_entries_are_ignored(rsa_key: SigningKey) -> None:
    weak = make_rsa_key("weak-1024", bits=1024)
    oct_key = {"kty": "oct", "kid": "shared", "k": b64url(b"s" * 32), "alg": "HS256"}
    enc_key = {**rsa_key.public_jwk, "kid": "enc-only", "use": "enc"}
    idp = FakeIdp(keys=[weak.public_jwk, oct_key, enc_key, rsa_key.public_jwk])
    verifier = make_verifier(idp)
    assert verifier.verify(mint(rsa_key)).subject == SUBJECT
    with pytest.raises(Unauthenticated):
        verifier.verify(mint(weak))
    enc_signer = SigningKey("enc-only", "RS256", rsa_key.private_key, enc_key)
    with pytest.raises(Unauthenticated):
        verifier.verify(mint(enc_signer))


@pytest.mark.parametrize("raw", ["", "not-a-jwt", "a.b.c", "x" * 20_000])
def test_SEC_004_malformed_or_oversized_tokens_are_rejected(idp: FakeIdp, raw: str) -> None:
    with pytest.raises(Unauthenticated):
        make_verifier(idp).verify(raw)
    assert idp.jwks_fetches == 0


# --------------------------------------------------------------------------- JWKS cache
def test_SEC_004_jwks_is_cached_for_one_hour(idp: FakeIdp, rsa_key: SigningKey) -> None:
    clock = FakeClock()
    verifier = make_verifier(idp, clock)
    for _ in range(5):
        verifier.verify(mint(rsa_key))
    assert (idp.discovery_fetches, idp.jwks_fetches) == (1, 1)
    clock.advance(JWKS_TTL.total_seconds() - 1)
    verifier.verify(mint(rsa_key))
    assert idp.jwks_fetches == 1
    clock.advance(2)
    verifier.verify(mint(rsa_key))
    assert idp.jwks_fetches == 2
    assert idp.discovery_fetches == 1


def test_SEC_004_unknown_kid_triggers_exactly_one_refetch_then_401(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    clock = FakeClock()
    verifier = make_verifier(idp, clock)
    verifier.verify(mint(rsa_key))
    assert idp.jwks_fetches == 1
    clock.advance(JWKS_REFETCH_MIN_INTERVAL.total_seconds() + 1)
    stranger = make_rsa_key("unknown-kid")
    with pytest.raises(Unauthenticated):
        verifier.verify(mint(stranger))
    assert idp.jwks_fetches == 2
    # Flood of random kids inside the 60 s window: no further fetches (no amplification).
    for i in range(20):
        with pytest.raises(Unauthenticated):
            verifier.verify(mint(SigningKey(f"random-{i}", "RS256", stranger.private_key, {})))
    assert idp.jwks_fetches == 2
    clock.advance(JWKS_REFETCH_MIN_INTERVAL.total_seconds() + 1)
    with pytest.raises(Unauthenticated):
        verifier.verify(mint(stranger))
    assert idp.jwks_fetches == 3


def test_SEC_004_rotated_key_is_picked_up_by_refetch(idp: FakeIdp, rsa_key: SigningKey) -> None:
    clock = FakeClock()
    verifier = make_verifier(idp, clock)
    verifier.verify(mint(rsa_key))
    clock.advance(JWKS_REFETCH_MIN_INTERVAL.total_seconds() + 1)
    rotated = make_rsa_key("rsa-2026-10")
    idp.extra_keys.append(rotated.public_jwk)
    assert verifier.verify(mint(rotated)).subject == SUBJECT
    assert idp.jwks_fetches == 2


def test_SEC_004_first_fetch_for_unknown_kid_counts_as_the_refetch(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    verifier = make_verifier(idp)
    with pytest.raises(Unauthenticated):
        verifier.verify(mint(make_rsa_key("never-seen")))
    assert idp.jwks_fetches == 1


@pytest.mark.parametrize("mode", ["http500", "connect_error", "garbage"])
def test_SEC_004_jwks_endpoint_down_maps_to_service_unavailable(
    idp: FakeIdp, rsa_key: SigningKey, mode: str
) -> None:
    idp.mode = mode
    with pytest.raises(ServiceUnavailable) as exc:
        make_verifier(idp).verify(mint(rsa_key))
    text = f"{exc.value} {exc.value.detail}"
    assert "internal-idp" not in text
    assert "cognito" not in text.lower()
    assert "exploded" not in text


def test_SEC_004_idp_outage_at_cold_start_is_not_amplified(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    clock = FakeClock()
    verifier = make_verifier(idp, clock)
    idp.mode = "connect_error"
    for _ in range(10):
        with pytest.raises(ServiceUnavailable):
            verifier.verify(mint(rsa_key))
    assert idp.requests == 1
    clock.advance(6)
    idp.mode = "up"
    assert verifier.verify(mint(rsa_key)).subject == SUBJECT


def test_SEC_004_stale_keys_are_used_briefly_when_refresh_fails(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    clock = FakeClock()
    verifier = make_verifier(idp, clock)
    verifier.verify(mint(rsa_key))
    idp.mode = "http500"
    clock.advance(JWKS_TTL.total_seconds() + 10)
    before = idp.requests
    for _ in range(5):
        assert verifier.verify(mint(rsa_key)).subject == SUBJECT  # stale-if-error
    assert idp.requests == before + 1  # one failed attempt, then back off
    clock.advance(JWKS_TTL.total_seconds())
    with pytest.raises(ServiceUnavailable):
        verifier.verify(mint(rsa_key))


def test_SEC_004_unknown_kid_while_idp_down_is_service_unavailable(
    idp: FakeIdp, rsa_key: SigningKey
) -> None:
    clock = FakeClock()
    verifier = make_verifier(idp, clock)
    verifier.verify(mint(rsa_key))
    clock.advance(JWKS_REFETCH_MIN_INTERVAL.total_seconds() + 1)
    idp.mode = "connect_error"
    with pytest.raises(ServiceUnavailable):
        verifier.verify(mint(make_rsa_key("rotated-while-down")))
    # Known keys keep working while the IdP is down.
    assert verifier.verify(mint(rsa_key)).subject == SUBJECT


def test_SEC_004_discovery_issuer_mismatch_fails_closed(rsa_key: SigningKey) -> None:
    idp = FakeIdp(keys=[rsa_key.public_jwk], issuer="https://evil.example/pool")
    with pytest.raises(ServiceUnavailable):
        make_verifier(idp).verify(mint(rsa_key))
    assert idp.jwks_fetches == 0


def test_SEC_004_explicit_jwks_uri_skips_discovery(idp: FakeIdp, rsa_key: SigningKey) -> None:
    verifier = make_verifier(idp, jwks_uri=JWKS_URL)
    verifier.verify(mint(rsa_key))
    assert (idp.discovery_fetches, idp.jwks_fetches) == (0, 1)


# --------------------------------------------------------------------------- MFA signal
@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"amr": ["pwd", "mfa"]}, True),
        ({"amr": "mfa"}, True),
        ({"amr": ["pwd"]}, False),
        ({"sos:mfa": "true"}, True),
        ({"sos:mfa": True}, True),
        ({"sos:mfa": "false"}, False),
        ({"sos:mfa": "yes"}, False),
        ({}, False),
    ],
)
def test_SEC_005_mfa_is_derived_from_amr_or_custom_claim(
    idp: FakeIdp, rsa_key: SigningKey, claims: dict[str, Any], expected: bool
) -> None:
    assert make_verifier(idp).verify(mint(rsa_key, **claims)).mfa is expected


def test_SEC_005_mfa_claim_name_is_configurable(idp: FakeIdp, rsa_key: SigningKey) -> None:
    policy = MfaClaimPolicy(amr_claim="acr_methods", flag_claim="custom:mfa")
    verifier = make_verifier(idp, mfa_policy=policy)
    assert verifier.verify(mint(rsa_key, acr_methods=["otp", "mfa"])).mfa is True
    custom: dict[str, Any] = {"custom:mfa": "true"}
    assert verifier.verify(mint(rsa_key, **custom)).mfa is True
    assert verifier.verify(mint(rsa_key, amr=["mfa"])).mfa is False


# --------------------------------------------------------------------------- hygiene
def test_SEC_004_verified_token_repr_excludes_raw_claims(idp: FakeIdp, rsa_key: SigningKey) -> None:
    token = make_verifier(idp).verify(mint(rsa_key, email="synthetic.clerk@example.test"))
    assert "synthetic.clerk" not in repr(token)
    assert "synthetic.clerk" not in str(token)
    with pytest.raises(TypeError):
        token.claims["sub"] = "tampered"  # type: ignore[index]


# --------------------------------------------------------------------------- configuration
@pytest.mark.parametrize(
    "issuer",
    [
        "http://localhost:8080/schoolos",
        "https://localhost/schoolos",
        "https://127.0.0.1:8443/x",
        "https://[::1]/x",
        "http://mock-oauth2-server:8080/schoolos",
        "https://mock-oauth2-server/platform",
        "http://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_X",  # plain http
    ],
)
def test_FR_IAM_001_dev_issuer_is_refused_when_production_like(issuer: str) -> None:
    assert is_dev_issuer(issuer)
    with pytest.raises(ValueError, match="issuer"):
        TokenVerifier(issuer=issuer, audience=AUDIENCE, production_like=True)


def test_FR_IAM_001_dev_issuer_is_allowed_locally() -> None:
    verifier = TokenVerifier(
        issuer="http://localhost:8080/schoolos", audience="schoolos-web", production_like=False
    )
    assert verifier.issuer == "http://localhost:8080/schoolos"


def test_FR_IAM_001_real_issuer_is_allowed_in_production() -> None:
    assert not is_dev_issuer(ISSUER)
    TokenVerifier(issuer=ISSUER, audience=AUDIENCE, production_like=True)


@pytest.mark.parametrize("audience", ["", " "])
def test_FR_IAM_001_blank_audience_is_refused(audience: str) -> None:
    with pytest.raises(ValueError, match="audience"):
        TokenVerifier(issuer=ISSUER, audience=audience, production_like=False)


def _prod_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": Environment.PROD,
        "key_wrapper": KeyWrapperKind.KMS,
        "database_url": SecretStr("postgresql+psycopg://sos_app:x@db:5432/schoolos?sslmode=verify-full"),
        "platform_database_url": SecretStr("postgresql+psycopg://sos_platform:y@db:5432/schoolos?sslmode=verify-full"),
        "service_token_key": SecretStr("k" * 48),
        "oidc_issuer": ISSUER,
        "oidc_audience": AUDIENCE,
        "platform_oidc_issuer": ISSUER.replace("SYNTHETIC", "PLATFORM"),
        "platform_oidc_audience": "synthplatform01",
    }
    values.update(overrides)
    return Settings(**values)


def test_FR_IAM_001_settings_build_separate_tenant_and_platform_verifiers() -> None:
    settings = _prod_settings()
    tenant = build_tenant_verifier(settings)
    platform = build_platform_verifier(settings)
    assert (tenant.issuer, tenant.audience) == (ISSUER, AUDIENCE)
    assert platform.issuer.endswith("PLATFORM")
    assert platform.audience == "synthplatform01"


@pytest.mark.parametrize(
    ("builder", "field_name"),
    [(build_tenant_verifier, "oidc_issuer"), (build_platform_verifier, "platform_oidc_issuer")],
)
def test_FR_IAM_001_settings_with_dev_issuer_refused_in_production(
    builder: Callable[[Settings], TokenVerifier], field_name: str
) -> None:
    settings = _prod_settings(**{field_name: "http://localhost:8080/schoolos"})
    with pytest.raises(ValueError, match="issuer"):
        builder(settings)


def test_FR_IAM_001_local_settings_defaults_build() -> None:
    settings = Settings(env=Environment.LOCAL)
    assert build_tenant_verifier(settings).issuer == settings.oidc_issuer
    assert build_platform_verifier(settings).issuer == settings.platform_oidc_issuer
