"""Pydantic IO models and summary validation for audit events (FR-AUD-001, Invariant 5).

An audit ``summary`` carries IDs, field names, counts and codes only, never personal values.
Validation is strict and fails closed: an audited operation with a bad summary raises, so the
mistake is caught in tests rather than leaking personal data into an append-only table.
"""

from __future__ import annotations

import importlib
import re
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ACTION_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
RESOURCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
LONG_DIGIT_RUN_RE = re.compile(r"\d{10,}")
# UUIDs embedded in a value (object keys ``t/<tenant>/docs/<doc>/...``): about 2-3% of random
# UUIDs have a last group of 10+ decimal digits, which is an identifier, not a phone or Aadhaar
# number. They are blanked before the digit-run check; any other run is still rejected.
EMBEDDED_UUID_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    r"(?![0-9A-Fa-f])"
)
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

MAX_STRING_LEN = 200
MAX_LIST_LEN = 100
MAX_KEYS = 50
MAX_DEPTH = 4
# Integers with 10+ digits look like phone/ID numbers; audit counts never get that large.
MAX_ABS_INT = 10**9 - 1

# Keys that name personal values (docs/08). Checked at every nesting level.
DENYLIST_KEYS = frozenset(
    {
        "name",
        "full_name",
        "dob",
        "date_of_birth",
        "phone",
        "mobile",
        "email",
        "address",
        "aadhaar",
        "aadhaar_number",
        "question",
        "answer",
        "prompt",
    }
)
# Tokens that make any key personal (e.g. ``guardian_phone``, ``student_name``). Use ``field``
# (not ``field_name``) to name the attribute that changed.
DENYLIST_TOKENS = frozenset(
    {"name", "surname", "dob", "phone", "mobile", "email", "aadhaar", "address"}
)

ZERO_HASH = bytes(32)

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

ActorType = Literal["user", "system", "platform"]
PlatformActorType = Literal["operator", "system"]


class SummaryError(ValueError):
    """The summary contains something that must not be audited (personal data, bad shape)."""


def _load_redactor() -> Callable[[str], Any] | None:
    """Return ``app.core.redaction.redact`` when that module exists (imported lazily)."""
    try:
        module = importlib.import_module("app.core.redaction")
    except ModuleNotFoundError:
        return None
    fn = getattr(module, "redact", None)
    return fn if callable(fn) else None


def _clean_string(value: str, path: str) -> str:
    value = unicodedata.normalize("NFC", value)
    if len(value) > MAX_STRING_LEN:
        raise SummaryError(f"{path}: string longer than {MAX_STRING_LEN} characters")
    if CONTROL_CHARS_RE.search(value):
        raise SummaryError(f"{path}: control characters are not allowed")
    if UUID_RE.match(value):
        # IDs pass unchanged (normalised to lowercase); never run redaction over them.
        return value.lower()
    # Reject (never silently mask) long digit runs: they look like Aadhaar/phone numbers and
    # personal values do not belong in audit summaries at all. Checked on the ORIGINAL value.
    if LONG_DIGIT_RUN_RE.search(EMBEDDED_UUID_RE.sub("-", value)):
        raise SummaryError(f"{path}: long digit sequences are not allowed in audit summaries")
    redactor = _load_redactor()
    if redactor is not None:
        redacted = redactor(value)
        if not isinstance(redacted, str):
            raise SummaryError(f"{path}: redaction returned a non-string value")
        if redacted != value:
            raise SummaryError(f"{path}: value contains personal data (phone, email or Aadhaar)")
    return value


def _check_key(key: object, path: str) -> str:
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise SummaryError(f"{path}: key must match {KEY_RE.pattern}")
    if key in DENYLIST_KEYS or DENYLIST_TOKENS.intersection(key.split("_")):
        raise SummaryError(f"{path}.{key}: key names personal data; audit IDs/fields instead")
    return key


def _clean(value: object, path: str, depth: int) -> JsonValue:
    if depth > MAX_DEPTH:
        raise SummaryError(f"{path}: nested deeper than {MAX_DEPTH}")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_ABS_INT:
            raise SummaryError(f"{path}: integer out of range for an audit summary")
        return value
    if isinstance(value, float):
        raise SummaryError(f"{path}: floats are not allowed (use integers or strings)")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        return _clean_string(value, path)
    if isinstance(value, list | tuple):
        if len(value) > MAX_LIST_LEN:
            raise SummaryError(f"{path}: more than {MAX_LIST_LEN} items")
        return [_clean(v, f"{path}[{i}]", depth + 1) for i, v in enumerate(value)]
    if isinstance(value, Mapping):
        return _clean_mapping(value, path, depth + 1)
    raise SummaryError(f"{path}: unsupported type {type(value).__name__}")


def _clean_mapping(value: Mapping[Any, Any], path: str, depth: int) -> dict[str, JsonValue]:
    if len(value) > MAX_KEYS:
        raise SummaryError(f"{path}: more than {MAX_KEYS} keys")
    return {_check_key(k, path): _clean(v, f"{path}.{k}", depth) for k, v in value.items()}


def sanitize_summary(summary: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Validate and normalise an audit summary; raise ``SummaryError`` if it is not allowed."""
    if not isinstance(summary, Mapping):
        raise SummaryError("summary must be a JSON object")
    return _clean_mapping(summary, "summary", 1)


class _EventInputBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str
    resource_type: str
    resource_id: uuid.UUID | None = None
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    actor_id: uuid.UUID | None = None
    request_id: str | None = None
    ip_hash: bytes | None = Field(default=None, max_length=64)

    @field_validator("action")
    @classmethod
    def _action(cls, v: str) -> str:
        if not ACTION_RE.match(v) or len(v) > 100:
            raise ValueError(f"action must match {ACTION_RE.pattern}")
        return v

    @field_validator("resource_type")
    @classmethod
    def _resource_type(cls, v: str) -> str:
        if not RESOURCE_TYPE_RE.match(v):
            raise ValueError(f"resource_type must match {RESOURCE_TYPE_RE.pattern}")
        return v

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, v: str | None) -> str | None:
        if v is not None and not REQUEST_ID_RE.match(v):
            raise ValueError("request_id must be 1-128 characters of [A-Za-z0-9._:-]")
        return v

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, v: object) -> dict[str, JsonValue]:
        if not isinstance(v, Mapping):
            raise ValueError("summary must be a JSON object")
        return sanitize_summary(v)


class AuditEventInput(_EventInputBase):
    """Validated input for ``service.record``."""

    actor_type: ActorType = "user"


class PlatformAuditEventInput(_EventInputBase):
    """Validated input for ``service.record_platform``."""

    actor_type: PlatformActorType = "operator"
    subject_tenant_id: uuid.UUID | None = None


class AuditEvent(BaseModel):
    """A stored tenant audit event."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    seq: int
    occurred_at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: uuid.UUID | None
    summary: dict[str, JsonValue]
    request_id: str | None
    ip_hash: bytes | None
    prev_hash: bytes
    hash: bytes


class PlatformAuditEvent(BaseModel):
    """A stored control-plane audit event."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    seq: int
    occurred_at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: uuid.UUID | None
    subject_tenant_id: uuid.UUID | None
    summary: dict[str, JsonValue]
    request_id: str | None
    ip_hash: bytes | None
    prev_hash: bytes
    hash: bytes


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of walking a chain. ``first_bad_seq`` is the earliest sequence found broken."""

    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str | None = None
