"""Engine settings and export pre-check profiles (FR-DQ-002, US-501, CLAUDE.md §6.13).

Pure module (no database): reads and validates the packaged YAML once.

- ``config/engine.yaml`` -> :class:`EngineConfig`: synchronous-run limit, source attribute
  mapping (Aadhaar-as-printed values are their own C3 attributes), DQ-008 duplicate classes,
  DQ-007 age bands per class code and the DQ-006 format-issue labels (EN/TE).
- ``config/profiles/<key>.yaml`` -> :class:`Profile`: what one board/portal submission needs
  (DQ-005 required fields, DQ-006 name format, DQ-009 APAAR details).
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from importlib import resources
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.textnorm import has_telugu
from app.dq.matching import MatchClass
from app.dq.rules import Severity

PROFILE_KEY_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ATTRIBUTE_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
FORMAT_ISSUES: Final = ("too_long", "too_short", "not_latin", "digits", "symbols")


class Label(BaseModel):
    """An English and a Telugu phrase."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    en: str = Field(min_length=1)
    te: str = Field(min_length=1)

    @model_validator(mode="after")
    def _telugu(self) -> Label:
        if not has_telugu(self.te):
            raise ValueError("the Telugu label must be written in Telugu script")
        return self

    def text(self, language: str) -> str:
        return self.te if language == "te" else self.en


class DuplicatePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name_classes: frozenset[MatchClass]
    parent_classes: frozenset[MatchClass]

    @field_validator("name_classes", "parent_classes")
    @classmethod
    def _no_missing(cls, value: frozenset[MatchClass]) -> frozenset[MatchClass]:
        if not value or MatchClass.MISSING in value:
            raise ValueError("duplicate classes must be non-empty and never MISSING")
        return value


class EngineConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    sync_max_students: int = Field(ge=0)
    source_attributes: dict[str, dict[str, str]]
    apaar_attributes: tuple[str, ...] = Field(min_length=1)
    duplicate: DuplicatePolicy
    age_bands: dict[str, tuple[int, int]]
    format_issues: dict[str, Label]

    @field_validator("age_bands")
    @classmethod
    def _bands(cls, value: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
        for code, (low, high) in value.items():
            if not 0 <= low <= high <= 30:
                raise ValueError(f"age band for {code} must satisfy 0 <= min <= max <= 30")
        return value

    @model_validator(mode="after")
    def _issues(self) -> EngineConfig:
        if set(self.format_issues) != set(FORMAT_ISSUES):
            raise ValueError(f"format_issues must list exactly {list(FORMAT_ISSUES)}")
        for mapping in self.source_attributes.values():
            for key, physical in mapping.items():
                if not (_ATTRIBUTE_RE.match(key) and _ATTRIBUTE_RE.match(physical)):
                    raise ValueError("source attribute keys must be lowercase identifiers")
        return self

    def physical_key(self, attribute_key: str, source: str) -> str:
        """The attribute key under which ``source`` stores ``attribute_key``."""
        return self.source_attributes.get(source, {}).get(attribute_key, attribute_key)


class NameFormat(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: tuple[str, ...] = Field(min_length=1)
    min_length: int = Field(ge=1)
    max_length: int = Field(ge=1)
    latin_only: bool = True
    allowed_punctuation: str = " "

    @model_validator(mode="after")
    def _lengths(self) -> NameFormat:
        if self.min_length > self.max_length:
            raise ValueError("name_format min_length is above max_length")
        return self


class Profile(BaseModel):
    """One board/portal submission format (the exports module reuses the key)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    version: int = Field(ge=1)
    label_en: str = Field(min_length=1)
    label_te: str = Field(min_length=1)
    required_fields: tuple[str, ...] = Field(min_length=1)
    unverified_identity_severity: Severity | None = None
    name_format: NameFormat | None = None
    needs_apaar: bool = False

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not PROFILE_KEY_RE.match(value):
            raise ValueError("profile keys are lowercase letters, digits and hyphens")
        return value

    @field_validator("required_fields")
    @classmethod
    def _fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or not all(_ATTRIBUTE_RE.match(v) for v in value):
            raise ValueError("required fields must be unique attribute keys")
        return value

    @model_validator(mode="after")
    def _telugu(self) -> Profile:
        if not has_telugu(self.label_te):
            raise ValueError("label_te must be written in Telugu script")
        return self

    def label(self, language: str) -> str:
        return self.label_te if language == "te" else self.label_en


def _read(path: str) -> Any:
    return yaml.safe_load(resources.files("app.dq").joinpath(path).read_text("utf-8"))


def parse_engine_config(raw: Any) -> EngineConfig:
    return EngineConfig.model_validate(raw)


@functools.cache
def load_engine_config() -> EngineConfig:
    """``app/dq/config/engine.yaml``, validated once."""
    return parse_engine_config(_read("config/engine.yaml"))


def parse_profiles(raw: Mapping[str, Any]) -> dict[str, Profile]:
    """``{file stem: raw yaml}`` -> profiles by key (the key must equal the file stem)."""
    out: dict[str, Profile] = {}
    for stem, data in raw.items():
        profile = Profile.model_validate(data)
        if profile.key != stem:
            raise ValueError(f"profile file {stem}.yaml declares key {profile.key!r}")
        out[profile.key] = profile
    return dict(sorted(out.items()))


@functools.cache
def load_profiles() -> dict[str, Profile]:
    """Every ``app/dq/config/profiles/*.yaml``, validated once."""
    folder = resources.files("app.dq").joinpath("config/profiles")
    raw = {
        entry.name.removesuffix(".yaml"): yaml.safe_load(entry.read_text("utf-8"))
        for entry in folder.iterdir()
        if entry.name.endswith(".yaml")
    }
    return parse_profiles(raw)
