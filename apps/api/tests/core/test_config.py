"""Settings guards: dev-only conveniences never run in staging/prod (SEC-009)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, KeyWrapperKind, Settings

# Shared-tier staging/prod reach RDS over TLS that checks the certificate and the host name
# (Terraform writes these URLs: modules/secrets/main.tf).
TLS = "?sslmode=verify-full&sslrootcert=/etc/ssl/rds/global-bundle.pem"
APP_DB = "postgresql+psycopg://sos_app:x@db.example:5432/schoolos" + TLS
PLATFORM_DB = "postgresql+psycopg://sos_platform:y@db.example:5432/schoolos" + TLS


def test_local_defaults_are_allowed() -> None:
    assert Settings(env=Environment.LOCAL).key_wrapper is KeyWrapperKind.LOCAL_DEV


def test_prod_rejects_local_dev_key_wrapper() -> None:
    with pytest.raises(ValidationError, match="local-dev"):
        Settings(env=Environment.PROD, key_wrapper=KeyWrapperKind.LOCAL_DEV)


def test_prod_rejects_dev_only_secrets() -> None:
    with pytest.raises(ValidationError, match="dev-only"):
        Settings(env=Environment.STAGING, key_wrapper=KeyWrapperKind.KMS)


def test_prod_accepts_real_values() -> None:
    s = Settings(
        env=Environment.PROD,
        key_wrapper=KeyWrapperKind.KMS,
        database_url=SecretStr(APP_DB),
        platform_database_url=SecretStr(PLATFORM_DB),
        service_token_key=SecretStr("k" * 48),
        billing_supplier_legal_name="Example Technologies Private Limited",
        billing_supplier_gstin="37ABCDE1234F1Z5",
    )
    assert s.is_production_like


def _prod(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": Environment.PROD,
        "key_wrapper": KeyWrapperKind.KMS,
        "database_url": SecretStr(APP_DB),
        "platform_database_url": SecretStr(PLATFORM_DB),
        "service_token_key": SecretStr("k" * 48),
        "billing_supplier_legal_name": "Example Technologies Private Limited",
        "billing_supplier_gstin": "37ABCDE1234F1Z5",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_FR_PLT_016_prod_rejects_placeholder_supplier_gstin() -> None:
    with pytest.raises(ValidationError, match="billing_supplier_gstin"):
        _prod(billing_supplier_gstin="37AAAAA0000A1Z5")


def test_FR_PLT_016_prod_rejects_dev_supplier_name() -> None:
    with pytest.raises(ValidationError, match="billing_supplier_legal_name"):
        _prod(billing_supplier_legal_name="SchoolOS Synthetic Supplier (dev)")


def test_FR_PLT_016_prod_accepts_real_supplier_identity() -> None:
    assert _prod().billing_supplier_gstin == "37ABCDE1234F1Z5"


def test_FR_PLT_016_dedicated_hosts_do_not_need_a_supplier_identity() -> None:
    from app.core.config import DEV_SUPPLIER_GSTIN, DEV_SUPPLIER_NAME, DeploymentMode

    s = _prod(
        deployment_mode=DeploymentMode.DEDICATED,
        billing_supplier_gstin=DEV_SUPPLIER_GSTIN,
        billing_supplier_legal_name=DEV_SUPPLIER_NAME,
    )
    assert s.deployment_mode is DeploymentMode.DEDICATED


def test_every_setting_is_documented_in_docs_10_section_11() -> None:
    """CLAUDE.md §4/§5: every SOS_* setting and its default is listed in docs/10 §11."""
    docs = (
        Path(__file__).resolve().parents[4] / "docs" / "10-infrastructure-and-devops.md"
    ).read_text("utf-8")
    section = docs[docs.index("## 11. Local development") : docs.index("## 12.")]
    documented = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", section))
    names = set()
    for field_name, field in Settings.model_fields.items():
        alias = field.validation_alias
        names.add(alias if isinstance(alias, str) else f"SOS_{field_name.upper()}")
    assert sorted(names - documented) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://admin.schoolos.example",
        "https://localhost:8000",
        "https://127.0.0.1",
        "ftp://admin.schoolos.example",
    ],
)
def test_SEC_009_prod_rejects_a_control_plane_url_that_is_not_public_https(url: str) -> None:
    """The heartbeat answer (announcements shown to school users) is not signed, so a
    dedicated host talks to the control plane over public https only (SEC-009, FR-PLT-026)."""
    from app.core.config import DeploymentMode

    with pytest.raises(ValidationError, match="SOS_CONTROL_PLANE_URL"):
        _prod(deployment_mode=DeploymentMode.DEDICATED, control_plane_url=url)


def test_SEC_009_prod_accepts_a_public_https_control_plane_url() -> None:
    from app.core.config import DeploymentMode

    s = _prod(
        deployment_mode=DeploymentMode.DEDICATED,
        control_plane_url="https://admin.schoolos.example",
    )
    assert s.control_plane_url == "https://admin.schoolos.example"


@pytest.mark.parametrize(
    "url",
    [
        "rediss://:token@valkey.example.internal:6379/0",
        "rediss://:token@valkey.example.internal:6379/0?ssl_cert_reqs=none",
        "rediss://:token@valkey.example.internal:6379/0?ssl_cert_reqs=CERT_NONE",
        "rediss://:token@valkey.example.internal:6379/0?ssl_cert_reqs=optional",
        "rediss://:token@valkey.example.internal:6379/0?ssl_cert_reqs=required&ssl_cert_reqs=none",
    ],
)
def test_SEC_011_prod_rejects_a_valkey_tls_url_without_certificate_checks(url: str) -> None:
    """The Celery broker (kombu) treats a rediss:// URL without ssl_cert_reqs as CERT_NONE, so
    the worker's TLS to Valkey would accept any certificate (audit 2026-10-05 P2-01)."""
    with pytest.raises(ValidationError, match="SOS_REDIS_URL"):
        _prod(redis_url=SecretStr(url))


