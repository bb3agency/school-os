"""Receipt and daily-report print pages (HTML/CSS, template ``v1``; FR-FEE-011, FR-FEE-018;
CLAUDE.md §10 "print is first-class").

Pure module: the service passes the frozen receipt content (a dict, exactly what was hashed at
issue) or finished report data and gets a self-contained page back. Every value goes through
:func:`html.escape` and the Aadhaar mask, except the typed UPI/bank reference, which is shown
unmasked in its own field only (FR-FEE-014, PRV-015). Pages have no scripts, links or images;
their only style block is allowed by hash in :data:`STYLE_CSP` when served to a browser. English
(staff and printed receipts; ADR-0036).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.core.redaction import mask_aadhaar

_FONTS: Final = '"Noto Sans", Arial, sans-serif'


def _receipt_style(size: str) -> str:
    small = size == "A5"
    return f"""
@page {{ size: {size}; margin: {"8mm" if small else "14mm"}; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #111; background: #fff; font-family: {_FONTS};
  font-size: {"9pt" if small else "11pt"}; line-height: 1.6; }}
main {{ position: relative; border: 1.5px solid #222;
  padding: {"4mm 5mm" if small else "7mm 8mm"}; }}
.watermark {{ position: absolute; top: 40%; left: 0; right: 0; text-align: center;
  transform: rotate(-20deg); font-size: {"30pt" if small else "44pt"}; font-weight: 700;
  color: rgba(150, 0, 0, 0.2); }}
