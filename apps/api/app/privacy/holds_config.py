"""Legal hold configuration (``holds.yaml``; ADR-0059; FR-DPDP-039..FR-DPDP-052).

Pure module (no database or web): reason codes, the review rules and the catalogue of
retention jobs that consult :func:`app.core.holds.check` (lead decision D84).
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

CODE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Review(_Model):
    max_months: int = Field(ge=1, le=24)
    remind_days_before: int = Field(ge=1, le=60)
    overdue_every_days: int = Field(ge=1, le=30)


class RetentionJob(_Model):
    record_class: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    scope: Literal["class", "student"]


class HoldsConfig(_Model):
    version: int
    verified: bool
    source: str = Field(min_length=10)
    reason_codes: dict[str, str]
    review: Review
    retention_jobs: dict[str, RetentionJob]
    exempt_jobs: dict[str, str]

    @field_validator("reason_codes")
    @classmethod
    def _codes(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("at least one reason code")
        for code, label in value.items():
            if not CODE.fullmatch(code) or not label:
                raise ValueError(f"bad reason code {code!r}")
        return value


def parse_config(raw: object) -> HoldsConfig:
    return HoldsConfig.model_validate(raw)


@lru_cache(maxsize=1)
def load_config() -> HoldsConfig:
    raw = yaml.safe_load(resources.files("app.privacy").joinpath("holds.yaml").read_text("utf-8"))
    return parse_config(raw)


__all__ = ["HoldsConfig", "RetentionJob", "Review", "load_config", "parse_config"]
