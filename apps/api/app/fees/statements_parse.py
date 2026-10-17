"""Bank statement reading (R2-2E; FR-FEE-016; ADR-0042 decision 9). Pure: rows in, credits out; no
database, no web, no feature modules.

Input is the ``(row number, cells)`` stream of :func:`app.core.spreadsheet.raw_rows` (zip bombs,
entity expansion, CSV width bombs and formula evaluation are handled there: SEC-017). This module

- finds the heading row of a preset (``upi.yaml``; or the first preset whose headings are found);
- refuses the whole file for a formula cell, an Aadhaar number written as printed (``1234 5678
  9012``, Verhoeff-valid), more than ``max_lines`` transaction rows, or rows it cannot read
  (a date it cannot parse, an amount that is not a positive rupee amount with at most two
  decimals, a value date outside the window) - a bank statement is read completely or not at all;
- keeps **credits only** (a debit is counted and dropped): value date, amount, the UPI reference
  (UTR: a 12-digit group of a UPI narration, else a 12-digit reference column) or another bank
  reference, and the narration with every digit run of 9+ digits (account, card, mobile,
  Aadhaar-like numbers and the UTR itself) masked to its last 4 digits, the local part of UPI IDs
  and e-mail addresses masked, and the rest cut to ``narration_max_length``.

Nothing here logs. Account numbers, holder names, balances and debit lines are never returned.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

from app.core.redaction import redact, verhoeff_valid
from app.core.spreadsheet import Cell, display_text
from app.fees.upi_config import Preset, UpiConfig, normalise_heading

RefKind = Literal["utr", "reference", "none"]
MAX_ISSUES: Final = 20
_TWO_PLACES: Final = Decimal("0.01")
_PRINTED_AADHAAR: Final = re.compile(
    r"(?<![0-9])([0-9]{4})[ \u00a0-]([0-9]{4})[ \u00a0-]([0-9]{4})(?![0-9])"
)
_AMOUNT_CLEAN: Final = re.compile(r"(?i)(₹|rs\.?|inr|\s|,)")
_AMOUNT_RE: Final = re.compile(r"^([0-9]+)(\.[0-9]{1,2})?$")
_CR_SUFFIX: Final = re.compile(r"(?i)\s*\(?\s*cr\.?\s*\)?\s*$")
_DR_SUFFIX: Final = re.compile(r"(?i)\s*\(?\s*dr\.?\s*\)?\s*$")
_VPA_RE: Final = re.compile(r"[A-Za-z0-9._\-]+@[A-Za-z][A-Za-z0-9.\-]*")
_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f]")
_EMPTY_AMOUNTS: Final = frozenset({"", "-", "--", "0", "0.0", "0.00", "nil"})
MASK: Final = "•"
_LONG_NUMBER: Final = 9  # all-digit references this long are account numbers, never kept


class StatementRefused(Exception):
    """The file is not read (``code`` shown to the user; ``issues`` name rows and columns, never
    values)."""

    def __init__(self, code: str, issues: Sequence[Issue] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class Issue:
    row: int
    column: str
    code: str


@dataclass(frozen=True, slots=True)
class ParsedLine:
    line_no: int  # the row number in the file
    value_date: dt.date
    amount: Decimal
    ref_kind: RefKind
    reference: str | None
    narration: str | None  # masked (see the module docstring)


@dataclass(frozen=True, slots=True)
class ParsedStatement:
    preset: Preset
    transactions: int  # rows with a date and an amount (credits and debits)
    lines: tuple[ParsedLine, ...]  # credits only

    @property
    def period(self) -> tuple[dt.date, dt.date] | None:
        if not self.lines:
            return None
        dates = [line.value_date for line in self.lines]
        return min(dates), max(dates)


@dataclass(slots=True)
class _Columns:
    date: int
    narration: int
    reference: int | None = None
    credit: int | None = None
    debit: int | None = None
    amount: int | None = None
    kind: int | None = None
    names: dict[int, str] = field(default_factory=dict)


# --- headings -------------------------------------------------------------------------------------


def _find(headings: dict[str, int], aliases: Iterable[str]) -> int | None:
    for alias in aliases:
        if alias in headings:
            return headings[alias]
    return None


def _columns(row: Sequence[Cell], preset: Preset) -> _Columns | None:
    headings: dict[str, int] = {}
    for index, cell in enumerate(row):
        text = display_text(cell.value) if not cell.formula else None
        if text:
            headings.setdefault(normalise_heading(text), index)
    date = _find(headings, preset.date_columns)
    narration = _find(headings, preset.narration_columns)
    if date is None or narration is None:
        return None
    cols = _Columns(date=date, narration=narration)
    cols.reference = _find(headings, preset.reference_columns)
    if preset.typed_amount:
        cols.amount = _find(headings, preset.amount_columns)
        cols.kind = _find(headings, preset.type_columns)
        if cols.amount is None or cols.kind is None:
            return None
    else:
        cols.credit = _find(headings, preset.credit_columns)
        cols.debit = _find(headings, preset.debit_columns)
        if cols.credit is None:
            return None
    cols.names = {
        index: name
        for name, index in (
            ("date", cols.date),
            ("narration", cols.narration),
            ("reference", cols.reference),
            ("credit", cols.credit),
            ("debit", cols.debit),
            ("amount", cols.amount),
            ("type", cols.kind),
        )
        if index is not None
    }
    return cols


# --- cells ----------------------------------------------------------------------------------------


def _cell(row: Sequence[Cell], index: int | None) -> Cell | None:
    if index is None or index >= len(row):
        return None
    return row[index]


def _text(row: Sequence[Cell], index: int | None) -> str | None:
    cell = _cell(row, index)
    if cell is None:
        return None
    value = display_text(cell.value)
    return value.strip() if value else None


class BadValue(Exception):
    """A cell that should hold a date or an amount but does not (``code``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _try_formats(text: str, formats: Sequence[str]) -> dt.date | None:
    for fmt in formats:
        try:
            return dt.datetime.strptime(text, fmt).date()  # noqa: DTZ007 - a calendar date
        except ValueError:
            continue
    return None


def _parse_date(cell: Cell | None, formats: Sequence[str]) -> dt.date | None:
    """``None`` for a row that is not a transaction (empty or wordy date cell: opening
    balance, totals, page headings); :class:`BadValue` for something that looks like a date but
    is not one."""
    value = cell.value if cell is not None else None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, bool | int | float):
        raise BadValue("bad_date")  # serial numbers are not read (formats would be guessed)
    text = " ".join(str(value or "").split())
    if not any(ch.isdigit() for ch in text):
        return None
    # "18/10/2026 10:15:00": banks sometimes print the time too.
    found = _try_formats(text, formats) or _try_formats(text.split(" ")[0], formats)
    if found is None:
        raise BadValue("bad_date")
    return found


