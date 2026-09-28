"""Invoice PDF template (docs/16 §5.8; FR-PLT-016, FR-PLT-017). Pure: no database, no storage.

Builds an :class:`InvoiceDocument` from an issued invoice row (``platform.invoices``, whose
supplier and recipient fields are the snapshot taken when it was issued) and its lines, and
turns it into one escaped A4 HTML page that :mod:`app.core.pdf` prints. The page carries what
CGST Rule 46 asks of a tax invoice: supplier name, address and GSTIN; a serial number of at most
16 characters; the date of issue; the recipient's name, address and GSTIN; place of supply
(state code and name); per line the description, SAC, quantity, rate and taxable value; the GST
rate and amount (CGST + SGST intra-state, IGST inter-state); the total; whether tax is payable
on reverse charge; and a signatory block.

The layout is template ``v0`` and is **pending CA review** (docs/16 §19 Q2, Q3, Q11); the marker
lives in the docs, not on the page. Wording, state names and the version come from
``billing.yaml`` → ``invoice_pdf``. Invoices hold no student data, and nothing here reads any.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.core.pdf import FONT_FAMILY, FONT_URL
from app.platform.common import config

PAISE: Final = Decimal("0.01")
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9-]+")


class InvoicePdfConfig(BaseModel):
    """``billing.yaml`` → ``invoice_pdf``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template_version: str = Field(pattern=r"^v[0-9]{1,3}$")
    language: str = Field(pattern=r"^en$")
    title: str = Field(min_length=1, max_length=60)
    object_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*)*/$")
    download_url_ttl_s: int = Field(ge=30, le=300)
    render_batch: int = Field(ge=1, le=200)
    reverse_charge: str = Field(pattern=r"^(Yes|No)$")
    zero_tax_note: str = Field(min_length=1, max_length=300)
    footer_note: str = Field(min_length=1, max_length=200)
    state_names: dict[str, str] = Field(min_length=1)


def pdf_config() -> InvoicePdfConfig:
    return InvoicePdfConfig.model_validate(config()["invoice_pdf"])


@dataclass(frozen=True, slots=True)
class Party:
    legal_name: str
    gstin: str | None
    address_lines: tuple[str, ...]
    state_code: str
    state_name: str


@dataclass(frozen=True, slots=True)
class Line:
    line_no: int
    description: str
    sac_code: str
    quantity: Decimal
    unit_price_inr: Decimal
    amount_inr: Decimal
    gst_rate: Decimal


@dataclass(frozen=True, slots=True)
class InvoiceDocument:
    template_version: str
    title: str
    invoice_number: str
    issue_date: dt.date
    due_date: dt.date | None
    period_start: dt.date
    period_last_day: dt.date
    supplier: Party
    recipient: Party
    place_of_supply_code: str
    place_of_supply_name: str
    tax_type: str
    lines: tuple[Line, ...]
    taxable_value_inr: Decimal
    cgst_inr: Decimal
    sgst_inr: Decimal
    igst_inr: Decimal
    total_inr: Decimal
    notes: str | None
    reverse_charge: str
    zero_tax_note: str | None
    footer_note: str


class NotNumbered(ValueError):
    """Only issued (numbered) invoices have a PDF; drafts have no number (FR-PLT-016)."""


def _state(cfg: InvoicePdfConfig, code: str) -> str:
    return cfg.state_names.get(code, "")


def _address_lines(address: Mapping[str, Any], state_name: str) -> tuple[str, ...]:
    lines = [str(address.get("address_line1") or "").strip()]
    if address.get("address_line2"):
        lines.append(str(address["address_line2"]).strip())
    city = ", ".join(str(address[k]).strip() for k in ("city", "district") if address.get(k))
    if city:
        lines.append(city)
    tail = " - ".join(p for p in (state_name, str(address.get("postal_code") or "").strip()) if p)
    if tail:
        lines.append(tail)
    return tuple(line for line in lines if line)


