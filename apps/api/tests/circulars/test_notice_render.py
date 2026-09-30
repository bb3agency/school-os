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
from app.core.languages import contains_telugu
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


def _page() -> str:
    """Built per test: the Telugu section depends on the switch (ADR-0036)."""
    return notice_html(
        school_name="Synthetic Model School",
        approved_on=dt.date(2026, 11, 1),
        title_en="Sports day",
        body_en="Sports day is on 14/11/2026 at 9:00 on the school ground.",
        title_te="క్రీడా దినోత్సవం",
        body_te="క్రీడా దినోత్సవం 14/11/2026 ఉదయం 9:00కు పాఠశాల మైదానంలో జరుగుతుంది.",
    )


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_NOTICE_006_a4_pdf_embeds_the_telugu_font(renderer: ChromiumRenderer) -> None:
    out = renderer.render(_page())
    assert out.startswith(b"%PDF-")
    assert b"NotoSansTelugu" in out, "the bundled Telugu font is embedded"


def test_ADR_0036_notice_pdf_is_english_without_the_telugu_font_while_telugu_is_hidden(
    renderer: ChromiumRenderer,
) -> None:
    page = _page()
    assert not contains_telugu(page)
    out = renderer.render(page)
    assert out.startswith(b"%PDF-")
    assert b"NotoSansTelugu" not in out
    assert b"Noto Sans Telugu" not in out


def test_FR_NOTICE_006_png_has_the_configured_width(renderer: ChromiumRenderer) -> None:
    width = rules().notices.image_width_px
    out = renderer.render_png(_page(), width_px=width)
    assert out.startswith(b"\x89PNG\r\n\x1a\n")
    png_width, png_height = struct.unpack(">II", out[16:24])
    assert png_width == width
    assert png_height > 300
