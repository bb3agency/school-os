"""Printable parent notice: one HTML page rendered to an A4 PDF and a PNG (FR-NOTICE-006).

Pure: builds escaped HTML from the approved notice (English then Telugu), the school's name and
the approval date. The worker hands it to ``core.pdf`` (headless Chromium with the bundled Noto
Sans Telugu; JavaScript off, every request except the font refused). Line heights and padding
leave room for Telugu vowel signs above and below the line, so no glyph is clipped.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import Final

from app.core.pdf import FONT_FAMILY, FONT_URL

_STYLE: Final = f"""
@font-face {{ font-family: "{FONT_FAMILY}"; src: url("{FONT_URL}") format("truetype");
  font-weight: 100 900; font-stretch: 62.5% 100%; }}
@page {{ size: A4 portrait; margin: 18mm 16mm 20mm; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #fff; }}
body {{ color: #111; font-family: "{FONT_FAMILY}", sans-serif; font-size: 12pt;
  line-height: 1.8; }}
.card {{ padding: 28px 32px; border: 2px solid #1d4ed8; border-radius: 10px; }}
.school {{ font-size: 11pt; font-weight: 600; color: #1e3a8a; margin: 0 0 4px; }}
.date {{ font-size: 10pt; color: #374151; margin: 0 0 18px; }}
h1 {{ font-size: 17pt; line-height: 1.6; margin: 0 0 8px; }}
p.body {{ white-space: pre-wrap; margin: 0; overflow-wrap: anywhere; }}
hr {{ border: 0; border-top: 1px solid #9ca3af; margin: 22px 0; }}
section[lang="te"] h1, section[lang="te"] p.body {{ line-height: 2.0; padding-top: 2px; }}
.footer {{ margin-top: 22px; font-size: 9pt; color: #4b5563; }}
@media screen {{ body {{ padding: 24px; font-size: 20px; }} h1 {{ font-size: 28px; }}
  .school {{ font-size: 18px; }} .date, .footer {{ font-size: 16px; }} }}
"""


def _e(value: str) -> str:
    return html.escape(value, quote=True)


def notice_html(
    *,
    school_name: str,
    approved_on: dt.date,
    title_en: str,
    body_en: str,
    title_te: str,
    body_te: str,
) -> str:
    """The notice page (every value escaped; no scripts, links or external resources)."""
    date = approved_on.strftime("%d/%m/%Y")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{_e(title_en)}</title><style>{_STYLE}</style></head><body>"
        '<div class="card">'
        f'<p class="school">{_e(school_name)}</p><p class="date">{date}</p>'
        f'<section lang="en"><h1>{_e(title_en)}</h1><p class="body">{_e(body_en)}</p></section>'
        "<hr>"
        f'<section lang="te"><h1>{_e(title_te)}</h1><p class="body">{_e(body_te)}</p></section>'
        '<p class="footer">SchoolOS</p>'
        "</div></body></html>"
    )


__all__ = ["notice_html"]
