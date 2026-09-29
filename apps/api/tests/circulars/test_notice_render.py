"""Parent notice rendered by the real headless Chromium (FR-NOTICE-006; CLAUDE.md §3, §10).

The approved notice page (English and Telugu) prints to an A4 PDF with the bundled Noto Sans
Telugu embedded, and to a PNG of the configured width. Skipped only when Chromium is not
installed (the worker image and CI have it).
"""

from __future__ import annotations

import datetime as dt
import struct

import pytest

from app.circulars.config import rules
from app.circulars.rendering import notice_html
from app.core.pdf import ChromiumRenderer

pytestmark = pytest.mark.chromium


def _chromium_available() -> bool:
    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(chromium_sandbox=False)
            browser.close()
    except Error:
        return False
    return True


@pytest.fixture(scope="module")
def renderer() -> ChromiumRenderer:
    if not _chromium_available():
        pytest.skip("headless Chromium is not installed on this machine")
    return ChromiumRenderer(sandbox=False, timeout_ms=60_000)


PAGE = notice_html(
    school_name="Synthetic Model School",
    approved_on=dt.date(2026, 11, 1),
    title_en="Sports day",
    body_en="Sports day is on 14/11/2026 at 9:00 on the school ground.",
    title_te="క్రీడా దినోత్సవం",
    body_te="క్రీడా దినోత్సవం 14/11/2026 ఉదయం 9:00కు పాఠశాల మైదానంలో జరుగుతుంది.",
)


def test_FR_NOTICE_006_a4_pdf_embeds_the_telugu_font(renderer: ChromiumRenderer) -> None:
    out = renderer.render(PAGE)
    assert out.startswith(b"%PDF-")
    assert b"NotoSansTelugu" in out, "the bundled Telugu font is embedded"


def test_FR_NOTICE_006_png_has_the_configured_width(renderer: ChromiumRenderer) -> None:
    width = rules().notices.image_width_px
    out = renderer.render_png(PAGE, width_px=width)
    assert out.startswith(b"\x89PNG\r\n\x1a\n")
    png_width, png_height = struct.unpack(">II", out[16:24])
    assert png_width == width
    assert png_height > 300
