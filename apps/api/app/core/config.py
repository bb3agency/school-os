"""Application settings (12-factor). The ONLY place that reads the environment.

Secrets arrive via environment variables injected from AWS Secrets Manager (or a local
``.env`` in development). Never read ``os.environ`` anywhere else.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

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


class AvScannerKind(StrEnum):
    """Malware scanner for uploads (FR-DOC-002). ``dev-noop`` refuses to run in staging/prod."""

    CLAMAV = "clamav"
    DEV_NOOP = "dev-noop"


class ExtractionProviderKind(StrEnum):
    """Register-photo extraction provider (FR-IMP-024). ``fake`` is synthetic and refuses to run
    in staging/prod; ``not-configured`` fails every batch with an operator-facing error until a
    real provider (OCR / Claude vision via ``knowledge/gateway``, M2) is chosen by evaluation."""

    FAKE = "fake"
    NOT_CONFIGURED = "not-configured"


class KnowledgeProviderMode(StrEnum):
    """How the knowledge module reaches model providers (docs/06, ADR-0005, ADR-0006).

    ``fake`` is offline and deterministic (local/CI; refused in staging/prod). ``live`` uses the
    providers and models named in ``app/knowledge/config/*.yaml`` (invariant 13) through
    ``knowledge/gateway`` with the API keys below."""

    FAKE = "fake"
    LIVE = "live"


class EmailProviderKind(StrEnum):
    """Email delivery for invitations and other notices (docs/03 §5 integrations: Amazon SES).

    ``off`` (default) sends nothing and queues nothing. ``fake`` keeps messages in memory and
    logs that one was "sent" (local/CI; refused in staging/prod). ``ses`` uses Amazon SES v2 in
    ``AWS_REGION`` with the task role's credentials."""

    OFF = "off"
    FAKE = "fake"
    SES = "ses"


MIB = 1024 * 1024
_LOCAL_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "::1", "0.0.0.0", "oidc", "mock-oauth2-server"}  # noqa: S104
)


def _is_public_https(url: str) -> bool:
    """https and not a loopback / dev-stub host (same idea as identity.tokens.is_dev_issuer)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and bool(host)
        and host not in _LOCAL_HOSTS
        and not host.endswith(".localhost")
        and not host.startswith("127.")
    )


# Placeholder supplier identity for local/CI invoices; refused in staging/prod (FR-PLT-016).
DEV_SUPPLIER_NAME = "SchoolOS Synthetic Supplier (dev)"
DEV_SUPPLIER_GSTIN = "37AAAAA0000A1Z5"
# Invoice PDFs refuse to render with this in staging/prod (app/platform/invoice_files.py).
DEV_SUPPLIER_ADDRESS = "Synthetic supplier address (dev); Vijayawada 520001, Andhra Pradesh"


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
    # Endpoint used only to SIGN browser-facing presigned URLs (the browser must reach it);
    # locally http://localhost:8333 while the API itself talks to http://s3:8333.
    s3_presign_endpoint_url: str | None = None
    # KMS key for SSE-KMS on uploaded files (FR-DOC-003). Unset locally (SeaweedFS).
    s3_kms_key_id: str | None = None

    # Documents and uploads (FR-DOC-001..004, SEC-016, docs/07 §10).
    documents_max_upload_bytes: int = Field(default=25 * MIB, ge=1, le=100 * MIB)
    documents_import_max_upload_bytes: int = Field(default=10 * MIB, ge=1, le=100 * MIB)
    # Presigned POST lifetime (<= 10 min) and presigned GET lifetime (<= 5 min, FR-DOC-004).
    documents_upload_url_ttl_s: int = Field(default=600, ge=30, le=600)
    documents_download_url_ttl_s: int = Field(default=300, ge=30, le=300)
    # Kinds accepted by magic bytes (never by extension); subset of pdf, jpg, png, docx, xlsx.
    documents_allowed_kinds: tuple[str, ...] = ("pdf", "jpg", "png", "docx", "xlsx")
    # Spreadsheet imports: xlsx by magic bytes, csv by text sniffing (UTF-8, no NUL bytes).
    documents_import_allowed_kinds: tuple[str, ...] = ("xlsx", "csv")
    av_scanner: AvScannerKind = AvScannerKind.DEV_NOOP
    clamav_host: str = "localhost"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_s: float = Field(default=30.0, gt=0, le=300)

    # Register-photo extraction (US-402, FR-IMP-020..024). Unset: ``fake`` in local/ci,
    # ``not-configured`` in staging/prod. Fields read with a confidence below the threshold are
    # highlighted for the reviewer (US-402 AC4).
    extraction_provider: ExtractionProviderKind | None = None
    extraction_low_confidence_threshold: float = Field(default=0.8, gt=0, le=1)

    # Knowledge / "Ask the school" (M2; docs/06, ADR-0005, ADR-0006). Off by default; a school
    # also needs the feature flag kb.ask.enabled. Unset mode: ``fake`` in local/ci, ``live`` in
    # staging/prod. Model IDs, budgets and thresholds are versioned files, not settings
    # (app/knowledge/config/*.yaml, invariant 13). Keys: organization API keys only, never a
    # personal subscription (invariant 10).
    kb_enabled: bool = False
    kb_provider_mode: KnowledgeProviderMode | None = None
    anthropic_api_key: SecretStr | None = None
    embeddings_api_key: SecretStr | None = None
    # Contextual retrieval and reranking switches per environment (docs/06 §4.11, §6; PO
    # 2026-09-30). Unset = the value in app/knowledge/config/retrieval.yaml (both off). Sizes,
    # budgets and model IDs stay in that file (invariant 13). Reranking with voyage reuses
    # SOS_EMBEDDINGS_API_KEY (same organization account).
    kb_contextual_chunks: Literal["off", "on"] | None = None
    kb_rerank: Literal["off", "voyage", "vertex"] | None = None

    oidc_issuer: str = "http://localhost:8080/schoolos"
    oidc_audience: str = "schoolos-web"
    platform_oidc_issuer: str = "http://localhost:8080/platform"
    platform_oidc_audience: str = "schoolos-platform"
    # Optional explicit JWKS URLs (skip discovery). Locally the issuer URL uses localhost, which
    # is not reachable from inside the api container, so compose points these at the stub.
    oidc_jwks_uri: str | None = None
    platform_oidc_jwks_uri: str | None = None
    # SchoolOS support sign-in to a school during break-glass (ADR-0023 option C): a dedicated
    # app client in the OPERATOR pool. Off unless the audience (support app client ID) is set;
    # a host without it keeps break-glass access unusable (fail closed). The issuer defaults to
    # SOS_PLATFORM_OIDC_ISSUER (same pool); set it on dedicated hosts.
    support_oidc_issuer: str | None = None
    support_oidc_audience: str | None = None
    support_oidc_jwks_uri: str | None = None
    service_token_key: SecretStr = SecretStr("dev-only-service-token-key-change-me-0123456789")

    key_wrapper: KeyWrapperKind = KeyWrapperKind.LOCAL_DEV
    local_dev_master_key: SecretStr | None = None
    kms_data_key_arn: str | None = None
    # Asymmetric KMS key (ECC_NIST_P256, SIGN_VERIFY) for signing daily audit archives.
    audit_signing_key_arn: str | None = None
    audit_archive_retention_days: int = Field(default=3 * 365 + 1, ge=1)

    otel_exporter_otlp_endpoint: str | None = None

    # Email (invitations; app/notifications/email.py). Off by default. ``email_from`` is the
    # verified SES sender ("SchoolOS <no-reply@example.org>"); ``email_app_url`` is the web app
    # address put in links (each dedicated host has its own). Never logged with recipients.
    email_provider: EmailProviderKind = EmailProviderKind.OFF
    email_from: str | None = None
    email_app_url: str | None = None
    email_ses_configuration_set: str | None = None

    # Control plane / fleet (docs/16 §10, §12; ADR-0015). Supplier identity for GST invoices.
    billing_supplier_legal_name: str = DEV_SUPPLIER_NAME
    billing_supplier_gstin: str = DEV_SUPPLIER_GSTIN
    billing_supplier_state_code: str = Field(default="37", pattern=r"^[0-9]{2}$")
    # Registered address printed on invoice PDFs (CGST Rule 46(a)); ";" separates printed lines.
    billing_supplier_address: str = Field(
        default=DEV_SUPPLIER_ADDRESS, min_length=1, max_length=300
    )
    # Control-plane bucket for invoice PDFs (ADR-0017 Amendment 2026-09-28). Unset: the files
    # bucket, under the control-plane prefix of app/platform/billing.yaml (never a school prefix).
    platform_invoice_bucket: str | None = None
    # Dedicated hosts: where and as whom the heartbeat client reports (outbound only).
    control_plane_url: str | None = None
    deployment_id: str | None = None
    dedicated_tenant_id: str | None = None
    heartbeat_key_id: str | None = None
    heartbeat_key: SecretStr | None = None

    @property
    def is_production_like(self) -> bool:
        return self.env in (Environment.STAGING, Environment.PROD)

    @property
    def support_enabled(self) -> bool:
        """True when the break-glass support app client is configured (ADR-0023)."""
        return bool(self.support_oidc_audience and self.support_oidc_audience.strip())

    @property
    def resolved_support_issuer(self) -> str:
        """The operator pool issuer that support tokens and operator identities come from."""
        value = (self.support_oidc_issuer or "").strip()
        return value or self.platform_oidc_issuer

    def _guard_support_client(self) -> None:
        """ADR-0023: the support client lives in the operator pool and is never one of the other
        app clients, so no token can be accepted in two places (T1/T2 token confusion)."""
        if not self.support_enabled:
            return
        issuer = self.resolved_support_issuer
        audience = (self.support_oidc_audience or "").strip()
        if issuer.rstrip("/") == self.oidc_issuer.rstrip("/"):
            raise ValueError("SOS_SUPPORT_OIDC_ISSUER must not be the staff issuer")
        if audience in (self.oidc_audience, self.platform_oidc_audience):
            raise ValueError(
                "SOS_SUPPORT_OIDC_AUDIENCE must be a dedicated app client "
                "(not the staff or operator admin client)"
            )
        if self.deployment_mode is DeploymentMode.SHARED and issuer.rstrip(
            "/"
        ) != self.platform_oidc_issuer.rstrip("/"):
            raise ValueError("SOS_SUPPORT_OIDC_ISSUER must be the operator pool issuer")
        if self.is_production_like:
            for name, url in (
                ("SOS_SUPPORT_OIDC_ISSUER", issuer),
                ("SOS_SUPPORT_OIDC_JWKS_URI", self.support_oidc_jwks_uri),
            ):
                if url is not None and not _is_public_https(url):
                    raise ValueError(f"{name} must be a public https URL in {self.env}")

    @property
    def resolved_kb_provider_mode(self) -> KnowledgeProviderMode:
        """The configured mode; unset means ``fake`` locally/in CI and ``live`` elsewhere."""
        if self.kb_provider_mode is not None:
            return self.kb_provider_mode
        if self.is_production_like:
            return KnowledgeProviderMode.LIVE
        return KnowledgeProviderMode.FAKE

    def _guard_knowledge(self) -> None:
        """Staging/prod: no fake providers; live AI needs a real organization API key."""
        if self.kb_provider_mode is KnowledgeProviderMode.FAKE:
            raise ValueError(f"SOS_KB_PROVIDER_MODE=fake is not allowed in {self.env}")
        for name in ("anthropic_api_key", "embeddings_api_key"):
            key: SecretStr | None = getattr(self, name)
            if key is not None and "dev-only" in key.get_secret_value():
                raise ValueError(f"{name} uses a dev-only value in {self.env}")
        if self.kb_enabled:
            key = self.anthropic_api_key
            if key is None or not key.get_secret_value().strip():
                raise ValueError(f"SOS_KB_ENABLED needs SOS_ANTHROPIC_API_KEY in {self.env}")

    @property
    def email_enabled(self) -> bool:
        return self.email_provider is not EmailProviderKind.OFF

    def _guard_email(self) -> None:
        """Email that is on needs a sender and a link target; staging/prod: never the fake
        provider, and links only to a public https address."""
        if not self.email_enabled:
            return
        if self.email_provider is EmailProviderKind.FAKE and self.is_production_like:
            raise ValueError(f"SOS_EMAIL_PROVIDER=fake is not allowed in {self.env}")
        if not (self.email_from or "").strip() or "@" not in (self.email_from or ""):
            raise ValueError("SOS_EMAIL_FROM must be set to a sender address when email is on")
        if not (self.email_app_url or "").strip():
            raise ValueError("SOS_EMAIL_APP_URL must be set when email is on")
        if self.is_production_like and not _is_public_https(self.email_app_url or ""):
            raise ValueError(f"SOS_EMAIL_APP_URL must be a public https URL in {self.env}")

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        """Fail closed: dev-only conveniences can never run in staging or production."""
        self._guard_support_client()
        self._guard_email()
        if self.is_production_like:
            if self.key_wrapper is KeyWrapperKind.LOCAL_DEV:
                raise ValueError("SOS_KEY_WRAPPER=local-dev is not allowed in staging/prod")
            for name in ("database_url", "platform_database_url", "service_token_key"):
                value: SecretStr = getattr(self, name)
                if "dev-only" in value.get_secret_value():
                    raise ValueError(f"{name} uses a dev-only default in {self.env}")
            # Invoices are tax documents: never issue them with the placeholder supplier. Only the
            # shared tier runs the control plane (billing); dedicated hosts never invoice.
            invoicing = self.deployment_mode is DeploymentMode.SHARED
            if invoicing and self.billing_supplier_gstin == DEV_SUPPLIER_GSTIN:
                raise ValueError(f"billing_supplier_gstin uses the dev placeholder in {self.env}")
            if invoicing and self.billing_supplier_legal_name == DEV_SUPPLIER_NAME:
                raise ValueError(
                    f"billing_supplier_legal_name uses the dev placeholder in {self.env}"
                )
            self._guard_knowledge()
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
