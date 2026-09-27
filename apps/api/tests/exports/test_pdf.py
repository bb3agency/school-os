"""PDF rendering with headless Chromium (CLAUDE.md §3, docs/07 §10; FR-EXP-002).

The renderer is an interface (tests use a fake elsewhere). The real-render tests below run
Chromium through Playwright from ``PLAYWRIGHT_BROWSERS_PATH`` and are skipped only when no
Chromium can be launched on this machine.
"""

from __future__ import annotations

import hashlib
import http.server
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.exports import pdf
from app.exports.config import load_config
from app.exports.pdf import FONT_SHA256, ChromiumRenderer, font_bytes
from app.exports.report import PrecheckInput, ReadyLine, build_precheck, render_precheck_html

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


class _Recorder(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []  # noqa: RUF012

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def local_server() -> Iterator[str]:
    _Recorder.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _report_html(language: str = "te") -> str:
    data = PrecheckInput(
        language=language,  # type: ignore[arg-type]
        school="సింథటిక్ మోడల్ పాఠశాల",
        profile_label="CISCE నమోదు 2026",
        export_ref=uuid.uuid4(),
        generated_at="27/09/2026 10:00",
        scope_label="IX-A",
        field_labels=("ప్రవేశ సంఖ్య", "పూర్తి పేరు"),
        findings=(),
        ready=(ReadyLine(uuid.uuid4(), "IX-A", ("1",), ("EX/1", "వెంకట సాయి")),),
        sensitive_masked=False,
    )
    cfg = load_config()
    return render_precheck_html(build_precheck(data, cfg), cfg)


def test_bundled_font_matches_recorded_checksum() -> None:
    data = font_bytes()
    assert hashlib.sha256(data).hexdigest() == FONT_SHA256
    assert data[:4] == b"\x00\x01\x00\x00"  # TrueType


def test_renderer_is_replaceable() -> None:
    class Fake:
        def render(self, html: str) -> bytes:
            return b"%PDF-fake"

    fake = Fake()
    pdf.set_renderer(fake)
    try:
        assert pdf.get_renderer() is fake
    finally:
        pdf.set_renderer(None)
    assert isinstance(pdf.get_renderer(), ChromiumRenderer)


def test_default_renderer_sandbox_follows_environment() -> None:
    # CI/local containers run as root without the Chromium sandbox; staging/prod use it.
    assert pdf.default_renderer().sandbox is False  # type: ignore[attr-defined]


def test_FR_EXP_002_real_render_telugu_pdf(renderer: ChromiumRenderer) -> None:
    out = renderer.render(_report_html("te"))
    assert out.startswith(b"%PDF-")
    assert len(out) > 1000
    assert b"NotoSansTelugu" in out, "the bundled Telugu font is embedded"


def test_docs_07_10_render_makes_no_network_requests(
    renderer: ChromiumRenderer, local_server: str
) -> None:
    hostile = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<link rel='stylesheet' href='{local_server}/style.css'>"
        f"<style>@import url('{local_server}/import.css'); "
        f"body {{ background: url('{local_server}/bg.png'); }}</style>"
        f"<script src='{local_server}/x.js'></script></head><body>"
        f"<img src='{local_server}/pixel.png'><iframe src='{local_server}/frame'></iframe>"
        "<p>పరీక్ష</p></body></html>"
    )
    out = renderer.render(hostile)
    assert out.startswith(b"%PDF-")
    assert _Recorder.hits == []
