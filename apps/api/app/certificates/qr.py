"""QR codes for certificate verification links (ADR-0045 decision 4; FR-CERT-020).

Pure module around ``segno`` (BSD-3-Clause, no dependencies; CLAUDE.md §11 licence check in
apps/api/pyproject.toml). The QR is an inline SVG path: no image file, no ``img`` element and no
network, so the print view's CSP (``img-src 'none'``) and the PDF renderer need nothing new.
Error correction ``M`` (15 %) survives a fold or a stamp across the corner of the paper.
"""

from __future__ import annotations

import re
from typing import Final

import segno

_SVG_OPEN: Final = re.compile(r"^<svg\b")
_SAFE_URL: Final = re.compile(r"^https?://[A-Za-z0-9.:\-\[\]]+/v/[0-9A-Z]{16}$")


def qr_svg(url: str, *, label: str) -> str:
    """An inline SVG of the QR code for ``url`` (a verification link), with an accessible name.
    Only verification links are accepted (``<origin>/v/<code>``): nothing else is ever encoded."""
    if not _SAFE_URL.fullmatch(url):
        raise ValueError("only verification links are encoded")
    code = segno.make(url, error="m", micro=False)
    svg = code.svg_inline(scale=3, border=2, dark="#000", light="#fff", svgclass="qr")
    escaped = label.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return _SVG_OPEN.sub(f'<svg role="img" aria-label="{escaped}"', svg, count=1)


__all__ = ["qr_svg"]
