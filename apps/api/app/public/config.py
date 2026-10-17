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
    "WEBHOOK_PROVIDERS",
    "HandleKind",
    "ParentKind",
    "PublicConfig",
    "WebhookProvider",
    "is_parent_kind",
    "rules",
]
