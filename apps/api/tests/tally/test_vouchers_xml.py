"""The Tally XML import file and the ledger-name rules (FM-03; FR-TALLY-012, FR-TALLY-018,
FR-TALLY-019). Pure: no database."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from xml.dom import minidom

import pytest
from pydantic import ValidationError

from app.core.redaction import verhoeff_check_digit
from app.tally.vouchers_schemas import VoucherSettingsIn
from app.tally.vouchers_xml import Line, UnbalancedVoucher, Voucher, render, xml_text

DAY = dt.date(2026, 11, 5)


def _receipt(party: str = "Fee receivable", narration: str = "Receipt") -> Voucher:
    amount = Decimal("1500.00")
    return Voucher(
        "Receipt",
        DAY,
        "R/2026-27/000001",
        party,
        narration,
        (Line("Cash", amount), Line(party, -amount)),
    )


def test_FR_TALLY_018_envelope_and_signs() -> None:
    body = render([_receipt()], company="Synthetic Model School 2026-27")
    assert body.startswith(b"<?xml version='1.0' encoding='utf-8'?>")
    doc = minidom.parseString(body)  # noqa: S318 - our own output, parsed only to assert on it
    assert doc.getElementsByTagName("TALLYREQUEST")[0].firstChild.data == "Import Data"  # type: ignore[union-attr]
    assert doc.getElementsByTagName("SVCURRENTCOMPANY")[0].firstChild.data.startswith("Synthetic")  # type: ignore[union-attr]
    voucher = doc.getElementsByTagName("VOUCHER")[0]
    assert voucher.getAttribute("VCHTYPE") == "Receipt"
    assert voucher.getAttribute("ACTION") == "Create"
    assert voucher.getElementsByTagName("DATE")[0].firstChild.data == "20261105"  # type: ignore[union-attr]
    entries = voucher.getElementsByTagName("ALLLEDGERENTRIES.LIST")
    signs = [
        (
            e.getElementsByTagName("LEDGERNAME")[0].firstChild.data,  # type: ignore[union-attr]
            e.getElementsByTagName("ISDEEMEDPOSITIVE")[0].firstChild.data,  # type: ignore[union-attr]
            e.getElementsByTagName("AMOUNT")[0].firstChild.data,  # type: ignore[union-attr]
        )
        for e in entries
    ]
    # Tally's convention: a debit is "deemed positive" with a negative amount.
    assert signs == [("Cash", "Yes", "-1500.00"), ("Fee receivable", "No", "1500.00")]


def test_FR_TALLY_019_names_are_escaped_and_xml_safe() -> None:
    party = 'Rao & Sons <"Family"> 9A'
    body = render([_receipt(party, "Fee \x00receipt\x1b of <b>Rao</b> & co")], company=None)
    text = body.decode("utf-8")
    assert "<!DOCTYPE" not in text
    assert "<!ENTITY" not in text
    assert "<b>" not in text
    assert "&amp;" in text
    assert "&lt;" in text
    assert "\x00" not in text
    assert "\x1b" not in text
    doc = minidom.parseString(body)  # noqa: S318 - our own output
    names = [n.firstChild.data for n in doc.getElementsByTagName("LEDGERNAME")]  # type: ignore[union-attr]
    assert names == ["Cash", party]
    assert not doc.getElementsByTagName("SVCURRENTCOMPANY")


def test_unbalanced_voucher_is_refused() -> None:
    bad = Voucher(
        "Receipt",
        DAY,
        "X",
        None,
        "n",
        (Line("Cash", Decimal("10.00")), Line("P", Decimal("-9.00"))),
    )
    with pytest.raises(UnbalancedVoucher):
        render([bad], company=None)


def test_xml_text_drops_only_invalid_characters() -> None:
    assert xml_text("A\tB\nC\x07D" + chr(0xFFFE)) == "A\tB\nCD"
    assert xml_text("తెలుగు") == "తెలుగు"


def _settings(**kw: object) -> VoucherSettingsIn:
    base: dict[str, object] = {"party_mode": "student", "receivable_ledger": "Fee receivable"}
    base.update(kw)
    return VoucherSettingsIn.model_validate(base)


def test_FR_TALLY_012_ledger_names_are_refused_not_cleaned() -> None:
    assert _settings(receivable_ledger="  Fee receivable ").receivable_ledger == "Fee receivable"
    body = "73920184556"
    aadhaar = body + verhoeff_check_digit(body)
    for bad in ("", "   ", "Cash" + chr(0x202E), "Cash\x07", f"Fee {aadhaar}", "x" * 101):
        with pytest.raises(ValidationError):
            _settings(receivable_ledger=bad)
    with pytest.raises(ValidationError):
        _settings(mode_ledgers={"cash": "Cash", "barter": "Goats"})
    with pytest.raises(ValidationError):
        _settings(party_mode="per_family")
