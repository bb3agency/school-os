"""Admin console configuration: the full export (``config.yaml``) and retention bounds
(``retention.yaml``) (US-1201, FR-ADM-001, FR-ADM-002; invariant 13).

Pure: reads the two versioned files next to this module and validates them; no database.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH: Final = Path(__file__).with_name("config.yaml")
RETENTION_PATH: Final = Path(__file__).with_name("retention.yaml")
MAX_DAYS: Final = 3650  # the database CHECK on ops.retention_settings.rules
CATEGORY_RE: Final = r"^[a-z][a-z0-9_]{0,62}$"
_JOB_RE: Final = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TenantExportConfig(_Frozen):
    layout_version: int = Field(ge=1)
    link_valid_hours: int = Field(ge=1, le=72)
    download_url_ttl_s: int = Field(ge=1, le=300)
    failed_cleanup_hours: int = Field(ge=1, le=168)
    max_audit_rows: int = Field(ge=1, le=100_000_000)
    max_document_bytes: int = Field(ge=1)
    task_soft_time_limit_s: int = Field(ge=60)
    task_time_limit_s: int = Field(ge=60)
    never_exported: tuple[str, ...]
    readme_en: str = Field(min_length=1)
    readme_te: str = Field(min_length=1)

    @model_validator(mode="after")
    def _limits(self) -> TenantExportConfig:
        if self.task_soft_time_limit_s >= self.task_time_limit_s:
            raise ValueError("task_soft_time_limit_s must be below task_time_limit_s")
        return self


class AdminConfig(_Frozen):
    version: int = Field(ge=1)
    tenant_export: TenantExportConfig


class RetentionCategory(_Frozen):
    default_days: int = Field(ge=1, le=MAX_DAYS)
    min_days: int = Field(ge=1, le=MAX_DAYS)
    max_days: int = Field(ge=1, le=MAX_DAYS)
    configurable: bool
    enforced_by: str | None

    @model_validator(mode="after")
    def _bounds(self) -> RetentionCategory:
        if not self.min_days <= self.default_days <= self.max_days:
            raise ValueError("min_days <= default_days <= max_days")
        if not self.configurable and self.min_days != self.max_days:
            raise ValueError("a fixed category has min_days == max_days")
        if self.configurable and self.enforced_by is None:
            raise ValueError("a configurable category names the job that enforces it")
        if self.enforced_by is not None and not _JOB_RE.match(self.enforced_by):
            raise ValueError("enforced_by is a task name")
        return self

    def clamp(self, days: int) -> int:
        return min(max(days, self.min_days), self.max_days)


class RetentionConfig(_Frozen):
    version: int = Field(ge=1)
    categories: dict[str, RetentionCategory] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys(self) -> RetentionConfig:
        bad = [k for k in self.categories if not re.match(CATEGORY_RE, k)]
        if bad:
            raise ValueError(f"category keys must match {CATEGORY_RE}: {bad}")
        return self


@lru_cache(maxsize=1)
def load_config() -> AdminConfig:
    return AdminConfig.model_validate(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def load_retention() -> RetentionConfig:
    return RetentionConfig.model_validate(
        yaml.safe_load(RETENTION_PATH.read_text(encoding="utf-8"))
    )


__all__ = [
    "AdminConfig",
    "RetentionCategory",
    "RetentionConfig",
    "TenantExportConfig",
    "load_config",
    "load_retention",
]
