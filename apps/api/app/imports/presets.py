"""Import template library: packaged column presets and blank templates (FR-IMP-030..033).

Pure module (no database or web). Each ``app/imports/templates/<key>.yaml`` describes a common
starting file (the blank SchoolOS template, a UDISE+ student list, a typical admission register
in Excel, a generic ERP student export): its column headers, the import target of each column,
the import source it is usually recorded from, and, for formats owned by someone else, the
public ``source`` URLs and ``verified: false`` (owner decision D3, ADR-0041).

- :func:`load_presets` validates the files once.
- :func:`preset_mapping` maps a file's headers onto a preset's targets (exact match after the
  same normalisation as the mapping suggestions; aliases count). Columns the preset does not
  know stay unmapped, so the clerk reviews them in the mapping step as before.
- :func:`template_header` is the header row of the downloadable blank template.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.imports.mapping import normalize_header

PRESET_KEY_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TARGET_RE: Final = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_URL_RE: Final = re.compile(r"^https://\S{4,500}$")
_FOLDER: Final = "templates"

ImportSource = Literal[
    "admission_register",
    "aadhaar_as_printed",
    "udise_plus",
    "board_registration",
    "birth_certificate",
    "parent_form",
    "tc_incoming",
    "manual_entry",
]


class PresetColumn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    header: str = Field(min_length=1, max_length=80)
    target: str
    aliases: tuple[str, ...] = ()
    note: str = Field(default="", max_length=300)

    @field_validator("target")
    @classmethod
    def _target(cls, value: str) -> str:
        if not _TARGET_RE.match(value):
            raise ValueError("target must be an attribute key or a structure column")
        return value


class Preset(BaseModel):
    """One packaged starting file and its column mapping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    version: int = Field(ge=1)
    label_en: str = Field(min_length=1, max_length=120)
    label_te: str = Field(min_length=1, max_length=120)
    description_en: str = Field(min_length=1, max_length=400)
    import_source: ImportSource
    template: bool = Field(
        default=False, description="Offered as a downloadable blank Excel template."
    )
    verified: bool = False
    source: tuple[str, ...] = ()
    columns: tuple[PresetColumn, ...] = Field(min_length=1, max_length=60)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not PRESET_KEY_RE.match(value):
            raise ValueError("preset keys are lowercase letters, digits and hyphens")
        return value

    @field_validator("source")
    @classmethod
    def _source(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not all(_URL_RE.match(v) for v in value):
            raise ValueError("source entries must be https URLs")
        return value

    @model_validator(mode="after")
    def _unique(self) -> Preset:
        targets = [c.target for c in self.columns]
        if len(set(targets)) != len(targets):
            raise ValueError(f"{self.key}: each target may appear once")
        names = [normalize_header(n) for c in self.columns for n in (c.header, *c.aliases)]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.key}: headers and aliases must be distinct")
        if "admission_no" not in targets:
            raise ValueError(f"{self.key}: the admission number column is required")
        return self


@dataclass(frozen=True, slots=True)
class PresetMatch:
    index: int
    header: str
    target: str | None


def parse_presets(raw: Mapping[str, Any]) -> dict[str, Preset]:
    """``{file stem: raw yaml}`` -> presets by key (the key must equal the file stem)."""
    out: dict[str, Preset] = {}
    for stem, data in raw.items():
        preset = Preset.model_validate(data)
        if preset.key != stem:
            raise ValueError(f"preset file {stem}.yaml declares key {preset.key!r}")
        out[preset.key] = preset
    return dict(sorted(out.items()))


@functools.cache
def load_presets() -> dict[str, Preset]:
    """Every ``app/imports/templates/*.yaml``, validated once."""
    folder = resources.files("app.imports").joinpath(_FOLDER)
    raw = {
        entry.name.removesuffix(".yaml"): yaml.safe_load(entry.read_text("utf-8"))
        for entry in folder.iterdir()
        if entry.name.endswith(".yaml")
    }
    return parse_presets(raw)


def preset_mapping(
    headers: Sequence[str], preset: Preset, allowed_targets: Collection[str]
) -> list[PresetMatch]:
    """One entry per file column: the preset's target for a header it knows (and the import
    source may record), else ``None``. Each target is used once (left-most column wins)."""
    lookup: dict[str, str] = {}
    for column in preset.columns:
        for name in (column.header, *column.aliases):
            lookup[normalize_header(name)] = column.target
    used: set[str] = set()
    out: list[PresetMatch] = []
    for index, header in enumerate(headers):
        target = lookup.get(normalize_header(header))
        if target is None or target in used or target not in allowed_targets:
            out.append(PresetMatch(index, header, None))
            continue
        used.add(target)
        out.append(PresetMatch(index, header, target))
    return out


def missing_columns(headers: Sequence[str], preset: Preset) -> list[str]:
    """The preset's column headers that the file does not have (by header or alias)."""
    present = {normalize_header(h) for h in headers}
    return [
        c.header
        for c in preset.columns
        if not any(normalize_header(n) in present for n in (c.header, *c.aliases))
    ]


def template_header(preset: Preset) -> list[str]:
    """The header row of the preset's blank template, in the preset's column order."""
    return [c.header for c in preset.columns]


__all__ = [
    "Preset",
    "PresetColumn",
    "PresetMatch",
    "load_presets",
    "missing_columns",
    "parse_presets",
    "preset_mapping",
    "template_header",
]