@pytest.mark.parametrize(
    "url",
    [
        "rediss://:token@valkey.example.internal:6379/0?ssl_cert_reqs=required",
        "rediss://:token@valkey.example.internal:6379/0?ssl_cert_reqs=CERT_REQUIRED",
        # Dedicated hosts: plaintext on the internal, isolated compose network (docs/10 §15).
        "redis://:token@valkey:6379/0",
    ],
)
def test_SEC_011_prod_accepts_a_verified_or_internal_valkey_url(url: str) -> None:
    assert _prod(redis_url=SecretStr(url)).redis_url.get_secret_value() == url


def test_SEC_011_local_allows_rediss_without_certificate_checks() -> None:
    url = "rediss://localhost:6380/0"
    assert Settings(env=Environment.LOCAL, redis_url=SecretStr(url)).redis_url is not None


def test_SEC_009_local_allows_a_plain_http_control_plane_url() -> None:
    s = Settings(env=Environment.LOCAL, control_plane_url="http://localhost:8000")
    assert s.control_plane_url == "http://localhost:8000"


@pytest.mark.parametrize(
    "field", ["database_url", "platform_database_url", "migrator_database_url"]
)
@pytest.mark.parametrize(
    "query",
    [
        "",
        "?sslmode=require",
        "?sslmode=verify-ca",
        "?sslmode=disable",
        "?sslmode=prefer",
        "?sslmode=verify-full&sslmode=disable",
        "?SSLMODE=verify-full",
    ],
)
def test_SEC_011_shared_prod_requires_verify_full_tls_to_postgres(field: str, query: str) -> None:
    """Audit 2026-10-05 (platform hardening, "TLS guard for Postgres"): in the shared tier the
    database is RDS across the VPC, so every database URL the process is given must check the
    server certificate and host name (``sslmode=verify-full``, exactly once)."""
    url = f"postgresql+psycopg://sos_role:z@db.example:5432/schoolos{query}"
    with pytest.raises(ValidationError, match="sslmode=verify-full"):
        _prod(**{field: SecretStr(url)})


def test_SEC_011_shared_prod_accepts_verify_full_and_an_unset_migrator_url() -> None:
    s = _prod()  # the api containers do not set SOS_MIGRATOR_DATABASE_URL (dev-only default)
    assert "verify-full" in s.database_url.get_secret_value()
    migrator = "postgresql+psycopg://sos_migrator:m@db.example:5432/schoolos" + TLS
    assert _prod(migrator_database_url=SecretStr(migrator)).migrator_database_url


def test_SEC_011_dedicated_hosts_and_local_do_not_need_postgres_tls() -> None:
    """A dedicated host's database is on its own compose network; local and CI use Docker."""
    from app.core.config import DeploymentMode

    plain = SecretStr("postgresql+psycopg://sos_app:x@db:5432/schoolos")
    s = _prod(deployment_mode=DeploymentMode.DEDICATED, database_url=plain)
    assert s.deployment_mode is DeploymentMode.DEDICATED
    assert Settings(env=Environment.LOCAL, database_url=plain).database_url == plain
