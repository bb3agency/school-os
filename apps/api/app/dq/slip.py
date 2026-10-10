"""Parent verification slip for board and portal readiness (FR-DQ-045, US-505, ADR-0040).

A self-contained A4 HTML page, one student per sheet, rendered with :class:`string.Template`:
every value goes through :func:`html.escape` and the Aadhaar mask (invariant 4, defence in
depth: the slip shows Aadhaar-as-printed name, date of birth and gender, never an Aadhaar
number, not even the last four digits). The page has no scripts, links, images or external
fonts; its only style block is allowed by hash (:data:`STYLE_CSP`), like the correction memo
(``app.changes.memo``). The same HTML prints to PDF with :mod:`app.core.pdf` when a worker job
needs it; the browser's print dialog is the first-class path (CLAUDE.md §10).

English first (ADR-0036): while Telugu is hidden the slip is English only; the Telugu lines
stay in the template (``<span class="te">``), dormant.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from string import Template
from typing import Final
from zoneinfo import ZoneInfo

from app.core.languages import telugu_enabled
from app.core.redaction import mask_aadhaar

IST: Final = ZoneInfo("Asia/Kolkata")

STATUS_LABELS: Final[dict[str, tuple[str, str]]] = {
    "ready": ("Ready: all records match", "సిద్ధం: అన్ని రికార్డులు సరిపోలుతున్నాయి"),
    "needs_parent": ("Parent action needed", "తల్లిదండ్రుల చర్య అవసరం"),
    "needs_school": ("School action needed", "పాఠశాల చర్య అవసరం"),
    "blocked": ("Needs a decision", "నిర్ణయం అవసరం"),
}

STYLE: Final = """
@page { size: A4; margin: 14mm 14mm 16mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #111; background: #fff;
  font-family: "Noto Sans", "Noto Sans Telugu", Arial, sans-serif; font-size: 10.5pt;
  line-height: 1.6; }
