"""APAAR ID and UDISE+ PEN attributes: the ``digits12`` type and the typed-field exemption from the
full-Aadhaar refusal (ADR-0037; FR-STU-013..015, FR-STU-012, PRV-013, PRV-020).

Pure unit tests. Numbers passing Verhoeff are built inside the test process (docs/12 §3).
"""

from __future__ import annotations

import random
from importlib import resources

import pytest
import yaml

from app.core.errors import ValidationFailed
from app.core.redaction import mask_aadhaar, verhoeff_check_digit, verhoeff_valid
from app.devtools.fake_ids import synthetic_apaar_id, synthetic_udise_pen
from app.students.definitions import (
    AADHAAR_CODE,
    AttributeDef,
    CanonicalPolicy,
    find_full_aadhaar,
    typed_digits12_keys,
    validate_value,
)

APAAR_POLICY = CanonicalPolicy(("udise_plus", "parent_form", "manual_entry"), True, None, ())


def verhoeff_number(seed: int = 11) -> str:
    rng = random.Random(seed)
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    return body + verhoeff_check_digit(body)


def apaar_def(*, key: str = "apaar_id", is_global: bool = True) -> AttributeDef:
    return AttributeDef(
        key=key,
        data_type="digits12",
        classification="C2",
        is_identity=False,
        policy=APAAR_POLICY,
        label_en="APAAR ID",
        label_te="APAAR ID",
        validation={"sources": ["udise_plus", "parent_form", "manual_entry"]},
        is_global=is_global,
    )


def catalog() -> dict[str, dict[str, object]]:
    raw = yaml.safe_load(
        resources.files("app.students").joinpath("attributes.yaml").read_text("utf-8")
    )
    attributes: dict[str, dict[str, object]] = raw["attributes"]
    return attributes


def test_FR_STU_013_apaar_id_is_a_c2_non_identity_attribute_needing_verification() -> None:
    spec = catalog()["apaar_id"]
    assert spec["data_type"] == "digits12"
    assert spec["classification"] == "C2"
    assert spec["is_identity"] is False
    policy = spec["canonical_policy"]
    assert isinstance(policy, dict)
    assert policy["precedence"] == ["udise_plus", "parent_form", "manual_entry"]
    assert policy["require_verified"] is True
    assert "anchor" not in policy
    validation = spec["validation"]
    assert isinstance(validation, dict)
    assert validation["sources"] == ["udise_plus", "parent_form", "manual_entry"]


def test_FR_STU_014_udise_pen_is_a_c2_non_identity_attribute_needing_verification() -> None:
    spec = catalog()["udise_pen"]
    assert spec["data_type"] == "text"
    assert spec["classification"] == "C2"
    assert spec["is_identity"] is False
    policy = spec["canonical_policy"]
    assert isinstance(policy, dict)
    assert policy["precedence"] == ["udise_plus", "tc_incoming", "manual_entry"]  # ADR-0039
    assert policy["require_verified"] is True
    validation = spec["validation"]
    assert isinstance(validation, dict)
    assert validation["sources"] == ["udise_plus", "tc_incoming", "manual_entry"]


def test_FR_STU_015_only_apaar_id_is_a_typed_digits12_key() -> None:
    assert typed_digits12_keys() == frozenset({"apaar_id"})


@pytest.mark.parametrize(
    "raw", ["{n}", " {n} ", "{a} {b} {c}", "{a}-{b}-{c}", "{a}{b} {c}"], ids=str
)
def test_FR_STU_015_digits12_accepts_twelve_digits_and_stores_them_plain(raw: str) -> None:
    n = synthetic_apaar_id(random.Random(3))
    text = raw.format(n=n, a=n[:4], b=n[4:8], c=n[8:])
    clean = validate_value(apaar_def(), "udise_plus", text)
    assert clean.plain == clean.text == n
    assert clean.norm is None


@pytest.mark.parametrize(
    "raw", ["12345678901", "1234567890123", "1234 5678 901A", "APAAR 123456789012", "١٢٣٤٥٦٧٨٩٠١٢"]
)
def test_FR_STU_015_digits12_refuses_anything_else(raw: str) -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_value(apaar_def(), "udise_plus", raw)
    assert [e["code"] for e in exc.value.errors] == ["digits12_required"]


def test_FR_STU_015_digits12_refuses_sources_outside_the_catalogue() -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_value(apaar_def(), "admission_register", synthetic_apaar_id(random.Random(1)))
    assert [e["code"] for e in exc.value.errors] == ["source_not_allowed"]


def test_FR_STU_015_a_verhoeff_valid_apaar_id_is_stored_as_typed() -> None:
    n = verhoeff_number()
    assert verhoeff_valid(n)
    clean = validate_value(apaar_def(), "parent_form", f"{n[:4]} {n[4:8]} {n[8:]}")
    assert clean.plain == n  # round-trips unmasked: the typed field is not free text


def test_FR_STU_012_digits12_exemption_needs_the_global_typed_key() -> None:
    """A school's own attribute (or any other key) never gets the exemption."""
    n = verhoeff_number()
    for definition in (apaar_def(is_global=False), apaar_def(key="other_id")):
        with pytest.raises(ValidationFailed) as exc:
            validate_value(definition, "udise_plus", n)
        assert [e["code"] for e in exc.value.errors] == [AADHAAR_CODE]


def test_FR_STU_012_body_guard_exempts_only_the_value_of_a_typed_apaar_entry() -> None:
    n = verhoeff_number()
    keys = typed_digits12_keys()
    payload = {
        "values": [
            {"attribute_key": "apaar_id", "source": "udise_plus", "value": n},
            {"attribute_key": "apaar_id", "source": "udise_plus", "value": f"APAAR {n}"},
            {"attribute_key": "udise_pen", "source": "udise_plus", "value": n},
            {"attribute_key": "aadhaar_last4", "source": "aadhaar_as_printed", "value": n},
            {"attribute_key": "full_name", "source": "admission_register", "value": n},
        ],
        "note": n,
    }
    assert find_full_aadhaar(payload, typed_keys=keys) == [
        "values.1.value",
        "values.2.value",
        "values.3.value",
        "values.4.value",
        "note",
    ]
    # Without the typed keys (every other guard) nothing is exempt.
    assert "values.0.value" in find_full_aadhaar(payload)
    # The attribute key itself is never exempt from the scan.
    assert find_full_aadhaar({"attribute_key": n, "value": n}, typed_keys=keys) == [
        "attribute_key",
        "value",
    ]


def test_PRV_020_free_text_mask_is_unchanged_for_apaar_values() -> None:
    """A Verhoeff-valid APAAR ID in free text is masked like any Aadhaar-like number."""
    n = verhoeff_number()
    for text in (f"APAAR ID {n}", f"APAAR: {n[:4]} {n[4:8]} {n[8:]}", n):
        masked = mask_aadhaar(text)
        assert n not in masked.replace(" ", "")
        assert f"XXXX XXXX {n[-4:]}" in masked


def test_FR_STU_015_synthetic_ids_never_look_like_aadhaar() -> None:
    rng = random.Random(42)
    seen = set()
    for _ in range(2000):
        apaar = synthetic_apaar_id(rng)
        assert len(apaar) == 12
        assert apaar.isascii()
        assert apaar.isdigit()
        assert not verhoeff_valid(apaar)
        assert mask_aadhaar(apaar) == apaar
        seen.add(apaar)
        pen = synthetic_udise_pen(rng)
        assert pen.isdigit()
        assert len(pen) == 11
    assert len(seen) > 1990
