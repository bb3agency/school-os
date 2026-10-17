"""The school's static UPI QR (R2-2E; FR-FEE-015; ADR-0042 decision 2 and 9). Pure: no database.

The QR encodes the school's own UPI ID and payee name with no amount (a "static" QR, NPCI's
``upi://pay`` intent: ``pa`` payee address, ``pn`` payee name, ``cu`` INR). A parent's UPI app
pays the school's bank account directly; SchoolOS never receives, holds or forwards money and
learns of a payment only when the clerk types the UTR or the bank statement is imported.

The image is an inline SVG path (``segno``, BSD-3-Clause, already a dependency of certificates
and messaging): no image file, no network, works under ``img-src 'none'`` print pages.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import quote

import segno

from app.fees.upi_config import VPA_SHAPE

_VPA_RE: Final = re.compile(VPA_SHAPE)
_SVG_OPEN: Final = re.compile(r"^<svg\b")
_NAME_UNSAFE: Final = re.compile(r"[\x00-\x1f\x7f&=?#%]")


def upi_uri(vpa: str, payee_name: str) -> str:
    """``upi://pay?pa=<vpa>&pn=<payee>&cu=INR`` (percent-encoded; never an amount or note)."""
    if not _VPA_RE.fullmatch(vpa):
        raise ValueError("not a UPI ID")
    name = " ".join(_NAME_UNSAFE.sub(" ", payee_name).split())
    if not name:
        raise ValueError("empty payee name")
    return f"upi://pay?pa={quote(vpa, safe='@.-_')}&pn={quote(name, safe='')}&cu=INR"


def qr_svg(uri: str, *, label: str, error: str = "m") -> str:
    """An SVG of the QR for a ``upi://pay`` URI, with an accessible name (``role="img"``)."""
    if not uri.startswith("upi://pay?pa="):
        raise ValueError("only UPI payment URIs are encoded")
    code = segno.make(uri, error=error, micro=False)
    svg = code.svg_inline(scale=4, border=2, dark="#000", light="#fff", svgclass="upi-qr")
    escaped = label.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return _SVG_OPEN.sub(
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{escaped}"', svg, count=1
    )


__all__ = ["qr_svg", "upi_uri"]