def _amount_text(text: str, *, allow_cr_suffix: bool) -> Decimal | None:
    text = text.strip()
    if allow_cr_suffix:
        if _DR_SUFFIX.search(text):
            raise BadValue("bad_amount")
        text = _CR_SUFFIX.sub("", text)
    text = _AMOUNT_CLEAN.sub("", text)
    if text.casefold() in _EMPTY_AMOUNTS:
        return None
    if not _AMOUNT_RE.match(text):
        raise BadValue("bad_amount")
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - the pattern allows digits only
        raise BadValue("bad_amount") from exc


def parse_amount(value: object, *, allow_cr_suffix: bool = False) -> Decimal | None:
    """A rupee amount: ``None`` when empty (or zero); :class:`BadValue` unless positive with at
    most two decimals. Accepts ``1,500.00``, ``₹ 1500``, ``1500.5`` and (credit columns)
    ``1500 Cr``."""
    number: Decimal | None
    if value is None:
        return None
    if isinstance(value, bool):
        raise BadValue("bad_amount")
    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(repr(value))
    elif isinstance(value, Decimal):
        number = value
    else:
        number = _amount_text(str(value), allow_cr_suffix=allow_cr_suffix)
    if number is None or number == 0:
        return None
    if number < 0 or number != number.quantize(_TWO_PLACES) or number >= Decimal("1000000000000"):
        raise BadValue("bad_amount")
    return number.quantize(_TWO_PLACES)