header {{ text-align: center; border-bottom: 1px solid #222; padding-bottom: 2mm; }}
.school {{ font-size: {"12pt" if small else "16pt"}; font-weight: 700; margin: 0; }}
.address, .affiliation {{ margin: 0; font-size: {"8pt" if small else "10pt"}; }}
h1 {{ text-align: center; font-size: {"11pt" if small else "14pt"}; margin: 2mm 0;
  letter-spacing: 0.06em; }}
.meta {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2mm 6mm;
  margin: 1mm 0 2mm; }}
table {{ width: 100%; border-collapse: collapse; margin: 1mm 0; }}
th, td {{ border: 1px solid #555; padding: 1mm 2mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }}
th {{ background: #f0f0f0; font-weight: 600; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
tfoot td {{ font-weight: 700; }}
.words {{ margin: 1mm 0; font-style: italic; }}
.mode {{ margin: 1mm 0; }}
.note {{ font-size: {"7.5pt" if small else "9pt"}; color: #333; margin: 1mm 0; }}
.cancelled {{ border: 2px solid #900; color: #900; padding: 1mm 2mm; font-weight: 700; }}
.signatures {{ display: flex; justify-content: space-between; gap: 8mm;
  margin-top: {"10mm" if small else "16mm"}; }}
.signatures div {{ flex: 1; border-top: 1px solid #111; padding-top: 1mm; text-align: center; }}
footer {{ margin-top: 2mm; font-size: 7pt; color: #444; }}
@media screen {{ body {{ background: #e9e9e9; }} main {{ max-width: {"148mm" if small else "190mm"};
  margin: 6mm auto; background: #fff; }} }}
"""


REPORT_STYLE: Final = f"""
@page {{ size: A4; margin: 12mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #111; background: #fff; font-family: {_FONTS}; font-size: 9pt; line-height: 1.6; }}
h1 {{ font-size: 14pt; margin: 0 0 1mm; }}
h2 {{ font-size: 11pt; margin: 4mm 0 1mm; }}
.school {{ font-size: 12pt; font-weight: 700; margin: 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 1mm 0; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
th, td {{ border: 1px solid #555; padding: 1mm 2mm; text-align: left; overflow-wrap: anywhere; }}
th {{ background: #eee; font-weight: 600; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
tr.cancelled td {{ color: #7a0000; text-decoration: line-through; }}
.totals td {{ font-weight: 700; }}
.closed {{ border: 1px solid #333; padding: 2mm; margin: 2mm 0; }}
.signatures {{ display: flex; justify-content: space-between; gap: 10mm; margin-top: 14mm; }}
.signatures div {{ flex: 1; border-top: 1px solid #111; padding-top: 1mm; text-align: center; }}
footer {{ margin-top: 4mm; font-size: 7.5pt; color: #444; }}
@media screen {{ body {{ background: #e9e9e9; padding: 6mm; }} main {{ background: #fff;
  padding: 6mm; max-width: 200mm; margin: 0 auto; }} }}
"""

RECEIPT_STYLE_A5: Final = _receipt_style("A5")
RECEIPT_STYLE_A4: Final = _receipt_style("A4")


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


STYLE_CSP: Final = (
    "default-src 'none'; style-src "
    + " ".join(_style_hash(s) for s in (RECEIPT_STYLE_A5, RECEIPT_STYLE_A4, REPORT_STYLE))
    + "; img-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
_PDF_CSP: Final = "default-src 'none'; style-src 'unsafe-inline'"

MODE_LABELS: Final = {
    "cash": "Cash",
    "cheque": "Cheque",
    "bank_transfer": "Bank transfer",
    "upi_qr": "UPI (school QR)",
    "razorpay": "Online (Razorpay)",
}


def _e(value: object) -> str:
    text = "" if value is None else str(value)
    return html.escape(mask_aadhaar(text), quote=True)


def _raw(value: object) -> str:
    """Escaped, not masked: only for the typed UTR / bank reference field (FR-FEE-014)."""
    return html.escape("" if value is None else str(value), quote=True)


def format_date(value: dt.date | str | None) -> str:
    """DD/MM/YYYY (PRD §8: Indian conventions)."""
    if value is None:
        return ""
    day = value if isinstance(value, dt.date) else dt.date.fromisoformat(value)
    return day.strftime("%d/%m/%Y")


def _page(title: str, style: str, body: str, *, for_pdf: bool) -> str:
    csp = f'<meta http-equiv="Content-Security-Policy" content="{_PDF_CSP}">' if for_pdf else ""
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"{csp}<title>{_e(title)}</title><style>{style}</style></head><body>{body}</body></html>"
    )


def _mode_text(content: Mapping[str, Any]) -> str:
    mode = str(content.get("mode", ""))
    details: Mapping[str, Any] = content.get("mode_details") or {}
    parts = [f"Paid by: <strong>{_e(MODE_LABELS.get(mode, mode))}</strong>"]
    if mode == "cheque":
        parts.append(f"Cheque no. {_e(details.get('cheque_number'))}")
        parts.append(f"dated {_e(format_date(details.get('cheque_date')))}")
        parts.append(f"{_e(details.get('bank_name'))}")
    elif mode == "bank_transfer":
        parts.append(f'Reference <span class="ref">{_raw(details.get("reference"))}</span>')
    elif mode == "upi_qr":
        parts.append(f'UPI reference (UTR) <span class="ref">{_raw(content.get("utr"))}</span>')
    elif mode == "razorpay":
        parts.append(f"Payment id {_e(details.get('payment_id'))}")
    return " · ".join(p for p in parts if p)


def render_receipt(
    content: Mapping[str, Any],
    *,
    cancelled_on: dt.date | None = None,
    for_pdf: bool = False,
) -> str:
    """The receipt page from its frozen content (A5 by default, A4 per school). A cancelled
    receipt keeps its number and prints "CANCELLED on <date>" (FR-FEE-012)."""
    size = "A4" if content.get("paper_size") == "A4" else "A5"
    style = RECEIPT_STYLE_A4 if size == "A4" else RECEIPT_STYLE_A5
    school: Mapping[str, Any] = content.get("school") or {}
    student: Mapping[str, Any] = content.get("student") or {}
    head = [f'<p class="school">{_e(school.get("name"))}</p>']
    for key in ("address", "affiliation"):
        if school.get(key):
            head.append(f'<p class="{key}">{_e(school.get(key))}</p>')
    rows = "".join(
        f"<tr><td>{_e(line.get('label'))}</td><td>{_e(line.get('head_name'))}</td>"
        f'<td class="num">{_e(line.get("amount_text"))}</td></tr>'
        for line in content.get("lines") or []
    )
    mark = ""
    watermark = ""
    if cancelled_on is not None:
        mark = f'<p class="cancelled">CANCELLED on {_e(format_date(cancelled_on))}</p>'
        watermark = '<div class="watermark" aria-hidden="true">CANCELLED</div>'
    body = (
        f"<main>{watermark}<header>{''.join(head)}</header>"
        "<h1>FEE RECEIPT</h1>"
        '<div class="meta">'
        f"<span>Receipt no.: <strong>{_e(content.get('receipt_number'))}</strong></span>"
        f"<span>Date: <strong>{_e(format_date(content.get('receipt_date')))}</strong></span>"
        "</div>"
        '<div class="meta">'
        f"<span>Student: <strong>{_e(student.get('name'))}</strong></span>"
        f"<span>Admission no.: <strong>{_e(student.get('admission_no'))}</strong></span>"
        f"<span>Class: <strong>{_e(student.get('class_section'))}</strong></span>"
        f"<span>Year: {_e(content.get('academic_year'))}</span>"
        "</div>"
        f"{mark}"
        "<table><thead><tr><th>Instalment</th><th>Fee head</th>"
        '<th class="num">Amount (₹)</th></tr></thead>'
        f"<tbody>{rows}</tbody>"
        f'<tfoot><tr><td colspan="2">Total</td><td class="num">'
        f"{_e(content.get('amount_text'))}</td></tr></tfoot></table>"
        f'<p class="words">{_e(content.get("amount_in_words"))}</p>'
        f'<p class="mode">{_mode_text(content)}</p>'
        f'<p class="note">{_e(content.get("gst_note"))}</p>'
        '<div class="signatures">'
        f"<div>Received by {_e(content.get('collected_by_name') or 'SchoolOS (online)')}</div>"
        "<div>Office seal</div></div>"
        f"<footer>Computer-generated receipt · SchoolOS · {_e(content.get('financial_year'))}"
        f" · template {_e(content.get('template_version'))}</footer></main>"
    )
    return _page(f"Receipt {content.get('receipt_number', '')}", style, body, for_pdf=for_pdf)


def render_voucher(content: Mapping[str, Any]) -> str:
    """The refund voucher page (A5, the receipt style) from its frozen content (FR-FEE-013)."""
    school: Mapping[str, Any] = content.get("school") or {}
    student: Mapping[str, Any] = content.get("student") or {}
    mode = str(content.get("mode", ""))
    details: Mapping[str, Any] = content.get("mode_details") or {}
    extra = ""
    if details.get("cheque_number"):
        extra = f" · Cheque no. {_e(details.get('cheque_number'))}"
    elif details.get("reference"):
        extra = f' · Reference <span class="ref">{_raw(details.get("reference"))}</span>'
    body = (
        f'<main><header><p class="school">{_e(school.get("name"))}</p>'
        + (f'<p class="address">{_e(school.get("address"))}</p>' if school.get("address") else "")
        + "</header><h1>REFUND VOUCHER</h1>"
        '<div class="meta">'
        f"<span>Voucher no.: <strong>{_e(content.get('voucher_number'))}</strong></span>"
        f"<span>Date: <strong>{_e(format_date(content.get('voucher_date')))}</strong></span>"
        "</div>"
        '<div class="meta">'
        f"<span>Student: <strong>{_e(student.get('name'))}</strong></span>"
        f"<span>Admission no.: <strong>{_e(student.get('admission_no'))}</strong></span>"
        f"<span>Class: <strong>{_e(student.get('class_section'))}</strong></span>"
        "</div>"
        f"<p>Against receipt <strong>{_e(content.get('receipt_number'))}</strong></p>"
        "<table><tbody>"
        f'<tr><th>Amount refunded (₹)</th><td class="num">{_e(content.get("amount_text"))}</td>'
        "</tr></tbody></table>"
        f'<p class="words">{_e(content.get("amount_in_words"))}</p>'
        f'<p class="mode">Refunded by: <strong>{_e(MODE_LABELS.get(mode, mode))}</strong>'
        f"{extra}</p>"
        '<div class="signatures"><div>Received by (payer)</div><div>Accountant</div>'
        "<div>Principal</div></div>"
        f"<footer>Computer-generated voucher · SchoolOS · {_e(content.get('financial_year'))}"
        "</footer></main>"
    )
    return _page(
        f"Refund voucher {content.get('voucher_number', '')}", RECEIPT_STYLE_A5, body, for_pdf=False
    )


def render_daily_report(
    *,
    school_name: str,
    day: dt.date,
    by_mode: Sequence[tuple[str, int, str]],
    by_collector: Sequence[tuple[str, int, str]],
    receipts: Sequence[tuple[str, str, str, str, str, str, bool]],
    cancellations: Sequence[tuple[str, str, str, str]],
    refunds: Sequence[tuple[str, str, str, str]],
    totals: Sequence[tuple[str, str]],
    closed: str | None,
) -> str:
    """The daily collection report on A4 (FR-FEE-018). Rows arrive as display strings."""

    def table(head: Sequence[str], rows: Sequence[Sequence[str]], num: set[int]) -> str:
        if not rows:
            return "<p>None.</p>"
        th = "".join(
            f'<th class="num">{_e(h)}</th>' if i in num else f"<th>{_e(h)}</th>"
            for i, h in enumerate(head)
        )
        body = "".join(
            "<tr>"
            + "".join(
                f'<td class="num">{_e(v)}</td>' if i in num else f"<td>{_e(v)}</td>"
                for i, v in enumerate(r)
            )
            + "</tr>"
            for r in rows
        )
        return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"

    receipt_rows = "".join(
        f"<tr{' class="cancelled"' if cancelled else ''}><td>{_e(n)}</td><td>{_e(s)}</td>"
        f"<td>{_e(k)}</td><td>{_e(MODE_LABELS.get(m, m))}</td><td>{_e(who)}</td>"
        f'<td class="num">{_e(a)}</td></tr>'
        for n, s, k, m, who, a, cancelled in receipts
    )
    receipts_table = (
        "<table><thead><tr><th>Receipt</th><th>Student</th><th>Class</th><th>Mode</th>"
        f'<th>Collected by</th><th class="num">Amount (₹)</th></tr></thead>'
        f"<tbody>{receipt_rows}</tbody></table>"
        if receipts
        else "<p>No receipts.</p>"
    )
    total_rows = "".join(
        f'<tr class="totals"><td>{_e(k)}</td><td class="num">{_e(v)}</td></tr>' for k, v in totals
    )
    body = (
        f'<main><p class="school">{_e(school_name)}</p>'
        f"<h1>Daily collection report · {_e(format_date(day))}</h1>"
        + (f'<p class="closed">{_e(closed)}</p>' if closed else "")
        + "<h2>Totals</h2>"
        + f"<table><tbody>{total_rows}</tbody></table>"
        + "<h2>By mode</h2>"
        + table(
            ["Mode", "Receipts", "Amount (₹)"],
            [(MODE_LABELS.get(m, m), str(n), a) for m, n, a in by_mode],
            {1, 2},
        )
        + "<h2>By collector</h2>"
        + table(
            ["Collected by", "Receipts", "Amount (₹)"],
            [(w, str(n), a) for w, n, a in by_collector],
            {1, 2},
        )
        + "<h2>Receipts</h2>"
        + receipts_table
        + "<h2>Cancellations approved today</h2>"
        + table(["Receipt", "Receipt date", "Reason", "Amount (₹)"], cancellations, {3})
        + "<h2>Refunds paid today</h2>"
        + table(["Voucher", "Receipt", "Mode", "Amount (₹)"], refunds, {3})
        + '<div class="signatures"><div>Cashier</div><div>Accountant</div>'
        "<div>Principal</div></div>"
        "<footer>Generated by SchoolOS · personal financial data: keep it in the office"
        "</footer></main>"
    )
    return _page(f"Daily collection {day.isoformat()}", REPORT_STYLE, body, for_pdf=False)


__all__ = [
    "MODE_LABELS",
    "RECEIPT_STYLE_A4",
    "RECEIPT_STYLE_A5",
    "REPORT_STYLE",
    "STYLE_CSP",
    "format_date",
    "render_daily_report",
    "render_receipt",
    "render_voucher",
]
