"""Certificate configuration: types, printed fields, inputs, serial format (``config.yaml``;
FR-CERT-001, FR-CERT-006, FR-CERT-009; invariant 13).

Pure: reads and validates the versioned file next to this module; no database. The database
CHECKs of migration ``0033_certificates`` (types, serial shape, reason lengths) are the second
line of defence.
"""

from __future__ import annotations

import re
import string
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH: Final = Path(__file__).with_name("config.yaml")
CERTIFICATE_TYPES: Final = ("transfer", "bonafide", "study", "conduct")
SERIAL_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,39}$")  # certificates_serial_shape
_KEY_RE: Final = r"^[a-z][a-z0-9_]{0,40}$"
_FIELDS: Final = frozenset({"prefix", "year", "number"})
# Never printed on a certificate, whatever the file says (FR-CERT-009, PRV-013).
NEVER_PRINTED: Final = frozenset(
    {
        "aadhaar_last4",
        "aadhaar_name_as_printed",
        "aadhaar_dob_as_printed",
        "aadhaar_gender_as_printed",
    }
)

CertificateType = Literal["transfer", "bonafide", "study", "conduct"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputSpec(_Frozen):
    kind: Literal["date", "choice", "text"]
    required: bool
    choices: tuple[str, ...] = ()
    max_length: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def _shape(self) -> InputSpec:
        if (self.kind == "choice") != bool(self.choices):
            raise ValueError("choices are given exactly for kind 'choice'")
        if (self.kind == "text") != (self.max_length is not None):
            raise ValueError("max_length is given exactly for kind 'text'")
        return self


class TypeSpec(_Frozen):
    label_en: str = Field(min_length=1, max_length=80)
    label_te: str = Field(min_length=1, max_length=80)
    prefix: str = Field(pattern=r"^[A-Z]{1,6}$")
    requires_approval: bool
    ends_enrolment: bool
    needs_enrolment: bool
    printed: tuple[str, ...] = Field(min_length=1)
    required: tuple[str, ...]
    inputs: dict[str, InputSpec]
    official_format_todo: tuple[str, ...]

    @model_validator(mode="after")
    def _fields(self) -> TypeSpec:
        for key in (*self.printed, *self.inputs, *self.official_format_todo):
            if not re.match(_KEY_RE, key):
                raise ValueError(f"bad field key {key!r}")
        if set(self.printed) & NEVER_PRINTED:
            raise ValueError("Aadhaar fields are never printed on a certificate")
        if not set(self.required) <= set(self.printed):
            raise ValueError("required fields must be printed fields")
        if len(set(self.printed)) != len(self.printed):
            raise ValueError("printed fields are listed once")
        return self


class SerialSpec(_Frozen):
    format: str = Field(min_length=1, max_length=40)
    number_width: int = Field(ge=1, le=6)

    @model_validator(mode="after")
    def _placeholders(self) -> SerialSpec:
        names = {f for _, f, _, _ in string.Formatter().parse(self.format) if f is not None}
        if not names <= _FIELDS or "number" not in names:
            raise ValueError("serial.format uses {prefix}, {year} and {number} only")
        return self


class ChoiceLabel(_Frozen):
    en: str = Field(min_length=1, max_length=120)
    te: str = Field(min_length=1, max_length=120)


class CertificatesConfig(_Frozen):
    version: int = Field(ge=1)
    template_version: str = Field(pattern=r"^v[0-9]{1,3}$")
    serial: SerialSpec
    reason_min_length: int = Field(ge=1)
    reason_max_length: int = Field(le=1000)
    document_acl_roles: tuple[str, ...] = Field(min_length=1)
    download_url_ttl_s: int = Field(ge=1, le=300)
    register_max_rows: int = Field(ge=1, le=100_000)
    types: dict[str, TypeSpec]
    choice_labels: dict[str, ChoiceLabel]
    detail_labels: dict[str, ChoiceLabel]
    blank_labels: dict[str, ChoiceLabel]

    @model_validator(mode="after")
    def _consistent(self) -> CertificatesConfig:
        if tuple(sorted(self.types)) != tuple(sorted(CERTIFICATE_TYPES)):
            raise ValueError(f"types must be exactly {CERTIFICATE_TYPES}")
        if not self.reason_min_length <= self.reason_max_length:
            raise ValueError("reason_min_length <= reason_max_length")
        for key, spec in self.types.items():
            for name, inp in spec.inputs.items():
                missing = [c for c in inp.choices if c not in self.choice_labels]
                if missing:
                    raise ValueError(f"{key}.{name}: no labels for {missing}")
                if name not in self.detail_labels:
                    raise ValueError(f"{key}.{name}: no detail label")
            unlabelled = [b for b in spec.official_format_todo if b not in self.blank_labels]
            if unlabelled:
                raise ValueError(f"{key}: no blank labels for {unlabelled}")
            sample = format_serial(self.serial, spec.prefix, "2026-27", 999999)
            if not SERIAL_RE.match(sample):
                raise ValueError(f"{key}: serial {sample!r} does not fit the database shape")
        return self

    def spec(self, certificate_type: str) -> TypeSpec:
        return self.types[certificate_type]


def format_serial(serial: SerialSpec, prefix: str, year_label: str, number: int) -> str:
    """``TC/2026-27/0001`` (FR-CERT-006). The year label is the academic year's label."""
    return serial.format.format(
        prefix=prefix, year=year_label, number=str(number).zfill(serial.number_width)
    )


@lru_cache(maxsize=1)
def load_config() -> CertificatesConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text("utf-8"))
    return CertificatesConfig.model_validate(raw)


__all__ = [
    "CERTIFICATE_TYPES",
    "NEVER_PRINTED",
    "CertificateType",
    "CertificatesConfig",
    "InputSpec",
    "TypeSpec",
    "format_serial",
    "load_config",
]
