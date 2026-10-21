"""Access-report formatting helpers (FR-DPDP-009, PRV-031)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.core.report_format import (
    EMPTY,
    cap_rows,
    fmt_date,
    fmt_datetime,
    fmt_money,
    fmt_text,
    humanise,
)
from app.core.subjects import MAX_SECTION_ROWS


def test_FR_DPDP_009_dates_are_ddmmyyyy_in_ist() -> None:
    assert fmt_date(dt.date(2026, 4, 1)) == "01/04/2026"
    # 20:00 UTC on 31 March is 01:30 IST on 1 April
    assert fmt_date(dt.datetime(2026, 3, 31, 20, 0, tzinfo=dt.UTC)) == "01/04/2026"
    assert fmt_datetime(dt.datetime(2026, 3, 31, 20, 0, tzinfo=dt.UTC)) == "01/04/2026 01:30"
    assert fmt_date(None) == EMPTY


def test_FR_DPDP_009_money_uses_indian_grouping() -> None:
    assert fmt_money(Decimal("1234")) == "₹1,234.00"
    assert fmt_money(Decimal("125000.5")) == "₹1,25,000.50"
    assert fmt_money(Decimal("-50")) == "-₹50.00"
    assert fmt_money(None) == EMPTY


def test_FR_DPDP_009_text_and_codes() -> None:
    assert fmt_text("  ") == EMPTY
    assert fmt_text(3) == "3"
    assert humanise("fee_receipt") == "Fee receipt"


def test_FR_DPDP_009_cap_rows_truncates_with_note() -> None:
    rows = list(range(MAX_SECTION_ROWS + 3))
    kept, note = cap_rows(rows)
    assert len(kept) == MAX_SECTION_ROWS
    assert note is not None
    assert "503" in note
    kept, note = cap_rows([1, 2])
    assert kept == [1, 2]
    assert note is None
    _, note = cap_rows(list(range(MAX_SECTION_ROWS)), total=1200)
    assert note is not None
    assert "1,200" in note
