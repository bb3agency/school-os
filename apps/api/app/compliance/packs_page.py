"""The printable index of an inspection pack (FC-07, FO-12; FR-COMP-038..FR-COMP-040). Pure: no
database.

A self-contained A4 HTML page rendered with :class:`string.Template`: every value goes through
:func:`html.escape` and the Aadhaar mask (defence in depth: the index holds labels, states and
dates only). No scripts, links, images or external fonts; its one style block is allowed by
hash (:data:`STYLE_CSP`), like the other print views. The browser's print dialog is the print
path (CLAUDE.md §10). English only (ADR-0036).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
from dataclasses import dataclass
from string import Template
from typing import Final
from zoneinfo import ZoneInfo

from app.compliance.packs_schemas import InspectionPackDetailOut, InspectionPackItemOut
from app.core.redaction import mask_aadhaar

IST: Final = ZoneInfo("Asia/Kolkata")
DASH: Final = "-"

STYLE: Final = """
@page { size: A4; margin: 14mm 12mm 16mm; }
* { box-sizing: border-box; }
body { margin: 0; color: #111; background: #fff; font-family: "Noto Sans", Arial, sans-serif;
  font-size: 10pt; line-height: 1.5; }
main { max-width: 186mm; margin: 0 auto; }
.school { font-size: 14pt; font-weight: 700; margin: 0; }
.head p { margin: 0; }
h1 { font-size: 14pt; margin: 5mm 0 1mm; }
h2 { font-size: 11pt; margin: 5mm 0 1mm; page-break-after: avoid; }
.unverified { border: 1px dashed #555; padding: 1.5mm 3mm; margin: 2mm 0; font-size: 9pt; }
dl.meta { display: grid; grid-template-columns: 40mm 1fr; gap: 0.5mm 4mm; margin: 3mm 0; }
dl.meta dt { font-weight: 600; } dl.meta dd { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 1mm 0 3mm; font-size: 9pt; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #555; padding: 1.2mm 1.6mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }
th { background: #f1f1f1; font-weight: 600; }
.state { font-weight: 600; white-space: nowrap; }
.note { font-size: 9pt; }
.signatures { display: flex; gap: 10mm; margin-top: 14mm; page-break-inside: avoid; }
.signatures div { flex: 1; border-top: 1px solid #111; padding-top: 1.5mm; font-size: 9pt; }
footer { margin-top: 5mm; font-size: 8pt; color: #444; }
@media screen { body { background: #eee; } main { background: #fff; padding: 10mm; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


# Content-Security-Policy of the index response: nothing but the one hashed style block.
STYLE_CSP: Final = (
    "default-src 'none'; style-src " + _style_hash(STYLE) + "; img-src 'none'; "
    "font-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

STATE_LABELS: Final = {
    "ready": "Ready",
    "expiring": "Expiring soon",
    "no_copy": "No copy on file",
    "incomplete": "Incomplete",
    "expired": "Expired",
    "missing": "Missing",
    "left_out": "Not included",
    "not_tracked": "Not tracked in SchoolOS",
}

SOURCE_LABELS: Final = {
    "certificate": "Compliance certificates",
    "register": "Registers (print view)",
    "disclosure": "CBSE disclosure page",
    "privacy_notice": "Privacy notices",
    "dpdp_readiness": "DPDP readiness checklist",
    "not_tracked": "Kept outside SchoolOS",
}


@dataclass(frozen=True, slots=True)
class IndexData:
    school_name: str
    school_address: str
    pack: InspectionPackDetailOut
    printed_at: dt.datetime


_PAGE: Final = Template(
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
<div class="head">
<p class="school">${school_name}</p>
${address}
</div>
<h1>${title}</h1>
<dl class="meta">
<dt>For</dt><dd>${authority}</dd>
<dt>Visit date</dt><dd>${visit_on}</dd>
<dt>Academic year</dt><dd>${year}</dd>
<dt>Pack made on</dt><dd>${generated_at}</dd>
<dt>Summary</dt><dd>${summary}</dd>
</dl>
${unverified}
<h2>Contents</h2>
<table><thead><tr><th>S. no.</th><th>Item</th><th>Kept in</th><th>State</th><th>Details</th>
<th>Fixed by</th></tr></thead><tbody>
${rows}
</tbody></table>
<p class="note">Print each item from the screen named under "Kept in" and place it behind this
index in the same order. Registers list students: they print only from the register screen,
after confirming it is you. Items not tracked in SchoolOS are kept in the school's own files.</p>
<div class="signatures">
<div>Prepared by (office)</div>
<div>Checked by (principal)</div>
</div>
<footer>Printed from SchoolOS on ${printed_at}. The state of each item is as it was when the pack
was made (pack file version ${version}).</footer>
</main>
</body>
</html>
"""
)

_UNVERIFIED: Final = (
    '<p class="unverified">This list follows public guidance SchoolOS could find; what this '
    "authority asks for is not yet confirmed and can differ by district and year.</p>"
)


def _e(value: str) -> str:
    return html.escape(mask_aadhaar(value), quote=True)


def _day(value: dt.date | None) -> str:
    return value.strftime("%d %b %Y") if value is not None else DASH


def ist(value: dt.datetime) -> str:
    return value.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def details(item: InspectionPackItemOut) -> str:  # noqa: PLR0911 - one line per source
    """The one-line detail of an item (dates and counts only)."""
    if item.source == "certificate":
        if item.state == "missing":
            return "Not recorded in SchoolOS"
        until = f"Valid until {_day(item.valid_until)}" if item.valid_until else "No expiry"
        copy = "copy on file" if item.has_copy else "no copy on file"
        return f"{until}; {copy}"
    if item.source == "register":
        return "Chosen for this pack" if item.included else "Not chosen when the pack was made"
    if item.source == "disclosure":
        if item.published_at is None:
            return "No page published"
        return f"Published {_day(item.published_at.astimezone(IST).date())}"
    if item.source == "privacy_notice":
        return f"Given {_day(item.issued_on)}" if item.issued_on else "No notice recorded"
    if item.source == "dpdp_readiness":
        return f"{item.done or 0} of {item.applicable or 0} done"
    return f"Planned in SchoolOS ({item.feature})" if item.feature else DASH


def _row(n: int, item: InspectionPackItemOut) -> str:
    cells = (
        str(n),
        item.label,
        SOURCE_LABELS.get(item.source, item.source),
        STATE_LABELS.get(item.state, item.state),
        details(item),
        item.fix_by,
    )
    return (
        "<tr>"
        + "".join(
            f'<td class="state">{_e(c)}</td>' if i == 3 else f"<td>{_e(c)}</td>"
            for i, c in enumerate(cells)
        )
        + "</tr>"
    )


def render(data: IndexData) -> str:
    """The index: the school, the authority, the summary counts and one row per item."""
    pack = data.pack
    c = pack.counts
    summary = (
        f"{c.ready} ready, {c.attention} need attention, {c.missing} missing, "
        f"{c.not_tracked} not tracked in SchoolOS"
        + (f", {c.left_out} not included" if c.left_out else "")
    )
    address = f"<p>{_e(data.school_address)}</p>" if data.school_address else ""
    return _PAGE.substitute(
        style=STYLE,
        title=_e(f"Inspection pack: {pack.pack_label}"),
        school_name=_e(data.school_name),
        address=address,
        authority=_e(pack.authority),
        visit_on=_e(_day(pack.visit_on)),
        year=_e(pack.academic_year_label or DASH),
        generated_at=_e(ist(pack.generated_at)),
        summary=_e(summary),
        unverified="" if pack.verified else _UNVERIFIED,
        rows="\n".join(_row(n, i) for n, i in enumerate(pack.items, start=1)),
        printed_at=_e(ist(data.printed_at)),
        version=_e(str(pack.pack_version)),
    )


__all__ = ["STATE_LABELS", "STYLE_CSP", "IndexData", "details", "render"]
