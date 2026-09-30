"""Knowledge settings and their staging/prod start-up guards (SEC-009, SEC-020, invariant 10).

``SOS_KB_ENABLED`` defaults to off; ``SOS_KB_PROVIDER_MODE`` unset means the offline fake in
local/CI and live providers in staging/prod, where the fake is refused. Live AI in staging/prod
needs an organization API key (never a placeholder). Model IDs and thresholds are NOT settings:
they live in ``app/knowledge/config/*.yaml`` (invariant 13).
"""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import (
    Environment,
    KeyWrapperKind,
    KnowledgeProviderMode,
    LlmCredentialsSource,
    Settings,
)


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


# Synthetic credential documents: the shapes only, no key material.
WIF = json.dumps(
    {
        "type": "external_account",
        "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/"
        "sos/providers/aws",
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/"
        "-/serviceAccounts/sos-vertex@sos-ai-prod.iam.gserviceaccount.com:generateAccessToken",
        "credential_source": {"environment_id": "aws1", "regional_cred_verification_url": "x"},
    }
)
SA_KEY = json.dumps(
    {
        "type": "service_account",
        "client_email": "sos-vertex@sos-ai-prod.iam.gserviceaccount.com",
        "private_key": "synthetic",
    }
)
PERSON = json.dumps({"type": "authorized_user", "client_id": "x", "refresh_token": "y"})


def _gemini(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "kb_enabled": True,
        "llm_gcp_project": "sos-ai-prod",
        "llm_gcp_credentials_source": LlmCredentialsSource.WORKLOAD_IDENTITY,
        "llm_gcp_credentials_json": SecretStr(WIF),
        "llm_zdr_confirmed": True,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("missing", "match"),
    [
        ({"llm_gcp_project": None}, "SOS_LLM_GCP_PROJECT"),
        ({"llm_gcp_credentials_json": None}, "SOS_LLM_GCP_CREDENTIALS_JSON"),
        ({"llm_gcp_credentials_source": None}, "SOS_LLM_GCP_CREDENTIALS_SOURCE"),
        ({"llm_zdr_confirmed": False}, "SOS_LLM_ZDR_CONFIRMED"),
    ],
)
def test_ADR_0033_prod_with_knowledge_on_needs_vertex_settings_and_zdr(
    missing: dict[str, object], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        _prod(**_gemini(**missing))


@pytest.mark.parametrize("location", ["global", "us-central1", "europe-west4", "asia-southeast1"])
def test_ADR_0033_prod_ai_stays_in_an_india_region(location: str) -> None:
    with pytest.raises(ValidationError, match="SOS_LLM_GCP_LOCATION must be an India region"):
        _prod(**_gemini(llm_gcp_location=location))


def test_ADR_0033_prod_may_not_skip_the_cache_config_check() -> None:
    with pytest.raises(ValidationError, match="SOS_LLM_VERIFY_CACHE_CONFIG"):
        _prod(**_gemini(llm_verify_cache_config=False))


@pytest.mark.parametrize("env", [Environment.LOCAL, Environment.PROD])
def test_invariant_10_a_persons_google_login_is_refused_everywhere(env: Environment) -> None:
    for source in LlmCredentialsSource:
        with pytest.raises(ValidationError, match="authorized_user"):
            _prod(
                **_gemini(
                    env=env,
                    llm_gcp_credentials_source=source,
                    llm_gcp_credentials_json=SecretStr(PERSON),
                )
            )


@pytest.mark.parametrize(
    ("source", "document", "match"),
    [
        (LlmCredentialsSource.SERVICE_ACCOUNT_KEY, WIF, "service_account key"),
        (LlmCredentialsSource.WORKLOAD_IDENTITY, SA_KEY, "external_account"),
        (
            LlmCredentialsSource.SERVICE_ACCOUNT_KEY,
            json.dumps({"type": "service_account", "client_email": "someone@gmail.com"}),
            "service account",
        ),
        (LlmCredentialsSource.WORKLOAD_IDENTITY, "AIzaSy-an-api-key", "not JSON"),
    ],
)
def test_invariant_10_credentials_must_be_the_named_service_identity(
    source: LlmCredentialsSource, document: str, match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        _prod(**_gemini(llm_gcp_credentials_source=source, llm_gcp_credentials_json=document))


def test_invariant_10_credentials_without_a_source_are_refused() -> None:
    with pytest.raises(ValidationError, match="SOS_LLM_GCP_CREDENTIALS_SOURCE"):
        Settings(llm_gcp_credentials_json=SecretStr(SA_KEY))


def test_SEC_020_prod_refuses_a_placeholder_api_key() -> None:
    with pytest.raises(ValidationError, match="anthropic_api_key"):
        _prod(kb_enabled=True, anthropic_api_key=SecretStr("dev-only-anthropic-key"))
    with pytest.raises(ValidationError, match="llm_gcp_credentials_json"):
        _prod(**_gemini(llm_gcp_credentials_json=SecretStr('{"dev-only": 1}')))


@pytest.mark.parametrize(
    ("source", "document"),
    [
        (LlmCredentialsSource.WORKLOAD_IDENTITY, WIF),
        (LlmCredentialsSource.SERVICE_ACCOUNT_KEY, SA_KEY),
    ],
)
def test_ADR_0033_prod_accepts_knowledge_on_vertex_with_a_service_identity(
    source: LlmCredentialsSource, document: str
) -> None:
    s = _prod(
        **_gemini(llm_gcp_credentials_source=source, llm_gcp_credentials_json=SecretStr(document))
    )
    assert s.kb_enabled is True
    assert s.llm_gcp_location == "asia-south1"
    assert s.resolved_kb_provider_mode is KnowledgeProviderMode.LIVE
    assert s.anthropic_api_key is None  # the Anthropic key is needed only for a fallback role


def test_SEC_009_google_credentials_are_secret_and_never_rendered() -> None:
    s = Settings(
        llm_gcp_credentials_source=LlmCredentialsSource.SERVICE_ACCOUNT_KEY,
        llm_gcp_credentials_json=SecretStr(SA_KEY),
    )
    assert "sos-vertex@" not in repr(s)
    assert "sos-vertex@" not in str(s.model_dump())


def test_SEC_009_api_keys_are_secret_and_never_rendered() -> None:
    s = Settings(anthropic_api_key=SecretStr("synthetic-key-abc"))
    assert "synthetic-key-abc" not in repr(s)
    assert "synthetic-key-abc" not in str(s.model_dump())
