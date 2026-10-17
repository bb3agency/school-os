"""Synthetic bank statements and UPI helpers for the R2-2E tests (synthetic data only, inv. 11).

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/fees`` and the security
suites. Every statement here is made up: synthetic payer names, made-up account numbers, random
UTRs. The layouts follow the presets of ``app/fees/upi.yaml``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import io
import sys
import uuid
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from openpyxl import Workbook
from sqlalchemy import Engine

from app.core.spreadsheet import XLSX_MIME

_HERE = Path(__file__).resolve()


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


F = _load("sos_test_fees_support", _HERE.with_name("support.py"))
D = F.D
W = F.W

ACCOUNT_NO = "50100123456789"  # made up; must never be stored
PAYER = "Synthetica Kothapalli"
PAYER_VPA = "synthetica.parent@oksbi"


@dataclasses.dataclass(frozen=True)
class Credit:
    day: dt.date
    amount: str
    utr: str | None = None
    payer: str = PAYER
    debit: bool = False
    upi: bool = True  # False: a NEFT credit with a bank reference


def _narration(preset: str, c: Credit) -> str:
    if not c.upi:
        return f"NEFT CR-SYNB0000001-{c.payer.upper()}-FEES"
    utr = c.utr or ""
    if preset == "hdfc":
        return f"UPI-{c.payer.upper()}-{PAYER_VPA}-SYNB0000001-{utr}-SCHOOL FEES"
    if preset == "icici":
        return f"UPI/{utr}/SCHOOL FEES/{PAYER_VPA}/Synthetic Bank/ICI1234567890"
    if preset == "axis":
        return f"UPI/P2A/{utr}/{c.payer.upper()}/Synthetic Bank/school fees"
    if preset == "kotak":
        return f"UPI/{c.payer.upper()}/{utr}/school fees"
    # sbi and generic
    return f"BY TRANSFER-UPI/CR/{utr}/{c.payer.upper()}/SYNB/{PAYER_VPA}/Fees--"


def _ref(preset: str, c: Credit) -> str:
    if not c.upi:
        return "SYNBN26291123456"
    if preset == "hdfc":
        return f"0000{c.utr}" if c.utr else ""
    if preset == "sbi":
        return f"TRANSFER FROM {ACCOUNT_NO}"
    return c.utr or ""


def _fmt(preset: str, day: dt.date) -> str:
    return {
        "sbi": day.strftime("%d %b %Y"),
        "hdfc": day.strftime("%d/%m/%y"),
        "icici": day.strftime("%d/%m/%Y"),
        "axis": day.strftime("%d-%m-%Y"),
        "kotak": day.strftime("%d-%m-%Y"),
    }.get(preset, day.strftime("%d/%m/%Y"))


def rows(preset: str, credits: Sequence[Credit]) -> list[list[Any]]:  # noqa: PLR0912
    """A statement as rows (preamble with a made-up account number, headings, transactions,
    totals)."""
    out: list[list[Any]] = [
        ["Account Name", "SYNTHETIC PUBLIC SCHOOL"],
        ["Account Number", ACCOUNT_NO],
        ["Statement period", "01/10/2026 to 18/10/2026"],
        [],
    ]
    balance = Decimal("100000.00")
    if preset == "sbi":
        out.append(["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit",
                    "Credit", "Balance"])  # fmt: skip
    elif preset == "hdfc":
        out.append(["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.",
                    "Deposit Amt.", "Closing Balance"])  # fmt: skip
    elif preset == "icici":
        out.append(["S No.", "Value Date", "Transaction Date", "Cheque Number",
                    "Transaction Remarks", "Withdrawal Amount (INR )", "Deposit Amount (INR )",
                    "Balance (INR )"])  # fmt: skip
    elif preset == "axis":
        out.append(["Tran Date", "CHQNO", "PARTICULARS", "DR", "CR", "BAL", "SOL"])
    elif preset == "kotak":
        out.append(["Sl. No.", "Transaction Date", "Value Date", "Description", "Chq / Ref No.",
                    "Amount", "Dr / Cr", "Balance"])  # fmt: skip
    else:
        out.append(["Date", "Description", "Reference", "Debit", "Credit", "Balance"])
    for n, c in enumerate(credits, start=1):
        amount = Decimal(c.amount)
        balance += -amount if c.debit else amount
        d = _fmt(preset, c.day)
        nar = _narration(preset, c) if not c.debit else "ATM WDL SYNTHETIC"
        ref = _ref(preset, c) if not c.debit else ""
        cr, dr = ("", c.amount) if c.debit else (c.amount, "")
        if preset == "sbi":
            out.append([d, d, nar, ref, dr, cr, str(balance)])
        elif preset == "hdfc":
            out.append([d, nar, ref, d, dr, cr, str(balance)])
        elif preset == "icici":
            out.append([str(n), d, d, ref, nar, dr, cr, str(balance)])
        elif preset == "axis":
            out.append([d, ref, nar, dr, cr, str(balance), "1234"])
        elif preset == "kotak":
            out.append([str(n), d, d, nar, ref, c.amount, "DR" if c.debit else "CR", str(balance)])
        else:
            out.append([d, nar, ref, dr, cr, str(balance)])
    out.append([])
    out.append(["", "Total", "", "", "", str(balance)])
    return out


def csv_bytes(table: Sequence[Sequence[Any]]) -> bytes:
    lines = []
    for row in table:
        cells = []
        for value in row:
            text = str(value)
            if any(ch in text for ch in ',"\n'):
                text = '"' + text.replace('"', '""') + '"'
            cells.append(text)
        lines.append(",".join(cells))
    return ("\n".join(lines) + "\n").encode("utf-8")


def xlsx_bytes(table: Sequence[Sequence[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in table:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def statement(preset: str, credits: Sequence[Credit], *, kind: str = "csv") -> bytes:
    table = rows(preset, credits)
    return xlsx_bytes(table) if kind == "xlsx" else csv_bytes(table)


def upload(
    admin: Engine, school: Any, data: bytes, *, role: str = "accountant", kind: str = "csv"
) -> uuid.UUID:
    """A virus-checked ``import_file`` the person uploaded (ACL: that person only)."""
    person = school.people[role]
    doc: uuid.UUID = D.make_document(
        admin,
        school.tenant_id,
        person.user_id,
        acl=[("membership", str(person.membership_id))],
        purpose="import_file",
        doc_type="import_file",
        data=data,
        mime_type=XLSX_MIME if kind == "xlsx" else "text/csv",
        ext=kind,
    )
    return doc


def document_exists(admin: Engine, document_id: uuid.UUID) -> bool:
    return bool(F.scalar(admin, "SELECT count(*) FROM kb.documents WHERE id = :d", d=document_id))


def upi_receipt(school: Any, amount: str = "1500.00", *, utr: str | None = None) -> Any:
    """An issued UPI receipt on a fresh account (with its UTR)."""
    account_id = F.account(school)
    return F.issue(school, account_id, amount, mode="upi_qr", utr=utr or F.utr())


def today() -> dt.date:
    value: dt.date = F.today()
    return value