def build_document(
    invoice: Mapping[str, Any],
    lines: Sequence[Mapping[str, Any]],
    *,
    supplier_address: str,
    cfg: InvoicePdfConfig,
) -> InvoiceDocument:
    """The printable view of one issued invoice (snapshot fields only; nothing is recomputed)."""
    number = invoice.get("invoice_number")
    issue_date = invoice.get("issue_date")
    if not number or issue_date is None:
        raise NotNumbered("draft invoices have no PDF")
    supplier_state = str(invoice["supplier_state_code"])
    place = str(invoice["place_of_supply_state_code"])
    recipient_address: Mapping[str, Any] = invoice.get("recipient_address") or {}
    recipient_state = str(recipient_address.get("state_code") or place)
    doc_lines = tuple(
        Line(
            line_no=int(r["line_no"]),
            description=str(r["description"]),
            sac_code=str(r["sac_code"]),
            quantity=Decimal(r["quantity"]),
            unit_price_inr=Decimal(r["unit_price_inr"]),
            amount_inr=Decimal(r["amount_inr"]),
            gst_rate=Decimal(r["gst_rate"]),
        )
        for r in sorted(lines, key=lambda r: int(r["line_no"]))
    )
    zero_tax = all(line.gst_rate == 0 for line in doc_lines)
    return InvoiceDocument(
        template_version=cfg.template_version,
        title=cfg.title,
        invoice_number=str(number),
        issue_date=issue_date,
        due_date=invoice.get("due_date"),
        period_start=invoice["period_start"],
        period_last_day=invoice["period_end"] - dt.timedelta(days=1),
        supplier=Party(
            legal_name=str(invoice["supplier_legal_name"]),
            gstin=str(invoice["supplier_gstin"]),
            address_lines=tuple(p.strip() for p in supplier_address.split(";") if p.strip()),
            state_code=supplier_state,
            state_name=_state(cfg, supplier_state),
        ),
        recipient=Party(
            legal_name=str(invoice["recipient_legal_name"]),
            gstin=invoice.get("recipient_gstin") or None,
            address_lines=_address_lines(recipient_address, _state(cfg, recipient_state)),
            state_code=recipient_state,
            state_name=_state(cfg, recipient_state),
        ),
        place_of_supply_code=place,
        place_of_supply_name=_state(cfg, place),
        tax_type=str(invoice["tax_type"]),
        lines=doc_lines,
        taxable_value_inr=Decimal(invoice["taxable_value_inr"]),
        cgst_inr=Decimal(invoice["cgst_inr"]),
        sgst_inr=Decimal(invoice["sgst_inr"]),
        igst_inr=Decimal(invoice["igst_inr"]),
        total_inr=Decimal(invoice["total_inr"]),
        notes=invoice.get("notes") or None,
        reverse_charge=cfg.reverse_charge,
        zero_tax_note=cfg.zero_tax_note if zero_tax else None,
        footer_note=cfg.footer_note,
    )


# --- formatting (pure) ------------------------------------------------------------------------


def format_inr(value: Decimal) -> str:
    """Indian digit grouping with paise: 1234567.5 -> '12,34,567.50'; negatives keep the sign."""
    q = value.quantize(PAISE)
    sign = "-" if q < 0 else ""
    whole, frac = f"{abs(q):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}{whole}.{frac}"


