"""Public handle configuration (``config.yaml`` next to this module; ADR-0046, ADR-0047).

Pure module (no database or web): loads and validates the packaged YAML once. Lifetimes, the
purge delay, token sizes and webhook body limits live there (invariant 13).
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from importlib import resources
from typing import Final, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

HandleKind = Literal[
    "cert_verify",
    "parent_notice",
    "parent_receipt",
    "parent_dues",
    "parent_optin",
    "razorpay_route",
    "meta_waba",
]
ParentKind = Literal["parent_notice", "parent_receipt", "parent_dues", "parent_optin"]
WebhookProvider = Literal["razorpay", "meta"]

HANDLE_KINDS: Final[tuple[HandleKind, ...]] = get_args(HandleKind)
PARENT_KINDS: Final[tuple[ParentKind, ...]] = get_args(ParentKind)
WEBHOOK_PROVIDERS: Final[tuple[WebhookProvider, ...]] = get_args(WebhookProvider)
# What each parent link points at (R2-3H pages <-> R2-3G sends; rememory 0ca03e6a). A parent page
# renders a handle only when its target_type is the one of its kind (else the uniform 404).
PARENT_TARGET_TYPES: Final[dict[ParentKind, str]] = {
    "parent_notice": "parent_notice",  # ops.parent_notices.id (approved)
    "parent_receipt": "fee_receipt",  # fin.receipts.id
    "parent_dues": "fee_account",  # fin.student_fee_accounts.id
    "parent_optin": "guardian",  # the guardian's id (same as guardian_id)
}
# The database refuses to delete expired handles sooner (policy purge_expired_only, 0055).
MIN_PURGE_AFTER_DAYS: Final = 30


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WebhookProviderConfig(_Model):
    max_body_bytes: int = Field(ge=1024, le=8 * 1024 * 1024)


class PublicConfig(_Model):
    version: int = Field(ge=1)
    lifetimes_days: dict[ParentKind, int]
    purge_after_days: int = Field(ge=MIN_PURGE_AFTER_DAYS, le=3650)
    purge_batch_size: int = Field(ge=1, le=100_000)
    parent_token_bytes: int = Field(ge=32, le=64)
    route_token_length: int = Field(ge=24, le=64)
    webhook_providers: dict[WebhookProvider, WebhookProviderConfig]
    replay_window_hours: int = Field(ge=1, le=24 * 30)
    pay_redirect_hosts: tuple[str, ...] = Field(min_length=1)

    @field_validator("pay_redirect_hosts")
    @classmethod
    def _plain_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for host in value:
            if not host or host != host.lower() or "/" in host or ":" in host or "*" in host:
                raise ValueError("pay_redirect_hosts are lower-case host names")
        return value

    def pay_redirect_allowed(self, host: str | None) -> bool:
        """``host`` is one of the hosts (or a subdomain of one)."""
        if not host:
            return False
        host = host.lower()
        return any(host == h or host.endswith("." + h) for h in self.pay_redirect_hosts)

    @field_validator("lifetimes_days")
    @classmethod
    def _every_parent_kind(cls, value: dict[ParentKind, int]) -> dict[ParentKind, int]:
        if set(value) != set(PARENT_KINDS):
            raise ValueError("lifetimes_days must list every parent kind exactly once")
        if any(not 1 <= days <= 366 for days in value.values()):
            raise ValueError("parent link lifetimes are 1-366 days")
        return value

    @field_validator("webhook_providers")
    @classmethod
    def _every_provider(
        cls, value: dict[WebhookProvider, WebhookProviderConfig]
    ) -> dict[WebhookProvider, WebhookProviderConfig]:
        if set(value) != set(WEBHOOK_PROVIDERS):
            raise ValueError("webhook_providers must list razorpay and meta")
        return value

    def lifetime(self, kind: ParentKind) -> dt.timedelta:
        return dt.timedelta(days=self.lifetimes_days[kind])


@lru_cache(maxsize=1)
def rules() -> PublicConfig:
    text = resources.files("app.public").joinpath("config.yaml").read_text("utf-8")
    return PublicConfig.model_validate(yaml.safe_load(text))


def is_parent_kind(kind: str) -> bool:
    return kind in PARENT_KINDS


__all__ = [
    "HANDLE_KINDS",
    "MIN_PURGE_AFTER_DAYS",
    "PARENT_KINDS",
    "PARENT_TARGET_TYPES",
    "WEBHOOK_PROVIDERS",
    "HandleKind",
    "ParentKind",
    "PublicConfig",
    "WebhookProvider",
    "is_parent_kind",
    "rules",
]
