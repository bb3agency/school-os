"""TallyPrime XML: export-only request builder and XXE-safe response parser (ADR-0032 §1).

Requests are ``ENVELOPE``s with ``TALLYREQUEST`` **Export** and an inline TDL collection; they are
built with :mod:`xml.etree.ElementTree` (every value escaped) and :func:`assert_export_only`
refuses anything else before a request leaves the agent. Tally's ``Import`` requests (which write
vouchers or masters) can never be produced here.

Responses are parsed with :mod:`defusedxml` (no DTDs, entities or external references), after:

- decoding by byte-order mark (UTF-16 LE/BE, UTF-8 with or without BOM);
- removing characters and character references that XML 1.0 does not allow (Tally is known to
  emit e.g. ``&#4;`` inside names).

Amounts are :class:`~decimal.Decimal`, never floats, normalised to "what the party owes the
school" (positive = due, negative = advance): in Tally's XML a debit is written as a negative
number (``-5000.00``), and some reports write ``5000.00 Dr`` / ``5000.00 Cr``.
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET  # building requests only; parsing uses defusedxml
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from defusedxml import ElementTree as SafeET  # type: ignore[import-untyped]

COLLECTION_ID: Final = "SOSCollection"
_INVALID_REF: Final = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")
_INVALID_CHARS: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_AMOUNT: Final = re.compile(r"^(-?)([0-9]+(?:\.[0-9]+)?)(?:\s*(Dr|Cr))?$", re.IGNORECASE)
_CURRENCY: Final = re.compile(r"(₹|Rs\.?|INR|₹)", re.IGNORECASE)
_MONTHS: Final = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class TallyXmlError(ValueError):
    """Tally's answer is not what an export returns (error envelope, bad XML, bad amount)."""


@dataclass(frozen=True, slots=True)
class TallyGroup:
    name: str
    parent: str | None


@dataclass(frozen=True, slots=True)
class TallyLedger:
    name: str
    parent: str
    guid: str | None
    owed: Decimal
    """What the party owes the school: positive = due, negative = advance."""


# --- requests -------------------------------------------------------------------------------------


def tally_date(value: dt.date) -> str:
    """Tally's date format for static variables: ``28-Sep-2026``."""
    return f"{value.day}-{_MONTHS[value.month - 1]}-{value.year}"


def _envelope(
    collection_type: str,
    methods: tuple[str, ...],
    *,
    company: str | None = None,
    child_of: str | None = None,
    to_date: dt.date | None = None,
) -> bytes:
    envelope = ET.Element("ENVELOPE")
    header = ET.SubElement(envelope, "HEADER")
    ET.SubElement(header, "VERSION").text = "1"
    ET.SubElement(header, "TALLYREQUEST").text = "Export"
    ET.SubElement(header, "TYPE").text = "Collection"
    ET.SubElement(header, "ID").text = COLLECTION_ID
    desc = ET.SubElement(ET.SubElement(envelope, "BODY"), "DESC")
    static = ET.SubElement(desc, "STATICVARIABLES")
    ET.SubElement(static, "SVEXPORTFORMAT").text = "$$SysName:XML"
    if company is not None:
        ET.SubElement(static, "SVCURRENTCOMPANY").text = company
    if to_date is not None:
        date = ET.SubElement(static, "SVTODATE", {"TYPE": "Date"})
        date.text = tally_date(to_date)
    message = ET.SubElement(ET.SubElement(desc, "TDL"), "TDLMESSAGE")
    collection = ET.SubElement(message, "COLLECTION", {"NAME": COLLECTION_ID, "ISMODIFY": "No"})
    ET.SubElement(collection, "TYPE").text = collection_type
    if child_of is not None:
        ET.SubElement(collection, "CHILDOF").text = child_of
        ET.SubElement(collection, "BELONGSTO").text = "Yes"
    for method in methods:
        ET.SubElement(collection, "NATIVEMETHOD").text = method
    body: bytes = ET.tostring(envelope, encoding="utf-8", xml_declaration=False)
    assert_export_only(body)
    return body


def companies_request() -> bytes:
    """The companies open in Tally (names only)."""
    return _envelope("Company", ("Name",))


def groups_request(company: str) -> bytes:
    """Every ledger group of ``company``: name and parent group. No ledgers, no people."""
    return _envelope("Group", ("Name", "Parent"), company=company)


def ledgers_request(company: str, group: str, to_date: dt.date) -> bytes:
    """Ledgers under ``group`` (and its sub-groups) with their closing balance at ``to_date``."""
    return _envelope(
        "Ledger",
        ("Name", "Parent", "ClosingBalance", "GUID"),
        company=company,
        child_of=group,
        to_date=to_date,
    )