def format_quantity(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text


def format_rate(value: Decimal) -> str:
    return f"{value.normalize():f}%"


def format_date(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


_ONES: Final = (
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS: Final = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def _integer_words(n: int) -> str:
    """Indian system: crore (10^7), lakh (10^5), thousand, hundred."""
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(f"{_integer_words(crore)} Crore")
    if lakh:
        parts.append(f"{_below_hundred(lakh)} Lakh")
    if thousand:
        parts.append(f"{_below_hundred(thousand)} Thousand")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(value: Decimal) -> str:
    """'Indian Rupees Five Thousand Nine Hundred and Fifty Paise Only' (non-negative amounts)."""
    q = value.quantize(PAISE)
    if q < 0:
        raise ValueError("invoice totals are never negative")
    rupees = int(q)
    paise = int((q - rupees) * 100)
    words = f"Indian Rupees {_integer_words(rupees)}"
    if paise:
        words += f" and {_below_hundred(paise)} Paise"
    return f"{words} Only"


def pdf_filename(invoice_number: str) -> str:
    """ASCII download name: 'SOS/26-27/000123' -> 'invoice-SOS-26-27-000123.pdf'."""
    stem = _FILENAME_SAFE.sub("-", invoice_number).strip("-") or "invoice"
    return f"invoice-{stem}.pdf"


# --- HTML (pure) ------------------------------------------------------------------------------


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


STYLE: Final = f"""
@font-face {{ font-family: "{FONT_FAMILY}"; src: url("{FONT_URL}") format("truetype");
  font-weight: 100 900; font-stretch: 62.5% 100%; }}
@page {{ size: A4 portrait; margin: 14mm 14mm 18mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #111; background: #fff; font-family: "{FONT_FAMILY}", sans-serif;
  font-size: 9pt; line-height: 1.5; }}
h1 {{ font-size: 15pt; margin: 0 0 2mm; letter-spacing: 0.02em; }}
h2 {{ font-size: 8pt; text-transform: uppercase; letter-spacing: 0.06em; color: #444;
  margin: 0 0 1mm; }}
p {{ margin: 0; }}
.top {{ display: flex; justify-content: space-between; gap: 8mm; align-items: flex-start;
  border-bottom: 1.5pt solid #111; padding-bottom: 3mm; margin-bottom: 4mm; }}
.meta {{ border-collapse: collapse; }}
.meta th {{ text-align: left; font-weight: 600; padding: 0.4mm 3mm 0.4mm 0; color: #333; }}
.meta td {{ text-align: right; padding: 0.4mm 0; font-variant-numeric: tabular-nums; }}
.parties {{ display: flex; gap: 6mm; margin-bottom: 4mm; }}
.party {{ flex: 1 1 0; border: 0.75pt solid #999; padding: 2.5mm 3mm; }}
.party .name {{ font-weight: 700; font-size: 10pt; }}
.gstin {{ margin-top: 1mm; font-variant-numeric: tabular-nums; }}
.supply {{ margin-bottom: 3mm; }}
table.lines {{ width: 100%; border-collapse: collapse; margin-bottom: 3mm; }}
table.lines th, table.lines td {{ border: 0.75pt solid #999; padding: 1.2mm 1.6mm;
  vertical-align: top; }}
table.lines th {{ background: #eee; font-weight: 600; text-align: left; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; }}
.totals {{ margin-left: auto; border-collapse: collapse; min-width: 80mm; break-inside: avoid; }}
.totals th {{ text-align: left; font-weight: 600; padding: 1mm 4mm 1mm 0; }}
.totals td {{ padding: 1mm 0; }}
.totals tr.grand th, .totals tr.grand td {{ border-top: 1.5pt solid #111; font-size: 11pt;
  font-weight: 700; padding-top: 1.5mm; }}
.words {{ margin: 3mm 0; }}
.note {{ margin: 2mm 0; padding: 2mm 3mm; border-left: 2pt solid #999; background: #f6f6f6; }}
.sign {{ margin-top: 8mm; display: flex; justify-content: flex-end; break-inside: avoid; }}
.sign div {{ text-align: center; min-width: 60mm; }}
.sign .line {{ margin-top: 14mm; border-top: 0.75pt solid #111; padding-top: 1mm; }}
.foot {{ margin-top: 6mm; color: #555; font-size: 7.5pt; }}
"""


def _party_html(label: str, party: Party) -> str:
    address = "".join(f"<p>{_e(line)}</p>" for line in party.address_lines)
    state = f"{party.state_code} - {party.state_name}" if party.state_name else party.state_code
    gstin = (
        f'<p class="gstin">GSTIN: {_e(party.gstin)}</p>'
        if party.gstin
        else '<p class="gstin">GSTIN: Unregistered</p>'
    )
    return (
        f'<section class="party"><h2>{_e(label)}</h2>'
        f'<p class="name">{_e(party.legal_name)}</p>{address}'
        f"{gstin}<p>State code: {_e(state)}</p></section>"
    )


def _tax_rows(doc: InvoiceDocument) -> str:
    rates = {line.gst_rate for line in doc.lines}
    single = next(iter(rates)) if len(rates) == 1 else None
    if doc.tax_type == "igst":
        label = f"IGST @ {format_rate(single)}" if single is not None else "IGST"
        return f'<tr><th>{_e(label)}</th><td class="num">{format_inr(doc.igst_inr)}</td></tr>'
    half = format_rate(single / 2) if single is not None else None
    cgst = f"CGST @ {half}" if half else "CGST"
    sgst = f"SGST @ {half}" if half else "SGST"
    return (
        f'<tr><th>{_e(cgst)}</th><td class="num">{format_inr(doc.cgst_inr)}</td></tr>'
        f'<tr><th>{_e(sgst)}</th><td class="num">{format_inr(doc.sgst_inr)}</td></tr>'
    )


def render_invoice_html(doc: InvoiceDocument) -> str:
    """One A4 portrait page (more when there are many lines); every value is escaped."""
    due = (
        f"<tr><th>Due date</th><td>{_e(format_date(doc.due_date))}</td></tr>"
        if doc.due_date
        else ""
    )
    meta = (
        '<table class="meta">'
        f"<tr><th>Invoice number</th><td>{_e(doc.invoice_number)}</td></tr>"
        f"<tr><th>Invoice date</th><td>{_e(format_date(doc.issue_date))}</td></tr>"
        f"{due}"
        f"<tr><th>Service period</th><td>{_e(format_date(doc.period_start))} to "
        f"{_e(format_date(doc.period_last_day))}</td></tr>"
        "</table>"
    )
    place = (
        f"{doc.place_of_supply_code} - {doc.place_of_supply_name}"
        if doc.place_of_supply_name
        else doc.place_of_supply_code
    )
    rows = "".join(
        "<tr>"
        f'<td class="num">{line.line_no}</td>'
        f"<td>{_e(line.description)}</td>"
        f"<td>{_e(line.sac_code)}</td>"
        f'<td class="num">{_e(format_quantity(line.quantity))}</td>'
        f'<td class="num">{format_inr(line.unit_price_inr)}</td>'
        f'<td class="num">{format_rate(line.gst_rate)}</td>'
        f'<td class="num">{format_inr(line.amount_inr)}</td>'
        "</tr>"
        for line in doc.lines
    )
    lines_table = (
        '<table class="lines"><thead><tr>'
        '<th class="num">No.</th><th>Description of service</th><th>SAC</th>'
        '<th class="num">Qty</th><th class="num">Rate (INR)</th><th class="num">GST</th>'
        '<th class="num">Taxable value (INR)</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )
    totals = (
        '<table class="totals">'
        f'<tr><th>Taxable value</th><td class="num">{format_inr(doc.taxable_value_inr)}</td></tr>'
        f"{_tax_rows(doc)}"
        f'<tr class="grand"><th>Total (INR)</th><td class="num">{format_inr(doc.total_inr)}</td>'
        "</tr></table>"
    )
    notes = f'<p class="note">{_e(doc.notes)}</p>' if doc.notes else ""
    zero = f'<p class="note">{_e(doc.zero_tax_note)}</p>' if doc.zero_tax_note else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{_e(doc.title)} {_e(doc.invoice_number)}</title>"
        f"<style>{STYLE}</style></head><body>"
        f'<header class="top"><div><h1>{_e(doc.title)}</h1>'
        f'<p class="name">{_e(doc.supplier.legal_name)}</p></div>{meta}</header>'
        '<div class="parties">'
        f"{_party_html('Supplier', doc.supplier)}"
        f"{_party_html('Bill to (recipient)', doc.recipient)}"
        "</div>"
        f'<p class="supply">Place of supply: <strong>{_e(place)}</strong> &middot; '
        f"Tax payable on reverse charge: <strong>{_e(doc.reverse_charge)}</strong></p>"
        f"{lines_table}{totals}"
        f'<p class="words">Amount in words: <strong>{_e(amount_in_words(doc.total_inr))}'
        "</strong></p>"
        f"{zero}{notes}"
        f'<div class="sign"><div><p>For {_e(doc.supplier.legal_name)}</p>'
        '<p class="line">Authorised signatory</p></div></div>'
        f'<p class="foot">{_e(doc.footer_note)}</p>'
        "</body></html>"
    )


__all__ = [
    "InvoiceDocument",
    "InvoicePdfConfig",
    "Line",
    "NotNumbered",
    "Party",
    "amount_in_words",
    "build_document",
    "format_inr",
    "pdf_config",
    "pdf_filename",
    "render_invoice_html",
]
