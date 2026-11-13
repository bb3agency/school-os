"""Fix-owner letters from readiness differences (FA-08; FR-DQ-049..FR-DQ-060). Pure: no
database.

Two A4 print views rendered with :class:`string.Template` from the versioned words in
``app/dq/config/fix_letters.yaml``:

- :func:`render_parent`: one letter per student, one per sheet, asking the parent to update the
  child's Aadhaar at an Aadhaar centre so it matches the school's record. It carries only what
  the parent needs: the child's name, admission number and class, the field, the value in the
  school's record and what differs (words such as "a word is missing", never the Aadhaar
  value: the parent holds the card, and Aadhaar-as-printed is restricted, C3).
- :func:`render_board`: one letter to the board office listing the registration corrections
  (PEN, student, admission number, class, field, the value in the registration, the correct
  value, its record and the evidence held).

Every value goes through :func:`html.escape` and the Aadhaar mask (invariant 4, defence in
depth: no letter ever holds an Aadhaar number). No scripts, links, images or external fonts;
the one style block is allowed by hash (:data:`STYLE_CSP`), like the UDISE+ letter
(``app.dq.udise_letter``). The browser's print dialog is the print path (CLAUDE.md §10).
English only (ADR-0036). No AI writes any of it.
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

from app.core.redaction import mask_aadhaar
from app.dq.udise_letter import IST, ist

STYLE: Final = """
@page { size: A4; margin: 14mm 14mm 16mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #111; background: #fff; font-family: "Noto Sans", Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.55; }
main { max-width: 184mm; margin: 0 auto; }
section.letter { padding: 2mm 0; break-after: page; page-break-after: always; }
section.letter:last-child { break-after: auto; page-break-after: auto; }
.school { font-size: 13pt; font-weight: 700; margin: 0; }
.head p { margin: 0; }
h1 { font-size: 13pt; margin: 4mm 0 2mm; }
h2 { font-size: 11pt; margin: 4mm 0 1mm; }
.unverified { border: 1px dashed #555; padding: 1.5mm 3mm; margin: 2mm 0; font-size: 9pt; }
dl.meta { display: grid; grid-template-columns: 36mm 1fr; gap: 0.5mm 4mm; margin: 2mm 0; }
dl.meta dt { font-weight: 600; } dl.meta dd { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 1mm 0 3mm; font-size: 9.5pt; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #555; padding: 1.4mm 1.8mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }
th { background: #f1f1f1; font-weight: 600; }
ol.steps { margin: 1mm 0 3mm 5mm; padding: 0; }
ol.steps li { margin: 0 0 1.2mm; }
.note { font-size: 9.5pt; }
.reply { font-weight: 700; }
.signatures { display: flex; gap: 10mm; margin-top: 12mm; page-break-inside: avoid; }
.signatures div { flex: 1; border-top: 1px solid #111; padding-top: 1.5mm; font-size: 9pt; }
.ack { border-top: 1px dashed #555; margin-top: 8mm; padding-top: 3mm; page-break-inside: avoid; }
footer { margin-top: 5mm; font-size: 8pt; color: #444; }
@media screen { body { background: #eee; } main { background: #fff; padding: 10mm; }
  section.letter + section.letter { border-top: 4mm solid #eee; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# Content-Security-Policy of both print views: nothing but the one hashed style block.
STYLE_CSP: Final = (
    "default-src 'none'; style-src " + _style_hash(STYLE) + "; img-src 'none'; "
    "font-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

_UNVERIFIED: Final = (
    '<p class="unverified">This letter follows the public guidance we could find; the format is '
    "not yet confirmed.</p>"
)


@dataclass(frozen=True, slots=True)
class Letterhead:
    school_name: str
    address: str
    affiliation: str
    place: str


# --- parent letters -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParentLine:
    field: str
    correct_value: str
    source: str
    differs: str


@dataclass(frozen=True, slots=True)
class ParentLetter:
    student: str
    admission_no: str
    class_section: str
    reference: str
    lines: Sequence[ParentLine]


@dataclass(frozen=True, slots=True)
class ParentWords:
    title: str
    salutation: str
    intro: str
    steps: Sequence[str]
    reply_by: str
    privacy_note: str
    wrong_record_note: str
    signature: str
    acknowledgement: str


@dataclass(frozen=True, slots=True)
class ParentData:
    head: Letterhead
    words: ParentWords
    profile: str
    verified: bool
    reply_on: dt.date
    letters: Sequence[ParentLetter]
    printed_at: dt.datetime


_DOC: Final = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>${title}</title>
<style>${style}</style>
</head>
<body>
<main>
${body}
</main>
</body>
</html>
"""
)

_PARENT: Final = Template(
    """<section class="letter">
${head}
<dl class="meta">
<dt>To</dt><dd>The parent or guardian of ${student}</dd>
<dt>Class</dt><dd>${class_section}</dd>
<dt>Admission no.</dt><dd>${admission_no}</dd>
<dt>Reference</dt><dd>${reference}</dd>
<dt>Date</dt><dd>${date}</dd>
<dt>Checked for</dt><dd>${profile}</dd>
</dl>
<h1>${title}</h1>
${unverified}
<p>${salutation}</p>
<p>${intro}</p>
<table><thead><tr><th>Detail</th><th>In the school's record</th><th>Record</th>
<th>What differs on Aadhaar</th></tr></thead><tbody>
${rows}
</tbody></table>
<ol class="steps">
${steps}
</ol>
<p class="reply">${reply_by} ${reply_on}.</p>
<p class="note">${wrong_record_note}</p>
<p class="note">${privacy_note}</p>
<div class="signatures"><div>${signature}</div><div>Place and date${place}</div></div>
<div class="ack">
<p>${acknowledgement} ${student}, ${class_section}, ${reference}</p>
<div class="signatures"><div>Signature of the parent or guardian</div><div>Date</div></div>
</div>
<footer>Printed from SchoolOS on ${printed_at}. The school's own records are changed only through
a change request with evidence.</footer>
</section>"""
)


def _e(value: str) -> str:
    return html.escape(mask_aadhaar(value), quote=True)


def _date(value: dt.date) -> str:
    return value.strftime("%d %b %Y")


def _head(head: Letterhead) -> str:
    parts = [f'<p class="school">{_e(head.school_name)}</p>']
    if head.address:
        parts.append(f"<p>{_e(head.address)}</p>")
    if head.affiliation:
        parts.append(f"<p>{_e(head.affiliation)}</p>")
    return '<div class="head">' + "".join(parts) + "</div>"


def _parent(data: ParentData, letter: ParentLetter) -> str:
    words = data.words
    rows = "\n".join(
        "<tr>"
        + "".join(
            f"<td>{_e(cell)}</td>"
            for cell in (line.field, line.correct_value, line.source, line.differs)
        )
        + "</tr>"
        for line in letter.lines
    )
    return _PARENT.substitute(
        head=_head(data.head),
        student=_e(letter.student),
        class_section=_e(letter.class_section),
        admission_no=_e(letter.admission_no),
        reference=_e(letter.reference),
        date=_e(data.printed_at.astimezone(IST).strftime("%d %b %Y")),
        profile=_e(data.profile),
        title=_e(words.title),
        unverified="" if data.verified else _UNVERIFIED,
        salutation=_e(words.salutation),
        intro=_e(words.intro),
        rows=rows,
        steps="\n".join(f"<li>{_e(step)}</li>" for step in words.steps),
        reply_by=_e(words.reply_by),
        reply_on=_e(_date(data.reply_on)),
        wrong_record_note=_e(words.wrong_record_note),
        privacy_note=_e(words.privacy_note),
        signature=_e(words.signature),
        place=f": {_e(data.head.place)}" if data.head.place else "",
        acknowledgement=_e(words.acknowledgement),
        printed_at=_e(ist(data.printed_at)),
    )


def render_parent(data: ParentData) -> str:
    """One letter per student, one per A4 sheet."""
    body = "\n".join(_parent(data, letter) for letter in data.letters)
    return _DOC.substitute(title=_e(data.words.title), style=STYLE, body=body)


# --- board letter -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BoardLine:
    pen: str
    student: str
    admission_no: str
    class_section: str
    field: str
    board_value: str
    correct_value: str
    source: str
    evidence: str


@dataclass(frozen=True, slots=True)
class BoardWords:
    title: str
    intro: str
    udise_note: str
    closing: str
    signature: str


@dataclass(frozen=True, slots=True)
class BoardData:
    head: Letterhead
    words: BoardWords
    recipient: str
    reference: str
    profile: str
    verified: bool
    lines: Sequence[BoardLine]
    students: int
    printed_at: dt.datetime


_BOARD: Final = Template(
    """<section class="letter">
${head}
<dl class="meta">
<dt>To</dt><dd>${recipient}</dd>
<dt>Reference</dt><dd>${reference}</dd>
<dt>Date</dt><dd>${date}</dd>
<dt>Checked against</dt><dd>${profile}</dd>
</dl>
<h1>${title}</h1>
${unverified}
<p>${intro}</p>
<p><strong>${total}</strong></p>
<table><thead><tr>${headers}</tr></thead><tbody>
${rows}
</tbody></table>
<p class="note">${udise_note}</p>
<p>${closing}</p>
<div class="signatures"><div>${signature}</div><div>Place and date${place}</div></div>
<footer>Printed from SchoolOS on ${printed_at}. This is a request: the school's own records are
changed only through a change request with evidence.</footer>
</section>"""
)

_BOARD_HEADERS: Final = (
    "S. no.",
    "PEN",
    "Student",
    "Adm. no.",
    "Class",
    "Field",
    "Now in the registration",
    "Correct value",
    "Source",
    "Evidence held",
)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def render_board(data: BoardData) -> str:
    """The letter to the board office: one table of the registration corrections."""
    words = data.words
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
                line.board_value,
                line.correct_value,
                line.source,
                line.evidence,
            )
        )
        + "</tr>"
        for i, line in enumerate(data.lines, start=1)
    )
    total = (
        f"{_plural(len(data.lines), 'correction', 'corrections')} for "
        f"{_plural(data.students, 'student', 'students')}."
    )
    body = _BOARD.substitute(
        head=_head(data.head),
        recipient=_e(data.recipient),
        reference=_e(data.reference),
        date=_e(data.printed_at.astimezone(IST).strftime("%d %b %Y")),
        profile=_e(data.profile),
        title=_e(words.title),
        unverified="" if data.verified else _UNVERIFIED,
        intro=_e(words.intro),
        total=_e(total),
        headers="".join(f"<th>{_e(h)}</th>" for h in _BOARD_HEADERS),
        rows=rows,
        udise_note=_e(words.udise_note),
        closing=_e(words.closing),
        signature=_e(words.signature),
        place=f": {_e(data.head.place)}" if data.head.place else "",
        printed_at=_e(ist(data.printed_at)),
    )
    return _DOC.substitute(title=_e(f"{words.title} · {data.reference}"), style=STYLE, body=body)


__all__ = [
    "STYLE_CSP",
    "BoardData",
    "BoardLine",
    "BoardWords",
    "Letterhead",
    "ParentData",
    "ParentLetter",
    "ParentLine",
    "ParentWords",
    "render_board",
    "render_parent",
]
