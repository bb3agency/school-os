"""Exports configuration: layouts, retention, limits and bilingual labels (FR-EXP-001).

Pure module (no database or web): loads and validates ``config.yaml`` next to it. Profile
layouts are keyed by the data-quality profile key (``app/dq/config/profiles``); the service
checks at use time that every required field of the DQ profile is in the layout.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_PATH: Final = Path(__file__).with_name("config.yaml")
Language = Literal["en", "te"]
LayoutKind = Literal["board", "portal"]
StructureColumn = Literal["class", "section", "roll_no"]
_DATE_FORMAT_RE: Final = re.compile(r"^(?:%[dmY]|[/.\- ])+$")
_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROFILE_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

REQUIRED_LABELS: Final = frozenset(
    {
        "sheet_summary",
        "sheet_findings",
        "sheet_ready",
        "sheet_students",
        "title_precheck",
        "title_student_list",
        "school",
        "profile",
        "generated_at",
        "export_ref",
        "scope",
        "scope_all",
        "students",
        "blockers",
        "warnings",
        "students_with_blockers",
        "students_ready",
        "no_findings",
        "sensitive_masked",
        "col_severity",
        "col_admission_no",
        "col_student",
        "col_class_section",
        "col_rule",
        "col_field",
        "col_values",
        "col_explanation",
        "col_route",
        "col_status",
        "col_class",
        "col_section",
        "col_roll_no",
        "status_blocked",
        "status_warning",
        "status_ready",
        "severity_blocker",
        "severity_high",
        "severity_medium",
        "severity_low",
        "severity_info",
        "page",
    }
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Bilingual(_Model):
    en: str = Field(min_length=1, max_length=300)
    te: str = Field(min_length=1, max_length=300)

    def text(self, language: Language) -> str:
        return self.te if language == "te" else self.en


def _check_date_format(value: str) -> str:
    if not _DATE_FORMAT_RE.match(value):
        raise ValueError("date_format may use only %d, %m, %Y and / . - or space")
    return value


class PdfConfig(_Model):
    timeout_ms: int = Field(ge=1000, le=300_000)
    chromium_sandbox: bool


class ProfileLayout(_Model):
    kind: LayoutKind
    layout_version: int = Field(ge=1)
    date_format: str
    fields: tuple[str, ...] = Field(min_length=1, max_length=60)
    value_maps: dict[str, dict[str, str]] = Field(default_factory=dict)

    _date = field_validator("date_format")(_check_date_format)

    @field_validator("fields")
    @classmethod
    def _unique_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or not all(_KEY_RE.match(k) for k in value):
            raise ValueError("fields must be unique attribute keys")
        return value

    @model_validator(mode="after")
    def _maps_for_fields(self) -> ProfileLayout:
        unknown = set(self.value_maps) - set(self.fields)
        if unknown:
            raise ValueError(f"value_maps for fields not in the layout: {sorted(unknown)}")
        return self


class StudentListLayout(_Model):
    layout_version: int = Field(ge=1)
    date_format: str
    structure_columns: tuple[StructureColumn, ...]

    _date = field_validator("date_format")(_check_date_format)


class ExportsConfig(_Model):
    version: int = Field(ge=1)
    retention_days: int = Field(ge=1, le=30)
    max_students: int = Field(ge=1, le=5000)
    max_findings: int = Field(ge=1, le=100_000)
    download_url_ttl_s: int = Field(ge=30, le=300)
    watermark: Bilingual
    pdf: PdfConfig
    never_exported: frozenset[str]
    aadhaar_last4_display: str
    profiles: dict[str, ProfileLayout] = Field(min_length=1)
    student_list: StudentListLayout
    source_labels: dict[str, Bilingual]
    labels: dict[str, Bilingual]

    @field_validator("aadhaar_last4_display")
    @classmethod
    def _last4_only(cls, value: str) -> str:
        if value.count("{last4}") != 1 or re.search(r"\d", value.replace("{last4}", "")):
            raise ValueError("aadhaar_last4_display must show {last4} once and no other digits")
        return value

    @field_validator("profiles")
    @classmethod
    def _profile_keys(cls, value: dict[str, ProfileLayout]) -> dict[str, ProfileLayout]:
        bad = [k for k in value if not _PROFILE_RE.match(k)]
        if bad:
            raise ValueError(f"invalid profile keys: {bad}")
        return value

    @model_validator(mode="after")
    def _labels_complete(self) -> ExportsConfig:
        missing = REQUIRED_LABELS - set(self.labels)
        if missing:
            raise ValueError(f"labels missing: {sorted(missing)}")
        for key, layout in self.profiles.items():
            leaked = set(layout.fields) & self.never_exported
            if leaked:
                raise ValueError(f"{key}: fields that are never exported: {sorted(leaked)}")
        return self

    def label(self, key: str, language: Language) -> str:
        return self.labels[key].text(language)

    def source_label(self, source: str, language: Language) -> str:
        label = self.source_labels.get(source)
        return label.text(language) if label is not None else source

    def watermark_text(self) -> str:
        """Both languages, as printed on every file (US-901 AC2)."""
        return f"{self.watermark.en} · {self.watermark.te}"


def parse_config(raw: object) -> ExportsConfig:
    return ExportsConfig.model_validate(raw)


@lru_cache(maxsize=1)
def load_config() -> ExportsConfig:
    return parse_config(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))


__all__ = [
    "Bilingual",
    "ExportsConfig",
    "Language",
    "LayoutKind",
    "PdfConfig",
    "ProfileLayout",
    "StudentListLayout",
    "load_config",
    "parse_config",
]
