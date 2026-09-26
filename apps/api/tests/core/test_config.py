"""Settings guards: dev-only conveniences never run in staging/prod (SEC-009)."""

from __future__ import annotations

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
    )
    assert s.is_production_like
