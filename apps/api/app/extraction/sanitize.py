"""Make provider output safe to store (FR-IMP-021, FR-IMP-022, PRV-015, PRV-016, SEC-013).

This is the first thing that touches a provider result, before any database write or log line:

1. Every string the provider returned (all cells, including columns we do not keep, and the page
   text) is checked for a Verhoeff-valid 12-digit number. A hit marks the page
   ``aadhaar_detected`` so its image is withheld (PRV-016).
2. Kept cells (``config.yaml`` ``fields``) are NFC-normalised, stripped of control characters,
   cut to ``value_max_length`` and masked with :func:`app.core.redaction.mask_aadhaar`
   (``XXXX XXXX 1234``); a masked cell is flagged ``masked``.
3. Confidence is clamped to 0..1 (missing = unknown = low confidence); bounding boxes are kept
   only when they are four finite fractions of the page.
4. The page text is discarded: nothing but the kept, masked cells leaves this function.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from app.core.redaction import contains_full_aadhaar, mask_aadhaar
from app.extraction.providers import FieldReading, PageExtraction
from app.extraction.settings import ExtractionConfig

_CONTROL: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACES: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class CleanRow:
    """One candidate row ready for ``sis.extraction_items.fields``."""

    fields: dict[str, dict[str, Any]]
    low_confidence: bool
    masked: bool


@dataclass(frozen=True, slots=True)
class CleanPage:
    rows: list[CleanRow]
    aadhaar_detected: bool
    dropped_fields: int

    @property
    def low_confidence_rows(self) -> int:
        return sum(1 for r in self.rows if r.low_confidence)


def _flatten(value: str) -> str:
    """NFC, control characters and line breaks to single spaces (so "1234\n5678\n9012" is
    seen as one number by the Verhoeff scan)."""
    text = unicodedata.normalize("NFC", value)
    return _SPACES.sub(" ", _CONTROL.sub(" ", text)).strip()


def _detect(value: str) -> bool:
    return contains_full_aadhaar(value) or contains_full_aadhaar(_flatten(value))


def _confidence(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(min(max(value, 0.0), 1.0), 3)


def _bbox(box: tuple[float, float, float, float] | None) -> list[float] | None:
    if box is None or not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in box):
        return None
    return [round(v, 4) for v in box]


def _strings(page: PageExtraction) -> Iterable[str]:
    yield page.raw_text
    for row in page.rows:
        for key, reading in row.items():
            yield key
            yield reading.value


def _clean_cell(reading: FieldReading, cfg: ExtractionConfig, threshold: float) -> dict[str, Any]:
    text = _flatten(reading.value)
    # Mask BEFORE cutting to length, so a number on the boundary is never half kept.
    masked_text = mask_aadhaar(text)
    value = masked_text[: cfg.value_max_length].strip()
    confidence = _confidence(reading.confidence)
    return {
        "value": value,
        "confidence": confidence,
        "bbox": _bbox(reading.bbox),
        "masked": masked_text != text,
        "low_confidence": confidence is None or confidence < threshold,
    }


def clean_page(page: PageExtraction, cfg: ExtractionConfig, *, threshold: float) -> CleanPage:
    """Mask, validate and reduce one provider result (see module docstring)."""
    detected = any(_detect(s) for s in _strings(page))
    keep = set(cfg.fields)
    rows: list[CleanRow] = []
    dropped = 0
    for raw in page.rows[: cfg.rows_per_page]:
        cells: dict[str, dict[str, Any]] = {}
        for key, reading in raw.items():
            if key not in keep:
                dropped += 1
                continue
            cell = _clean_cell(reading, cfg, threshold)
            if cell["value"]:
                cells[key] = cell
        if not cells:
            continue
        rows.append(
            CleanRow(
                fields=cells,
                low_confidence=any(c["low_confidence"] for c in cells.values()),
                masked=any(c["masked"] for c in cells.values()),
            )
        )
    dropped += sum(len(r) for r in page.rows[cfg.rows_per_page :])
    return CleanPage(rows=rows, aadhaar_detected=detected, dropped_fields=dropped)
