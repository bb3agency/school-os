"""Compatibility re-export: the HTML -> PDF renderer lives in :mod:`app.core.pdf` (one hardened
renderer for school exports and control-plane invoices; ADR-0025). Exports code imports it from
there; this module keeps ``app.exports.pdf`` working for existing callers and tests. The
renderer override is shared: :func:`set_renderer` here and in ``app.core.pdf`` are the same
function.
"""

from __future__ import annotations

from app.core.pdf import (
    FONT_FAMILY,
    FONT_PATH,
    FONT_SHA256,
    FONT_URL,
    FOOTER_TEMPLATE,
    ChromiumRenderer,
    PdfRenderer,
    RenderError,
    default_renderer,
    font_bytes,
    get_renderer,
    set_renderer,
)

__all__ = [
    "FONT_FAMILY",
    "FONT_PATH",
    "FONT_SHA256",
    "FONT_URL",
    "FOOTER_TEMPLATE",
    "ChromiumRenderer",
    "PdfRenderer",
    "RenderError",
    "default_renderer",
    "font_bytes",
    "get_renderer",
    "set_renderer",
]
