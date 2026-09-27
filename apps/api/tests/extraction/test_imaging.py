"""Image redaction of register pages that showed a full Aadhaar number (PRV-016, ADR-0007,
invariant 4). Synthetic images and numbers only; valid-checksum numbers exist only in memory.

- ``locate``: every Aadhaar-like number in the provider output is placed on the image through
  span or cell boxes (also when word-level OCR splits it into several spans); a number that
  cannot be placed makes the page unredactable.
- ``black_out``: the boxes (padded) are filled black and the image is re-encoded from pixels
  only: no EXIF, ICC or PNG text chunks survive.
- ``redact_page``: decode -> locate -> black out -> read the copy again with the provider; a
  number still legible (or a copy that cannot be read) makes the page unredactable.
"""

from __future__ import annotations

import dataclasses
import io
import sys
from collections.abc import Sequence

import pytest
from PIL import Image

from app.core.config import Environment, Settings
from app.extraction import imaging
from app.extraction.imaging import Unredactable
from app.extraction.providers import (
    ExtractionFailed,
    ExtractionUnavailable,
    FakeExtractionProvider,
    FieldReading,
    PageExtraction,
    TextSpan,
    fake_script_png,
)
from app.extraction.settings import extraction_config

X = sys.modules["sos_test_extraction_support"]
HINTS = ("en", "te")
NUMBER = X.valid_aadhaar_like(7)
CFG = extraction_config().redaction


