"""Value validation and Aadhaar rejection (FR-STU-006, FR-STU-012, SEC-013, US-303 AC1).

Pure unit tests. Numbers passing Verhoeff are built inside the test process (docs/12 §3).
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from app.core.errors import ValidationFailed
from app.core.redaction import verhoeff_check_digit
from app.devtools.fake_ids import invalid_aadhaar_like
from app.students.definitions import (
    AADHAAR_CODE,
    AADHAAR_MESSAGE_KEY,
    AttributeDef,
    CanonicalPolicy,
    aadhaar_display,
    find_full_aadhaar,
    is_full_aadhaar,
    normalize_phone,
    reject_full_aadhaar,
    validate_value,
)

POLICY = CanonicalPolicy(("admission_register",), True, "admission_register", ())


def valid_aadhaar(seed: int = 7) -> str:
    rng = random.Random(seed)
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    return body + verhoeff_check_digit(body)


def definition(
    key: str, data_type: str, classification: str = "C2", **validation: object
) -> AttributeDef:
    return AttributeDef(
        key=key,
        data_type=data_type,  # type: ignore[arg-type]
        classification=classification,  # type: ignore[arg-type]
        is_identity=False,
        policy=POLICY,
        label_en=key,
        label_te=key,
        validation=validation,
    )


NAME = definition("full_name", "text", max_length=120, name=True)
DOB = definition("dob", "date", not_future=True)
GENDER = definition("gender", "enum", values=["female", "male", "transgender"])
LAST4 = definition(
    "aadhaar_last4", "digits4", "C3", pattern="^[0-9]{4}$", sources=["aadhaar_as_printed"]
)
TODAY = dt.date(2026, 9, 26)


def _codes(exc: pytest.ExceptionInfo[ValidationFailed]) -> list[str]:
    return [e["code"] for e in exc.value.errors]


@pytest.mark.parametrize(
    "text",
    [
        "{n}",
        "{g}",  # grouped 4-4-4
        "Aadhaar: {g}",
        "{d}",  # dashed
        "name {n} end",
    ],
)
def test_FR_STU_012_full_aadhaar_is_rejected_with_last4_message(text: str) -> None:
    n = valid_aadhaar()
    formatted = text.format(n=n, g=f"{n[:4]} {n[4:8]} {n[8:]}", d=f"{n[:4]}-{n[4:8]}-{n[8:]}")
    with pytest.raises(ValidationFailed) as exc:
        validate_value(NAME, "admission_register", formatted, today=TODAY)
    assert exc.value.errors == [
        {"field": "value", "code": AADHAAR_CODE, "message_key": AADHAAR_MESSAGE_KEY}
    ]
    assert formatted not in str(exc.value.detail)


@pytest.mark.parametrize(
    "gap", ["\t", "\n", "   ", "      ", "\u2003", "\u202f", "\u200b", ".", "\u2212", ","]
)
def test_FR_STU_012_full_aadhaar_with_any_gap_is_rejected_not_stored(gap: str) -> None:
    """SEC-013 / invariant 4 (audit 2026-10-04, DL-01): the check ran on the raw text and the
    value was then stored with its whitespace collapsed, so ``1234<TAB>5678<TAB>9012`` was
    stored as ``1234 5678 9012``: a full Aadhaar number in sis.attribute_values."""
    n = valid_aadhaar()
    raw = gap.join([n[:4], n[4:8], n[8:]])
    for target in (NAME, LAST4):
        with pytest.raises(ValidationFailed) as exc:
            validate_value(target, "aadhaar_as_printed", raw, today=TODAY)
        assert _codes(exc) == [AADHAAR_CODE]
    assert is_full_aadhaar(raw)
    assert find_full_aadhaar({"notes": [raw]}) == ["notes.0"]
    with pytest.raises(ValidationFailed):
        normalize_phone(raw)


def test_FR_STU_012_aadhaar_last4_accepts_exactly_four_digits() -> None:
    assert validate_value(LAST4, "aadhaar_as_printed", " 4821 ").plain == "4821"
    for bad in ("482", "48211", "48a1", "XXXX4821"):
        with pytest.raises(ValidationFailed) as exc:
            validate_value(LAST4, "aadhaar_as_printed", bad)
        assert exc.value.errors[0]["message_key"] == AADHAAR_MESSAGE_KEY
    with pytest.raises(ValidationFailed) as exc:
        validate_value(LAST4, "aadhaar_as_printed", valid_aadhaar())
    assert _codes(exc) == [AADHAAR_CODE]
    with pytest.raises(ValidationFailed) as exc:
        validate_value(LAST4, "udise_plus", "4821")
    assert _codes(exc) == ["source_not_allowed"]


def test_PRV_014_aadhaar_display_format() -> None:
    assert aadhaar_display("4821") == "XXXX XXXX 4821"


def test_invalid_checksum_twelve_digits_is_not_an_aadhaar_number() -> None:
    fake = invalid_aadhaar_like(random.Random(3))
    assert not is_full_aadhaar(fake)


def test_explicit_indian_mobile_is_not_treated_as_aadhaar() -> None:
    # Find a +91 mobile whose 12 digits pass Verhoeff: it is still a phone number.
    for tail in range(10_000):
        digits = f"919876{tail:06d}"
        if is_full_aadhaar(digits):
            phone = f"+91 {digits[2:7]} {digits[7:]}"
            assert not is_full_aadhaar(phone)
            assert normalize_phone(phone) == digits[2:]
            return
    pytest.fail("no Verhoeff-valid phone-like number found")


def test_find_full_aadhaar_reports_every_path_including_numbers() -> None:
    n = valid_aadhaar()
    payload = {
        "values": [
            {"attribute_key": "full_name", "value": "Synthetic Name"},
            {"attribute_key": "caste", "value": f"see {n}"},
        ],
        "phone": int(n),
        "ok": True,
    }
    assert find_full_aadhaar(payload) == ["values.1.value", "phone"]
    assert find_full_aadhaar(n) == ["body"]


def test_reject_full_aadhaar_names_each_field() -> None:
    n = valid_aadhaar()
    with pytest.raises(ValidationFailed) as exc:
        reject_full_aadhaar({"full_name": "Synthetic", "phone": n, "address": f"H.No {n}"})
    assert [e["field"] for e in exc.value.errors] == ["phone", "address"]
    reject_full_aadhaar({"full_name": "Synthetic", "phone": None})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9876543210", "9876543210"),
        ("+91 98765 43210", "9876543210"),
        ("098765-43210", "9876543210"),
    ],
)
def test_phone_normalisation(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "5876543210", "98765432101", "phone"])
def test_phone_rejects_non_mobile(raw: str) -> None:
    with pytest.raises(ValidationFailed) as exc:
        normalize_phone(raw)
    assert _codes(exc) == ["invalid_phone"]


def test_name_is_nfc_trimmed_and_keyed_for_matching() -> None:
    clean = validate_value(NAME, "admission_register", "  K.  Venkata   Sai ", today=TODAY)
    assert clean.text == "K. Venkata Sai"
    assert clean.norm == "K VENKATA SAI"
    telugu = validate_value(NAME, "admission_register", "వెంకట సాయి", today=TODAY)
    assert telugu.text == "వెంకట సాయి"
    assert telugu.norm is not None
    assert telugu.norm.isascii()


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("2012-02-30", "invalid_date"),
        ("14/03/2012", "invalid_date"),
        ("1899-12-31", "date_too_early"),
        ("2026-09-27", "date_in_future"),
    ],
)
def test_dates_are_validated(raw: str, code: str) -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_value(DOB, "admission_register", raw, today=TODAY)
    assert _codes(exc) == [code]


def test_date_and_enum_values() -> None:
    assert validate_value(DOB, "admission_register", "2012-03-14", today=TODAY).date == dt.date(
        2012, 3, 14
    )
    assert validate_value(GENDER, "admission_register", "Female").plain == "female"
    with pytest.raises(ValidationFailed) as exc:
        validate_value(GENDER, "admission_register", "unknown")
    assert _codes(exc) == ["not_allowed"]


def test_text_limits() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_value(NAME, "admission_register", "A" * 121)
    assert _codes(exc) == ["too_long"]
    with pytest.raises(ValidationFailed) as exc:
        validate_value(NAME, "admission_register", "   ")
    assert _codes(exc) == ["missing"]
