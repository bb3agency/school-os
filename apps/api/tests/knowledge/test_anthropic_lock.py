"""Claude safety lock (owner decision 2026-10-01; SEC-020, invariant 10, docs/08 §8, docs/10 §11).

In staging and prod the API and the worker refuse to start while any role of
``app/knowledge/config/models.yaml`` (including a fallback that has been applied, which makes the
role's provider ``anthropic``) sends traffic to Anthropic, unless ``SOS_ANTHROPIC_ZDR_CONFIRMED``
says the Anthropic Zero Data Retention agreement and DPA are in place. Local and CI are free to use
the fake provider (and any configured provider). Gemini-only configuration is unaffected.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import Environment, KeyWrapperKind, Settings
from app.knowledge import service as knowledge
from app.knowledge.config.llm import load_llm_config
from app.knowledge.gateway import factory
from app.knowledge.gateway.factory import ProviderModeError, require_provider_agreements


def _settings(env: Environment, **overrides: object) -> Settings:
    base: dict[str, object] = {"env": env}
    if env in (Environment.STAGING, Environment.PROD):
        base.update(
            key_wrapper=KeyWrapperKind.KMS,
            database_url=SecretStr(
                "postgresql+psycopg://sos_app:x@db:5432/schoolos?sslmode=verify-full"
            ),
            platform_database_url=SecretStr(
                "postgresql+psycopg://sos_platform:y@db:5432/schoolos?sslmode=verify-full"
            ),
            service_token_key=SecretStr("k" * 48),
            billing_supplier_legal_name="Example Technologies Private Limited",
            billing_supplier_gstin="37ABCDE1234F1Z5",
        )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


PRODUCTION_LIKE = [Environment.STAGING, Environment.PROD]


def test_SEC_020_anthropic_zdr_is_unconfirmed_by_default() -> None:
    assert Settings(env=Environment.LOCAL).anthropic_zdr_confirmed is False


def test_SEC_020_shipped_config_is_gemini_only() -> None:
    assert load_llm_config().anthropic_roles() == ()


def test_SEC_020_applied_fallback_counts_as_anthropic() -> None:
    config = load_llm_config().use_fallback(["answer"])
    assert config.anthropic_roles() == ("answer",)


@pytest.mark.parametrize("env", PRODUCTION_LIKE)
def test_SEC_020_anthropic_role_is_refused_without_confirmation(env: Environment) -> None:
    config = load_llm_config().use_fallback(["answer", "router"])
    with pytest.raises(ProviderModeError) as caught:
        require_provider_agreements(_settings(env), config)
    message = str(caught.value)
    assert "answer, router" in message
    assert "SOS_ANTHROPIC_ZDR_CONFIRMED=true" in message
    assert "DPA" in message
    assert "models.yaml" in message  # the other way out: switch the roles back to gemini


@pytest.mark.parametrize("env", PRODUCTION_LIKE)
def test_SEC_020_eval_judge_on_anthropic_also_needs_confirmation(env: Environment) -> None:
    """Any role counts, the offline eval judge too (fail closed)."""
    config = load_llm_config().use_fallback(["eval_judge"])
    with pytest.raises(ProviderModeError, match="eval_judge"):
        require_provider_agreements(_settings(env), config)


@pytest.mark.parametrize("env", PRODUCTION_LIKE)
def test_SEC_020_anthropic_role_is_allowed_when_confirmed(env: Environment) -> None:
    config = load_llm_config().use_fallback()
    require_provider_agreements(_settings(env, anthropic_zdr_confirmed=True), config)


@pytest.mark.parametrize("env", PRODUCTION_LIKE)
def test_SEC_020_gemini_only_config_needs_no_anthropic_confirmation(env: Environment) -> None:
    require_provider_agreements(_settings(env), load_llm_config())


@pytest.mark.parametrize("env", [Environment.LOCAL, Environment.CI])
def test_SEC_020_local_and_ci_may_use_anthropic_roles_freely(env: Environment) -> None:
    require_provider_agreements(_settings(env), load_llm_config().use_fallback())


def test_SEC_020_live_anthropic_transport_is_refused_without_confirmation() -> None:
    """Defence in depth: building the transports checks again (a Settings built elsewhere)."""
    settings = _settings(
        Environment.PROD, kb_enabled=False, anthropic_api_key=SecretStr("sk-synthetic")
    )
    with pytest.raises(ProviderModeError, match="SOS_ANTHROPIC_ZDR_CONFIRMED"):
        factory.build_transports(settings, load_llm_config().use_fallback())


def test_SEC_020_api_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setattr(knowledge, "load_llm_config", lambda: load_llm_config().use_fallback())
    with pytest.raises(ProviderModeError, match="SOS_ANTHROPIC_ZDR_CONFIRMED"):
        create_app(_settings(Environment.PROD))
    create_app(_settings(Environment.PROD, anthropic_zdr_confirmed=True))


def test_SEC_020_worker_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from sos_worker import celery_app

    monkeypatch.setattr(knowledge, "load_llm_config", lambda: load_llm_config().use_fallback())
    monkeypatch.setattr(celery_app, "get_settings", lambda: _settings(Environment.STAGING))
    with pytest.raises(ProviderModeError, match="SOS_ANTHROPIC_ZDR_CONFIRMED"):
        celery_app.create_celery()
