"""Unit tests: summary validation (Invariants 4 and 5) and canonical hashing (FR-AUD-003)."""

from __future__ import annotations

import hashlib
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.audit.hashing import canonical_bytes, chain_hash, format_timestamp, tenant_event_dict
from app.audit.schemas import ZERO_HASH, AuditEventInput, SummaryError, sanitize_summary
from pydantic import ValidationError

# ---- summary validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "summary",
    [
        {"name": "x"},
        {"full_name": "x"},
        {"dob": "2015-01-01"},
        {"date_of_birth": "x"},
        {"phone": "x"},
        {"mobile": "x"},
        {"email": "x"},
        {"address": "x"},
        {"aadhaar": "x"},
        {"aadhaar_number": "x"},
        {"question": "x"},
        {"answer": "x"},
        {"prompt": "x"},
        {"guardian_phone": "x"},
        {"student_name": "x"},
        {"changes": {"old": {"mother_name": "x"}}},
        {"items": [{"email": "x"}]},
    ],
)
def test_FR_AUD_001_summary_rejects_personal_keys(summary: dict[str, object]) -> None:
    with pytest.raises(SummaryError, match="personal"):
        sanitize_summary(summary)


@pytest.mark.parametrize(
    ("summary", "message"),
    [
        ({"note": "x" * 201}, "longer than 200"),
        ({"ref": "123412341234"}, "digit"),
        ({"ref": "ph 98765 43210 / 9876543210"}, "digit"),
        ({"count": 9_876_543_210}, "out of range"),
        ({"ratio": 0.5}, "floats"),
        ({"Field": "dob"}, "key must match"),
        ({"field-name": "dob"}, "key must match"),
        ({"a": {"b": {"c": {"d": {"e": 1}}}}}, "nested"),
        ({"ids": list(range(101))}, "more than 100"),
        ({"blob": b"bytes"}, "unsupported"),
        ({"note": "line\nbreak"}, "control"),
    ],
)
def test_FR_AUD_001_summary_rejects_bad_values(summary: dict[str, object], message: str) -> None:
    with pytest.raises(SummaryError, match=message):
        sanitize_summary(summary)


def test_FR_AUD_001_summary_accepts_ids_fields_counts_codes() -> None:
    sid = uuid.UUID("12345678-1234-1234-1234-123456789012")  # digit-heavy UUID must pass
    out = sanitize_summary(
        {
            "student_id": sid,
            "document_ids": [str(sid).upper()],
            "fields": ["dob", "gender"],
            "count": 42,
            "ok": True,
            "reason_code": "DQ-005",
            "extra": None,
            "nested": {"source": "admission_register"},
        }
    )
    assert out["student_id"] == str(sid)
    assert out["document_ids"] == [str(sid)]
    assert out["nested"] == {"source": "admission_register"}


def test_FR_AUD_001_summary_strings_are_nfc_normalised() -> None:
    decomposed = "café"
    assert sanitize_summary({"code": decomposed})["code"] == "café"


def test_INV_4_summary_strings_pass_through_redaction_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = types.ModuleType("app.core.redaction")
    calls: list[str] = []

    def redact(value: str) -> str:
        calls.append(value)
        return value.replace("secret", "[redacted]")

    fake.redact = redact  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.core.redaction", fake)
    sid = str(uuid.uuid4())
    out = sanitize_summary({"code": "secret-code", "student_id": sid})
    assert out == {"code": "[redacted]-code", "student_id": sid}
    assert calls == ["secret-code"], "UUIDs must never be run through redaction"


def test_FR_AUD_001_action_and_summary_validated_by_model() -> None:
    with pytest.raises(ValidationError):
        AuditEventInput(action="student", resource_type="student", summary={})
    with pytest.raises(ValidationError):
        AuditEventInput(action="student.created", resource_type="student", summary={"dob": "x"})
    ok = AuditEventInput(action="kb.asked", resource_type="kb_query", summary={"hits": 3})
    assert ok.actor_type == "user"


# ---- canonical hashing -------------------------------------------------------------------------

EVENT_ROW = {
    "id": uuid.UUID("0192a3b4-c5d6-7e8f-9012-3456789abcde"),
    "tenant_id": uuid.UUID("00000000-0000-4000-8000-000000000001"),
    "seq": 1,
    "occurred_at": datetime(2026, 9, 26, 4, 5, 6, 123, tzinfo=UTC),
    "actor_type": "user",
    "actor_id": None,
    "action": "student.created",
    "resource_type": "student",
    "resource_id": None,
    "summary": {"fields": ["gender", "dob"], "count": 2},
    "request_id": "req-1",
    "ip_hash": b"\xab\xcd",
}

# Hand-written RFC 8785 form (keys sorted, no whitespace): pins the format for external verifiers.
EXPECTED_CANONICAL = (
    b'{"action":"student.created","actor_id":null,"actor_type":"user",'
    b'"id":"0192a3b4-c5d6-7e8f-9012-3456789abcde","ip_hash":"abcd",'
    b'"occurred_at":"2026-09-26T04:05:06.000123Z","request_id":"req-1",'
    b'"resource_id":null,"resource_type":"student","seq":1,'
    b'"summary":{"count":2,"fields":["gender","dob"]},'
    b'"tenant_id":"00000000-0000-4000-8000-000000000001"}'
)


def test_FR_AUD_003_canonical_form_is_rfc8785() -> None:
    assert canonical_bytes(tenant_event_dict(EVENT_ROW)) == EXPECTED_CANONICAL


def test_FR_AUD_003_hash_is_sha256_of_prev_and_canonical_json() -> None:
    expected = hashlib.sha256(ZERO_HASH + EXPECTED_CANONICAL).digest()
    assert chain_hash(ZERO_HASH, tenant_event_dict(EVENT_ROW)) == expected
    assert ZERO_HASH == b"\x00" * 32


def test_FR_AUD_003_timestamp_is_normalised_to_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 9, 26, 9, 35, 6, 123, tzinfo=ist)
    assert format_timestamp(local) == "2026-09-26T04:05:06.000123Z"
    with pytest.raises(ValueError, match="timezone-aware"):
        format_timestamp(datetime(2026, 9, 26))  # noqa: DTZ001


def test_FR_AUD_003_any_field_change_changes_hash() -> None:
    base = chain_hash(ZERO_HASH, tenant_event_dict(EVENT_ROW))
    for key, value in [
        ("seq", 2),
        ("action", "student.updated"),
        ("summary", {"fields": ["dob", "gender"], "count": 2}),
        ("ip_hash", None),
        ("occurred_at", EVENT_ROW["occurred_at"] + timedelta(microseconds=1)),  # type: ignore[operator]
    ]:
        assert chain_hash(ZERO_HASH, tenant_event_dict({**EVENT_ROW, key: value})) != base, key
    assert chain_hash(b"\x01" * 32, tenant_event_dict(EVENT_ROW)) != base