main { max-width: 182mm; margin: 0 auto; }
section.slip { padding: 4mm 0; break-after: page; page-break-after: always; }
section.slip:last-child { break-after: auto; page-break-after: auto; }
.school { font-size: 13pt; font-weight: 700; margin: 0; }
h1 { font-size: 14pt; margin: 1mm 0 2mm; line-height: 1.5; }
.te { display: block; font-weight: 400; color: #333; }
.unverified { border: 1px dashed #555; padding: 1.5mm 3mm; margin: 2mm 0; font-size: 9pt; }
dl.who { display: grid; grid-template-columns: 34mm 1fr; gap: 0.5mm 4mm; margin: 2mm 0 3mm; }
dl.who dt { font-weight: 600; } dl.who dd { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 3mm; page-break-inside: avoid; }
th, td { border: 1px solid #555; padding: 1.6mm 2.2mm; text-align: left; vertical-align: top;
  line-height: 1.7; overflow-wrap: anywhere; }
th { background: #f1f1f1; font-weight: 600; }
td.differs { border: 2px solid #111; font-weight: 700; }
td.differs::after { content: " \\2260"; }
.status { font-weight: 700; margin: 2mm 0; }
ol.issues { margin: 1mm 0 3mm 5mm; padding: 0; }
ol.issues li { margin: 0 0 1.5mm; }
.owner { display: block; font-style: italic; }
.declaration { border: 1px solid #111; padding: 2.5mm 3.5mm; margin: 3mm 0; }
.lines { margin: 2mm 0 0; }
.line { border-bottom: 1px solid #111; height: 8mm; }
.signatures { display: flex; gap: 8mm; margin-top: 12mm; page-break-inside: avoid; }
.signatures div { flex: 1; border-top: 1px solid #111; padding-top: 1.5mm; font-size: 9.5pt; }
footer { margin-top: 5mm; font-size: 8.5pt; color: #444; }
@media screen { body { background: #eee; } main { background: #fff; padding: 10mm; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# Content-Security-Policy of the slip response: nothing but the one hashed style block.
STYLE_CSP: Final = (
    "default-src 'none'; style-src " + _style_hash(STYLE) + "; img-src 'none'; "
    "font-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


@dataclass(frozen=True, slots=True)
class SlipText:
    en: str
    te: str = ""


@dataclass(frozen=True, slots=True)
class SlipRow:
    """One field: its label and the value each record holds (already masked when hidden)."""

    label: SlipText
    cells: Sequence[str]
    differs: Sequence[bool]


@dataclass(frozen=True, slots=True)
class SlipIssue:
    field: SlipText
    text: SlipText
    owner: SlipText


@dataclass(frozen=True, slots=True)
class SlipStudent:
    name: str
    admission_no: str
    class_section: str
    status: str
    rows: Sequence[SlipRow]
    issues: Sequence[SlipIssue] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SlipData:
    school_name: str
    profile: SlipText
    verified: bool
    columns: Sequence[SlipText]
    students: Sequence[SlipStudent]
    printed_at: dt.datetime


_PAGE: Final = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Parent verification slips · ${profile_en}</title>
<style>${style}</style>
</head>
<body>
<main>
${slips}
</main>
</body>
</html>
"""
)

_SLIP: Final = Template(
    """<section class="slip">
<p class="school">${school_name}</p>
<h1>Parent verification slip: ${profile_en}
<span class="te">తల్లిదండ్రుల ధృవీకరణ పత్రం: ${profile_te}</span></h1>
${unverified}
<dl class="who">
<dt>Student</dt><dd>${name}</dd>
<dt>Admission number</dt><dd>${admission_no}</dd>
<dt>Class and section</dt><dd>${class_section}</dd>
</dl>
<p>Boards and portals accept a child's details only when every record matches letter for
letter, including spaces. Please check each value below against the child's documents.
<span class="te">ప్రతి రికార్డు అక్షరం అక్షరం, ఖాళీలతో సహా సరిపోలితేనే బోర్డులు, పోర్టళ్లు వివరాలను
అంగీకరిస్తాయి. దయచేసి కింది ప్రతి విలువను పిల్లల పత్రాలతో సరిచూడండి.</span></p>
<table>
<thead><tr><th>Detail <span class="te">వివరం</span></th>${headers}</tr></thead>
<tbody>
${rows}
</tbody>
</table>
<p class="status">${status_en} <span class="te">${status_te}</span></p>
${issues}
<div class="declaration">
<p>I have checked these details. The correct details, as on the child's birth certificate and
Aadhaar card, are:
<span class="te">నేను ఈ వివరాలను సరిచూశాను. పిల్లల జనన ధృవీకరణ పత్రం, ఆధార్ కార్డు ప్రకారం సరైన
వివరాలు:</span></p>
<div class="lines"><div class="line"></div><div class="line"></div></div>
</div>
<div class="signatures">
<div>Parent or guardian: name, signature, date
<span class="te">తల్లి/తండ్రి లేదా సంరక్షకులు: పేరు, సంతకం, తేదీ</span></div>
<div>Class teacher: signature, date
<span class="te">తరగతి ఉపాధ్యాయులు: సంతకం, తేదీ</span></div>
</div>
<footer>Printed from SchoolOS on ${printed_at}. Corrections to the school's records are made
only through a change request with evidence.</footer>
</section>"""
)

_UNVERIFIED: Final = (
    '<p class="unverified">This check follows the public guidance we could find; the official '
    'format is not yet confirmed. <span class="te">ఈ తనిఖీ అందుబాటులో ఉన్న ప్రజా మార్గదర్శకాల ప్రకారం '
    "చేయబడింది; అధికారిక ఫార్మాట్ ఇంకా నిర్ధారించబడలేదు.</span></p>"
)


def _e(value: str) -> str:
    return html.escape(mask_aadhaar(value), quote=True)


def ist(value: dt.datetime) -> str:
    return value.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def _text(text: SlipText) -> str:
    te = f' <span class="te">{_e(text.te)}</span>' if text.te else ""
    return f"{_e(text.en)}{te}"


def _student(data: SlipData, student: SlipStudent) -> str:
    headers = "".join(f"<th>{_text(c)}</th>" for c in data.columns)
    rows = "\n".join(
        "<tr><th>"
        + _text(row.label)
        + "</th>"
        + "".join(
            f'<td class="differs">{_e(cell)}</td>' if differs else f"<td>{_e(cell)}</td>"
            for cell, differs in zip(row.cells, row.differs, strict=True)
        )
        + "</tr>"
        for row in student.rows
    )
    if student.issues:
        issues = (
            '<ol class="issues">'
            + "".join(
                f"<li><strong>{_text(i.field)}</strong>: {_text(i.text)}"
                f'<span class="owner">{_text(i.owner)}</span></li>'
                for i in student.issues
            )
            + "</ol>"
        )
    else:
        issues = ""
    status_en, status_te = STATUS_LABELS[student.status]
    return _SLIP.substitute(
        school_name=_e(data.school_name),
        profile_en=_e(data.profile.en),
        profile_te=_e(data.profile.te),
        unverified="" if data.verified else _UNVERIFIED,
        name=_e(student.name),
        admission_no=_e(student.admission_no),
        class_section=_e(student.class_section),
        headers=headers,
        rows=rows,
        status_en=_e(status_en),
        status_te=_e(status_te),
        issues=issues,
        printed_at=_e(ist(data.printed_at)),
    )


# Every Telugu line sits in a ``<span class="te">`` whose content is escaped text (never a tag),
# so dropping those spans leaves the English page.
_TE_SPAN: Final = re.compile(r'\s*<span class="te">[^<]*</span>')


def render(data: SlipData) -> str:
    """The slips page (one ``section.slip`` per student, a page break between them)."""
    slips = "\n".join(_student(data, s) for s in data.students)
    page = _PAGE.substitute(profile_en=_e(data.profile.en), style=STYLE, slips=slips)
    return page if telugu_enabled() else _TE_SPAN.sub("", page)


__all__ = [
    "STATUS_LABELS",
    "STYLE_CSP",
    "SlipData",
    "SlipIssue",
    "SlipRow",
    "SlipStudent",
    "SlipText",
    "render",
]
