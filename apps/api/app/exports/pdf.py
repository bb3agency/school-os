"""PDF rendering: HTML/CSS templates printed by headless Chromium via Playwright in the worker
(CLAUDE.md §3; docs/04 §6 queue ``pdf``; docs/07 §10).

:class:`PdfRenderer` is the interface; :class:`ChromiumRenderer` the real implementation and
tests may install a fake with :func:`set_renderer`. Hardening:

- JavaScript is disabled, service workers blocked, downloads refused.
- Every request the page makes is intercepted: the bundled Noto Sans Telugu font (checked
  against its SHA-256) is served from memory at :data:`FONT_URL`; everything else is aborted,
  so a template (or a value in it) can never reach the network or a local file.
- The Chromium sandbox is on in staging and prod (``pdf.chromium_sandbox`` in config.yaml);
  local and CI containers run as root, where Chromium cannot sandbox itself.
- Templates escape every value (:mod:`app.exports.report`).

Browsers come from ``PLAYWRIGHT_BROWSERS_PATH`` (baked into the worker image); the renderer
never downloads one.
"""

from __future__ import annotations

import hashlib
import threading
from functools import lru_cache
from pathlib import Path
from typing import Final, Protocol

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route, sync_playwright

from app.core.config import Environment, get_settings
from app.core.logging import get_logger
from app.exports.config import load_config

log = get_logger(__name__)

FONT_PATH: Final = Path(__file__).with_name("fonts") / "NotoSansTelugu-VF.ttf"
FONT_SHA256: Final = "e618af7bf999df192ed4f388eba2e563f2b5015034e9cbb317b5bd793bd7334d"
FONT_URL: Final = "https://assets.sos.invalid/fonts/noto-sans-telugu.ttf"
FONT_FAMILY: Final = "SOS Noto Sans Telugu"
FOOTER_TEMPLATE: Final = (
    '<div style="width:100%;font-size:8px;color:#444;text-align:center;'
    'font-family:sans-serif;">SchoolOS &middot; <span class="pageNumber"></span> / '
    '<span class="totalPages"></span></div>'
)


class RenderError(RuntimeError):
    """The PDF could not be produced (never carries document contents)."""


class PdfRenderer(Protocol):
    def render(self, html: str) -> bytes: ...


@lru_cache(maxsize=1)
def font_bytes() -> bytes:
    """The bundled font, verified against the SHA-256 recorded in fonts/SOURCE.txt."""
    data = FONT_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest() != FONT_SHA256:
        raise RenderError("bundled font does not match its recorded checksum")
    return data


def _route(route: Route) -> None:
    if route.request.url == FONT_URL:
        route.fulfill(status=200, body=font_bytes(), content_type="font/ttf")
    else:
        route.abort("blockedbyclient")


class ChromiumRenderer:
    """Print HTML to an A4 PDF with headless Chromium (one browser per document)."""

    def __init__(self, *, sandbox: bool, timeout_ms: int) -> None:
        self.sandbox = sandbox
        self.timeout_ms = timeout_ms

    def render(self, html: str) -> bytes:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(chromium_sandbox=self.sandbox, timeout=self.timeout_ms)
                try:
                    context = browser.new_context(
                        java_script_enabled=False,
                        accept_downloads=False,
                        service_workers="block",
                    )
                    context.route("**/*", _route)
                    page = context.new_page()
                    page.set_default_timeout(self.timeout_ms)
                    page.set_content(html, wait_until="load")
                    page.emulate_media(media="print")
                    return page.pdf(
                        format="A4",
                        print_background=True,
                        prefer_css_page_size=True,
                        display_header_footer=True,
                        header_template="<span></span>",
                        footer_template=FOOTER_TEMPLATE,
                    )
                finally:
                    browser.close()
        except PlaywrightError as exc:
            log.error("exports.pdf.render_failed", error_type=type(exc).__name__)
            raise RenderError("pdf_render_failed") from exc


def default_renderer() -> PdfRenderer:
    cfg = load_config().pdf
    env = get_settings().env
    sandbox = cfg.chromium_sandbox and env in (Environment.STAGING, Environment.PROD)
    return ChromiumRenderer(sandbox=sandbox, timeout_ms=cfg.timeout_ms)


_lock = threading.Lock()
_renderer: PdfRenderer | None = None


def get_renderer() -> PdfRenderer:
    global _renderer  # noqa: PLW0603
    with _lock:
        if _renderer is None:
            _renderer = default_renderer()
        return _renderer


def set_renderer(renderer: PdfRenderer | None) -> None:
    """Override the renderer (tests); ``None`` rebuilds the default from config."""
    global _renderer  # noqa: PLW0603
    with _lock:
        _renderer = renderer


__all__ = [
    "FONT_FAMILY",
    "FONT_URL",
    "ChromiumRenderer",
    "PdfRenderer",
    "RenderError",
    "default_renderer",
    "font_bytes",
    "get_renderer",
    "set_renderer",
]
