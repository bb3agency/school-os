"""The Tally XML import file (FM-03; FR-TALLY-018, FR-TALLY-019). Pure: no database, no I/O.

The file is an ``ENVELOPE`` with ``TALLYREQUEST`` "Import Data" and one ``VOUCHER`` per voucher
(``app/tally/config.yaml`` ``vouchers``: tags and voucher type names, ``verified: false`` until a
pilot import). Each voucher's ledger lines balance; a debit is ``ISDEEMEDPOSITIVE`` Yes with a
negative ``AMOUNT`` and a credit No with a positive one (Tally's convention).

Safety: the document is built with :mod:`xml.etree.ElementTree`, which escapes ``&``, ``<``,
``>`` and quotes in text and attributes; characters XML 1.0 cannot carry (control characters,
lone surrogates, U+FFFE/U+FFFF) are dropped first. No DOCTYPE, no entities, no processing
instructions are written, so the importer is never asked to resolve anything (XXE-safe output).
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.tally.config import rules

_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class Line:
    """One ledger line: ``amount`` positive is a debit, negative a credit."""

    ledger: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Voucher:
    vch_type: str
    date: dt.date
    number: str
    party: str | None
    narration: str
    lines: tuple[Line, ...]


class UnbalancedVoucher(ValueError):
    pass


def xml_text(value: str) -> str:
    """``value`` without the characters XML 1.0 cannot carry (escaping is ElementTree's)."""
    return _NOT_XML.sub("", value)


def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = xml_text(text)
    return el


def _amount(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _voucher(parent: ET.Element, v: Voucher) -> None:
    if sum((line.amount for line in v.lines), ZERO) != ZERO or len(v.lines) < 2:
        raise UnbalancedVoucher(v.number)
    el = ET.SubElement(parent, "VOUCHER", {"VCHTYPE": xml_text(v.vch_type), "ACTION": "Create"})
    _sub(el, "DATE", v.date.strftime("%Y%m%d"))
    _sub(el, "VOUCHERTYPENAME", v.vch_type)
    _sub(el, "VOUCHERNUMBER", v.number)
    if v.party:
        _sub(el, "PARTYLEDGERNAME", v.party)
    _sub(el, "NARRATION", v.narration)
    for line in v.lines:
        entry = ET.SubElement(el, "ALLLEDGERENTRIES.LIST")
        _sub(entry, "LEDGERNAME", line.ledger)
        _sub(entry, "ISDEEMEDPOSITIVE", "Yes" if line.amount > 0 else "No")
        _sub(entry, "AMOUNT", _amount(-line.amount))


def render(vouchers: Sequence[Voucher], *, company: str | None) -> bytes:
    """The whole file as UTF-8 bytes with an XML declaration."""
    cfg = rules().vouchers
    envelope = ET.Element("ENVELOPE")
    header = ET.SubElement(envelope, "HEADER")
    _sub(header, "TALLYREQUEST", cfg.tally_request)
    body = ET.SubElement(envelope, "BODY")
    data = ET.SubElement(body, "IMPORTDATA")
    desc = ET.SubElement(data, "REQUESTDESC")
    _sub(desc, "REPORTNAME", cfg.report_name)
    if company:
        static = ET.SubElement(desc, "STATICVARIABLES")
        _sub(static, "SVCURRENTCOMPANY", company)
    request = ET.SubElement(data, "REQUESTDATA")
    for v in vouchers:
        message = ET.SubElement(request, "TALLYMESSAGE")
        _voucher(message, v)
    ET.indent(envelope, space=" ")
    out: bytes = ET.tostring(envelope, encoding="utf-8", xml_declaration=True)
    return out


__all__ = ["Line", "UnbalancedVoucher", "Voucher", "render", "xml_text"]