def assert_export_only(body: bytes) -> None:
    """Refuse any envelope that is not a single export request (defence in depth: Tally's HTTP
    server writes to the books on ``Import``)."""
    root = SafeET.fromstring(body)
    requests = [(e.text or "").strip().casefold() for e in root.iter("TALLYREQUEST")]
    if root.tag != "ENVELOPE" or requests != ["export"]:
        raise TallyXmlError("only Tally export requests may be sent")
    if any(True for _ in root.iter("IMPORTDATA")) or any(True for _ in root.iter("REQUESTDATA")):
        raise TallyXmlError("only Tally export requests may be sent")


# --- responses ------------------------------------------------------------------------------------


def decode(raw: bytes) -> str:
    """Tally's bytes as text (BOM-aware), with XML 1.0-invalid characters removed."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8")
    elif len(raw) > 1 and (raw[0] == 0 or raw[1] == 0):
        text = raw.decode("utf-16-be" if raw[0] == 0 else "utf-16-le")
    else:
        text = raw.decode("utf-8", errors="replace")

    def _ref(match: re.Match[str]) -> str:
        value = match.group(1)
        code = int(value[1:], 16) if value.startswith("x") else int(value)
        allowed = code in (9, 10, 13) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD
        allowed = allowed or 0x10000 <= code <= 0x10FFFF
        return match.group(0) if allowed else ""

    text = _INVALID_REF.sub(_ref, text)
    text = _INVALID_CHARS.sub("", text)
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", text)


def _root(raw: bytes) -> ET.Element:
    try:
        root: ET.Element = SafeET.fromstring(decode(raw))
    except ET.ParseError as exc:
        raise TallyXmlError("Tally's answer is not well-formed XML") from exc
    errors = [e.text for e in root.iter("LINEERROR") if e.text]
    if errors:
        raise TallyXmlError("Tally reported an error for the request")
    return root


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _name(element: ET.Element) -> str | None:
    name = element.get("NAME") or _text(element, "NAME")
    if name is None:
        listed = element.find("NAME.LIST/NAME")
        name = listed.text if listed is not None else None
    return name.strip() if name and name.strip() else None


def parse_amount(value: str | None) -> Decimal:
    """Tally's closing balance as what the party owes the school (positive = due)."""
    if value is None or not value.strip():
        return Decimal("0.00")
    text = _CURRENCY.sub("", value).replace(",", "").strip()
    match = _AMOUNT.match(text)
    if match is None:
        raise TallyXmlError("not a Tally amount")
    sign, number, side = match.groups()
    try:
        amount = Decimal(number)
    except InvalidOperation as exc:  # pragma: no cover - the pattern allows only digits
        raise TallyXmlError("not a Tally amount") from exc
    if side is not None:
        owed = amount if side.casefold() == "dr" else -amount
        if sign:
            owed = -owed
    else:
        owed = amount if sign else -amount  # XML: a debit (owed to us) is negative
    try:
        return owed.quantize(Decimal("0.01"))
    except InvalidOperation as exc:  # more digits than a Decimal context holds
        raise TallyXmlError("not a Tally amount") from exc


def parse_companies(raw: bytes) -> list[str]:
    root = _root(raw)
    names = [_name(e) for e in root.iter("COMPANY")]
    return list(dict.fromkeys(n for n in names if n))


def parse_groups(raw: bytes) -> list[TallyGroup]:
    root = _root(raw)
    out: dict[str, TallyGroup] = {}
    for element in root.iter("GROUP"):
        name = _name(element)
        if name and name not in out:
            out[name] = TallyGroup(name=name, parent=_text(element, "PARENT"))
    return list(out.values())


def parse_ledgers(raw: bytes) -> list[TallyLedger]:
    root = _root(raw)
    out: list[TallyLedger] = []
    for element in root.iter("LEDGER"):
        name = _name(element)
        if not name:
            continue
        out.append(
            TallyLedger(
                name=name,
                parent=_text(element, "PARENT") or "",
                guid=_text(element, "GUID"),
                owed=parse_amount(_text(element, "CLOSINGBALANCE")),
            )
        )
    return out


__all__ = [
    "COLLECTION_ID",
    "TallyGroup",
    "TallyLedger",
    "TallyXmlError",
    "assert_export_only",
    "companies_request",
    "decode",
    "groups_request",
    "ledgers_request",
    "parse_amount",
    "parse_companies",
    "parse_groups",
    "parse_ledgers",
    "tally_date",
]
