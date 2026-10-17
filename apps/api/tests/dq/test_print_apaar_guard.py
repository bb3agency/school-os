"""SEC-047 guard (ADR-0037, PRV-020, invariant 4): the HTML/PDF print templates (parent
verification slips, APAAR consent forms) pass every value through the Aadhaar mask, which
would also mask a typed APAAR ID that happens to pass the Verhoeff check (about one in ten).
Neither page prints the APAAR ID today. These tests fail the day one starts to, so the change
must carry the typed value past the mask the way the spreadsheet exports do
(``app.exports.tables.TypedDigits12``, covered by ``tests/exports/test_apaar_export.py``)
instead of silently printing a masked APAAR ID. Pure tests, no database."""

from __future__ import annotations

from app.apaar import service as apaar_service
from app.apaar import templates as apaar_templates
from app.core.redaction import verhoeff_check_digit
from app.dq import slip
from app.dq.profiles import load_profiles

APAAR_KEY = "apaar_id"
GUARD = (
    "SEC-047: this print would show the APAAR ID through the Aadhaar mask; print the typed "
    "apaar_id value unmasked (as app.exports.tables.TypedDigits12 does) and update this guard"
)


def _verhoeff_valid_twelve() -> str:
    head = "29876543210"  # synthetic
    return head + verhoeff_check_digit(head)


def test_SEC_047_the_print_mask_would_hide_a_verhoeff_valid_apaar_id() -> None:
    """Why the guard exists: both templates' escaping masks a Verhoeff-valid 12-digit value."""
    value = _verhoeff_valid_twelve()
    assert value not in apaar_templates._e(value)
    assert value not in slip._e(value)


def test_SEC_047_no_readiness_slip_prints_the_apaar_id() -> None:
    """A readiness profile's compared fields become slip rows: none may be the APAAR ID."""
    for key, profile in load_profiles().items():
        if profile.readiness is None:
            continue
        attributes = {field.attribute for field in profile.readiness.fields}
        assert APAAR_KEY not in attributes, f"{key}: {GUARD}"


def test_SEC_047_the_consent_form_does_not_print_the_apaar_id() -> None:
    """The consent form prints only these attributes (name, admission number, dob, PEN)."""
    assert APAAR_KEY not in apaar_service._FORM_KEYS, GUARD
    assert "apaar" not in " ".join(apaar_templates.FormStudent.__dataclass_fields__), GUARD
