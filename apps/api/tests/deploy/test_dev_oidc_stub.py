"""The dev OIDC stub stays local (SEC-004, SEC-005, FR-IAM-001, ADR-0012, ADR-0018).

``infra/docker/oidc.json`` makes the local stub (mock-oauth2-server) issue staff tokens that
already say MFA was done (``sos:mfa``/``amr``), so a developer only types a synthetic subject.
That is safe only because the stub can never be what a staging/prod API or BFF trusts. These
tests pin that down by reading the repository files (no Docker, Terraform or network):

- the stub image and ``oidc.json`` appear only in the root ``docker-compose.yml``, in one service
  that is in the ``dev`` profile, publishes on loopback only and nothing else depends on;
- no deploy file (Terraform, dedicated compose, Dockerfiles, CI deploy workflows) mentions them;
- every issuer the stub serves (as the containers, the host processes and the browser see it) is a
  development issuer that the API refuses when ``SOS_ENV`` is staging or prod.

The application's MFA and step-up checks are unchanged and tested in ``tests/identity``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from app.core.config import Environment, KeyWrapperKind, Settings
from app.identity.tokens import (
    TokenVerifier,
    build_platform_verifier,
    build_support_verifier,
    build_tenant_verifier,
    is_dev_issuer,
)

REPO = Path(__file__).resolve().parents[4]
STUB_CONFIG = REPO / "infra" / "docker" / "oidc.json"
ROOT_COMPOSE = REPO / "docker-compose.yml"
ENV_EXAMPLE = REPO / ".env.example"
STUB_IMAGE = "mock-oauth2-server"
STUB_MARKERS = (STUB_IMAGE, "oidc.json", "oidc.localhost", "infra/docker")

# Everything that builds or configures a staging/prod/dedicated deployment.
DEPLOY_ROOTS = (
    REPO / "infra" / "terraform",
    REPO / "deploy",
)
DEPLOY_FILES = (
    REPO / "apps" / "api" / "Dockerfile",
    REPO / "apps" / "web" / "Dockerfile",
    REPO / ".github" / "workflows" / "deploy-staging.yml",
    REPO / ".github" / "workflows" / "deploy-dedicated.yml",
)
TEXT_SUFFIXES = frozenset(
    {".tf", ".tfvars", ".tftpl", ".yaml", ".yml", ".sh", ".json", ".md", ".template", ".env", ""}
)


def stub_config() -> dict[str, Any]:
    doc = json.loads(STUB_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def root_services() -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(ROOT_COMPOSE.read_text(encoding="utf-8"))
    services = doc["services"]
    assert isinstance(services, dict)
    return services


def stub_issuer_ids() -> list[str]:
    return [str(cb["issuerId"]) for cb in stub_config()["tokenCallbacks"]]


def stub_issuers() -> list[str]:
    """Every issuer URL the stub serves, as containers, host processes and browsers see it."""
    hosts = ("http://oidc.localhost:8080", "http://localhost:8080", "http://oidc:8080")
    return [f"{host}/{issuer_id}" for host in hosts for issuer_id in stub_issuer_ids()]


def deploy_files() -> Iterator[Path]:
    for root in DEPLOY_ROOTS:
        for path in root.rglob("*"):
            if ".terraform" in path.parts or not path.is_file():
                continue
            if path.suffix in TEXT_SUFFIXES or path.name.startswith(".env"):
                yield path
    yield from (p for p in DEPLOY_FILES if p.is_file())


# --- the stub itself ---------------------------------------------------------------------------


def test_SEC_005_stub_keeps_interactive_login() -> None:
    # The developer still signs in through the stub's login page (a subject is typed each time).
    assert stub_config()["interactiveLogin"] is True


def test_SEC_005_stub_staff_issuer_asserts_mfa_like_the_platform_issuer() -> None:
    claims = {
        cb["issuerId"]: cb["requestMappings"][0]["claims"] for cb in stub_config()["tokenCallbacks"]
    }
    for issuer_id in ("schoolos", "platform"):
        assert claims[issuer_id]["sos:mfa"] == "true"
        assert "mfa" in claims[issuer_id]["amr"]
    # auth_time is deliberately NOT a static claim: a fixed value would be either stale (step-up
    # fails) or in the future (the API refuses it). Step-up locally pastes a fresh one (README).
    assert all("auth_time" not in c for c in claims.values())


def test_FR_IAM_001_every_stub_mapping_marks_its_tokens_as_access_tokens() -> None:
    """The API accepts only tokens marked as access tokens (``typ`` ``at+jwt`` or
    ``token_use = access``; audit 2026-10-05). The stub signs plain ``JWT``, so every mapping
    of every issuer sets ``token_use = access`` or local sign-in would stop working."""
    for cb in stub_config()["tokenCallbacks"]:
        for mapping in cb["requestMappings"]:
            assert mapping["claims"]["token_use"] == "access", (cb["issuerId"], mapping["match"])


def test_ADR_0023_stub_support_client_lives_only_in_the_operator_issuer() -> None:
    """The local stub mimics the Cognito support app client of the operator pool: client_id,
    token_use=access and MFA. Every mapping of every issuer asserts MFA; none pins auth_time."""
    support = []
    for cb in stub_config()["tokenCallbacks"]:
        for mapping in cb["requestMappings"]:
            claims = mapping["claims"]
            assert claims["sos:mfa"] == "true"
            assert "mfa" in claims["amr"]
            assert "auth_time" not in claims
            if mapping["match"] == "schoolos-support":
                support.append((cb["issuerId"], claims))
    assert [issuer for issuer, _ in support] == ["platform"]
    claims = support[0][1]
    assert claims["client_id"] == "schoolos-support"
    assert claims["token_use"] == "access"
    assert claims["aud"] == ["schoolos-support"]
    platform = next(cb for cb in stub_config()["tokenCallbacks"] if cb["issuerId"] == "platform")
    # The specific mapping must come before the wildcard (first match wins).
    assert platform["requestMappings"][0]["match"] == "schoolos-support"
    assert platform["requestMappings"][-1]["match"] == "*"


# --- only the local compose file runs it ------------------------------------------------------


def test_SEC_004_only_the_dev_profile_oidc_service_uses_the_stub() -> None:
    services = root_services()
    users = [
        name
        for name, spec in services.items()
        if STUB_IMAGE in str(spec.get("image", ""))
        or any("oidc.json" in str(v) for v in spec.get("volumes", []))
    ]
    assert users == ["oidc"]
    oidc = services["oidc"]
    assert oidc["profiles"] == ["dev"]
    assert all(str(p).startswith("127.0.0.1:") for p in oidc["ports"])


def test_SEC_004_no_default_service_depends_on_the_stub() -> None:
    for name, spec in root_services().items():
        if name == "oidc":
            continue
        depends = spec.get("depends_on", {})
        assert "oidc" not in depends, f"{name} must not require the dev-only oidc service"


def test_SEC_004_no_deploy_file_references_the_stub() -> None:
    scanned = list(deploy_files())
    names = {p.relative_to(REPO).as_posix() for p in scanned}
    # Guard the guard: the scan must actually see the deploy files.
    assert "deploy/dedicated/compose.yaml" in names
    assert any(n.startswith("infra/terraform/modules/shared_platform/") for n in names)
    offenders = [
        f"{path.relative_to(REPO)}: {marker}"
        for path in scanned
        for marker in STUB_MARKERS
        if marker in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert not offenders, offenders


# --- the API refuses its issuers outside local ------------------------------------------------


def _settings(env: Environment, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": env,
        "key_wrapper": KeyWrapperKind.KMS,
        "database_url": SecretStr(
            "postgresql+psycopg://sos_app:x@db:5432/schoolos?sslmode=verify-full"
        ),
        "platform_database_url": SecretStr(
            "postgresql+psycopg://sos_platform:y@db:5432/schoolos?sslmode=verify-full"
        ),
        "service_token_key": SecretStr("k" * 48),
        "oidc_issuer": "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHETIC",
        "oidc_audience": "synthclient01",
        "platform_oidc_issuer": "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_PLATFORM",
        "platform_oidc_audience": "synthplatform01",
    }
    values.update(overrides)
    return Settings(**values)


def _env_example_issuers() -> list[str]:
    issuers = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip().endswith("OIDC_ISSUER") and value.strip():
            issuers.append(value.strip())
    assert issuers, ".env.example should configure the local stub issuers"
    return issuers


@pytest.mark.parametrize("issuer", [*stub_issuers(), *_env_example_issuers()])
def test_FR_IAM_001_every_stub_issuer_is_a_dev_issuer(issuer: str) -> None:
    assert is_dev_issuer(issuer)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
@pytest.mark.parametrize(
    ("builder", "field_name"),
    [(build_tenant_verifier, "oidc_issuer"), (build_platform_verifier, "platform_oidc_issuer")],
)
@pytest.mark.parametrize("issuer", stub_issuers())
def test_FR_IAM_001_stub_issuers_refused_in_staging_and_prod(
    env: Environment,
    builder: Callable[[Settings], TokenVerifier],
    field_name: str,
    issuer: str,
) -> None:
    settings = _settings(env, **{field_name: issuer})
    with pytest.raises(ValueError, match="issuer"):
        builder(settings)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
@pytest.mark.parametrize("issuer", stub_issuers())
def test_ADR_0023_stub_issuers_refused_for_support_sign_in_outside_local(
    env: Environment, issuer: str
) -> None:
    with pytest.raises(ValueError, match=r"SOS_SUPPORT_OIDC_ISSUER|issuer"):
        build_support_verifier(
            _settings(
                env,
                deployment_mode="dedicated",
                support_oidc_issuer=issuer,
                support_oidc_audience="synthsupport01",
            )
        )


@pytest.mark.parametrize("issuer", stub_issuers())
def test_FR_IAM_001_stub_issuers_accepted_only_locally(issuer: str) -> None:
    settings = Settings(env=Environment.LOCAL, oidc_issuer=issuer, platform_oidc_issuer=issuer)
    assert build_tenant_verifier(settings).issuer == issuer
    assert build_platform_verifier(settings).issuer == issuer
