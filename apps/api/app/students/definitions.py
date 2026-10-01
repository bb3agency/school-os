"""Attribute definitions and value validation (FR-STU-006, FR-STU-012, SEC-013).

Pure functions over :class:`AttributeDef` (built from ``sis.attribute_definitions`` rows). Every
text input is checked for a full Aadhaar number first (12 digits passing Verhoeff), so the
rejection is the same wherever a value comes from: API, import or extraction.

The one exception is the typed APAAR ID (ADR-0037 option (a); FR-STU-015, PRV-020): the value of
a global ``digits12`` attribute listed in ``attributes.yaml`` (``apaar_id``) is exactly 12 ASCII
digits and is stored as that attribute only, even when it passes the Verhoeff check. It is never
treated as, or matched against, an Aadhaar number. Every other field still refuses one.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Final, Literal

import yaml

from app.core.errors import ValidationFailed
from app.core.redaction import contains_full_aadhaar
from app.core.textnorm import comparison_key

DataType = Literal["text", "date", "enum", "digits4", "digits12"]
Classification = Literal["C1", "C2", "C3"]

AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
AADHAAR_MESSAGE_KEY: Final = "errors.aadhaar_last4_only"
MIN_DATE: Final = dt.date(1900, 1, 1)
_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f]")
_WS_RE: Final = re.compile(r"\s+")
_MAX_TEXT: Final = 1000
# digits12 (FR-STU-015): 12 ASCII digits, optionally grouped 4-4-4 by one space or hyphen.
_DIGITS12_RE: Final = re.compile(r"[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}")
DIGITS12_CODE: Final = "digits12_required"
_EXPLICIT_PHONE_RE: Final = re.compile(r"\+91[ \u00a0-]?[6-9][0-9]{4}[ \u00a0-]?[0-9]{5}")


@dataclass(frozen=True, slots=True)
class CanonicalPolicy:
    precedence: tuple[str, ...]
    require_verified: bool
    anchor: str | None
    show_conflicts_from: tuple[str, ...]

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> CanonicalPolicy:
        anchor = raw.get("anchor")
        return cls(
            precedence=tuple(str(s) for s in raw.get("precedence", ())),
            require_verified=bool(raw.get("require_verified", False)),
            anchor=str(anchor) if anchor else None,
            show_conflicts_from=tuple(str(s) for s in raw.get("show_conflicts_from", ())),
        )


@dataclass(frozen=True, slots=True)
class AttributeDef:
    key: str
    data_type: DataType
    classification: Classification
    is_identity: bool
    policy: CanonicalPolicy
    label_en: str
    label_te: str
    sort_order: int = 1000
    validation: Mapping[str, Any] = field(default_factory=dict)
    is_global: bool = True

    @property
    def sensitive(self) -> bool:
        return self.classification == "C3"

    @property
    def is_name(self) -> bool:
        return bool(self.validation.get("name", False))

    @property
    def allowed_sources(self) -> tuple[str, ...] | None:
        sources = self.validation.get("sources")
        return tuple(str(s) for s in sources) if sources else None

    @property
    def allowed_values(self) -> tuple[str, ...] | None:
        values = self.validation.get("values")
        return tuple(str(v) for v in values) if values else None

    @property
    def max_length(self) -> int | None:
        """Longest accepted value after cleaning (:func:`validate_value`); ``None`` for dates."""
        if self.data_type == "date":
            return None
        return int(self.validation.get("max_length", _MAX_TEXT))

    @property
    def pattern(self) -> str | None:
        pattern = self.validation.get("pattern")
        return pattern if isinstance(pattern, str) else None

    @property
    def not_future(self) -> bool:
        return bool(self.validation.get("not_future", False))


@dataclass(frozen=True, slots=True)
class CleanValue:
    """A validated value ready to store: exactly one of text/date is set; ``plain`` is the
    canonical string (ISO for dates) used for C3 encryption and comparisons."""

    plain: str
    text: str | None = None
    date: dt.date | None = None
    norm: str | None = None


def error(field_name: str, code: str, message_key: str | None = None) -> dict[str, str]:
    return {"field": field_name, "code": code, "message_key": message_key or f"errors.{code}"}


def aadhaar_error(field_name: str) -> dict[str, str]:
    return error(field_name, AADHAAR_CODE, AADHAAR_MESSAGE_KEY)


def is_full_aadhaar(value: str) -> bool:
    """True if ``value`` contains a full Aadhaar number (12 digits passing Verhoeff).

    A value that is exactly an explicit Indian mobile number (``+91 98765 43210``) is a phone,
    even if its 12 digits happen to pass the checksum (same rule as ``core.redaction``).
    """
    if _EXPLICIT_PHONE_RE.fullmatch(value.strip()):
        return False
    return contains_full_aadhaar(value)


AADHAAR_DETAIL: Final = "Don't enter Aadhaar numbers. Enter only the last 4 digits."


def reject_full_aadhaar(values: Mapping[str, object]) -> None:
    """422 ``aadhaar_full_number_rejected`` for every field holding a full Aadhaar number."""
    errors = [
        aadhaar_error(name)
        for name, value in values.items()
        if isinstance(value, str | int)
        and not isinstance(value, bool)
        and is_full_aadhaar(str(value))
    ]
    if errors:
        raise ValidationFailed(errors, detail=AADHAAR_DETAIL)


@functools.cache
def typed_digits12_keys() -> frozenset[str]:
    """Global ``digits12`` attributes of the packaged catalogue (``apaar_id``, ADR-0037)."""
    raw = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    return frozenset(
        key for key, spec in raw["attributes"].items() if spec.get("data_type") == "digits12"
    )


def digits12_value(raw: str) -> str | None:
    """The 12 ASCII digits of a ``digits12`` input (``1234 5678 9012`` -> ``123456789012``)."""
    text = clean_text(raw)
    if _DIGITS12_RE.fullmatch(text) is None:
        return None
    return text.replace(" ", "").replace("-", "")


def _typed_digits12(payload: Mapping[Any, Any], typed_keys: Collection[str]) -> bool:
    """A ``{attribute_key: <typed digits12 key>, value: <12 digits>}`` entry (ADR-0037)."""
    key, value = payload.get("attribute_key"), payload.get("value")
    return key in typed_keys and isinstance(value, str) and digits12_value(value) is not None


def find_full_aadhaar(
    payload: object, path: str = "", *, typed_keys: Collection[str] = frozenset()
) -> list[str]:
    """Paths of all strings/integers in a JSON-like payload that contain a full Aadhaar number.

    ``typed_keys`` (:func:`typed_digits12_keys`, student routes only): the ``value`` of an entry
    whose ``attribute_key`` is one of them is skipped when it is exactly 12 digits, so a typed
    APAAR ID is not refused (FR-STU-015). Its ``attribute_key`` and every other field are still
    scanned.
    """
    found: list[str] = []
    if isinstance(payload, bool) or payload is None:
        return found
    if isinstance(payload, str | int):
        if is_full_aadhaar(str(payload)):
            found.append(path or "body")
    elif isinstance(payload, Mapping):
        skip_value = bool(typed_keys) and _typed_digits12(payload, typed_keys)
        for key, value in payload.items():
            if skip_value and key == "value":
                continue
            found.extend(
                find_full_aadhaar(
                    value, f"{path}.{key}" if path else str(key), typed_keys=typed_keys
                )
            )
    elif isinstance(payload, Sequence):
        for i, value in enumerate(payload):
            found.extend(
                find_full_aadhaar(value, f"{path}.{i}" if path else str(i), typed_keys=typed_keys)
            )
    return found


def clean_text(raw: str) -> str:
    """NFC, trimmed, single spaces; control characters rejected by the caller."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", raw)).strip()