# --- references and narration ---------------------------------------------------------------------


def find_reference(
    narration: str | None, reference: str | None, cfg: UpiConfig
) -> tuple[RefKind, str | None]:
    """The UTR of a UPI credit (its narration's 12-digit group, else a 12-digit reference
    column), else another bank reference (6-35 letters and digits), else nothing."""
    rules = cfg.utr
    if narration and re.search(rules.upi_marker, narration):
        found = re.search(rules.narration_group, narration)
        if found:
            return "utr", found.group(1)
    value = (reference or "").strip()
    if value:
        found = re.match(rules.reference, value)
        if found:
            return "utr", found.group(1)
        # Another reference is kept only as one token of letters and digits that is not a long
        # number (an account number in the reference column, "TRANSFER FROM 5010...", is not).
        long_number = value.isdigit() and len(value) >= _LONG_NUMBER
        if re.match(rules.other_reference, value) and not long_number:
            return "reference", value
    return "none", None


def _mask_digits(match: re.Match[str]) -> str:
    digits = match.group(0)
    return MASK * 4 + digits[-4:]


def _mask_vpa(match: re.Match[str]) -> str:
    _, _, domain = match.group(0).partition("@")
    return f"{MASK * 4}@{domain}"


def mask_narration(text: str | None, cfg: UpiConfig) -> str | None:
    """The narration as stored: NFC, no control characters, single spaces; UPI IDs and e-mail
    addresses masked to their domain; digit runs of ``narration_mask_digits_from`` or more masked
    to their last 4 digits; then :func:`app.core.redaction.redact` (Aadhaar-like numbers in any
    layout, mobile numbers); cut to ``narration_max_length``."""
    if not text:
        return None
    spec = cfg.statements
    clean = _CONTROL_RE.sub(" ", unicodedata.normalize("NFC", text))
    clean = " ".join(clean.split())
    if not clean:
        return None
    clean = _VPA_RE.sub(_mask_vpa, clean)
    clean = re.sub(rf"[0-9]{{{spec.narration_mask_digits_from},}}", _mask_digits, clean)
    clean = redact(clean)
    return clean[: spec.narration_max_length]


def printed_aadhaar(text: str) -> bool:
    """An Aadhaar number written as printed on the card (three groups of four, Verhoeff-valid).
    Unbroken 12-digit numbers in a statement are UPI references (ADR-0042 P9) and are masked in
    the narration instead of refusing the file."""
    return any(verhoeff_valid("".join(m.groups())) for m in _PRINTED_AADHAAR.finditer(text))


# --- the file -------------------------------------------------------------------------------------


def _header(
    rows: list[tuple[int, list[Cell]]], presets: Sequence[Preset], limit: int
) -> tuple[Preset, int, _Columns]:
    for preset in presets:
        for position, (_, cells) in enumerate(rows[:limit]):
            cols = _columns(cells, preset)
            if cols is not None:
                return preset, position, cols
    raise StatementRefused("no_header")


def _refuse_hostile(rows: Sequence[tuple[int, list[Cell]]]) -> None:
    for row_no, cells in rows:
        for cell in cells:
            if cell.formula:
                raise StatementRefused("formula_cell", [Issue(row_no, "", "formula_cell")])
            if isinstance(cell.value, str) and printed_aadhaar(cell.value):
                raise StatementRefused("aadhaar_in_file", [Issue(row_no, "", "aadhaar_in_file")])


