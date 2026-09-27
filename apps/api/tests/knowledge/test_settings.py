"""Knowledge settings and their staging/prod start-up guards (SEC-009, SEC-020, invariant 10).

``SOS_KB_ENABLED`` defaults to off; ``SOS_KB_PROVIDER_MODE`` unset means the offline fake in
local/CI and live providers in staging/prod, where the fake is refused. Live AI in staging/prod
needs an organization API key (never a placeholder). Model IDs and thresholds are NOT settings:
they live in ``app/knowledge/config/*.yaml`` (invariant 13).
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, KeyWrapperKind, KnowledgeProviderMode, Settings


def _prod(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": Environment.PROD,
        "key_wrapper": KeyWrapperKind.KMS,
        "database_url": SecretStr("postgresql+psycopg://sos_app:x@db:5432/schoolos"),
        "platform_database_url": SecretStr("postgresql+psycopg://sos_platform:y@db:5432/schoolos"),
        "service_token_key": SecretStr("k" * 48),
        "billing_supplier_legal_name": "Example Technologies Private Limited",
        "billing_supplier_gstin": "37ABCDE1234F1Z5",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_SEC_020_knowledge_is_off_and_keyless_by_default() -> None:
    s = Settings(env=Environment.LOCAL)
    assert s.kb_enabled is False
    assert s.kb_provider_mode is None
    assert s.anthropic_api_key is None
    assert s.embeddings_api_key is None


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (Environment.LOCAL, KnowledgeProviderMode.FAKE),
        (Environment.CI, KnowledgeProviderMode.FAKE),
    ],
)
def test_SEC_020_unset_mode_is_the_offline_fake_locally(
    env: Environment, expected: KnowledgeProviderMode
) -> None:
    assert Settings(env=env).resolved_kb_provider_mode is expected


def test_SEC_020_unset_mode_is_live_in_prod() -> None:
    assert _prod().resolved_kb_provider_mode is KnowledgeProviderMode.LIVE


def test_SEC_020_local_may_opt_into_live_providers() -> None:
    s = Settings(env=Environment.LOCAL, kb_provider_mode=KnowledgeProviderMode.LIVE)
    assert s.resolved_kb_provider_mode is KnowledgeProviderMode.LIVE


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_SEC_020_fake_providers_are_refused_in_staging_and_prod(env: Environment) -> None:
    with pytest.raises(ValidationError, match="SOS_KB_PROVIDER_MODE=fake"):
        _prod(env=env, kb_provider_mode=KnowledgeProviderMode.FAKE)


def test_SEC_020_prod_with_knowledge_off_needs_no_key() -> None:
    """Dedicated hosts ship with an empty key until the school buys AI (compose ``:-``)."""
    s = _prod(anthropic_api_key=SecretStr(""))
    assert s.kb_enabled is False


@pytest.mark.parametrize("key", [None, "", "   "])
def test_SEC_020_prod_with_knowledge_on_needs_an_api_key(key: str | None) -> None:
    with pytest.raises(ValidationError, match="SOS_ANTHROPIC_API_KEY"):
        _prod(kb_enabled=True, anthropic_api_key=None if key is None else SecretStr(key))


def test_SEC_020_prod_refuses_a_placeholder_api_key() -> None:
    with pytest.raises(ValidationError, match="anthropic_api_key"):
        _prod(kb_enabled=True, anthropic_api_key=SecretStr("dev-only-anthropic-key"))


def test_SEC_020_prod_accepts_knowledge_with_a_key() -> None:
    s = _prod(kb_enabled=True, anthropic_api_key=SecretStr("synthetic-organization-key-0123"))
    assert s.kb_enabled is True
    assert s.resolved_kb_provider_mode is KnowledgeProviderMode.LIVE


def test_SEC_009_api_keys_are_secret_and_never_rendered() -> None:
    s = Settings(anthropic_api_key=SecretStr("synthetic-key-abc"))
    assert "synthetic-key-abc" not in repr(s)
    assert "synthetic-key-abc" not in str(s.model_dump())
