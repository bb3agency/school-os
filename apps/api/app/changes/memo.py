"""Printable correction memo for the paper register (FR-CR-005, US-601 AC4).

A self-contained, bilingual (English + Telugu) A4 HTML page rendered with
:class:`string.Template`: every value is passed through :func:`html.escape` before substitution,
so a name such as ``<script>`` renders as text. The page has no scripts, links, images or
external fonts; its only style block is allowed by hash (:data:`STYLE_CSP`). PDF rendering with
headless Chromium arrives with exports (M1 exports module) and reuses this HTML.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
from dataclasses import asdict, dataclass
from string import Template
from typing import Final
from zoneinfo import ZoneInfo

IST: Final = ZoneInfo("Asia/Kolkata")

STATUS_LABELS: Final[dict[str, tuple[str, str]]] = {
    "pending": ("Waiting for approval", "ఆమోదం కోసం వేచి ఉంది"),
    "approved": ("Approved", "ఆమోదించబడింది"),
    "rejected": ("Not approved", "ఆమోదించబడలేదు"),
    "expired": ("Expired without a decision", "నిర్ణయం లేకుండా గడువు ముగిసింది"),
    "cancelled": ("Cancelled by the requester", "అభ్యర్థించినవారు రద్దు చేశారు"),
}
SOURCE_LABELS: Final[dict[str, tuple[str, str]]] = {
    "admission_register": ("Admission register", "ప్రవేశ రిజిస్టర్"),
    "aadhaar_as_printed": ("Aadhaar (as printed)", "ఆధార్ (ముద్రించినట్లు)"),
    "udise_plus": ("UDISE+", "UDISE+"),
    "board_registration": ("Board registration", "బోర్డు నమోదు"),
    "birth_certificate": ("Birth certificate", "జనన ధృవీకరణ పత్రం"),
    "parent_form": ("Parent form", "తల్లిదండ్రుల ఫారం"),
    "tc_incoming": ("Transfer certificate (incoming)", "బదిలీ ధృవీకరణ పత్రం (వచ్చినది)"),
    "manual_entry": ("Entered by the office", "కార్యాలయం నమోదు చేసినది"),
}

INSTRUCTIONS_EN: Final = (
    "Correct the paper register only because this request is approved. Strike through the old "
    "entry with a single line so it stays readable, write the new value beside it and add the "
    "memo reference. The approver signs and dates the correction. File this memo with a copy of "
    "the evidence document."
)
INSTRUCTIONS_TE: Final = (
    "ఈ అభ్యర్థన ఆమోదించబడినందున మాత్రమే కాగితపు రిజిస్టర్‌ను సరిచేయండి. పాత నమోదును చదవగలిగేలా "
    "ఒకే గీతతో కొట్టివేసి, దాని పక్కన కొత్త విలువను, ఈ మెమో సూచిక సంఖ్యను రాయండి. ఆమోదించినవారు "
    "సవరణపై సంతకం చేసి తేదీ వేయాలి. ఈ మెమోను ఆధార పత్రం ప్రతితో కలిపి భద్రపరచండి."
)
NOT_APPROVED_EN: Final = "This correction is not approved. Do not change the register."
NOT_APPROVED_TE: Final = "ఈ సవరణ ఆమోదించబడలేదు. రిజిస్టర్‌ను మార్చవద్దు."

STYLE: Final = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #111; background: #fff;
  font-family: "Noto Sans", "Noto Sans Telugu", Arial, sans-serif; font-size: 11pt;
  line-height: 1.6; }
main { max-width: 178mm; margin: 0 auto; padding: 8mm 0; }
h1 { font-size: 15pt; margin: 0 0 2mm; line-height: 1.5; }
.school { font-size: 13pt; font-weight: 600; margin: 0; }
.ref { margin: 0 0 4mm; color: #333; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0 5mm; page-break-inside: avoid; }
th, td { border: 1px solid #555; padding: 2mm 3mm; text-align: left; vertical-align: top;
  line-height: 1.7; overflow-wrap: anywhere; }
th { width: 38%; background: #f1f1f1; font-weight: 600; }
.te { display: block; font-weight: 400; color: #333; }
.notice { border: 2px solid #111; padding: 3mm 4mm; margin: 4mm 0; line-height: 1.7; }
.warning { border-color: #a00; color: #a00; font-weight: 600; }
.signatures { display: flex; gap: 12mm; margin-top: 16mm; page-break-inside: avoid; }
.signatures div { flex: 1; border-top: 1px solid #111; padding-top: 2mm; line-height: 1.6; }
footer { margin-top: 8mm; font-size: 9pt; color: #444; }
@media screen { body { background: #eee; } main { background: #fff; padding: 12mm; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# Content-Security-Policy for the memo response: nothing but the one hashed style block.
STYLE_CSP: Final = (
    "default-src 'none'; style-src " + _style_hash(STYLE) + "; img-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

_PAGE: Final = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Correction memo ${reference}</title>
<style>${style}</style>
</head>
<body>
<main>
<p class="school">${school_name}</p>
<h1>Correction memo for the student record
<span class="te">విద్యార్థి రికార్డు సవరణ మెమో</span></h1>
<p class="ref">Memo reference / మెమో సూచిక: <strong>${reference}</strong></p>
<table>
<tr>
<th>Student <span class="te">విద్యార్థి</span></th>
<td>${student_name}</td></tr>
<tr>
<th>Admission number <span class="te">ప్రవేశ సంఖ్య</span></th>
<td>${admission_no}</td></tr>
<tr>
<th>Class and section <span class="te">తరగతి, సెక్షన్</span></th>
<td>${class_section}</td></tr>
<tr>
<th>Field <span class="te">అంశం</span></th>
<td>${attribute_en} <span class="te">${attribute_te}</span></td></tr>
<tr>
<th>Record <span class="te">రికార్డు</span></th>
<td>${source_en} <span class="te">${source_te}</span></td></tr>
<tr>
<th>Old value <span class="te">పాత విలువ</span></th>
<td>${old_value}</td></tr>
<tr>
<th>New value <span class="te">కొత్త విలువ</span></th>
<td>${new_value}</td></tr>
<tr>
<th>Reason <span class="te">కారణం</span></th>
<td>${reason}</td></tr>
<tr>
<th>Evidence document <span class="te">ఆధార పత్రం</span></th>
<td>${evidence}</td></tr>
<tr>
<th>Requested by <span class="te">అభ్యర్థించినవారు</span></th>
<td>${requested_by} · ${requested_at}</td></tr>
<tr>
<th>Decision <span class="te">నిర్ణయం</span></th>
<td>${status_en} <span class="te">${status_te}</span></td></tr>
<tr>
<th>Decided by <span class="te">నిర్ణయించినవారు</span></th>
<td>${decided_by} · ${decided_at}</td></tr>
<tr>
<th>Decision note <span class="te">నిర్ణయ వివరణ</span></th>
<td>${decision_note}</td></tr>
</table>
<div class="${notice_class}"><p>${notice_en}</p><p>${notice_te}</p></div>
<div class="signatures">
<div>Register corrected by (name, signature, date)
<span class="te">రిజిస్టర్ సరిచేసినవారు (పేరు, సంతకం, తేదీ)</span></div>
<div>Approver (signature, date)
<span class="te">ఆమోదించినవారు (సంతకం, తేదీ)</span></div>
</div>
<footer>Printed from SchoolOS on ${printed_at}. Reference ${request_id}.</footer>
</main>
</body>
</html>
"""
)