def _amount_of(
    cells: Sequence[Cell], cols: _Columns, preset: Preset
) -> tuple[Decimal | None, bool]:
    """``(credit, is_transaction)``: the credit amount (``None`` for a debit) and whether the row
    has any amount at all."""
    if preset.typed_amount:
        cell = _cell(cells, cols.amount)
        amount = parse_amount(cell.value if cell else None)
        if amount is None:
            return None, False
        kind = normalise_heading(_text(cells, cols.kind) or "")
        if not kind:
            raise BadValue("bad_type")
        return (amount if kind in preset.credit_markers else None), True
    credit_cell = _cell(cells, cols.credit)
    credit = parse_amount(credit_cell.value if credit_cell else None, allow_cr_suffix=True)
    if credit is not None:
        return credit, True
    debit_cell = _cell(cells, cols.debit)
    try:
        debit = parse_amount(debit_cell.value if debit_cell else None, allow_cr_suffix=True)
    except BadValue:
        debit = Decimal(1)  # an odd debit cell still makes the row a (dropped) debit
    return None, debit is not None


@dataclass(slots=True)
class _Reading:
    cfg: UpiConfig
    preset: Preset
    cols: _Columns
    earliest: dt.date
    latest: dt.date
    issues: list[Issue] = field(default_factory=list)
    lines: list[ParsedLine] = field(default_factory=list)
    transactions: int = 0

    def row(self, row_no: int, cells: Sequence[Cell]) -> None:
        cols, preset = self.cols, self.preset
        try:
            day = _parse_date(_cell(cells, cols.date), preset.date_formats)
        except BadValue as bad:
            self.issues.append(Issue(row_no, "date", bad.code))
            return
        if day is None:
            return  # not a transaction row (headings, balances, totals, wrapped narration)
        try:
            credit, is_transaction = _amount_of(cells, cols, preset)
        except BadValue as bad:
            column = "type" if bad.code == "bad_type" else "amount"
            self.issues.append(Issue(row_no, column, bad.code))
            return
        if not is_transaction:
            return
        self.transactions += 1
        if self.transactions > self.cfg.statements.max_lines:
            raise StatementRefused("too_many_lines")
        if credit is None:
            return
        if not self.earliest <= day <= self.latest:
            self.issues.append(Issue(row_no, "date", "date_out_of_range"))
            return
        narration = _text(cells, cols.narration)
        ref_kind, reference = find_reference(narration, _text(cells, cols.reference), self.cfg)
        self.lines.append(
            ParsedLine(
                line_no=row_no,
                value_date=day,
                amount=credit,
                ref_kind=ref_kind,
                reference=reference,
                narration=mask_narration(narration, self.cfg),
            )
        )


def parse_statement(
    rows: Iterable[tuple[int, list[Cell]]],
    cfg: UpiConfig,
    *,
    today: dt.date,
    preset_key: str | None = None,
) -> ParsedStatement:
    """Read a whole statement (see the module docstring). ``preset_key`` ``None`` tries the
    bank presets first and the generic one last. Raises :class:`StatementRefused`."""
    spec = cfg.statements
    materialised = list(rows)
    _refuse_hostile(materialised)
    if preset_key is None:
        presets: Sequence[Preset] = (*cfg.presets[1:], cfg.presets[0])
    else:
        chosen = cfg.preset(preset_key)
        if chosen is None:
            raise StatementRefused("unknown_preset")
        presets = (chosen,)
    preset, position, cols = _header(materialised, presets, spec.header_search_rows)
    reading = _Reading(
        cfg=cfg,
        preset=preset,
        cols=cols,
        earliest=today - dt.timedelta(days=spec.max_days_back),
        latest=today + dt.timedelta(days=spec.max_days_ahead),
    )
    for row_no, cells in materialised[position + 1 :]:
        if len(reading.issues) >= MAX_ISSUES:
            break
        reading.row(row_no, cells)
    if reading.issues:
        raise StatementRefused("rows_unreadable", reading.issues[:MAX_ISSUES])
    return ParsedStatement(
        preset=preset, transactions=reading.transactions, lines=tuple(reading.lines)
    )


__all__ = [
    "BadValue",
    "Issue",
    "ParsedLine",
    "ParsedStatement",
    "StatementRefused",
    "find_reference",
    "mask_narration",
    "parse_amount",
    "parse_statement",
    "printed_aadhaar",
]
