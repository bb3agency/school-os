"""Disclosure formats (ADR-0051 decision 1; FR-DISC-001, FR-DISC-002, FR-DISC-006).

Pure module: reads and validates the versioned format files in ``formats/`` next to this module
(one YAML file per format version, named ``{key}.v{version}.yaml``). Loaded once; an unknown fill,
source or input type fails the load, so a bad file stops the API at start-up (FR-DISC-002).

Several versions of a format live side by side (FR-DISC-013): new drafts use the highest version
(:func:`current`); a published snapshot keeps the key and version it was published with and its
frozen content, so an older version stays readable and renderable (:func:`get`).

Rules checked at load:

- item keys are unique across the format and stable (``[a-z][a-z0-9_]``);
- ``school`` items name one of :data:`SCHOOL_SOURCES`, ``derived`` items one of
  :data:`DERIVED_SOURCES` (computed by ``app.disclosure.derived`` through other modules'
  services);
- ``input`` items name a type of :data:`INPUT_TYPES` with its limits; only ``email`` and ``phone``
  items may be ``contact`` items (the only ones that may hold a phone-like number); a
  ``count_table`` item lists its ``rows`` and ``columns`` (counts per row and column, e.g. staff
  by category and qualification: numbers only, never names; FR-DISC-016, lead decision D84);
- no label (item, row or column) describes an individual child (FR-DISC-006): the words in
  :data:`_CHILD_WORDS` are refused in labels.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

FORMATS_DIR: Final = Path(__file__).with_name("formats")
DEFAULT_FORMAT: Final = "cbse-appendix-ix"
SCHOOL_SOURCES: Final = frozenset({"school.name", "school.address"})
DERIVED_SOURCES: Final = frozenset(
    {
        "principal",
        "teachers_total",
        "sections_count",
        "teacher_section_ratio",
        "staff_by_role",
        "staff_by_category",
        "registered_students",
        "fee_structure",
        "academic_year",
    }
)
INPUT_TYPES: Final = frozenset(
    {"text", "integer", "number", "yes_no", "email", "phone", "results", "count_table"}
)
_FILE_NAME: Final = re.compile(r"^(?P<key>[a-z0-9][a-z0-9-]{1,63})\.v(?P<version>[1-9][0-9]{0,3})$")
_CHILD_WORDS: Final = re.compile(
    r"\b(student'?s? name|pupil|child'?s? name|admission no|roll no|date of birth|aadhaar)\b",
    re.IGNORECASE,
)

Fill = Literal["school", "derived", "input", "document", "link"]
InputType = Literal[
    "text", "integer", "number", "yes_no", "email", "phone", "results", "count_table"
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Source(_Frozen):
    title: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, pattern=r"^https://[^\s]+$", max_length=400)


class Choice(_Frozen):
    """A row or column of a ``count_table`` item."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=120)


def _unique_choices(item: str, kind: str, choices: tuple[Choice, ...]) -> None:
    keys = [c.key for c in choices]
    if len(set(keys)) != len(keys):
        raise ValueError(f"item {item}: {kind} keys must be unique")
    for c in choices:
        if _CHILD_WORDS.search(c.label):
            raise ValueError(f"item {item}: no item may describe an individual child")


class FormatItem(_Frozen):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str = Field(min_length=1, max_length=300)
    fill: Fill
    source: str | None = None
    type: InputType | None = None
    max_length: int | None = Field(default=None, ge=1, le=2000)
    max_value: int | None = Field(default=None, ge=1, le=1_000_000_000)
    max_rows: int | None = Field(default=None, ge=1, le=30)
    rows: tuple[Choice, ...] | None = Field(default=None, min_length=1, max_length=20)
    columns: tuple[Choice, ...] | None = Field(default=None, min_length=1, max_length=10)
    required: bool = False
    contact: bool = False
    documents: bool = False

    @model_validator(mode="after")
    def _shape(self) -> FormatItem:
        if _CHILD_WORDS.search(self.label):
            raise ValueError(f"item {self.key}: no item may describe an individual child")
        if self.fill == "school" and self.source not in SCHOOL_SOURCES:
            raise ValueError(f"item {self.key}: unknown school source {self.source!r}")
        if self.fill == "derived" and self.source not in DERIVED_SOURCES:
            raise ValueError(f"item {self.key}: unknown derived source {self.source!r}")
        if self.fill not in ("school", "derived") and self.source is not None:
            raise ValueError(f"item {self.key}: only school and derived items have a source")
        if (self.fill == "input") != (self.type is not None):
            raise ValueError(f"item {self.key}: input items (only) name a type")
        if self.documents and self.fill != "derived":
            raise ValueError(f"item {self.key}: only derived items may also take documents")
        if self.contact and self.type not in ("email", "phone"):
            raise ValueError(f"item {self.key}: only e-mail and phone items are contact items")
        if self.type in ("email", "phone") and not self.contact:
            raise ValueError(f"item {self.key}: e-mail and phone items are contact items")
        if self.required and self.fill != "input":
            raise ValueError(f"item {self.key}: only input items can be required")
        if self.type in ("text", "email", "phone") and self.max_length is None:
            raise ValueError(f"item {self.key}: text items need max_length")
        if self.type in ("integer", "number") and self.max_value is None:
            raise ValueError(f"item {self.key}: number items need max_value")
        if self.type == "results" and self.max_rows is None:
            raise ValueError(f"item {self.key}: results items need max_rows")
        return self

    @model_validator(mode="after")
    def _table_shape(self) -> FormatItem:
        if self.type != "count_table":
            if self.rows is not None or self.columns is not None:
                raise ValueError(f"item {self.key}: only count tables have rows and columns")
            return self
        if self.rows is None or self.columns is None or self.max_value is None:
            raise ValueError(f"item {self.key}: count tables need rows, columns and max_value")
        _unique_choices(self.key, "row", self.rows)
        _unique_choices(self.key, "column", self.columns)
        return self

    @property
    def takes_documents(self) -> bool:
        return self.fill == "document" or self.documents