@dataclass(frozen=True, slots=True)
class MemoData:
    """Everything shown on the memo, as plain (unescaped) text."""

    school_name: str
    reference: str
    request_id: str
    student_name: str
    admission_no: str
    class_section: str
    attribute_en: str
    attribute_te: str
    source: str
    old_value: str
    new_value: str
    reason: str
    evidence: str
    requested_by: str
    requested_at: dt.datetime
    status: str
    decided_by: str
    decided_at: dt.datetime | None
    decision_note: str
    printed_at: dt.datetime


def ist(value: dt.datetime | None) -> str:
    """``27 Sep 2026, 14:05 IST`` (or an em dash)."""
    if value is None:
        return "—"
    return value.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def render(data: MemoData) -> str:
    """The memo page; every substituted value is HTML-escaped (quotes included)."""
    fields = {k: v for k, v in asdict(data).items() if not isinstance(v, dt.datetime | None)}
    status_en, status_te = STATUS_LABELS[data.status]
    source_en, source_te = SOURCE_LABELS.get(data.source, (data.source, data.source))
    approved = data.status == "approved"
    values: dict[str, str] = {
        **{k: str(v) for k, v in fields.items() if k not in {"source", "status"}},
        "requested_at": ist(data.requested_at),
        "decided_at": ist(data.decided_at),
        "printed_at": ist(data.printed_at),
        "status_en": status_en,
        "status_te": status_te,
        "source_en": source_en,
        "source_te": source_te,
        "notice_class": "notice" if approved else "notice warning",
        "notice_en": INSTRUCTIONS_EN if approved else NOT_APPROVED_EN,
        "notice_te": INSTRUCTIONS_TE if approved else NOT_APPROVED_TE,
    }
    escaped = {k: html.escape(v, quote=True) for k, v in values.items()}
    return _PAGE.substitute(escaped, style=STYLE)
