"""PDF rendering: HTML/CSS templates printed by headless Chromium via Playwright in the worker
(CLAUDE.md §3; docs/04 §6 queue ``pdf``; docs/07 §10; ADR-0025).

A neutral building block in ``core`` so every module that prints a document uses the same
hardened renderer: school exports (``app.exports``) and control-plane invoices
(``app.platform.invoice_pdf``), and parent notices as PDF and PNG (``app.circulars``).
It knows nothing about what it prints: callers pass finished,
escaped HTML and get bytes back.

:class:`PdfRenderer` is the interface; :class:`ChromiumRenderer` the real implementation and
tests may install a fake with :func:`set_renderer`. Hardening:

- JavaScript is disabled, service workers blocked, downloads refused.
- Every request the page makes is intercepted: the bundled Noto Sans Telugu font (checked
  against its SHA-256) is served from memory at :data:`FONT_URL`; everything else is aborted,
  so a template (or a value in it) can never reach the network or a local file.
- English first (ADR-0036): while Telugu is hidden (``SOS_TELUGU_ENABLED`` off, read through
  :mod:`app.core.languages`) the font is neither declared (:func:`font_face_css` is empty,
  :func:`font_stack` leaves it out) nor served, so no PDF or PNG loads or embeds it.
- The Chromium sandbox is on in staging and prod (``chromium_sandbox`` in ``app/core/pdf.yaml``);
  local and CI containers run as root, where Chromium cannot sandbox itself.
- Templates escape every value (the callers: :mod:`app.exports.report`,
  :mod:`app.platform.invoice_pdf`).

Browsers come from ``PLAYWRIGHT_BROWSERS_PATH`` (baked into the worker image); the renderer
never downloads one.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Final, Protocol

import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Route, sync_playwright
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Environment, get_settings
from app.core.languages import telugu_enabled
from app.core.logging import get_logger

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


class PdfConfig(BaseModel):
    """``app/core/pdf.yaml`` (versioned configuration shipped inside the package)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    timeout_ms: int = Field(ge=1000, le=300_000)
    chromium_sandbox: bool


@lru_cache(maxsize=1)
def load_config() -> PdfConfig:
    raw = yaml.safe_load(resources.files("app.core").joinpath("pdf.yaml").read_text("utf-8"))
    return PdfConfig.model_validate(raw)


class RenderError(RuntimeError):
    """The PDF could not be produced (never carries document contents)."""


class PdfRenderer(Protocol):
    def render(self, html: str) -> bytes: ...


class ImageRenderer(Protocol):
    def render_png(self, html: str, *, width_px: int) -> bytes:
        """A PNG of the whole page laid out ``width_px`` CSS pixels wide (screen media)."""
        ...


@lru_cache(maxsize=1)
def font_bytes() -> bytes:
    """The bundled font, verified against the SHA-256 recorded in fonts/SOURCE.txt."""
    data = FONT_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest() != FONT_SHA256:
        raise RenderError("bundled font does not match its recorded checksum")
    return data


def font_face_css() -> str:
    """The ``@font-face`` rule of the bundled Telugu font for a print template, or ``""`` while
    Telugu is hidden (ADR-0036): the font is then never requested, loaded or embedded."""
    if not telugu_enabled():
        return ""
    return (
        f'@font-face {{ font-family: "{FONT_FAMILY}"; src: url("{FONT_URL}") format("truetype");\n'
        "  font-weight: 100 900; font-stretch: 62.5% 100%; }\n"
    )


def font_stack(fallback: str = "sans-serif") -> str:
    """A CSS ``font-family`` value: the bundled Telugu font first while Telugu is shown,
    otherwise only ``fallback`` (ADR-0036)."""
    return f'"{FONT_FAMILY}", {fallback}' if telugu_enabled() else fallback


def _route(route: Route) -> None:
    # The font is served only while Telugu is shown (ADR-0036); otherwise it is refused like
    # any other request, so it cannot be embedded even if a page asks for it.
    if route.request.url == FONT_URL and telugu_enabled():
        route.fulfill(status=200, body=font_bytes(), content_type="font/ttf")
    else:
        route.abort("blockedbyclient")


class ChromiumRenderer:
    """Print HTML to an A4 PDF with headless Chromium (one browser per document)."""

    def __init__(self, *, sandbox: bool, timeout_ms: int) -> None:
        self.sandbox = sandbox
        self.timeout_ms = timeout_ms

    def render(self, html: str) -> bytes:
        return self._run(html, self._pdf)

    def render_png(self, html: str, *, width_px: int) -> bytes:
        """Screenshot of the whole page (M4 parent notices as images; same hardening)."""

        def shoot(page: Page) -> bytes:
            page.set_viewport_size({"width": width_px, "height": 400})
            page.set_content(html, wait_until="load")
            page.emulate_media(media="screen")
            return page.screenshot(full_page=True, type="png")

        return self._run(html, shoot, load=False)

    @staticmethod
    def _pdf(page: Page) -> bytes:
        page.emulate_media(media="print")
        return page.pdf(
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=FOOTER_TEMPLATE,
        )

    def _run(self, html: str, work: Callable[[Page], bytes], *, load: bool = True) -> bytes:
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
                    if load:
                        page.set_content(html, wait_until="load")
                    return work(page)
                finally:
                    browser.close()
        except PlaywrightError as exc:
            log.error("pdf.render_failed", error_type=type(exc).__name__)
            raise RenderError("pdf_render_failed") from exc


def default_renderer() -> PdfRenderer:
    cfg = load_config()
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


_image_renderer: ImageRenderer | None = None


def get_image_renderer() -> ImageRenderer:
    """The PNG renderer (the same hardened headless Chromium as PDFs)."""
    global _image_renderer  # noqa: PLW0603
    with _lock:
        if _image_renderer is None:
            cfg = load_config()
            env = get_settings().env
            sandbox = cfg.chromium_sandbox and env in (Environment.STAGING, Environment.PROD)
            _image_renderer = ChromiumRenderer(sandbox=sandbox, timeout_ms=cfg.timeout_ms)
        return _image_renderer


def set_image_renderer(renderer: ImageRenderer | None) -> None:
    """Override the PNG renderer (tests); ``None`` rebuilds the default from config."""
    global _image_renderer  # noqa: PLW0603
    with _lock:
        _image_renderer = renderer


__all__ = [
    "FONT_FAMILY",
    "FONT_URL",
    "ChromiumRenderer",
    "ImageRenderer",
    "PdfConfig",
    "PdfRenderer",
    "RenderError",
    "default_renderer",
    "font_bytes",
    "font_face_css",
    "font_stack",
    "get_image_renderer",
    "get_renderer",
    "load_config",
    "set_image_renderer",
    "set_renderer",
]
