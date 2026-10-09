"""The shared HTML -> PDF renderer in ``core`` (CLAUDE.md §3, docs/07 §10, ADR-0025; FR-EXP-002,
FR-PLT-017).

Real Chromium renders are covered in ``tests/exports/test_pdf.py`` and
``tests/platform/test_invoice_pdf.py`` (skipped without Chromium). These checks always run.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.core.pdf as core_pdf
from app.core.config import Environment, Settings
from app.exports import pdf as exports_pdf


def test_ADR_0025_renderer_config_ships_with_core_and_keeps_the_sandbox_on() -> None:
    cfg = core_pdf.load_config()
    assert cfg.chromium_sandbox is True
    assert 1000 <= cfg.timeout_ms <= 300_000
    with pytest.raises(ValidationError):
        core_pdf.PdfConfig.model_validate({"version": 1, "timeout_ms": 1, "chromium_sandbox": True})
    with pytest.raises(ValidationError):
        core_pdf.PdfConfig.model_validate(
            {"version": 1, "timeout_ms": 60000, "chromium_sandbox": True, "extra": 1}
        )


def test_ADR_0025_sandbox_is_required_in_staging_and_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    for env, expected in ((Environment.CI, False), (Environment.PROD, True)):
        settings = Settings.model_construct(env=env)
        monkeypatch.setattr(core_pdf, "get_settings", lambda s=settings: s)
        renderer = core_pdf.default_renderer()
        assert isinstance(renderer, core_pdf.ChromiumRenderer)
        assert renderer.sandbox is expected


def test_renderer_moved_to_core_exports_reuses_the_same_objects() -> None:
    """``app.exports.pdf`` is a re-export: one renderer override for every caller."""
    assert exports_pdf.set_renderer is core_pdf.set_renderer
    assert exports_pdf.get_renderer is core_pdf.get_renderer
    assert exports_pdf.ChromiumRenderer is core_pdf.ChromiumRenderer

    class Fake:
        def render(self, html: str) -> bytes:
            return b"%PDF-fake"

    fake = Fake()
    exports_pdf.set_renderer(fake)
    try:
        assert core_pdf.get_renderer() is fake
    finally:
        core_pdf.set_renderer(None)


def test_core_pdf_imports_no_feature_module() -> None:
    tree = ast.parse(Path(core_pdf.__file__).read_text("utf-8"))
    modules = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module is not None
    }
    app_modules = {m for m in modules if m.startswith("app.")}
    # app.core.languages (ADR-0036) is core too: whether the Telugu font is declared and served.
    assert app_modules <= {"app.core.config", "app.core.languages", "app.core.logging"}, app_modules


def test_bundled_font_lives_next_to_the_core_renderer() -> None:
    assert core_pdf.FONT_PATH.parent == Path(core_pdf.__file__).with_name("fonts")
    assert (core_pdf.FONT_PATH.parent / "OFL.txt").is_file()
    assert core_pdf.font_bytes()[:4] == b"\x00\x01\x00\x00"


class _Request:
    def __init__(self, url: str) -> None:
        self.url = url


class _Route:
    """Records what the renderer's request handler did with one request."""

    def __init__(self, url: str) -> None:
        self.request = _Request(url)
        self.fulfilled: bytes | None = None
        self.aborted = False

    def fulfill(self, *, status: int, body: bytes, content_type: str) -> None:
        self.fulfilled = body

    def abort(self, error_code: str) -> None:
        self.aborted = True


def test_ADR_0036_telugu_font_is_not_declared_or_served_while_telugu_is_hidden() -> None:
    assert core_pdf.font_face_css() == ""
    assert core_pdf.font_stack() == "sans-serif"
    assert core_pdf.font_stack('"Noto Sans", Arial, sans-serif') == '"Noto Sans", Arial, sans-serif'
    route = _Route(core_pdf.FONT_URL)
    core_pdf._route(route)  # type: ignore[arg-type]
    assert route.aborted
    assert route.fulfilled is None


def test_ADR_0036_telugu_font_is_declared_and_served_when_switched_on(telugu_on: None) -> None:
    css = core_pdf.font_face_css()
    assert "@font-face" in css
    assert core_pdf.FONT_URL in css
    assert core_pdf.FONT_FAMILY in css
    assert core_pdf.font_stack() == f'"{core_pdf.FONT_FAMILY}", sans-serif'
    route = _Route(core_pdf.FONT_URL)
    core_pdf._route(route)  # type: ignore[arg-type]
    assert route.fulfilled == core_pdf.font_bytes()
    assert not route.aborted
    other = _Route("https://example.invalid/x.css")
    core_pdf._route(other)  # type: ignore[arg-type]
    assert other.aborted


class _FakePage:
    def __init__(self, seen: dict[str, object]) -> None:
        self.seen = seen

    def set_default_timeout(self, _ms: int) -> None: ...

    def set_content(self, html: str, wait_until: str) -> None:
        self.seen["html"] = html

    def emulate_media(self, media: str) -> None: ...

    def pdf(self, **_kw: object) -> bytes:
        return b"%PDF-fake"


class _FakeBrowser:
    def __init__(self, seen: dict[str, object]) -> None:
        self.seen = seen

    def new_context(self, **kw: object) -> _FakeBrowser:
        self.seen["context"] = kw
        return self

    def route(self, pattern: str, _handler: object) -> None:
        self.seen["route"] = pattern

    def new_page(self) -> _FakePage:
        return _FakePage(self.seen)

    def close(self) -> None: ...


class _FakePlaywright:
    def __init__(self, seen: dict[str, object]) -> None:
        self.seen = seen
        self.chromium = self

    def launch(self, **kw: object) -> _FakeBrowser:
        self.seen["launch"] = kw
        return _FakeBrowser(self.seen)

    def __enter__(self) -> _FakePlaywright:
        return self

    def __exit__(self, *_exc: object) -> None: ...


@pytest.mark.parametrize(
    "html",
    [
        "<!doctype html><html><head><title>t</title></head><body>x</body></html>",
        "<p>no head</p>",
    ],
)
def test_AA_hardening_dns_prefetch_is_off_in_the_pdf_renderer(
    monkeypatch: pytest.MonkeyPatch, html: str
) -> None:
    """Api-auth audit 2026-10-04 hardening note: every request is aborted by the route handler,
    but Chromium resolves ``<link rel=dns-prefetch>`` hosts outside it. The browser starts with
    DNS prefetch and background networking off, and every page says
    ``x-dns-prefetch-control: off`` first thing in its head."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(core_pdf, "sync_playwright", lambda: _FakePlaywright(seen))
    out = core_pdf.ChromiumRenderer(sandbox=False, timeout_ms=1000).render(html)
    assert out == b"%PDF-fake"
    launch = seen["launch"]
    assert isinstance(launch, dict)
    args = launch["args"]
    assert isinstance(args, list)
    assert {
        "--dns-prefetch-disable",
        "--disable-background-networking",
        "--no-pings",
    } <= set(args)
    assert seen["route"] == "**/*"
    rendered = seen["html"]
    assert isinstance(rendered, str)
    meta = '<meta http-equiv="x-dns-prefetch-control" content="off">'
    assert meta in rendered
    head = rendered.lower().find("<head>")
    assert (
        rendered.find(meta) < rendered.find("<title>") if head >= 0 else rendered.startswith(meta)
    )
