"""The disclosure as a file for the school's own website and as an A4 PDF (ADR-0051 decision 5;
FR-DISC-011; docs/17 §5.10 "Print").

Pure module: :func:`render_page` turns a snapshot's frozen content into one self-contained HTML
page: no scripts, no images, no external requests except the links to the hosted documents
(``{public origin}/d/{key}/documents/{n}``) and the infrastructure video link the school typed;
one inline style block; every value escaped and Aadhaar-masked (defence in depth: inputs already
refuse Aadhaar-like numbers). The same page prints the PDF (``pdf`` queue). English (ADR-0036).
"""

from __future__ import annotations

import datetime as dt
import html
from collections.abc import Mapping
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.core.redaction import mask_aadhaar

IST: Final = ZoneInfo("Asia/Kolkata")
_FONTS: Final = '"Noto Sans", Arial, sans-serif'

STYLE: Final = f"""
@page {{ size: A4; margin: 14mm 14mm 16mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #1b1b1b; background: #fff; font-family: {_FONTS}; font-size: 10.5pt;
  line-height: 1.55; }}
main {{ max-width: 190mm; margin: 0 auto; padding: 6mm 4mm; }}
header {{ border-bottom: 2px solid #1b1b1b; padding-bottom: 3mm; margin-bottom: 4mm; }}
.school {{ font-size: 18pt; font-weight: 700; margin: 0; overflow-wrap: anywhere; }}
h1 {{ font-size: 12pt; font-weight: 600; margin: 1mm 0 0; letter-spacing: 0.02em; }}
h2 {{ font-size: 11.5pt; margin: 6mm 0 2mm; padding-bottom: 1mm; border-bottom: 1px solid #999;
  break-after: avoid; }}
dl {{ margin: 0; }}
.row {{ display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); gap: 2mm 5mm;
  padding: 1.5mm 0; border-bottom: 1px solid #ddd; break-inside: avoid; }}
dt {{ font-weight: 600; overflow-wrap: anywhere; }}
dd {{ margin: 0; overflow-wrap: anywhere; }}
.empty {{ color: #666; }}
table {{ width: 100%; border-collapse: collapse; margin: 1mm 0;
  font-variant-numeric: tabular-nums; }}
th, td {{ border: 1px solid #888; padding: 1mm 2mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }}
th {{ background: #f0f0f0; font-weight: 600; }}
ul {{ margin: 0; padding-left: 5mm; }}
footer {{ margin-top: 8mm; padding-top: 2mm; border-top: 1px solid #999; font-size: 9pt;
  color: #333; }}
@media (max-width: 600px) {{ .row {{ grid-template-columns: 1fr; gap: 0.5mm; }} }}
"""


def _e(value: object) -> str:
    return html.escape(mask_aadhaar("" if value is None else str(value)), quote=True)


def published_on(published_at: dt.datetime) -> str:
    return published_at.astimezone(IST).strftime("%d %b %Y")


def _table(table: Mapping[str, Any]) -> str:
    head = "".join(f'<th scope="col">{_e(c)}</th>' for c in table.get("columns") or [])
    rows = "".join(
        "<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>"
        for row in table.get("rows") or []
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def _item(item: Mapping[str, Any], document_url: str) -> str:
    parts: list[str] = []
    if item.get("value"):
        parts.append(f"<span>{_e(item['value'])}</span>")
    if item.get("table"):
        parts.append(_table(item["table"]))
    docs = item.get("documents") or []
    if docs:
        links = "".join(
            f'<li><a href="{_e(document_url.format(n=int(d["n"])))}" rel="noreferrer">'
            f"{_e(d.get('title'))} (PDF)</a></li>"
            for d in docs
        )
        parts.append(f"<ul>{links}</ul>")
    if item.get("link"):
        parts.append(
            f'<a href="{_e(item["link"])}" rel="noreferrer noopener">{_e(item["link"])}</a>'
        )
    body = "".join(parts) or '<span class="empty">Not provided</span>'
    return f'<div class="row"><dt>{_e(item.get("label"))}</dt><dd>{body}</dd></div>'


def render_page(content: Mapping[str, Any], *, published_at: dt.datetime, document_url: str) -> str:
    """The whole page. ``document_url`` is a template with ``{n}`` for a published document's
    number (absolute on the public origin, so the file works from the school's own site)."""
    school = content.get("school_name") or ""
    title = content.get("title") or "Mandatory public disclosure"
    sections = "".join(
        f"<section><h2>{_e(s.get('title'))}</h2><dl>"
        + "".join(_item(i, document_url) for i in s.get("items") or [])
        + "</dl></section>"
        for s in content.get("sections") or []
    )
    date = published_on(published_at)
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>{_e(title)} - {_e(school)}</title>"
        f"<style>{STYLE}</style></head><body><main>"
        f'<header><p class="school">{_e(school)}</p><h1>{_e(title)}</h1></header>'
        f"{sections}"
        f"<footer><p>Published by {_e(school)} on {_e(date)}.</p>"
        "<p>Prepared with SchoolOS.</p></footer>"
        "</main></body></html>"
    )


__all__ = ["STYLE", "published_on", "render_page"]
