"""``app/tally/config.yaml`` (versioned configuration; invariant 13; ADR-0032). Pure."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION_PATTERN = r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,6}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EnrolmentRules(_Model):
    code_ttl_minutes: int = Field(ge=5, le=120)
    max_attempts_per_address_per_hour: int = Field(ge=1, le=1000)
    max_failures_per_address_per_hour: int = Field(ge=1, le=100)
    max_failures_per_school_per_hour: int = Field(ge=1, le=1000)
    max_active_devices: int = Field(ge=1, le=5)

    @model_validator(mode="after")
    def _school_cap_above_address_cap(self) -> Self:
        """One address alone must never reach the school-wide cap (AA-15)."""
        if self.max_failures_per_school_per_hour <= self.max_failures_per_address_per_hour:
            raise ValueError("max_failures_per_school_per_hour must exceed the per-address cap")
        return self


class SigningRules(_Model):
    skew_seconds: int = Field(ge=30, le=900)
    nonce_ttl_seconds: int = Field(ge=60, le=3600)
    key_rotation_overlap_days: int = Field(ge=1, le=30)
    rotate_after_days: int = Field(ge=7, le=365)


class RateLimits(_Model):
    config: int = Field(ge=1, le=3600)
    catalog: int = Field(ge=1, le=3600)
    sync: int = Field(ge=1, le=3600)
    key_rotation: int = Field(ge=1, le=86400)


class SyncRules(_Model):
    interval_minutes: int = Field(ge=5, le=1440)
    max_parties: int = Field(ge=1, le=20000)
    max_groups: int = Field(ge=1, le=2000)
    min_agent_version: str = Field(pattern=VERSION_PATTERN)


class SilenceRules(_Model):
    notify_after_hours: int = Field(ge=1, le=720)


class RetentionRules(_Model):
    sync_days: int = Field(ge=30, le=3650)


class MappingRules(_Model):
    max_candidates: int = Field(ge=0, le=20)


class TallyRules(_Model):
    version: int = Field(ge=1)
    flag: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    enrolment: EnrolmentRules
    signing: SigningRules
    rate_limits_seconds: RateLimits
    sync: SyncRules
    silence: SilenceRules
    retention: RetentionRules
    mapping: MappingRules


@lru_cache(maxsize=1)
def rules() -> TallyRules:
    raw = yaml.safe_load(resources.files("app.tally").joinpath("config.yaml").read_text("utf-8"))
    return TallyRules.model_validate(raw)


def version_tuple(version: str) -> tuple[int, int, int]:
    """``"1.2.3"`` -> ``(1, 2, 3)`` (versions are checked against :data:`VERSION_PATTERN`)."""
    major, minor, patch = (int(p) for p in version.split("."))
    return major, minor, patch


__all__ = ["VERSION_PATTERN", "TallyRules", "rules", "version_tuple"]
