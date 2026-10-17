"""Transfer certificate formats (ADR-0045 decision 1; FR-CERT-015..018; US-2001, US-2006).

Pure module: reads and validates the versioned format files in ``formats/`` next to this module
(one YAML file per format, the file name is its key). ``generic-v1`` is the built-in format of
M3 (``config.yaml`` ``types.transfer``: printed fields, then details, then blank lines to fill by
hand); it has no file. A school picks its TC format in its settings
(``certificate_format.transfer``, ``tenant.settings.manage`` with step-up); an issued certificate
stores the format key and version it was issued with.

Rules checked at load (FR-CERT-009, FR-CERT-016..018):

- an ``attribute`` line never names Aadhaar data, and never a restricted attribute: those are
  ``restricted`` lines, only for :data:`RESTRICTED_ATTRIBUTES` (religion, caste), printed blank
  unless the school asks;
- ``identifier`` lines are only ``udise_pen`` and ``apaar_id`` (their printing rule lives in the
  service);
- every input is used by exactly one line and every line's input exists; choices have labels;
- no line is about fees: a TC is never withheld or annotated for dues (owner decision D16).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.certificates.config import NEVER_PRINTED, ChoiceLabel, InputSpec, load_config

FORMATS_DIR: Final = Path(__file__).with_name("formats")
GENERIC_KEY: Final = "generic-v1"
GENERIC_VERSION: Final = 1
FORMAT_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
# C3 attributes a TC may print only when the school turned on print_restricted_tc_fields
# (FR-CERT-018, owner decision D15). Kept equal to app.tenancy.schemas.RESTRICTED_TC_FIELDS
# (tests/certificates/test_formats.py).
RESTRICTED_ATTRIBUTES: Final = frozenset({"religion", "caste"})
IDENTIFIERS: Final = frozenset({"udise_pen", "apaar_id"})
DERIVED: Final = frozenset({"dob_in_words", "admission_class", "class_at_leaving"})
_FEE_WORDS: Final = re.compile(r"\b(fee|fees|dues|paid)\b", re.IGNORECASE)

Fill = Literal["attribute", "input", "derived", "identifier", "restricted"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Source(_Frozen):
    title: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, pattern=r"^https://[^\s]+$", max_length=300)


class FormatLine(_Frozen):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    label_en: str = Field(min_length=1, max_length=200)
    label_te: str = Field(min_length=1, max_length=200)
    fill: Fill
    attribute: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")
    input: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,40}$")
    derived: str | None = None
    identifier: str | None = None
    required: bool = False

    @model_validator(mode="after")
    def _shape(self) -> FormatLine:
        given = {
            "attribute": self.attribute is not None,
            "input": self.input is not None,
            "derived": self.derived is not None,
            "identifier": self.identifier is not None,
        }
        wanted = "attribute" if self.fill == "restricted" else self.fill
        if [k for k, v in given.items() if v] != [wanted]:
            raise ValueError(f"line {self.key}: fill {self.fill!r} names exactly its source")
        if self.required and self.fill != "attribute":
            raise ValueError(f"line {self.key}: only attribute lines can be required")
        if self.fill == "attribute" and self.attribute in NEVER_PRINTED | RESTRICTED_ATTRIBUTES:
            raise ValueError(f"line {self.key}: {self.attribute} is never printed as a value")
        if self.fill == "restricted" and self.attribute not in RESTRICTED_ATTRIBUTES:
            raise ValueError(
                f"line {self.key}: only {sorted(RESTRICTED_ATTRIBUTES)} are restricted"
            )
        if self.fill == "identifier" and self.identifier not in IDENTIFIERS:
            raise ValueError(f"line {self.key}: identifiers are {sorted(IDENTIFIERS)}")
        if self.fill == "derived" and self.derived not in DERIVED:
            raise ValueError(f"line {self.key}: derived values are {sorted(DERIVED)}")
        if _FEE_WORDS.search(self.label_en) or "fee" in self.key:
            raise ValueError(f"line {self.key}: a TC never carries fees or dues (D16)")
        return self


class TcFormat(_Frozen):
    key: str = Field(pattern=FORMAT_KEY_RE.pattern)
    version: int = Field(ge=1, le=999)
    certificate_type: Literal["transfer"]
    label_en: str = Field(min_length=1, max_length=120)
    label_te: str = Field(min_length=1, max_length=120)
    title_en: str = Field(min_length=1, max_length=80)
    title_te: str = Field(min_length=1, max_length=80)
    verified: bool
    sources: tuple[Source, ...] = Field(min_length=1)
    notes: str = Field(default="", max_length=2000)
    inputs: dict[str, InputSpec]
    choice_labels: dict[str, ChoiceLabel] = Field(default_factory=dict)
    lines: tuple[FormatLine, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> TcFormat:
        keys = [line.key for line in self.lines]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.key}: line keys are listed once")
        used = [line.input for line in self.lines if line.input is not None]
        if sorted(used) != sorted(self.inputs):
            raise ValueError(f"{self.key}: every input is printed by exactly one line")
        known = {**load_config().choice_labels, **self.choice_labels}
        for name, spec in self.inputs.items():
            missing = [c for c in spec.choices if c not in known]
            if missing:
                raise ValueError(f"{self.key}.{name}: no labels for {missing}")
        if not any(line.required for line in self.lines):
            raise ValueError(f"{self.key}: at least one attribute line is required")
        return self

    def choice_label(self, value: str) -> ChoiceLabel | None:
        return self.choice_labels.get(value) or load_config().choice_labels.get(value)

    @property
    def attributes(self) -> tuple[str, ...]:
        """C1/C2 attributes the format prints (``attribute`` lines), in order."""
        return tuple(ln.attribute for ln in self.lines if ln.fill == "attribute" and ln.attribute)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(ln.attribute for ln in self.lines if ln.required and ln.attribute)

    @property
    def restricted(self) -> tuple[str, ...]:
        return tuple(ln.attribute for ln in self.lines if ln.fill == "restricted" and ln.attribute)


@lru_cache(maxsize=1)
def formats() -> dict[str, TcFormat]:
    """Every format file, by key (the file name without ``.yaml`` must equal the key)."""
    out: dict[str, TcFormat] = {}
    for path in sorted(FORMATS_DIR.glob("*.yaml")):
        fmt = TcFormat.model_validate(yaml.safe_load(path.read_text("utf-8")))
        if path.stem != fmt.key or fmt.key == GENERIC_KEY:
            raise ValueError(f"{path.name}: the file name must be the format key")
        out[fmt.key] = fmt
    return out


def format_keys() -> tuple[str, ...]:
    """Every TC format a school may choose: the built-in generic one, then the files."""
    return (GENERIC_KEY, *formats())


def get_format(key: str) -> TcFormat | None:
    """The file format ``key``; ``None`` for ``generic-v1`` (built in) or an unknown key."""
    return formats().get(key)


__all__ = [
    "DERIVED",
    "FORMATS_DIR",
    "GENERIC_KEY",
    "GENERIC_VERSION",
    "IDENTIFIERS",
    "RESTRICTED_ATTRIBUTES",
    "FormatLine",
    "Source",
    "TcFormat",
    "format_keys",
    "formats",
    "get_format",
]