def validate_value(
    definition: AttributeDef,
    source: str,
    raw: str,
    *,
    field_name: str = "value",
    today: dt.date | None = None,
) -> CleanValue:
    """Validate and normalise one value for ``definition`` (422 with field-level codes)."""
    if _is_typed_digits12(definition):
        return _digits12(definition, source, raw, field_name)
    if is_full_aadhaar(raw):
        raise ValidationFailed([aadhaar_error(field_name)], detail=AADHAAR_DETAIL)
    allowed = definition.allowed_sources
    if allowed is not None and source not in allowed:
        raise ValidationFailed([error("source", "source_not_allowed")])
    value = clean_text(raw)
    if not value:
        raise ValidationFailed([error(field_name, "missing")])
    if _CONTROL_RE.search(value) or len(value) > _MAX_TEXT:
        raise ValidationFailed([error(field_name, "invalid")])
    rules = definition.validation
    if definition.data_type == "date":
        return _date_value(value, rules, field_name, today or dt.datetime.now(dt.UTC).date())
    max_length = int(rules.get("max_length", _MAX_TEXT))
    if len(value) > max_length:
        raise ValidationFailed([error(field_name, "too_long")])
    if definition.data_type == "digits4" and re.fullmatch(r"[0-9]{4}", value) is None:
        raise ValidationFailed([error(field_name, "digits4_required", AADHAAR_MESSAGE_KEY)])
    pattern = rules.get("pattern")
    if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
        raise ValidationFailed([error(field_name, "invalid_format")])
    if definition.data_type == "enum":
        options = [str(v) for v in rules.get("values", ())]
        lowered = value.lower()
        if lowered not in options:
            raise ValidationFailed([error(field_name, "not_allowed")])
        value = lowered
    norm = comparison_key(value) if definition.is_name else None
    return CleanValue(plain=value, text=value, norm=norm)


