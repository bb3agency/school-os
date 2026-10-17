"""Bank statement reading without a database (R2-2E; FR-FEE-016; ADR-0042 decision 9; SEC-017;
invariants 4, 5, 13). Synthetic statements per preset, credits only, UTR extraction, narration
masking (no account, mobile, card or Aadhaar-like numbers kept), refusals (formulas, printed
Aadhaar numbers, too many rows, zip bombs, unreadable rows) and the static UPI QR."""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import Workbook

from app.core.spreadsheet import SpreadsheetError, raw_rows
from app.fees.statements_parse import (
    StatementRefused,
    find_reference,
    mask_narration,
    parse_amount,
    parse_statement,
    printed_aadhaar,
)
from app.fees.upi_config import load_upi_config, normalise_heading
from app.fees.upi_qr import qr_svg, upi_uri

S: Any = __import__("sys").modules["sos_test_fees_upi_support"]

CFG = load_upi_config()
TODAY = dt.date(2026, 10, 18)
UTR_A = "629112345678"
UTR_B = "629187654321"


def _parse(data: bytes, kind: str = "csv", preset: str | None = None) -> Any:
    return parse_statement(
        raw_rows(data, kind, CFG.statements), CFG, today=TODAY, preset_key=preset
    )


def _credits() -> list[Any]:
    return [
        S.Credit(TODAY - dt.timedelta(days=2), "1500.00", UTR_A),
        S.Credit(TODAY - dt.timedelta(days=1), "2000.00", debit=True),
        S.Credit(TODAY - dt.timedelta(days=1), "750.50", UTR_B, payer="Synthetica Rao"),
        S.Credit(TODAY, "3000.00", upi=False),
    ]


# --- presets --------------------------------------------------------------------------------------


@pytest.mark.parametrize("preset", ["generic", "sbi", "hdfc", "icici", "axis", "kotak"])
@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_FR_FEE_016_each_preset_reads_credits_only(preset: str, kind: str) -> None:
    data = S.statement(preset, _credits(), kind=kind)
    out = _parse(data, kind)
    assert out.preset.key == preset, "detected without being told"
    assert out.transactions == 4
    assert [(x.value_date, x.amount, x.ref_kind, x.reference) for x in out.lines] == [
        (TODAY - dt.timedelta(days=2), Decimal("1500.00"), "utr", UTR_A),
        (TODAY - dt.timedelta(days=1), Decimal("750.50"), "utr", UTR_B),
        (TODAY, Decimal("3000.00"), "reference", "SYNBN26291123456"),
    ]
    assert out.period == (TODAY - dt.timedelta(days=2), TODAY)
    # Named explicitly, the same file reads the same.
    again = _parse(data, kind, preset)
    assert again.lines == out.lines


def test_FR_FEE_016_no_account_number_or_payer_id_is_kept() -> None:
    out = _parse(S.statement("sbi", _credits()))
    text = repr(out)
    assert S.ACCOUNT_NO not in text
    assert S.PAYER_VPA not in text
    assert "synthetica.parent" not in text
    first = out.lines[0]
    # The UTR has its own field; in the narration every long number is masked to its last 4.
    assert first.narration is not None
    assert UTR_A not in first.narration
    assert "••••5678" in first.narration
    assert "••••@oksbi" in first.narration


def test_FR_FEE_016_sbi_reference_column_with_an_account_number_is_not_kept() -> None:
    kind, ref = find_reference("NEFT CR-SYNTHETIC", f"TRANSFER FROM {S.ACCOUNT_NO}", CFG)
    assert (kind, ref) == ("none", None)
    assert find_reference("IMPS", S.ACCOUNT_NO, CFG) == ("none", None)
    assert find_reference("CHQ DEP", "123456", CFG) == ("reference", "123456")
    assert find_reference("UPI", f"0000{UTR_A}", CFG) == ("utr", UTR_A)


def test_FR_FEE_016_utr_is_found_only_in_upi_narrations() -> None:
    assert find_reference(f"UPI/CR/{UTR_A}/X", None, CFG) == ("utr", UTR_A)
    # A 12-digit number in a non-UPI narration is not taken for a UTR.
    assert find_reference(f"NEFT CR {UTR_A} X", None, CFG) == ("none", None)
    # 13 digits are not a UTR.
    assert find_reference(f"UPI/CR/{UTR_A}9/X", None, CFG) == ("none", None)


def test_FR_FEE_016_narration_masks_mobiles_emails_and_aadhaar_like_numbers() -> None:
    text = "UPI/CR/629112345678/SYNTH/9876543210/synth@example.com/2345 6789 0124/x"
    masked = mask_narration(text, CFG)
    assert masked is not None
    assert "9876543210" not in masked
    assert "synth@example.com" not in masked
    assert "629112345678" not in masked
    assert len(mask_narration("x" * 1000, CFG) or "") == CFG.statements.narration_max_length


# --- refusals -------------------------------------------------------------------------------------


def test_FR_FEE_016_formula_cells_are_refused() -> None:
    table = S.rows("generic", _credits())
    table[6][4] = '=HYPERLINK("http://example.invalid","x")'
    with pytest.raises(StatementRefused) as err:
        _parse(S.csv_bytes(table))
    assert err.value.code == "formula_cell"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in S.rows("generic", _credits()):
        ws.append(row)
    ws["E7"] = "=SUM(1,2)"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(StatementRefused) as err:
        _parse(buf.getvalue(), "xlsx")
    assert err.value.code == "formula_cell"


