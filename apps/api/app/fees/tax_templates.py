"""The tuition fee certificate page (HTML/CSS, template ``v1``; FM-06; FR-FEE-053; CLAUDE.md §10
"print is first-class").

Pure module: the service passes the frozen certificate content (exactly what was hashed at issue)
and gets a self-contained A4 page back. Every value goes through :func:`html.escape` and the
Aadhaar mask. No scripts, links or images; the only style block is allowed by hash in
:data:`STYLE_CSP` when served to a browser. English (ADR-0036).
"""

from __future__ import annotations

import base64
import hashlib
import html
from collections.abc import Mapping
from typing import Any, Final

from app.core.redaction import mask_aadhaar
from app.fees.templates import format_date

STYLE: Final = """
@page { size: A4; margin: 16mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { color: #111; background: #fff; font-family: "Noto Sans", Arial, sans-serif;
  font-size: 11pt; line-height: 1.6; }
main { position: relative; border: 1.5px solid #222; padding: 8mm 10mm; }
.watermark { position: absolute; top: 40%; left: 0; right: 0; text-align: center;
  transform: rotate(-20deg); font-size: 40pt; font-weight: 700; color: rgba(150, 0, 0, 0.18); }
header { text-align: center; border-bottom: 1px solid #222; padding-bottom: 3mm; }
.school { font-size: 16pt; font-weight: 700; margin: 0; }
.address, .affiliation { margin: 0; font-size: 10pt; }
h1 { text-align: center; font-size: 14pt; margin: 4mm 0 2mm; letter-spacing: 0.06em; }
.meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2mm 8mm;
  margin: 1mm 0 3mm; }
p { margin: 2mm 0; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #555; padding: 1mm 2mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }
th { background: #f0f0f0; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tfoot td { font-weight: 700; }
.words { font-style: italic; }
.legal { font-size: 9.5pt; }
.disclaimer { font-size: 9pt; color: #333; border: 1px solid #999; padding: 2mm 3mm; }
.superseded { border: 2px solid #900; color: #900; padding: 1mm 2mm; font-weight: 700; }
.signatures { display: flex; justify-content: space-between; gap: 12mm; margin-top: 18mm; }
.signatures div { flex: 1; border-top: 1px solid #111; padding-top: 1mm; text-align: center; }
footer { margin-top: 3mm; font-size: 7.5pt; color: #444; }
@media screen { body { background: #e9e9e9; } main { max-width: 190mm; margin: 6mm auto;
  background: #fff; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


STYLE_CSP: Final = (
    f"default-src 'none'; style-src {_style_hash(STYLE)}; img-src 'none'; font-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_PDF_CSP: Final = "default-src 'none'; style-src 'unsafe-inline'"


def _e(value: object) -> str:
    text = "" if value is None else str(value)
    return html.escape(mask_aadhaar(text), quote=True)


def render_certificate(
    content: Mapping[str, Any], *, superseded_by: str | None = None, for_pdf: bool = False
) -> str:
    """The certificate page from its frozen content. A superseded certificate keeps its serial and
    prints "SUPERSEDED by <serial>" (FR-FEE-052)."""
    school: Mapping[str, Any] = content.get("school") or {}
    student: Mapping[str, Any] = content.get("student") or {}
    legal: Mapping[str, Any] = content.get("legal") or {}
    head = [f'<p class="school">{_e(school.get("name"))}</p>']
    for key in ("address", "affiliation"):
        if school.get(key):
            head.append(f'<p class="{key}">{_e(school.get(key))}</p>')
    rows = "".join(
        f"<tr><td>{_e(r.get('receipt_number'))}</td>"
        f"<td>{_e(format_date(r.get('receipt_date')))}</td>"
        f'<td class="num">{_e(r.get("amount_text"))}</td>'
        f'<td class="num">{_e(r.get("refunded_text"))}</td></tr>'
        for r in content.get("receipts") or []
    )
    period = (
        f"{_e(format_date(content.get('period_from')))} to "
        f"{_e(format_date(content.get('period_to')))}"
    )
    if content.get("in_progress"):
        period += f" (paid up to {_e(format_date(content.get('as_of')))}; the year is in progress)"
    mark = watermark = ""
    if superseded_by is not None:
        mark = f'<p class="superseded">SUPERSEDED by certificate {_e(superseded_by)}</p>'
        watermark = '<div class="watermark" aria-hidden="true">SUPERSEDED</div>'
    heads = ", ".join(str(h) for h in content.get("heads") or [])
    body = (
        f"<main>{watermark}<header>{''.join(head)}</header>"
        f"<h1>{_e(content.get('title'))}</h1>"
        '<div class="meta">'
        f"<span>Certificate no.: <strong>{_e(content.get('serial_number'))}</strong></span>"
        f"<span>Date: <strong>{_e(format_date(content.get('issued_on')))}</strong></span>"
        "</div>"
        '<div class="meta">'
        f"<span>Student: <strong>{_e(student.get('name'))}</strong></span>"
        f"<span>Admission no.: <strong>{_e(student.get('admission_no'))}</strong></span>"
        f"<span>Class: <strong>{_e(student.get('class_section'))}</strong></span>"
        "</div>"
        f"{mark}"
        f"<p>Financial year <strong>{_e(content.get('financial_year'))}</strong>: {period}.</p>"
        f"<p>{_e(legal.get('certifies'))}</p>"
        f"<p>Fee heads counted as tuition: {_e(heads)}.</p>"
        "<table><thead><tr><th>Receipt no.</th><th>Receipt date</th>"
        '<th class="num">Tuition paid (₹)</th><th class="num">Refunded (₹)</th></tr></thead>'
        f"<tbody>{rows}</tbody>"
        "<tfoot>"
        f'<tr><td colspan="2">Total paid</td><td class="num">{_e(content.get("paid_text"))}'
        f'</td><td class="num">{_e(content.get("refunded_text"))}</td></tr>'
        f'<tr><td colspan="2">Tuition fees paid, net of refunds</td><td class="num" colspan="2">'
        f"{_e(content.get('amount_text'))}</td></tr>"
        "</tfoot></table>"
        f'<p class="words">{_e(content.get("amount_in_words"))}</p>'
        f'<p class="legal">For: {_e(legal.get("section_reference"))}.</p>'
        f'<p class="disclaimer">{_e(legal.get("disclaimer"))}</p>'
        '<div class="signatures">'
        f"<div>{_e(content.get('signatory_label'))}</div><div>School seal</div></div>"
        f"<footer>Computer-generated certificate · SchoolOS · {_e(content.get('financial_year'))}"
        f" · template {_e(content.get('template_version'))}</footer></main>"
    )
    csp = f'<meta http-equiv="Content-Security-Policy" content="{_PDF_CSP}">' if for_pdf else ""
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"{csp}<title>Tuition fee certificate {_e(content.get('serial_number'))}</title>"
        f"<style>{STYLE}</style></head><body>{body}</body></html>"
    )


__all__ = ["STYLE", "STYLE_CSP", "render_certificate"]
