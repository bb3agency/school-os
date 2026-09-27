"""Image redaction of register pages that showed a full Aadhaar number (PRV-016, ADR-0007).

docs/08 PRV-016: the stored image is redacted (the number regions blacked out using OCR bounding
boxes) and the original discarded. The pipeline (:mod:`app.extraction.service`) calls
:func:`redact_page` for a page that :func:`app.extraction.sanitize.page_has_aadhaar` flagged;
the result replaces the original version (``documents.replace_with_redacted``). When this module
raises :class:`Unredactable`, the original is discarded without a copy
(``documents.discard_version``) and the page's rows cannot be confirmed.

Steps, all in memory (nothing here stores, logs or returns digits):

1. :func:`decode`: PNG or JPEG only, at most ``redaction.max_pixels``; EXIF orientation applied
   so the pixels match provider boxes (the page **as displayed**, providers module docstring).
2. :func:`locate`: every Aadhaar-like window :func:`app.core.redaction.find_aadhaar` finds (the
   masking rules: Verhoeff-valid, or next to an Aadhaar keyword) in the text layer (spans joined
   in reading order, so a number split into words is found) and in every cell is mapped to the
   boxes that hold it. A number without a box, a box off the page, or a Verhoeff-valid number
   in the page text that no box covers makes the page unredactable (``not_located``).
3. :func:`black_out`: the boxes, padded (``padding_px`` or ``padding_ratio`` of the box height,
   whichever is larger), are filled black; the pixels are copied into a fresh image and
   re-encoded in the same format, so no EXIF, ICC profile or PNG text chunk survives.
4. The copy is read again by the same provider; if any string of that reading still holds a
   Verhoeff-valid number (``still_legible``) or the copy cannot be read (``reread_failed``),
   the page is unredactable. Transient provider errors propagate (the task retries the page).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Final

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from app.core.redaction import find_aadhaar
from app.extraction import sanitize
from app.extraction.providers import (
    ExtractionFailed,
    ExtractionProvider,
    PageExtraction,
    PixelBox,
)
from app.extraction.settings import RedactionConfig, extraction_config

_FORMATS: Final = {"PNG": "image/png", "JPEG": "image/jpeg"}
_BLACK: Final = (0, 0, 0)


class Unredactable(Exception):
    """The page's image cannot be safely redacted; ``code`` says why (IDs and codes only)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DecodedPage:
    """A page as displayed (RGB) and the format it came in (``PNG`` or ``JPEG``)."""

    image: Image.Image
    format: str


@dataclass(frozen=True, slots=True)
class RedactedImage:
    """The redacted copy: encoded bytes, their content type and how many boxes were filled."""

    data: bytes
    mime_type: str
    regions: int

    def __repr__(self) -> str:
        return f"RedactedImage(mime_type={self.mime_type!r}, regions={self.regions})"


def decode(data: bytes, cfg: RedactionConfig) -> DecodedPage:
    try:
        with Image.open(io.BytesIO(data), formats=list(_FORMATS)) as opened:
            fmt = str(opened.format)
            if opened.width * opened.height > cfg.max_pixels:
                raise Unredactable("too_large")
            upright = ImageOps.exif_transpose(opened)
            return DecodedPage(upright.convert("RGB"), fmt)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise Unredactable("undecodable") from None


def _fraction_box(bbox: tuple[float, float, float, float], size: tuple[int, int]) -> PixelBox:
    x, y, w, h = bbox
    return (
        math.floor(x * size[0]),
        math.floor(y * size[1]),
        math.ceil((x + w) * size[0]),
        math.ceil((y + h) * size[1]),
    )


def _on_page(box: PixelBox, size: tuple[int, int]) -> PixelBox:
    left, top = max(box[0], 0), max(box[1], 0)
    right, bottom = min(box[2], size[0]), min(box[3], size[1])
    if right <= left or bottom <= top:
        raise Unredactable("not_located")
    return (left, top, right, bottom)


def locate(page: PageExtraction, size: tuple[int, int]) -> list[PixelBox]:
    """The boxes (clamped to the page) that hold Aadhaar-like numbers; see module docstring."""
    boxes: list[PixelBox] = []
    placed: set[str] = set()

    # Text layer, joined in reading order; each span's character range in the joined text.
    texts = [sanitize.flatten(span.text) for span in page.spans]
    ranges: list[tuple[int, int]] = []
    pos = 0
    for text in texts:
        ranges.append((pos, pos + len(text)))
        pos += len(text) + 1
    for match in find_aadhaar(" ".join(texts)):
        for span, (start, end) in zip(page.spans, ranges, strict=True):
            if start < match.end and match.start < end:
                if span.box is None:
                    raise Unredactable("not_located")
                boxes.append(_on_page(span.box, size))
        placed.add(match.digits)

    for row in page.rows:
        for reading in row.values():
            found = find_aadhaar(sanitize.flatten(reading.value))
            if not found:
                continue
            if reading.bbox is None:
                raise Unredactable("not_located")
            boxes.append(_on_page(_fraction_box(reading.bbox, size), size))
            placed.update(m.digits for m in found)

    # A checksum-valid number in the page text that no box covers cannot be blacked out.
    for match in find_aadhaar(sanitize.flatten(page.raw_text)):
        if match.verhoeff and match.digits not in placed:
            raise Unredactable("not_located")
    return boxes


def _padded(box: PixelBox, cfg: RedactionConfig, size: tuple[int, int]) -> PixelBox:
    pad = max(cfg.padding_px, math.ceil((box[3] - box[1]) * cfg.padding_ratio))
    return (
        max(box[0] - pad, 0),
        max(box[1] - pad, 0),
        min(box[2] + pad, size[0]),
        min(box[3] + pad, size[1]),
    )


def black_out(page: DecodedPage, boxes: list[PixelBox], cfg: RedactionConfig) -> RedactedImage:
    """Fill the padded ``boxes`` black and encode from pixels only (no metadata)."""
    size = page.image.size
    # A fresh image carries no ``info`` (EXIF, ICC profile, PNG text) from the original.
    clean = Image.new("RGB", size)
    clean.paste(page.image)
    draw = ImageDraw.Draw(clean)
    for box in boxes:
        left, top, right, bottom = _padded(box, cfg, size)
        draw.rectangle((left, top, right - 1, bottom - 1), fill=_BLACK)
    out = io.BytesIO()
    if page.format == "JPEG":
        clean.save(out, "JPEG", quality=cfg.jpeg_quality)
    else:
        clean.save(out, "PNG", optimize=True)
    return RedactedImage(out.getvalue(), _FORMATS[page.format], len(boxes))


def redact_page(
    image: bytes,
    reading: PageExtraction,
    provider: ExtractionProvider,
    *,
    language_hints: tuple[str, ...],
    cfg: RedactionConfig | None = None,
) -> RedactedImage:
    """Decode, locate, black out and verify (module docstring). Raises :class:`Unredactable`;
    lets transient provider errors (``ExtractionUnavailable``) propagate."""
    cfg = cfg or extraction_config().redaction
    page = decode(image, cfg)
    boxes = locate(reading, page.image.size)
    if not boxes:
        raise Unredactable("not_located")
    redacted = black_out(page, boxes, cfg)
    try:
        again = provider.extract(redacted.data, language_hints=language_hints)
    except ExtractionFailed:
        raise Unredactable("reread_failed") from None
    if sanitize.page_has_aadhaar(again):
        raise Unredactable("still_legible")
    return redacted


__all__ = [
    "DecodedPage",
    "RedactedImage",
    "Unredactable",
    "black_out",
    "decode",
    "locate",
    "redact_page",
]
