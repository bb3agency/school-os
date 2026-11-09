"""The UDISE+ correction request letter to the MEO / MIS coordinator (FO-02; FR-UDI-004,
FR-UDI-005). Pure: no database.

A self-contained A4 HTML page rendered with :class:`string.Template`: every value goes through
:func:`html.escape` and the Aadhaar mask (invariant 4, defence in depth: the letter never holds
an Aadhaar number, only "matches Aadhaar: yes/no"). No scripts, links, images or external
fonts; its one style block is allowed by hash (:data:`STYLE_CSP`), like the parent slip
(``app.dq.slip``). The browser's print dialog is the print path (CLAUDE.md §10). English only
(ADR-0036): the letter goes to a government office.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
from collections.abc import Sequence
from dataclasses import dataclass
from string import Template
from typing import Final
from zoneinfo import ZoneInfo

from app.core.redaction import mask_aadhaar

IST: Final = ZoneInfo("Asia/Kolkata")

STYLE: Final = """
@page { size: A4; margin: 14mm 12mm 16mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #111; background: #fff; font-family: "Noto Sans", Arial, sans-serif;
  font-size: 10pt; line-height: 1.5; }
main { max-width: 186mm; margin: 0 auto; }
.school { font-size: 13pt; font-weight: 700; margin: 0; }
.head p { margin: 0; }
h1 { font-size: 13pt; margin: 4mm 0 2mm; }
h2 { font-size: 11pt; margin: 4mm 0 1mm; page-break-after: avoid; }
.unverified { border: 1px dashed #555; padding: 1.5mm 3mm; margin: 2mm 0; font-size: 9pt; }
dl.meta { display: grid; grid-template-columns: 38mm 1fr; gap: 0.5mm 4mm; margin: 2mm 0; }
dl.meta dt { font-weight: 600; } dl.meta dd { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 1mm 0 3mm; font-size: 9pt; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #555; padding: 1.2mm 1.6mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }
th { background: #f1f1f1; font-weight: 600; }
.count { font-weight: 400; }
.note { font-size: 9pt; }
.signatures { display: flex; gap: 10mm; margin-top: 14mm; page-break-inside: avoid; }
.signatures div { flex: 1; border-top: 1px solid #111; padding-top: 1.5mm; font-size: 9pt; }
footer { margin-top: 5mm; font-size: 8pt; color: #444; }
@media screen { body { background: #eee; } main { background: #fff; padding: 10mm; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# Content-Security-Policy of the letter response: nothing but the one hashed style block.
STYLE_CSP: Final = (
    "default-src 'none'; style-src " + _style_hash(STYLE) + "; img-src 'none'; "
    "font-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


@dataclass(frozen=True, slots=True)
class LetterLine:
    pen: str
    student: str
    admission_no: str
    class_section: str
    field: str
    udise_value: str
    correct_value: str
    source: str
    evidence: str
    matches_aadhaar: str


@dataclass(frozen=True, slots=True)
class LetterGroup:
    label: str
    lines: Sequence[LetterLine]


@dataclass(frozen=True, slots=True)
class LetterData:
    school_name: str
    school_address: str
    school_affiliation: str
    place: str
    title: str
    intro: str
    aadhaar_note: str
    closing: str
    signature: str
    recipient: str
    reference: str
    profile: str
    verified: bool
    groups: Sequence[LetterGroup]
    students: int
    printed_at: dt.datetime


_PAGE: Final = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>${title} · ${reference}</title>
<style>${style}</style>
</head>
<body>
<main>
<div class="head">
<p class="school">${school_name}</p>
${address}
${affiliation}
</div>
<dl class="meta">
<dt>To</dt><dd>${recipient}</dd>
<dt>Reference</dt><dd>${reference}</dd>
<dt>Date</dt><dd>${date}</dd>
<dt>School UDISE code</dt><dd>&nbsp;</dd>
<dt>Checked against</dt><dd>${profile}</dd>
</dl>
<h1>${title}</h1>
${unverified}
<p>${intro}</p>
<p><strong>${total}</strong></p>
${groups}
<p class="note">${aadhaar_note}</p>
<p>${closing}</p>
<div class="signatures">
<div>${signature}</div>
<div>Place and date${place}</div>
</div>
<footer>Printed from SchoolOS on ${printed_at}. This is a request: the school's own records are
changed only through a change request with evidence.</footer>
</main>
</body>
</html>
"""
)

_UNVERIFIED: Final = (
    '<p class="unverified">This request follows the public guidance we could find; the format '
    "your office accepts is not yet confirmed.</p>"
)

_HEADERS: Final = (
    "S. no.",
    "PEN",
    "Student",
    "Adm. no.",
    "Class",
    "Field",
    "Now in UDISE+",
    "Correct value",
    "Source",
    "Evidence held",
    "Matches Aadhaar",
)


def _e(value: str) -> str:
    return html.escape(mask_aadhaar(value), quote=True)


def ist(value: dt.datetime) -> str:
    return value.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _group(group: LetterGroup) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in _HEADERS)
    rows = "\n".join(
        "<tr>"
        + "".join(
            f"<td>{_e(cell)}</td>"
            for cell in (
                str(i),
                line.pen,
                line.student,
                line.admission_no,
                line.class_section,
                line.field,
                line.udise_value,
                line.correct_value,
                line.source,
                line.evidence,
                line.matches_aadhaar,
            )
        )
        + "</tr>"
        for i, line in enumerate(group.lines, start=1)
    )
    count = _plural(len(group.lines), "correction", "corrections")
    return (
        f'<h2>{_e(group.label)} <span class="count">({_e(count)})</span></h2>\n'
        f"<table><thead><tr>{head}</tr></thead><tbody>\n{rows}\n</tbody></table>"
    )


def render(data: LetterData) -> str:
    """The letter: the school, the recipient (a role), one table per group of fields."""
    lines = sum(len(g.lines) for g in data.groups)
    total = (
        f"{_plural(lines, 'correction', 'corrections')} for "
        f"{_plural(data.students, 'student', 'students')}."
    )
    address = f"<p>{_e(data.school_address)}</p>" if data.school_address else ""
    affiliation = f"<p>{_e(data.school_affiliation)}</p>" if data.school_affiliation else ""
    return _PAGE.substitute(
        style=STYLE,
        title=_e(data.title),
        reference=_e(data.reference),
        school_name=_e(data.school_name),
        address=address,
        affiliation=affiliation,
        recipient=_e(data.recipient),
        date=_e(data.printed_at.astimezone(IST).strftime("%d %b %Y")),
        profile=_e(data.profile),
        unverified="" if data.verified else _UNVERIFIED,
        intro=_e(data.intro),
        total=_e(total),
        groups="\n".join(_group(g) for g in data.groups if g.lines),
        aadhaar_note=_e(data.aadhaar_note),
        closing=_e(data.closing),
        signature=_e(data.signature),
        place=f": {_e(data.place)}" if data.place else "",
        printed_at=_e(ist(data.printed_at)),
    )


__all__ = ["STYLE_CSP", "LetterData", "LetterGroup", "LetterLine", "render"]
