"""Print pages for privacy notices, response letters and access reports (A4; FR-DPDP-002,
FR-DPDP-007, FR-DPDP-009, FR-DPDP-016; docs/17 §5.10 "Print").

Pure module: the service passes finished values and gets a self-contained, escaped page back.
Every value goes through :func:`html.escape` and the Aadhaar mask (invariant 4, defence in
depth). Pages have no scripts, links, images or forms; their only style block is allowed by hash
in :data:`STYLE_CSP`. The notice Markdown is the app copy of ``docs/legal/`` (a small subset:
headings, paragraphs, bold and italic, bullet lists, pipe tables, block quotes).
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.core.redaction import mask_aadhaar

NOT_SET: Final = "[not set]"
_PLACEHOLDER: Final = re.compile(r"\{\{([a-z_]+)\}\}")

STYLE: Final = """
@page { size: A4; margin: 16mm 16mm 18mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { color: #111; background: #fff; font-family: "Noto Sans", Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.6; }
header { border-bottom: 1px solid #222; padding-bottom: 2mm; margin-bottom: 4mm; }
.school { font-size: 15pt; font-weight: 700; margin: 0; }
.address { margin: 0; font-size: 9.5pt; color: #333; }
h1 { font-size: 14pt; margin: 3mm 0 2mm; }
h2 { font-size: 12pt; margin: 5mm 0 1.5mm; }
h3 { font-size: 11pt; margin: 4mm 0 1mm; }
p { margin: 1.5mm 0; }
ul { margin: 1mm 0 2mm 5mm; padding-left: 4mm; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 3mm; }
th, td { border: 1px solid #666; padding: 1.2mm 2mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }
th { background: #f1f1f1; font-weight: 600; }
.meta { font-size: 9pt; color: #333; }
.section { page-break-inside: auto; }
.note { font-size: 9pt; color: #333; font-style: italic; }
.banner { font-size: 8.5pt; color: #444; border: 1px solid #999; padding: 1.5mm 2mm;
  margin-bottom: 3mm; }
.letter p { margin: 2.5mm 0; }
footer { margin-top: 6mm; border-top: 1px solid #999; padding-top: 2mm; font-size: 9pt; }
@media screen { body { background: #e9e9e9; } main { max-width: 190mm; margin: 6mm auto;
  padding: 10mm; background: #fff; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


STYLE_CSP: Final = (
    f"default-src 'none'; style-src {_style_hash(STYLE)}; img-src 'none'; font-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _e(value: object) -> str:
    text = "" if value is None else str(value)
    return html.escape(mask_aadhaar(text), quote=True)


def fill(text: str, values: Mapping[str, str | None]) -> str:
    """Replace ``{{name}}`` with its value (unfilled or unknown: ``[not set]``). Plain text."""

    def one(m: re.Match[str]) -> str:
        value = values.get(m.group(1))
        return value if value else NOT_SET

    return _PLACEHOLDER.sub(one, text)


def _inline(text: str) -> str:
    """Escape, then bold (``**x**``), italic (``*x*``) and code (```x```) spans."""
    out = _e(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", out)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", out)


def _table(lines: Sequence[str]) -> str:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    head, body = rows[0], rows[2:]
    th = "".join(f"<th>{_inline(c)}</th>" for c in head)
    trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def markdown_html(markdown: str) -> str:
    """The small Markdown subset of the legal templates as escaped HTML."""
    blocks: list[str] = []
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")) + 0, 3)
            level = max(level, 1)
            blocks.append(f"<h{level}>{_inline(line.lstrip('#').strip())}</h{level}>")
            i += 1
            continue
        if line.lstrip().startswith("|"):
            chunk = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                chunk.append(lines[i])
                i += 1
            blocks.append(_table(chunk))
            continue
        if line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            blocks.append(f"<ul>{''.join(items)}</ul>")
            continue
        para = []
        while (
            i < len(lines)
            and lines[i].strip()
            and not lines[i].startswith(("#", "- "))
            and not lines[i].lstrip().startswith("|")
        ):
            para.append(lines[i].lstrip("> ").strip())
            i += 1
        blocks.append(f"<p>{_inline(' '.join(para))}</p>")
    return "".join(blocks)


@dataclass(frozen=True, slots=True)
class Letterhead:
    school: str
    address: str | None
    contact_line: str


def _page(title: str, head: Letterhead, body: str, *, banner: str | None = None) -> str:
    address = f'<p class="address">{_e(head.address)}</p>' if head.address else ""
    banner_html = f'<p class="banner">{_e(banner)}</p>' if banner else ""
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
        f"<style>{STYLE}</style></head><body><main>"
        f'<header><p class="school">{_e(head.school)}</p>{address}</header>'
        f"{banner_html}{body}"
        f"<footer>{_e(head.contact_line)}</footer>"
        "</main></body></html>"
    )


def notice_page(title: str, head: Letterhead, markdown: str) -> str:
    """A filled notice (the Markdown already has its placeholders filled)."""
    return _page(
        title,
        head,
        markdown_html(markdown),
        banner="Drafted by SchoolOS from a template. Not legal advice.",
    )


def letter_page(
    title: str,
    head: Letterhead,
    *,
    reference: str,
    date_line: str,
    body: str,
) -> str:
    """A response letter: plain filled text, paragraphs split on blank lines."""
    paras = "".join(f"<p>{_e(p.strip())}</p>" for p in body.split("\n\n") if p.strip())
    return _page(
        title,
        head,
        f'<p class="meta">Reference {_e(reference)} · {_e(date_line)}</p>'
        f'<h1>{_e(title)}</h1><div class="letter">{paras}</div>',
    )


@dataclass(frozen=True, slots=True)
class ReportBlock:
    """One record class in the access report with the contributors' sections."""

    label: str
    source: str
    retention: str
    erasure_words: str
    sections: Sequence[tuple[str, Sequence[str], Sequence[Sequence[str]], str | None]]


def report_page(
    head: Letterhead,
    *,
    subject_name: str,
    reference: str,
    date_line: str,
    blocks: Sequence[ReportBlock],
) -> str:
    """The access report (FR-DPDP-009): what the school holds about one person, per record
    class, with where it came from and how long it is kept."""
    parts = [
        f"<h1>What {_e(head.school)} holds about {_e(subject_name)}</h1>",
        f'<p class="meta">Reference {_e(reference)} · {_e(date_line)}</p>',
        f"<p>This report lists what {_e(head.school)} holds about {_e(subject_name)} in "
        f"SchoolOS on {_e(date_line)}. Aadhaar numbers are never stored; only the last 4 "
        "digits and the details as printed on the card are kept, and these are shown at the "
        "office.</p>",
    ]
    if not blocks:
        parts.append("<p>SchoolOS holds no records about this person.</p>")
    for block in blocks:
        inner = [f"<h2>{_e(block.label)}</h2>"]
        inner.append(
            f'<p class="meta">Where it came from: {_e(block.source)} · Kept: '
            f"{_e(block.retention)} · {_e(block.erasure_words)}</p>"
        )
        for title, columns, rows, note in block.sections:
            inner.append(f"<h3>{_e(title)}</h3>")
            if rows:
                th = "".join(f"<th>{_e(c)}</th>" for c in columns)
                trs = "".join(
                    "<tr>" + "".join(f"<td>{_e(v)}</td>" for v in r) + "</tr>" for r in rows
                )
                inner.append(f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")
            else:
                inner.append("<p>None.</p>")
            if note:
                inner.append(f'<p class="note">{_e(note)}</p>')
        parts.append(f'<section class="section">{"".join(inner)}</section>')
    return _page(f"Access report {reference}", head, "".join(parts))


__all__ = [
    "NOT_SET",
    "STYLE_CSP",
    "Letterhead",
    "ReportBlock",
    "fill",
    "letter_page",
    "markdown_html",
    "notice_page",
    "report_page",
]