class FormatSection(_Frozen):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    title: str = Field(min_length=1, max_length=200)
    items: tuple[FormatItem, ...] = Field(min_length=1, max_length=60)


class DisclosureFormat(_Frozen):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    version: int = Field(ge=1)
    board: Literal["CBSE"]
    title: str = Field(min_length=1, max_length=200)
    verified: bool
    sources: tuple[Source, ...] = Field(min_length=1)
    sections: tuple[FormatSection, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _unique(self) -> DisclosureFormat:
        keys = [i.key for s in self.sections for i in s.items]
        if len(set(keys)) != len(keys):
            raise ValueError("item keys must be unique across the format")
        sections = [s.key for s in self.sections]
        if len(set(sections)) != len(sections):
            raise ValueError("section keys must be unique")
        return self

    def items(self) -> tuple[FormatItem, ...]:
        return tuple(i for s in self.sections for i in s.items)

    def item(self, key: str) -> FormatItem | None:
        return next((i for i in self.items() if i.key == key), None)


def load_file(path: Path) -> DisclosureFormat:
    """One format file; its name must be ``{key}.v{version}.yaml`` for its own key and version."""
    match = _FILE_NAME.fullmatch(path.stem)
    if match is None:
        raise ValueError(f"{path.name}: format files are named <key>.v<version>.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fmt = DisclosureFormat.model_validate(data)
    if (fmt.key, fmt.version) != (match["key"], int(match["version"])):
        raise ValueError(f"{path.name}: key and version must match the file name")
    return fmt


def collect(found: list[DisclosureFormat]) -> dict[str, dict[int, DisclosureFormat]]:
    """Formats by key and version; each key's versions run 1, 2, 3 ... without gaps."""
    out: dict[str, dict[int, DisclosureFormat]] = {}
    for fmt in found:
        if fmt.version in out.get(fmt.key, {}):
            raise ValueError(f"{fmt.key}: version {fmt.version} is defined twice")
        out.setdefault(fmt.key, {})[fmt.version] = fmt
    for key, versions in out.items():
        if sorted(versions) != list(range(1, len(versions) + 1)):
            raise ValueError(f"{key}: versions must run 1, 2, 3 ... without gaps")
    return out


@lru_cache(maxsize=1)
def formats() -> dict[str, dict[int, DisclosureFormat]]:
    """Every format file by key and version (validated; cached)."""
    out = collect([load_file(p) for p in sorted(FORMATS_DIR.glob("*.yaml"))])
    if DEFAULT_FORMAT not in out:
        raise ValueError(f"the default format {DEFAULT_FORMAT} is missing")
    return out


def latest(versions: Mapping[int, DisclosureFormat]) -> DisclosureFormat:
    return versions[max(versions)]


def current() -> DisclosureFormat:
    """The format new drafts use: the highest version of CBSE Appendix IX (FR-DISC-013)."""
    return latest(formats()[DEFAULT_FORMAT])


def get(key: str, version: int) -> DisclosureFormat | None:
    """One version of a format (older drafts and snapshots name theirs), or ``None``."""
    return formats().get(key, {}).get(version)


__all__ = [
    "DEFAULT_FORMAT",
    "DERIVED_SOURCES",
    "INPUT_TYPES",
    "SCHOOL_SOURCES",
    "Choice",
    "DisclosureFormat",
    "Fill",
    "FormatItem",
    "FormatSection",
    "InputType",
    "Source",
    "collect",
    "current",
    "formats",
    "get",
    "latest",
    "load_file",
]
