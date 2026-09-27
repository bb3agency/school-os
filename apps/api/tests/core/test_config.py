"""Settings guards: dev-only conveniences never run in staging/prod (SEC-009)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, KeyWrapperKind, Settings


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
        database_url=SecretStr("postgresql+psycopg://sos_app:x@db:5432/schoolos"),
        platform_database_url=SecretStr("postgresql+psycopg://sos_platform:y@db:5432/schoolos"),
        service_token_key=SecretStr("k" * 48),
        billing_supplier_legal_name="Example Technologies Private Limited",
        billing_supplier_gstin="37ABCDE1234F1Z5",
    )
    assert s.is_production_like


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
