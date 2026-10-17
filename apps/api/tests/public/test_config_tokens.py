"""Public handle configuration and keys (ADR-0046 decisions 3-4, ADR-0047; FR-PUB-005,
FR-PUB-006, SEC-037). Pure: no database."""

from __future__ import annotations

import base64
import hashlib

import pytest
from pydantic import ValidationError

from app.public import tokens
from app.public.config import MIN_PURGE_AFTER_DAYS, PARENT_KINDS, PublicConfig, rules


def test_FR_PUB_006_lifetimes_are_the_owner_decided_ones() -> None:
    cfg = rules()
    assert cfg.lifetimes_days == {
        "parent_notice": 30,
        "parent_receipt": 90,
        "parent_dues": 7,
        "parent_optin": 7,
    }
    assert cfg.purge_after_days == 30
    assert set(cfg.webhook_providers) == {"razorpay", "meta"}
    assert cfg.webhook_providers["razorpay"].max_body_bytes == 256 * 1024
    assert cfg.webhook_providers["meta"].max_body_bytes == 3 * 1024 * 1024


def test_FR_PUB_006_config_refuses_short_purge_and_missing_kinds() -> None:
    raw = rules().model_dump(mode="json")
    with pytest.raises(ValidationError):
        PublicConfig.model_validate({**raw, "purge_after_days": MIN_PURGE_AFTER_DAYS - 1})
    lifetimes = dict(raw["lifetimes_days"])
    lifetimes.pop("parent_dues")
    with pytest.raises(ValidationError, match="every parent kind"):
        PublicConfig.model_validate({**raw, "lifetimes_days": lifetimes})
    with pytest.raises(ValidationError):
        PublicConfig.model_validate({**raw, "parent_token_bytes": 16})


def test_FR_PUB_005_parent_tokens_are_256_bit_base64url_without_structure() -> None:
    seen = {tokens.new_parent_token() for _ in range(200)}
    assert len(seen) == 200
    for token in seen:
        assert len(token) == 43
        assert "=" not in token
        raw = base64.urlsafe_b64decode(token + "=")
        assert len(raw) == 32
        assert all(tokens.is_well_formed(kind, token) for kind in PARENT_KINDS)


def test_ADR_0047_route_values_are_24_letters_and_digits() -> None:
    route = tokens.new_route_token()
    assert len(route) == 24
    assert route.isalnum()
    assert tokens.is_well_formed("razorpay_route", route)
    assert not tokens.is_well_formed("parent_notice", route)


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("parent_notice", "short"),
        ("parent_notice", "a" * 42 + "="),
        ("parent_notice", "a" * 42 + "é"),
        ("parent_receipt", "a" * 44),
        ("razorpay_route", "abc-def-ghi-jkl-mno-pqr-"),
        ("cert_verify", "abc"),
        ("cert_verify", "abcd efgh ijkl"),
        ("meta_waba", "12ab"),
    ],
)
def test_SEC_037_malformed_keys_never_reach_a_lookup(kind: str, key: str) -> None:
    assert not tokens.is_well_formed(kind, key)  # type: ignore[arg-type]


def test_SEC_037_only_the_sha256_of_a_key_is_used() -> None:
    token = tokens.new_parent_token()
    assert tokens.key_hash(token) == hashlib.sha256(token.encode()).digest()
    assert len(tokens.key_hash(token)) == 32
