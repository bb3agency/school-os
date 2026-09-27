"""Extraction provider interface and the dev/prod provider choice (FR-IMP-024, FR-IMP-021).

No database: the fake provider is deterministic, refuses staging/prod, and staging/prod default
to a provider that fails with an operator-facing error until a real one is chosen.
"""

from __future__ import annotations

import io
import sys

import pytest
from pydantic import SecretStr

from app.core.config import Environment, ExtractionProviderKind, KeyWrapperKind, Settings
from app.core.redaction import contains_full_aadhaar
from app.extraction.providers import (
    FAKE_INK,
    FAKE_PAPER,
    ExtractionFailed,
    ExtractionUnavailable,
    FakeExtractionProvider,
    NotConfiguredProvider,
    ProviderNotConfigured,
    ProviderRefused,
    TextSpan,
    build_provider,
    fake_script_png,
    provider_kind,
)
from app.extraction.settings import extraction_config

X = sys.modules["sos_test_extraction_support"]
HINTS = ("en", "te")


def _settings(env: Environment, **extra: object) -> Settings:
    if env in (Environment.STAGING, Environment.PROD):
        return Settings(
            env=env,
            key_wrapper=KeyWrapperKind.KMS,
            database_url=SecretStr("postgresql+psycopg://sos_app:x@db:5432/schoolos"),
            platform_database_url=SecretStr("postgresql+psycopg://sos_platform:y@db:5432/s"),
            service_token_key=SecretStr("k" * 48),
            **extra,  # type: ignore[arg-type]
        )
    return Settings(env=env, **extra)  # type: ignore[arg-type]


@pytest.mark.parametrize("env", [Environment.LOCAL, Environment.CI])
def test_FR_IMP_024_fake_is_the_default_locally_and_in_ci(env: Environment) -> None:
    assert provider_kind(_settings(env)) is ExtractionProviderKind.FAKE
    assert isinstance(build_provider(_settings(env)), FakeExtractionProvider)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_FR_IMP_024_staging_and_prod_default_to_not_configured(env: Environment) -> None:
    provider = build_provider(_settings(env))
    assert isinstance(provider, NotConfiguredProvider)
    with pytest.raises(ProviderNotConfigured, match="SOS_EXTRACTION_PROVIDER"):
        provider.extract(fake_script_png({"rows": []}), language_hints=HINTS)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_FR_IMP_024_fake_provider_is_refused_in_production_like_envs(env: Environment) -> None:
    settings = _settings(env, extraction_provider=ExtractionProviderKind.FAKE)
    with pytest.raises(ProviderRefused):
        build_provider(settings)
    # Even an instance built elsewhere refuses to run there.
    local = FakeExtractionProvider(_settings(Environment.CI))
    local._settings = settings
    with pytest.raises(ProviderRefused):
        local.extract(fake_script_png({"rows": []}), language_hints=HINTS)


def test_FR_IMP_021_generated_rows_are_deterministic_with_confidence_and_regions() -> None:
    provider = FakeExtractionProvider(_settings(Environment.CI))
    image = b"\xff\xd8\xff\xe0 synthetic jpeg page " + b"7" * 64 + b"\xff\xd9"
    first = provider.extract(image, language_hints=HINTS)
    again = provider.extract(image, language_hints=HINTS)
    assert first == again
    cfg = extraction_config()
    assert cfg.fake.rows_min <= len(first.rows) <= cfg.fake.rows_max
    for row in first.rows:
        assert set(row) <= set(cfg.fields)
        for reading in row.values():
            assert reading.confidence is not None
            assert 0.0 <= reading.confidence <= 1.0
            assert reading.bbox is not None
            assert all(0.0 <= v <= 1.0 for v in reading.bbox)
    assert not contains_full_aadhaar(first.raw_text), "the fake never invents Aadhaar numbers"
    other = provider.extract(image + b"x", language_hints=HINTS)
    assert other != first


