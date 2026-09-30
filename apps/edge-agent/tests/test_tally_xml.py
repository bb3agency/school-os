"""Tally XML: export-only requests, XXE-safe parsing, Tally's quirks, amounts (ADR-0032 §1;
FR-TALLY-003). Synthetic fixtures only."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from defusedxml import DefusedXmlException  # type: ignore[import-untyped]

from sos_edge_agent import tally_xml as tx

from .conftest import fixture

TODAY = dt.date(2026, 9, 28)


@pytest.mark.parametrize(
    "request_body",
    [
        tx.companies_request(),
        tx.groups_request("Synthetic Model School 2026-27"),
        tx.ledgers_request("Synthetic Model School 2026-27", "Sundry Debtors", TODAY),
    ],
)
def test_FR_TALLY_003_every_request_is_a_single_export(request_body: bytes) -> None:
    tx.assert_export_only(request_body)
    assert b"<TALLYREQUEST>Export</TALLYREQUEST>" in request_body
    assert b"Import" not in request_body


@pytest.mark.parametrize(
    "body",
    [
        b"<ENVELOPE><HEADER><TALLYREQUEST>Import</TALLYREQUEST></HEADER></ENVELOPE>",
        b"<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST>"
        b"<TALLYREQUEST>Import</TALLYREQUEST></HEADER></ENVELOPE>",
        b"<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER>"
        b"<BODY><IMPORTDATA/></BODY></ENVELOPE>",
        b"<OTHER><TALLYREQUEST>Export</TALLYREQUEST></OTHER>",
    ],
)
def test_FR_TALLY_003_anything_that_could_write_to_tally_is_refused(body: bytes) -> None:
    with pytest.raises(tx.TallyXmlError):
        tx.assert_export_only(body)


def test_FR_TALLY_003_names_are_escaped_not_interpreted() -> None:
    body = tx.ledgers_request(
        "Synthetic <School> & Co", "</CHILDOF><TALLYREQUEST>Import</TALLYREQUEST>", TODAY
    )
    tx.assert_export_only(body)
    assert b"&lt;School&gt; &amp; Co" in body
    assert b'<SVTODATE TYPE="Date">28-Sep-2026</SVTODATE>' in body
    assert tx.tally_date(dt.date(2027, 1, 5)) == "5-Jan-2027"


def test_FR_TALLY_003_companies_groups_and_ledgers_are_read() -> None:
    assert tx.parse_companies(fixture("companies.xml")) == [
        "Synthetic Model School 2026-27",
        "Synthetic Model School 2025-26",
    ]
    groups = tx.parse_groups(fixture("groups.xml"))
    assert [(g.name, g.parent) for g in groups] == [
        ("Current Assets", None),
        ("Sundry Debtors", "Current Assets"),
        ("Class IX Fees", "Sundry Debtors"),
        ("Staff Advances", "Loans & Advances (Asset)"),
        ("Indirect Expenses", None),  # Tally's &#4; removed
    ]
    ledgers = tx.parse_ledgers(fixture("ledgers_sundry_debtors.xml"))
    assert [(x.name, x.parent, x.owed) for x in ledgers] == [
        ("Synthetica Venkata Sai 9A", "Sundry Debtors", Decimal("15000.00")),
        ("Synthetica Advance Paid", "Sundry Debtors", Decimal("-2000.00")),
        ("Synthetica Lakshmi Devi 9B", "Class IX Fees", Decimal("4500.25")),
        ("Synthetica No Guid Ledger", "Sundry Debtors", Decimal("0.00")),
    ]
    assert ledgers[0].guid == "a1b2c3d4-0000-4000-8000-000000000001-00000101"
    assert ledgers[3].guid is None


@pytest.mark.parametrize(
    ("text", "owed"),
    [
        ("-15000.00", "15000.00"),  # XML: a debit is negative -> the party owes the school
        ("2000.00", "-2000.00"),  # a credit -> an advance
        ("4500.25 Dr", "4500.25"),
        ("4500.25 Cr", "-4500.25"),
        ("₹ -1,50,000.50", "150000.50"),
        ("", "0.00"),
        (None, "0.00"),
    ],
)
def test_FR_TALLY_003_amounts_are_decimals_owed_to_the_school(text: str | None, owed: str) -> None:
    assert tx.parse_amount(text) == Decimal(owed)


# An amount with more digits than a Decimal context holds (28) cannot be quantised: refused
# like any other bad amount, not an ArithmeticError the sync loop does not catch.
@pytest.mark.parametrize("text", ["abc", "1.2.3", "12 Dr Cr", "-" + "9" * 40, "9" * 30 + ".50 Dr"])
def test_FR_TALLY_003_bad_amounts_are_refused(text: str) -> None:
    with pytest.raises(tx.TallyXmlError):
        tx.parse_amount(text)


def test_FR_TALLY_003_utf16_answers_are_decoded() -> None:
    raw = fixture("companies.xml").decode("utf-8").encode("utf-16")
    assert tx.parse_companies(raw)[0] == "Synthetic Model School 2026-27"
    no_bom = fixture("companies.xml").decode("utf-8").encode("utf-16-le")
    assert tx.parse_companies(no_bom)[0] == "Synthetic Model School 2026-27"


@pytest.mark.parametrize(
    "raw",
    [
        # External entity (XXE): must never be resolved.
        b'<?xml version="1.0"?><!DOCTYPE e [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<ENVELOPE><LEDGER NAME='&x;'/></ENVELOPE>",
        # Entity expansion ("billion laughs").
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        b"<ENVELOPE><LEDGER NAME='&b;'/></ENVELOPE>",
    ],
)
def test_FR_TALLY_003_parsing_is_xxe_safe(raw: bytes) -> None:
    with pytest.raises((DefusedXmlException, tx.TallyXmlError)):
        tx.parse_ledgers(raw)


def test_FR_TALLY_003_tally_errors_and_bad_xml_are_reported() -> None:
    with pytest.raises(tx.TallyXmlError, match="reported an error"):
        tx.parse_groups(fixture("line_error.xml"))
    with pytest.raises(tx.TallyXmlError, match="well-formed"):
        tx.parse_groups(b"<ENVELOPE><BODY>")