def _png(
    size: tuple[int, int] = (300, 120), colour: tuple[int, int, int] = (200, 200, 200)
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


def _split_spans() -> list[TextSpan]:
    """Word-level OCR: the number arrives as three spans."""
    return [
        TextSpan("Synthetica", (5, 5, 80, 25)),
        TextSpan(NUMBER[:4], (100, 50, 140, 70)),
        TextSpan(NUMBER[4:8], (145, 50, 185, 70)),
        TextSpan(NUMBER[8:], (190, 50, 230, 70)),
        TextSpan("Rao", (5, 90, 40, 110)),
    ]


class _Stub:
    """A provider that reads the redacted copy as ``again`` (or raises it)."""

    name = "stub"

    def __init__(self, again: PageExtraction | Exception) -> None:
        self.again = again
        self.calls: list[bytes] = []

    def extract(self, page_image: bytes, *, language_hints: Sequence[str]) -> PageExtraction:
        self.calls.append(page_image)
        if isinstance(self.again, Exception):
            raise self.again
        return self.again


# --- locate ---------------------------------------------------------------------------------


def test_PRV_016_locate_boxes_every_span_of_a_number_split_by_word_ocr() -> None:
    page = PageExtraction(spans=_split_spans(), raw_text="")
    boxes = imaging.locate(page, (300, 120))
    assert sorted(boxes) == [(100, 50, 140, 70), (145, 50, 185, 70), (190, 50, 230, 70)]


def test_PRV_016_locate_uses_cell_boxes_as_fractions_of_the_page() -> None:
    cell = FieldReading(f"Father {NUMBER}", 0.9, (0.5, 0.25, 0.25, 0.5))
    page = PageExtraction(rows=[{"father_name": cell}], spans=[])
    assert imaging.locate(page, (400, 200)) == [(200, 50, 300, 150)]


def test_PRV_016_locate_also_boxes_keyword_context_numbers() -> None:
    # Not Verhoeff-valid, but next to "Aadhaar": masked in text, so blacked out on the image.
    invalid = NUMBER[:-1] + str((int(NUMBER[-1]) + 1) % 10)
    page = PageExtraction(
        spans=[TextSpan("Aadhaar", (0, 0, 50, 10)), TextSpan(invalid, (60, 0, 160, 10))]
    )
    assert (60, 0, 160, 10) in imaging.locate(page, (200, 20))


@pytest.mark.parametrize(
    "page",
    [
        PageExtraction(spans=[TextSpan(NUMBER, None)]),
        PageExtraction(rows=[{"uid": FieldReading(NUMBER, 0.9, None)}]),
        # The page text shows a number that no span or cell places on the image.
        PageExtraction(raw_text=f"x {NUMBER} y", spans=[TextSpan("x", (0, 0, 5, 5))]),
        # A box entirely outside the image.
        PageExtraction(spans=[TextSpan(NUMBER, (500, 500, 600, 520))]),
    ],
    ids=["span-without-box", "cell-without-box", "text-only", "box-off-page"],
)
def test_PRV_016_a_number_that_cannot_be_placed_makes_the_page_unredactable(
    page: PageExtraction,
) -> None:
    with pytest.raises(Unredactable) as err:
        imaging.locate(page, (300, 120))
    assert err.value.code == "not_located"
    assert NUMBER not in str(err.value)


def test_PRV_016_locate_finds_nothing_on_a_clean_page() -> None:
    page = PageExtraction(spans=[TextSpan("Synthetica Rao", (0, 0, 10, 10))], raw_text="A-1024")
    assert imaging.locate(page, (100, 100)) == []


# --- black out ------------------------------------------------------------------------------


def test_PRV_016_black_out_fills_padded_boxes_and_keeps_the_rest() -> None:
    decoded = imaging.decode(_png(), CFG)
    out = imaging.black_out(decoded, [(100, 50, 140, 70)], CFG)
    assert out.mime_type == "image/png"
    assert out.regions == 1
    with Image.open(io.BytesIO(out.data)) as img:
        rgb = img.convert("RGB")
        assert rgb.size == (300, 120)
        for x, y in ((100, 50), (139, 69), (120, 60), (100 - CFG.padding_px, 50)):
            assert rgb.getpixel((x, y)) == (0, 0, 0), (x, y)
        assert rgb.getpixel((20, 20)) == (200, 200, 200)
        assert rgb.getpixel((250, 110)) == (200, 200, 200)


def test_PRV_016_redacted_png_carries_no_metadata_from_the_original() -> None:
    script_png = fake_script_png(
        {"size": [200, 80], "spans": [{"text": NUMBER, "box": [10, 10, 120, 30]}], "rows": []}
    )
    out = imaging.black_out(imaging.decode(script_png, CFG), [(10, 10, 120, 30)], CFG)
    for marker in (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"iCCP", b"sos-fake-extraction"):
        assert marker not in out.data
    assert NUMBER.encode() not in out.data


def test_PRV_016_jpeg_is_turned_upright_and_stripped_of_exif() -> None:
    src = Image.new("RGB", (200, 100), (180, 180, 180))
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW to display
    exif[0x010E] = f"Synthetic {NUMBER}"  # ImageDescription
    raw = io.BytesIO()
    src.save(raw, "JPEG", exif=exif.tobytes(), quality=95)
    decoded = imaging.decode(raw.getvalue(), CFG)
    assert decoded.image.size == (100, 200), "boxes are in the page as displayed"
    out = imaging.black_out(decoded, [(10, 10, 60, 40)], CFG)
    assert out.mime_type == "image/jpeg"
    assert b"Exif\x00\x00" not in out.data
    assert NUMBER.encode() not in out.data
    with Image.open(io.BytesIO(out.data)) as img:
        assert img.size == (100, 200)
        assert not img.getexif()
        region = img.convert("L").crop((12, 12, 58, 38))
        high = region.getextrema()[1]
        assert isinstance(high, int)
        assert high < 16, "the box is black (JPEG noise allowed)"


# --- decode ---------------------------------------------------------------------------------


def test_PRV_016_undecodable_or_oversized_images_are_unredactable() -> None:
    with pytest.raises(Unredactable) as err:
        imaging.decode(b"\x89PNG\r\n\x1a\n not really", CFG)
    assert err.value.code == "undecodable"
    with pytest.raises(Unredactable) as err:
        imaging.decode(b"%PDF-1.7 synthetic", CFG)
    assert err.value.code == "undecodable"
    small = dataclasses.replace(CFG, max_pixels=100)
    with pytest.raises(Unredactable) as err:
        imaging.decode(_png((20, 20)), small)
    assert err.value.code == "too_large"


# --- redact_page ----------------------------------------------------------------------------


def _script_page() -> tuple[bytes, PageExtraction]:
    spans = [
        {"text": "Synthetica", "box": [5, 5, 80, 25]},
        {"text": NUMBER[:4], "box": [100, 50, 140, 70]},
        {"text": NUMBER[4:8], "box": [145, 50, 185, 70]},
        {"text": NUMBER[8:], "box": [190, 50, 230, 70]},
    ]
    png = fake_script_png({"size": [300, 120], "spans": spans, "rows": []})
    provider = FakeExtractionProvider(Settings(env=Environment.CI))
    return png, provider.extract(png, language_hints=HINTS)


def test_PRV_016_redact_page_blacks_out_and_reads_the_copy_again() -> None:
    png, first = _script_page()
    reader = _Stub(PageExtraction(spans=[TextSpan("Synthetica", (5, 5, 80, 25))]))
    out = imaging.redact_page(png, first, reader, language_hints=HINTS)
    assert out.regions == 3
    assert reader.calls == [out.data], "the redacted copy, not the original, is read again"
    with Image.open(io.BytesIO(out.data)) as img:
        assert img.convert("RGB").getpixel((120, 60)) == (0, 0, 0)


def test_PRV_016_redact_page_with_the_fake_provider_end_to_end() -> None:
    png, first = _script_page()
    provider = FakeExtractionProvider(Settings(env=Environment.CI))
    out = imaging.redact_page(png, first, provider, language_hints=HINTS)
    assert b"sos-fake-extraction" not in out.data


@pytest.mark.parametrize(
    ("again", "code"),
    [
        (
            PageExtraction(raw_text=f"still {NUMBER[:4]} {NUMBER[4:8]} {NUMBER[8:]}"),
            "still_legible",
        ),
        (PageExtraction(spans=[TextSpan(NUMBER, (0, 0, 1, 1))]), "still_legible"),
        (PageExtraction(rows=[{"x": FieldReading(NUMBER)}]), "still_legible"),
        (ExtractionFailed("scripted unreadable page"), "reread_failed"),
    ],
    ids=["text", "span", "cell", "unreadable"],
)
def test_PRV_016_a_copy_that_still_shows_a_number_is_unredactable(
    again: PageExtraction | Exception, code: str
) -> None:
    png, first = _script_page()
    with pytest.raises(Unredactable) as err:
        imaging.redact_page(png, first, _Stub(again), language_hints=HINTS)
    assert err.value.code == code
    assert NUMBER not in str(err.value)


def test_PRV_016_transient_provider_errors_on_the_second_read_propagate() -> None:
    png, first = _script_page()
    with pytest.raises(ExtractionUnavailable):
        imaging.redact_page(png, first, _Stub(ExtractionUnavailable("busy")), language_hints=HINTS)


def test_PRV_016_nothing_to_place_is_unredactable_not_a_silent_pass() -> None:
    # The pipeline only calls redact_page for a page flagged by sanitize; if no box can be
    # found for it, the page must not be treated as redacted.
    with pytest.raises(Unredactable) as err:
        imaging.redact_page(
            _png(), PageExtraction(raw_text=NUMBER), _Stub(PageExtraction()), language_hints=HINTS
        )
    assert err.value.code == "not_located"
