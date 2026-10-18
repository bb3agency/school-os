"""Messaging configuration (``config.yaml`` next to this module; ADR-0043; FR-MSG-001..024).

Pure module (no database or web): loads and validates the packaged YAML once. Graph API
endpoints and version, opt-in rules, keywords, error-code classes, messaging limits, the
displayed cost estimate and retention periods live there (invariant 13).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Final, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

OptinCategory = Literal["notices", "attendance", "fees"]
OPTIN_CATEGORIES: Final[tuple[OptinCategory, ...]] = get_args(OptinCategory)
Language = Literal["en", "te"]
LANGUAGES: Final[tuple[Language, ...]] = get_args(Language)
Keyword = Literal["stop", "join", "other"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GraphConfig(_Model):
    base_url: HttpUrl
    api_version: str = Field(pattern=r"^v[0-9]{1,3}\.[0-9]$")
    timeout_s: float = Field(gt=0, le=60)
    embedded_signup_version: str = Field(pattern=r"^v[0-9]+$")
    templates_page_size: int = Field(ge=1, le=500)

    @field_validator("base_url")
    @classmethod
    def _meta_only(cls, value: HttpUrl) -> HttpUrl:
        # The egress allowlist names graph.facebook.com only (docs/10 §11).
        if value.scheme != "https" or value.host != "graph.facebook.com":
            raise ValueError("the Graph API base URL must be https://graph.facebook.com")
        return value


class OptinConfig(_Model):
    categories: tuple[OptinCategory, ...]
    wording_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,39}$")
    join_code_length: int = Field(ge=6, le=16)
    join_code_alphabet: str = Field(min_length=20, max_length=64)
    join_code_days: int = Field(ge=1, le=90)
    max_slips_per_print: int = Field(ge=1, le=2000)
    max_decision_age_days: int = Field(ge=1, le=3650)

    @field_validator("categories")
    @classmethod
    def _all_categories(cls, value: tuple[OptinCategory, ...]) -> tuple[OptinCategory, ...]:
        if set(value) != set(OPTIN_CATEGORIES) or len(value) != len(OPTIN_CATEGORIES):
            raise ValueError("optin.categories must list notices, attendance and fees once")
        return value

    @field_validator("join_code_alphabet")
    @classmethod
    def _plain_alphabet(cls, value: str) -> str:
        if not value.isascii() or not value.isalnum() or value.upper() != value:
            raise ValueError("the join code alphabet is upper-case letters and digits")
        if len(set(value)) != len(value) or set(value) & set("0O1IL"):
            raise ValueError("the join code alphabet has no repeats and no look-alikes")
        return value


class KeywordConfig(_Model):
    stop: tuple[str, ...] = Field(min_length=1)
    join: tuple[str, ...] = Field(min_length=1)
    stop_button_payload: str = Field(pattern=r"^[A-Z_]{3,32}$")
    stop_button_labels: tuple[str, ...] = ()


class SendingConfig(_Model):
    inr_required_from: dt.date
    daily_cap_margin_percent: int = Field(ge=0, le=90)
    pausing_quality: tuple[str, ...]
    permanent_error_codes: frozenset[int]
    transient_error_codes: frozenset[int]
    max_attempts: int = Field(ge=1, le=10)
    retry_backoff_minutes: tuple[int, ...] = Field(min_length=1, max_length=9)
    early_close: dt.time
    early_close_purposes: frozenset[str]
    max_queue_hours: int = Field(ge=1, le=240)
    batch_per_run: int = Field(ge=1, le=10_000)
    claim_seconds: int = Field(ge=30, le=3600)

    @field_validator("retry_backoff_minutes")
    @classmethod
    def _within_an_hour(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        # FR-MSG-016: up to 3 attempts within 60 minutes.
        if any(v < 1 for v in value) or sum(value) > 60:
            raise ValueError("retry backoff minutes must be positive and add up to at most 60")
        return value

    def backoff(self, attempts: int) -> dt.timedelta:
        """Wait after the ``attempts``-th failed try (1-based)."""
        steps = self.retry_backoff_minutes
        return dt.timedelta(minutes=steps[min(max(attempts, 1), len(steps)) - 1])


class BroadcastConfig(_Model):
    preview_minutes: int = Field(ge=1, le=240)
    max_sections: int = Field(ge=1, le=300)
    preview_retention_hours: int = Field(ge=1, le=720)


class AutomaticConfig(_Model):
    fee_reminder_time: dt.time


class CostConfig(_Model):
    utility_rate_inr: Decimal = Field(gt=0, le=10)
    gst_percent: Decimal = Field(ge=0, le=50)


class RetentionConfig(_Model):
    provider_events_days: int = Field(ge=1, le=365)
    inbound_events_days: int = Field(ge=1, le=365)
    messages_days: int = Field(ge=30, le=3650)
    message_vars_days: int = Field(ge=1, le=3650)
    join_codes_after_expiry_days: int = Field(ge=0, le=365)
    purge_batch_size: int = Field(ge=1, le=100_000)


class InboxConfig(_Model):
    max_attempts: int = Field(ge=1, le=20)
    batch_size: int = Field(ge=1, le=5000)


class MessagingConfig(_Model):
    version: int = Field(ge=1)
    graph: GraphConfig
    optin: OptinConfig
    keywords: KeywordConfig
    auto_reply_per_day: int = Field(ge=0, le=5)
    sending: SendingConfig
    messaging_limits: dict[str, int]
    cost: CostConfig
    retention: RetentionConfig
    inbox: InboxConfig
    broadcasts: BroadcastConfig
    automatic: AutomaticConfig

    def limit_for(self, tier: str | None) -> int | None:
        """Unique users per 24 hours for a Meta messaging-limit tier (``None`` when unknown)."""
        return self.messaging_limits.get(tier) if tier else None

    def default_daily_cap(self, tier: str | None) -> int | None:
        """Owner decision D25: Meta's limit minus the configured margin (10%)."""
        limit = self.limit_for(tier)
        if limit is None:
            return None
        return max(1, limit * (100 - self.sending.daily_cap_margin_percent) // 100)


@lru_cache(maxsize=1)
def load_config() -> MessagingConfig:
    text = resources.files("app.messaging").joinpath("config.yaml").read_text("utf-8")
    return MessagingConfig.model_validate(yaml.safe_load(text))


def _fold(value: str) -> str:
    """Keyword comparison form: NFC, no spaces or punctuation, upper case (FR-MSG-010)."""
    norm = unicodedata.normalize("NFC", value)
    kept = "".join(ch for ch in norm if unicodedata.category(ch)[0] in ("L", "M", "N"))
    return kept.upper()


def classify(text: str | None, button_payload: str | None = None) -> tuple[Keyword, str | None]:
    """Classify an inbound message: ``("stop", None)``, ``("join", code)`` or
    ``("other", None)``. Case, spacing and punctuation are ignored; English and Telugu keywords
    always work (ADR-0044 decision 8). The text itself is never kept by the caller."""
    cfg = load_config().keywords
    if button_payload is not None and button_payload == cfg.stop_button_payload:
        return "stop", None
    if not text:
        return "other", None
    folded = _fold(text)
    if folded in {_fold(k) for k in cfg.stop}:
        return "stop", None
    for keyword in cfg.join:
        word = _fold(keyword)
        if folded.startswith(word):
            code = folded[len(word) :]
            alphabet = set(load_config().optin.join_code_alphabet)
            length = load_config().optin.join_code_length
            if len(code) == length and set(code) <= alphabet:
                return "join", code
            return "join", None
    return "other", None


__all__ = [
    "LANGUAGES",
    "OPTIN_CATEGORIES",
    "Keyword",
    "Language",
    "MessagingConfig",
    "OptinCategory",
    "classify",
    "load_config",
]
