"""Online payments configuration (``payments.yaml``; ADR-0042; FR-PAY-001..016; invariant 13).

Pure: reads and validates the versioned file next to this module; no database, web or feature
modules (import-linter ``fees-pure-library``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

CONFIG_PATH: Final = Path(__file__).with_name("payments.yaml")
KNOWN_EVENTS: Final = frozenset(
    {
        "payment_link.paid",
        "payment_link.expired",
        "payment_link.cancelled",
        "payment.captured",
        "payment.failed",
        "refund.processed",
        "refund.failed",
        "settlement.processed",
        "account.app.authorization_revoked",
    }
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RazorpaySpec(_Frozen):
    api_base_url: HttpUrl
    auth_base_url: HttpUrl
    timeout_s: float = Field(gt=0, le=60)
    webhook_events: tuple[str, ...] = Field(min_length=1)
    oauth_scope: str = Field(pattern=r"^(read_only|read_write)$")
    access_token_days: int = Field(ge=1, le=365)
    refresh_token_days: int = Field(ge=1, le=730)

    @field_validator("api_base_url", "auth_base_url")
    @classmethod
    def _razorpay_hosts(cls, value: HttpUrl) -> HttpUrl:
        # FR-PAY-001: the egress allowlist names only these two hosts.
        if value.scheme != "https" or value.host not in ("api.razorpay.com", "auth.razorpay.com"):
            raise ValueError("only https://api.razorpay.com and https://auth.razorpay.com")
        return value

    @field_validator("webhook_events")
    @classmethod
    def _known(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = set(value) - KNOWN_EVENTS
        if unknown:
            raise ValueError(f"unknown webhook events {sorted(unknown)}")
        return value


class LinksSpec(_Frozen):
    default_expiry_days: int = Field(ge=1, le=180)
    min_expiry_minutes: int = Field(ge=15, le=1440)
    max_expiry_days: int = Field(ge=1, le=180)
    description: str = Field(min_length=1, max_length=200)
    school_name_max_length: int = Field(ge=5, le=80)
    max_open_per_account: int = Field(ge=1, le=50)

    @field_validator("description")
    @classmethod
    def _placeholders(cls, value: str) -> str:
        if "{code}" not in value or "{school}" not in value:
            raise ValueError("the description names the school and the intent code only")
        return value


class WebhookSpec(_Frozen):
    secret_bytes: int = Field(ge=32, le=64)
    previous_secret_days: int = Field(ge=1, le=30)
    event_id_pattern: str = Field(min_length=3, max_length=100)


class InboxSpec(_Frozen):
    batch_size: int = Field(ge=1, le=500)
    max_attempts: int = Field(ge=1, le=100)
    retry_backoff_s: tuple[int, ...] = Field(min_length=1)
    retention_days: int = Field(ge=30, le=3650)

    def backoff(self, attempts: int) -> int:
        steps = self.retry_backoff_s
        return steps[min(max(attempts - 1, 0), len(steps) - 1)]


class OAuthSpec(_Frozen):
    state_ttl_s: int = Field(ge=60, le=3600)
    refresh_before_days: int = Field(ge=1, le=60)
    alert_before_refresh_expiry_days: int = Field(ge=1, le=60)


class ReconciliationSpec(_Frozen):
    settlement_after_working_days: int = Field(ge=1, le=15)
    page_size: int = Field(ge=1, le=100)
    max_pages: int = Field(ge=1, le=500)
    list_max: int = Field(ge=1, le=1000)


class PaymentsConfig(_Frozen):
    version: int = Field(ge=1)
    razorpay: RazorpaySpec
    links: LinksSpec
    webhook: WebhookSpec
    inbox: InboxSpec
    oauth: OAuthSpec
    reconciliation: ReconciliationSpec


@lru_cache(maxsize=1)
def load_payments_config(path: Path = CONFIG_PATH) -> PaymentsConfig:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return PaymentsConfig.model_validate(raw)


__all__ = ["CONFIG_PATH", "KNOWN_EVENTS", "PaymentsConfig", "load_payments_config"]
