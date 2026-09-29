"""Certificate and register pages printed by headless Chromium (CLAUDE.md §3, §10; FR-CERT-010,
FR-REG-004): A4, the bundled Telugu font embedded, Telugu text in the text layer, no network.

Skipped only when no Chromium can be launched on this machine (like tests/exports/test_pdf.py).
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

import pypdfium2 as pdfium
import pytest

from app.certificates import templates
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


def _content() -> Any:
    loader = sys.modules.get("sos_test_certificates_config_tests")
    if loader is None:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).with_name("test_config_and_templates.py")
        spec = importlib.util.spec_from_file_location("sos_test_certificates_config_tests", path)
        assert spec is not None
        assert spec.loader is not None
        loader = importlib.util.module_from_spec(spec)
        sys.modules["sos_test_certificates_config_tests"] = loader
        spec.loader.exec_module(loader)
    return loader._content()


def _text(pdf: bytes) -> tuple[int, str, tuple[float, float]]:
    doc = pdfium.PdfDocument(pdf)
    try:
        pages = len(doc)
        text = "".join(doc[i].get_textpage().get_text_range() for i in range(pages))
        size = doc[0].get_size()
    finally:
        doc.close()
    return pages, text, size


def test_FR_CERT_010_transfer_certificate_prints_on_a4_with_telugu(
    renderer: ChromiumRenderer,
) -> None:
    pdf = renderer.render(templates.render_certificate(_content(), reference="R1", for_pdf=True))
    assert pdf.startswith(b"%PDF")
    pages, text, (width, height) = _text(pdf)
    assert pages == 1, "a TC fits one A4 page"
    assert abs(width - 595.3) < 2, "A4 portrait"
    assert abs(height - 841.9) < 2, "A4 portrait"
    assert "TC/2026-27/0007" in text
    assert "బదిలీ" in text, "Telugu is in the text layer (font embedded, shaped)"
    assert b"NotoSansTelugu" in pdf or b"Noto Sans Telugu" in pdf


def test_FR_REG_004_register_prints_landscape(renderer: ChromiumRenderer) -> None:
    page = templates.render_register(
        templates.RegisterPage(
            title_en="Transfer certificate register (counterfoil)",
            title_te="బదిలీ ధృవీకరణ పత్రాల రిజిస్టర్",
            school_name="Synthetic Model School",
            school_name_te="కృత్రిమ మోడల్ పాఠశాల",
            academic_year_label="2026-27",
            printed_at=dt.datetime(2026, 9, 29, tzinfo=dt.UTC),
            header=[("Serial no.", "క్రమ సంఖ్య"), ("Name of the pupil", "విద్యార్థి పేరు")],
            rows=[[f"TC/2026-27/{n:04d}", f"Synthetica Student {n}"] for n in range(1, 80)],
            cancelled=[n % 10 == 0 for n in range(1, 80)],
            empty_en="None",
            empty_te="లేవు",
        )
    ).replace("<style>", "<style>" + templates._FONT_FACE, 1)
    pdf = renderer.render(page)
    pages, text, (width, height) = _text(pdf)
    assert pages >= 2
    assert width > height, "A4 landscape"
    assert "TC/2026-27/0079" in text
    # pdfium may split Telugu clusters with spaces in its text layer: compare without them.
    assert "క్రమసంఖ్య" in "".join(text.split())