def test_FR_FEE_016_printed_aadhaar_numbers_are_refused() -> None:
    # 2345 6789 0124 passes the Verhoeff check (synthetic, not a real person's number).
    assert printed_aadhaar("ID 2345 6789 0124")
    table = S.rows("generic", _credits())
    table[5][1] = "UPI/CR/629112345678/SYNTH AADHAAR 2345 6789 0124/x"
    with pytest.raises(StatementRefused) as err:
        _parse(S.csv_bytes(table))
    assert err.value.code == "aadhaar_in_file"
    # An unbroken 12-digit UPI reference is never refused, even when Verhoeff-valid (P9).
    assert not printed_aadhaar("UPI/CR/234567890124/x")


def test_FR_FEE_016_more_than_5000_transactions_are_refused() -> None:
    credits = [S.Credit(TODAY, "1.00", f"{n:012d}") for n in range(5001)]
    with pytest.raises(StatementRefused) as err:
        _parse(S.statement("generic", credits))
    assert err.value.code == "too_many_lines"


def test_SEC_017_zip_bomb_is_refused_before_parsing() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"\0" * (60 * 1024 * 1024))
    with pytest.raises(SpreadsheetError):
        _parse(buf.getvalue(), "xlsx")


def test_FR_FEE_016_unreadable_rows_refuse_the_whole_file_with_row_numbers() -> None:
    table = S.rows("generic", _credits())
    table[5][0] = "31/31/2026"
    table[7][4] = "12.345"
    with pytest.raises(StatementRefused) as err:
        _parse(S.csv_bytes(table))
    assert err.value.code == "rows_unreadable"
    assert [(i.row, i.column, i.code) for i in err.value.issues] == [
        (6, "date", "bad_date"),
        (8, "amount", "bad_amount"),
    ]


def test_FR_FEE_016_old_or_future_credits_are_refused() -> None:
    credits = [S.Credit(TODAY + dt.timedelta(days=5), "10.00", UTR_A)]
    with pytest.raises(StatementRefused) as err:
        _parse(S.statement("generic", credits))
    assert err.value.issues[0].code == "date_out_of_range"


def test_FR_FEE_016_unknown_layout_and_unknown_preset() -> None:
    with pytest.raises(StatementRefused) as err:
        _parse(b"a,b,c\n1,2,3\n")
    assert err.value.code == "no_header"
    with pytest.raises(StatementRefused) as err:
        _parse(S.statement("generic", _credits()), preset="nobank")
    assert err.value.code == "unknown_preset"
    # A named preset does not read another bank's layout.
    with pytest.raises(StatementRefused) as err:
        _parse(S.statement("hdfc", _credits()), preset="axis")
    assert err.value.code == "no_header"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1,500.00", Decimal("1500.00")),
        ("₹ 1500", Decimal("1500.00")),
        ("1500.5", Decimal("1500.50")),
        ("1500.00 Cr", Decimal("1500.00")),
        (1500, Decimal("1500.00")),
        (1500.5, Decimal("1500.50")),
        ("", None),
        ("-", None),
        ("0.00", None),
        (None, None),
    ],
)
def test_FR_FEE_028_amounts_are_exact(value: object, expected: Decimal | None) -> None:
    assert parse_amount(value, allow_cr_suffix=True) == expected


@pytest.mark.parametrize("value", ["12.345", "-5", "1e3", "abc", "1500 Dr", True, 0.1 + 0.2])
def test_FR_FEE_028_odd_amounts_are_refused(value: object) -> None:
    from app.fees.statements_parse import BadValue

    with pytest.raises(BadValue):
        parse_amount(value, allow_cr_suffix=True)


# --- configuration and QR ------------------------------------------------------------------------


def test_INV_13_presets_are_valid_and_headings_normalised() -> None:
    assert CFG.presets[0].key == "generic"
    assert {p.key for p in CFG.presets} >= {"generic", "sbi", "hdfc", "icici", "axis", "kotak"}
    assert all(not p.verified for p in CFG.presets), "confirmed with a pilot school first (D3)"
    assert normalise_heading("Ref No./Cheque No.") == "ref no cheque no"
    assert normalise_heading("Deposit Amount (INR )") == "deposit amount inr"
    assert CFG.statements.max_lines == 5000
    assert CFG.statements.narration_keep_days == 90


def test_FR_FEE_015_static_qr_has_no_amount() -> None:
    uri = upi_uri("synthetic.school@sbi", "Synthetic Public School & Co")
    assert uri == "upi://pay?pa=synthetic.school@sbi&pn=Synthetic%20Public%20School%20Co&cu=INR"
    assert "am=" not in uri
    svg = qr_svg(uri, label="UPI QR code for Synthetic Public School")
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" role="img"')
    assert "<script" not in svg
    with pytest.raises(ValueError, match="UPI ID"):
        upi_uri("not a vpa", "x")
    with pytest.raises(ValueError, match="UPI payment"):
        qr_svg("https://example.invalid", label="x")
