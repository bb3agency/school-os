"""Printable WhatsApp join slips (A4, two per page; FR-MSG-009, US-2202 AC2; CLAUDE.md §10 "print
is first-class").

Pure module: the service passes finished data and gets a self-contained page back. Each slip is
in its guardian's parent language (ADR-0044), names the child and class (the office hands it to
the right family), lists the categories, and carries a QR code and a link to
``https://wa.me/<school number>?text=JOIN%20<code>`` plus the message to send. Every value goes
through :func:`html.escape`; the QR code is inline SVG (segno, no image request). No scripts,
links that send data, or forms; the only style block is allowed by hash in :data:`STYLE_CSP`.
Telugu slips use the system Telugu font with line heights of 1.7 or more (no clipped vowel
signs).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote

import segno

from app.messaging.catalogue import catalogue
from app.messaging.config import Language, OptinCategory

_FONTS: Final = '"Noto Sans", "Noto Sans Telugu", "Nirmala UI", "Gautami", Arial, sans-serif'

STYLE: Final = f"""
@page {{ size: A4; margin: 10mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ color: #111; background: #fff; font-family: {_FONTS}; font-size: 10.5pt;
  line-height: 1.7; }}
.slip {{ height: 136mm; border: 1px dashed #555; padding: 6mm 8mm; display: grid;
  grid-template-columns: 1fr 44mm; gap: 4mm 8mm; page-break-inside: avoid;
  break-inside: avoid; margin-bottom: 4mm; }}
.slip:nth-of-type(2n) {{ page-break-after: always; break-after: page; margin-bottom: 0; }}
.school {{ font-size: 13pt; font-weight: 700; margin: 0; line-height: 1.6; }}
h1 {{ font-size: 14pt; margin: 1mm 0 2mm; line-height: 1.7; }}
p, li {{ margin: 1mm 0; line-height: 1.8; }}
ul {{ margin: 1mm 0 2mm; padding-left: 5mm; }}
.send {{ font-family: "Noto Sans Mono", Consolas, monospace; font-size: 15pt; font-weight: 700;
  letter-spacing: 1px; border: 1.5px solid #111; padding: 1.5mm 3mm; display: inline-block; }}
.qr svg {{ width: 44mm; height: 44mm; }}
.link {{ font-size: 8.5pt; overflow-wrap: anywhere; color: #333; }}
.small {{ font-size: 9pt; color: #333; }}
footer {{ grid-column: 1 / -1; font-size: 8.5pt; color: #444; border-top: 1px solid #bbb;
  padding-top: 1.5mm; }}
@media screen {{ body {{ background: #e9e9e9; }} main {{ max-width: 190mm; margin: 6mm auto; }}
  .slip {{ background: #fff; }} }}
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


STYLE_CSP: Final = (
    f"default-src 'none'; style-src {_style_hash(STYLE)}; img-src 'none'; font-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


@dataclass(frozen=True, slots=True)
class Slip:
    language: Language
    student_name: str
    class_section: str
    code: str
    categories: tuple[OptinCategory, ...]
    valid_until: dt.date


def join_link(whatsapp_number: str, code: str) -> str:
    """``wa.me`` link that opens WhatsApp with ``JOIN <code>`` to the school's number."""
    digits = "".join(ch for ch in whatsapp_number if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote('JOIN ' + code)}"


def _qr(link: str) -> str:
    return str(segno.make(link, error="m").svg_inline(scale=4, border=2, omitsize=True))


def _slip(school: str, number: str, slip: Slip) -> str:
    text = catalogue().slip[slip.language]
    e = html.escape
    link = join_link(number, slip.code)
    items = "".join(f"<li>{e(text.get('category_' + c))}</li>" for c in slip.categories)
    who = " · ".join(v for v in (slip.student_name, slip.class_section) if v)
    return (
        f'<section class="slip" lang="{slip.language}">'
        f'<div><p class="school">{e(school)}</p><h1>{e(text.get("title"))}</h1>'
        f"<p>{e(text.get('for_parents_of'))}: <strong>{e(who)}</strong></p>"
        f"<p>{e(text.get('categories_intro'))}</p><ul>{items}</ul>"
        f'<p>{e(text.get("step_send"))}</p><p><span class="send">JOIN {e(slip.code)}</span></p>'
        f'<p class="small">{e(text.get("voluntary"))}</p></div>'
        f'<div class="qr">{_qr(link)}<p class="small">{e(text.get("step_scan"))}</p>'
        f'<p class="link">{e(link)}</p>'
        f'<p class="small">{e(text.get("valid_until"))}: '
        f"{slip.valid_until.strftime('%d/%m/%Y')}</p></div>"
        f"<footer>{e(text.get('footer'))}</footer></section>"
    )


def render(*, school_name: str, whatsapp_number: str, slips: Sequence[Slip]) -> str:
    """The print page (A4, two slips per page)."""
    body = "".join(_slip(school_name, whatsapp_number, s) for s in slips)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{html.escape(school_name)}: WhatsApp join slips</title>"
        f"<style>{STYLE}</style></head><body><main>{body}</main></body></html>"
    )


__all__ = ["STYLE_CSP", "Slip", "join_link", "render"]