def _is_typed_digits12(definition: AttributeDef) -> bool:
    return (
        definition.data_type == "digits12"
        and definition.is_global
        and definition.key in typed_digits12_keys()
    )


def _digits12(definition: AttributeDef, source: str, raw: str, field_name: str) -> CleanValue:
    """FR-STU-015: exactly 12 ASCII digits, stored only as this attribute (ADR-0037). Anything
    else that holds a full Aadhaar number is refused as one."""
    allowed = definition.allowed_sources
    if allowed is not None and source not in allowed:
        raise ValidationFailed([error("source", "source_not_allowed")])
    digits = digits12_value(raw)
    if digits is None:
        if is_full_aadhaar(raw):
            raise ValidationFailed([aadhaar_error(field_name)], detail=AADHAAR_DETAIL)
        if not clean_text(raw):
            raise ValidationFailed([error(field_name, "missing")])
        raise ValidationFailed([error(field_name, DIGITS12_CODE)])
    return CleanValue(plain=digits, text=digits)


def _date_value(
    value: str, rules: Mapping[str, Any], field_name: str, today: dt.date
) -> CleanValue:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        raise ValidationFailed([error(field_name, "invalid_date")]) from None
    if len(value) != 10:
        raise ValidationFailed([error(field_name, "invalid_date")])
    if parsed < MIN_DATE:
        raise ValidationFailed([error(field_name, "date_too_early")])
    if rules.get("not_future") and parsed > today:
        raise ValidationFailed([error(field_name, "date_in_future")])
    return CleanValue(plain=parsed.isoformat(), date=parsed)


# --- Aadhaar display (PRV-014) ---------------------------------------------------------------


def aadhaar_display(last4: str) -> str:
    """``XXXX XXXX 1234`` (the only way an Aadhaar reference is ever shown)."""
    return f"XXXX XXXX {last4}"


MASK: Final = "••••"


# --- guardian phone ----------------------------------------------------------------------------

_PHONE_DIGITS_RE: Final = re.compile(r"(?:\+?91|0)?([6-9][0-9]{9})")


def normalize_phone(raw: str, *, field_name: str = "phone") -> str:
    """An Indian mobile number as 10 digits (``+91``/``0`` prefixes and separators removed)."""
    if is_full_aadhaar(raw):
        raise ValidationFailed([aadhaar_error(field_name)], detail=AADHAAR_DETAIL)
    compact = re.sub(r"[\s().-]", "", unicodedata.normalize("NFC", raw))
    match = _PHONE_DIGITS_RE.fullmatch(compact)
    if match is None:
        raise ValidationFailed([error(field_name, "invalid_phone")])
    return match.group(1)


def phone_display(phone: str) -> str:
    return f"+91 {phone[:5]} {phone[5:]}"
