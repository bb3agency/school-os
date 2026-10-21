"""Disclosure formats (ADR-0051 decision 1; FR-DISC-001, FR-DISC-002, FR-DISC-006).

Pure module: reads and validates the versioned format files in ``formats/`` next to this module
(one YAML file per format; the file name is its key). Loaded once; an unknown fill, source or
input type fails the load, so a bad file stops the API at start-up (FR-DISC-002).

Rules checked at load:

- item keys are unique across the format and stable (``[a-z][a-z0-9_]``);
- ``school`` items name one of :data:`SCHOOL_SOURCES`, ``derived`` items one of
  :data:`DERIVED_SOURCES` (computed by ``app.disclosure.derived`` through other modules'
  services);
- ``input`` items name a type of :data:`INPUT_TYPES` with its limits; only ``email`` and ``phone``
  items may be ``contact`` items (the only ones that may hold a phone-like number);
- no label describes an individual child (FR-DISC-006): the words in :data:`_CHILD_WORDS` are
  refused in labels.
"""

from __future__ import annotations

import re
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
        "fee_structure",
        "academic_year",
    }
)
INPUT_TYPES: Final = frozenset({"text", "integer", "number", "yes_no", "email", "phone", "results"})
_CHILD_WORDS: Final = re.compile(
    r"\b(student'?s? name|pupil|child'?s? name|admission no|roll no|date of birth|aadhaar)\b",
    re.IGNORECASE,
)

Fill = Literal["school", "derived", "input", "document", "link"]
InputType = Literal["text", "integer", "number", "yes_no", "email", "phone", "results"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Source(_Frozen):
    title: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, pattern=r"^https://[^\s]+$", max_length=400)


class FormatItem(_Frozen):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str = Field(min_length=1, max_length=300)
    fill: Fill
    source: str | None = None
    type: InputType | None = None
    max_length: int | None = Field(default=None, ge=1, le=2000)
    max_value: int | None = Field(default=None, ge=1, le=1_000_000_000)
    max_rows: int | None = Field(default=None, ge=1, le=30)
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


def _load_file(path: Path) -> DisclosureFormat:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fmt = DisclosureFormat.model_validate(data)
    if fmt.key != path.stem:
        raise ValueError(f"{path.name}: key {fmt.key!r} must match the file name")
    return fmt


@lru_cache(maxsize=1)
def formats() -> dict[str, DisclosureFormat]:
    """Every format file, by key (validated; cached)."""
    out = {p.stem: _load_file(p) for p in sorted(FORMATS_DIR.glob("*.yaml"))}
    if DEFAULT_FORMAT not in out:
        raise ValueError(f"the default format {DEFAULT_FORMAT} is missing")
    return out


def current() -> DisclosureFormat:
    """The format schools fill now (CBSE Appendix IX)."""
    return formats()[DEFAULT_FORMAT]


__all__ = [
    "DEFAULT_FORMAT",
    "DERIVED_SOURCES",
    "INPUT_TYPES",
    "SCHOOL_SOURCES",
    "DisclosureFormat",
    "Fill",
    "FormatItem",
    "FormatSection",
    "InputType",
    "Source",
    "current",
    "formats",
]
