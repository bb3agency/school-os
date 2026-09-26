"""Application settings (12-factor). The ONLY place that reads the environment.

Secrets arrive via environment variables injected from AWS Secrets Manager (or a local
``.env`` in development). Never read ``os.environ`` anywhere else.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PROD = "prod"


class DeploymentMode(StrEnum):
    SHARED = "shared"
    DEDICATED = "dedicated"


class KeyWrapperKind(StrEnum):
    KMS = "kms"
    LOCAL_DEV = "local-dev"


class Settings(BaseSettings):
    """Typed settings; env prefix ``SOS_``."""

    model_config = SettingsConfigDict(env_prefix="SOS_", extra="ignore", frozen=True)

    env: Environment = Environment.LOCAL
    deployment_mode: DeploymentMode = DeploymentMode.SHARED
    service_name: str = "api"
    version: str = "0.0.0-dev"
    log_level: str = "INFO"

    # Database: the app role is subject to RLS; the platform role only sees schema `platform`.
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://sos_app:dev-only-app@localhost:5432/schoolos"
    )
    platform_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://sos_platform:dev-only-platform@localhost:5432/schoolos"
    )
    migrator_database_url: SecretStr = SecretStr(
        "postgresql+psycopg://sos_migrator:dev-only-migrator@localhost:5432/schoolos"
    )
    db_pool_size: int = 10
    db_statement_timeout_ms: int = Field(default=5000, ge=100)
    worker_statement_timeout_ms: int = Field(default=120_000, ge=100)

    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    s3_endpoint_url: str | None = None
    s3_bucket_files: str = "sos-local-files"
    s3_bucket_audit: str = "sos-local-audit-archive"
    aws_region: str = Field(default="ap-south-1", validation_alias="AWS_REGION")

    oidc_issuer: str = "http://localhost:8080/schoolos"
    oidc_audience: str = "schoolos-web"
    platform_oidc_issuer: str = "http://localhost:8080/platform"
    platform_oidc_audience: str = "schoolos-platform"
    # Optional explicit JWKS URLs (skip discovery). Locally the issuer URL uses localhost, which
    # is not reachable from inside the api container, so compose points these at the stub.
    oidc_jwks_uri: str | None = None
    platform_oidc_jwks_uri: str | None = None
    service_token_key: SecretStr = SecretStr("dev-only-service-token-key-change-me-0123456789")

    key_wrapper: KeyWrapperKind = KeyWrapperKind.LOCAL_DEV
    local_dev_master_key: SecretStr | None = None
    kms_data_key_arn: str | None = None
    # Asymmetric KMS key (ECC_NIST_P256, SIGN_VERIFY) for signing daily audit archives.
    audit_signing_key_arn: str | None = None
    audit_archive_retention_days: int = Field(default=3 * 365 + 1, ge=1)

    otel_exporter_otlp_endpoint: str | None = None

    # Control plane / fleet (docs/16 §10, §12; ADR-0015). Supplier identity for GST invoices.
    billing_supplier_legal_name: str = "SchoolOS Synthetic Supplier (dev)"
    billing_supplier_gstin: str = "37AAAAA0000A1Z5"
    billing_supplier_state_code: str = Field(default="37", pattern=r"^[0-9]{2}$")
    # Dedicated hosts: where and as whom the heartbeat client reports (outbound only).
    control_plane_url: str | None = None
    deployment_id: str | None = None
    dedicated_tenant_id: str | None = None
    heartbeat_key_id: str | None = None
    heartbeat_key: SecretStr | None = None

    @property
    def is_production_like(self) -> bool:
        return self.env in (Environment.STAGING, Environment.PROD)

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        """Fail closed: dev-only conveniences can never run in staging or production."""
        if self.is_production_like:
            if self.key_wrapper is KeyWrapperKind.LOCAL_DEV:
                raise ValueError("SOS_KEY_WRAPPER=local-dev is not allowed in staging/prod")
            for name in ("database_url", "platform_database_url", "service_token_key"):
                value: SecretStr = getattr(self, name)
                if "dev-only" in value.get_secret_value():
                    raise ValueError(f"{name} uses a dev-only default in {self.env}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