def test_FR_IMP_024_scripted_png_drives_the_fake_provider() -> None:
    provider = FakeExtractionProvider(_settings(Environment.CI))
    script = {
        "rows": [{"full_name": {"value": "సింథటిక లక్ష్మి", "confidence": 0.5, "bbox": [0, 0, 1, 1]}}],
        "raw_text": "page text",
    }
    result = provider.extract(fake_script_png(script), language_hints=HINTS)
    assert result.raw_text == "page text"
    reading = result.rows[0]["full_name"]
    assert reading.value == "సింథటిక లక్ష్మి"
    assert reading.confidence == 0.5
    assert reading.bbox == (0.0, 0.0, 1.0, 1.0)
    with pytest.raises(ExtractionUnavailable):
        provider.extract(fake_script_png({"fail": "unavailable"}), language_hints=HINTS)
    with pytest.raises(ExtractionFailed):
        provider.extract(fake_script_png({"fail": "unreadable"}), language_hints=HINTS)


def test_FR_IMP_024_scripted_png_is_a_valid_png_for_the_upload_checks() -> None:
    from app.documents import filetypes

    png = X.page_png([X.register_row("Synthetica Page")])
    assert filetypes.sniff(png) is filetypes.PNG


# --- PRV-016 geometry: text spans with pixel boxes ------------------------------------------


def _rendered(spans: list[dict[str, object]], size: tuple[int, int] = (200, 100)) -> bytes:
    return fake_script_png({"size": list(size), "spans": spans, "rows": []})


def test_PRV_016_rendered_script_is_a_real_page_read_back_with_span_boxes() -> None:
    from PIL import Image

    png = _rendered([{"text": "Synthetica", "box": [10, 10, 60, 20]}, {"text": "Rao"}])
    with Image.open(io.BytesIO(png)) as page:
        assert page.size == (200, 100)
        assert page.convert("RGB").getpixel((20, 15)) == FAKE_INK
        assert page.convert("RGB").getpixel((150, 80)) == FAKE_PAPER
    result = FakeExtractionProvider(_settings(Environment.CI)).extract(png, language_hints=HINTS)
    assert result.spans == [
        TextSpan("Synthetica", (10, 10, 60, 20)),
        TextSpan("Rao", None),  # an engine may give text without geometry
    ]
    assert result.raw_text == "Synthetica Rao"


def test_PRV_016_blacked_out_regions_are_not_read_back() -> None:
    from PIL import Image, ImageDraw, PngImagePlugin

    png = _rendered(
        [{"text": "keep", "box": [10, 10, 40, 20]}, {"text": "gone", "box": [100, 50, 150, 70]}]
    )
    with Image.open(io.BytesIO(png)) as opened:
        script = opened.info["sos-fake-extraction"]
        page = opened.convert("RGB")
    ImageDraw.Draw(page).rectangle((98, 48, 152, 72), fill=(0, 0, 0))
    info = PngImagePlugin.PngInfo()
    info.add_text("sos-fake-extraction", script)  # test only: keep the script to read it back
    out = io.BytesIO()
    page.save(out, "PNG", pnginfo=info)
    result = FakeExtractionProvider(_settings(Environment.CI)).extract(
        out.getvalue(), language_hints=HINTS
    )
    assert [s.text for s in result.spans] == ["keep"]
    assert result.raw_text == "keep"


def test_PRV_016_generated_pages_report_cell_spans_in_pixels() -> None:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (400, 300), (255, 255, 255)).save(out, "PNG")
    result = FakeExtractionProvider(_settings(Environment.CI)).extract(
        out.getvalue(), language_hints=HINTS
    )
    cells = [r for row in result.rows for r in row.values()]
    assert len(result.spans) == len(cells) > 0
    for span in result.spans:
        assert span.box is not None
        left, top, right, bottom = span.box
        assert 0 <= left < right <= 400
        assert 0 <= top < bottom <= 300
