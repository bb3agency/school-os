"""Print pages of the enrolment statistics sheet (FO-06) and the scheme verification attendance
list (FO-11): FR-EXP-013, FR-EXP-022. Pure: no database.

A self-contained A4 HTML page built with :class:`string.Template`: every value goes through
:func:`html.escape` after :func:`app.exports.tables.clean_text` (NFC, control characters
removed, Aadhaar-like numbers masked; invariant 4, defence in depth). No scripts, links, images
or external fonts; the one style block is allowed by hash (:data:`STYLE_CSP`), like the UDISE+
correction letter. The browser's print dialog is the print path (CLAUDE.md §10). English only
(ADR-0036): both pages go to a government office or the school's management.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from string import Template
from typing import Final
from zoneinfo import ZoneInfo

from app.exports.tables import clean_text

IST: Final = ZoneInfo("Asia/Kolkata")

STYLE: Final = """
@page { size: A4; margin: 12mm 10mm 14mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #111; background: #fff; font-family: "Noto Sans", Arial, sans-serif;
  font-size: 9.5pt; line-height: 1.45; }
main { max-width: 190mm; margin: 0 auto; }
.school { font-size: 13pt; font-weight: 700; margin: 0; }
.head p { margin: 0; }
h1 { font-size: 13pt; margin: 3mm 0 2mm; }
h2 { font-size: 10.5pt; margin: 4mm 0 1mm; page-break-after: avoid; }
.unverified { border: 1px dashed #555; padding: 1.5mm 3mm; margin: 2mm 0; font-size: 8.5pt; }
dl.meta { display: grid; grid-template-columns: 40mm 1fr; gap: 0.5mm 4mm; margin: 2mm 0; }
dl.meta dt { font-weight: 600; } dl.meta dd { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 1mm 0 3mm; font-size: 8.5pt; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #555; padding: 1mm 1.4mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
th { background: #f1f1f1; font-weight: 600; }
tr.total td { font-weight: 700; }
.note { font-size: 8.5pt; }
ul.gaps { margin: 1mm 0 3mm; padding-left: 5mm; font-size: 8.5pt; }
.signatures { display: flex; gap: 10mm; margin-top: 14mm; page-break-inside: avoid; }
.signatures div { flex: 1; border-top: 1px solid #111; padding-top: 1.5mm; font-size: 9pt; }
footer { margin-top: 5mm; font-size: 8pt; color: #444; }
@media screen { body { background: #eee; } main { background: #fff; padding: 10mm; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# Content-Security-Policy of the page response: nothing but the one hashed style block.
STYLE_CSP: Final = (
    "default-src 'none'; style-src " + _style_hash(STYLE) + "; img-src 'none'; "
    "font-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _e(value: object) -> str:
    return html.escape(clean_text(value), quote=True)


@dataclass(frozen=True, slots=True)
class PageTable:
    """One printed table: a caption, a header row, body rows and an optional totals row. Columns
    from ``numeric_from`` on are right-aligned numbers."""

    caption: str
    header: Sequence[str]
    rows: Sequence[Sequence[object]]
    total: Sequence[object] | None = None
    numeric_from: int = 1
    note: str | None = None


@dataclass(frozen=True, slots=True)
class PageData:
    school_name: str
    school_address: str
    school_affiliation: str
    place: str
    title: str
    meta: Sequence[tuple[str, str]]
    verified: bool
    unverified_note: str
    tables: Sequence[PageTable]
    printed_at: dt.datetime
    reference: str
    gaps: Sequence[str] = field(default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)
    signatures: Sequence[str] = ("Prepared by", "Head of the institution (signature and seal)")
    footer: str = "Counted from the school's own records."


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
<h1>${title}</h1>
<dl class="meta">
${meta}
</dl>
${unverified}
${gaps}
${tables}
${notes}
<div class="signatures">
${signatures}
</div>
<footer>Printed from SchoolOS on ${printed_at} (${reference}). ${footer}</footer>
</main>
</body>
</html>
"""
)


def _cell(value: object, *, numeric: bool, tag: str = "td") -> str:
    cls = ' class="n"' if numeric else ""
    return f"<{tag}{cls}>{_e(value)}</{tag}>"


def _table(t: PageTable) -> str:
    head = "".join(_cell(h, numeric=i >= t.numeric_from, tag="th") for i, h in enumerate(t.header))
    body = "".join(
        "<tr>" + "".join(_cell(v, numeric=i >= t.numeric_from) for i, v in enumerate(row)) + "</tr>"
        for row in t.rows
    )
    if not t.rows:
        body = f'<tr><td colspan="{len(t.header)}">None.</td></tr>'
    if t.total is not None:
        body += (
            '<tr class="total">'
            + "".join(_cell(v, numeric=i >= t.numeric_from) for i, v in enumerate(t.total))
            + "</tr>"
        )
    note = f'<p class="note">{_e(t.note)}</p>' if t.note else ""
    return (
        f"<h2>{_e(t.caption)}</h2>{note}"
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


def render(data: PageData) -> str:
    """The A4 page (HTML)."""
    address = f"<p>{_e(data.school_address)}</p>" if data.school_address else ""
    affiliation = f"<p>{_e(data.school_affiliation)}</p>" if data.school_affiliation else ""
    meta = "\n".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in data.meta)
    unverified = "" if data.verified else f'<p class="unverified">{_e(data.unverified_note)}</p>'
    gaps = (
        '<h2>Missing in the records</h2><ul class="gaps">'
        + "".join(f"<li>{_e(g)}</li>" for g in data.gaps)
        + "</ul>"
        if data.gaps
        else ""
    )
    notes = "".join(f'<p class="note">{_e(n)}</p>' for n in data.notes)
    place = f": {data.place}" if data.place else ""
    signatures = "".join(f"<div>{_e(s)}</div>" for s in data.signatures)
    signatures += f"<div>Place and date{_e(place)}</div>"
    return _PAGE.substitute(
        title=_e(data.title),
        reference=_e(data.reference),
        style=STYLE,
        school_name=_e(data.school_name),
        address=address,
        affiliation=affiliation,
        meta=meta,
        unverified=unverified,
        gaps=gaps,
        tables="\n".join(_table(t) for t in data.tables),
        notes=notes,
        signatures=signatures,
        printed_at=_e(data.printed_at.astimezone(IST).strftime("%d/%m/%Y %H:%M IST")),
        footer=_e(data.footer),
    )
