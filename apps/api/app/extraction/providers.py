"""Extraction providers: page image -> candidate register rows (FR-IMP-021, FR-IMP-024).

The pipeline talks to one small interface, :class:`ExtractionProvider`, selected by settings
(``SOS_EXTRACTION_PROVIDER``). Output is untrusted: :mod:`app.extraction.sanitize` masks Aadhaar
numbers and validates every value before anything is stored.

Built here (M1):

- :class:`FakeExtractionProvider` (``fake``): deterministic synthetic rows for local/ci and
  tests; it refuses to run in staging/prod. A PNG may carry a scripted result in a ``tEXt``
  chunk with keyword ``sos-fake-extraction`` (JSON ``{"rows": [...], "raw_text": "...",
  "fail": "unavailable" | "unreadable"}``); otherwise rows are derived from the image hash.
- :class:`NotConfiguredProvider` (``not-configured``, the staging/prod default): every page
  fails with an operator-facing error until a real provider is chosen.

Extension point (M2, chosen by evaluation on Telugu + English, printed and handwritten, docs/06
§4.2): an OCR engine adapter, or Claude vision, which MUST go through ``app.knowledge.gateway``
(CLAUDE.md §11: no LLM SDK outside the gateway; ZDR; model IDs in config). Add a
``ExtractionProviderKind`` value, an adapter class implementing :class:`ExtractionProvider`, and
a branch in :func:`build_provider`. Adapters return raw readings; they never mask, store or log.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import struct
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.core.config import ExtractionProviderKind, Settings
from app.extraction.settings import extraction_config

PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
FAKE_SCRIPT_KEYWORD: Final = b"sos-fake-extraction"
_MAX_SCRIPT_BYTES: Final = 256 * 1024


class ProviderRefused(RuntimeError):
    """A dev-only provider was asked to run in staging/prod (fail closed)."""

    code = "provider_refused"


class ProviderNotConfigured(RuntimeError):
    """No extraction provider is configured for this deployment (operator action needed)."""

    code = "provider_not_configured"


class ExtractionUnavailable(RuntimeError):
    """Transient provider failure (timeout, throttling): the task retries with backoff."""

    code = "provider_unavailable"


class ExtractionFailed(RuntimeError):
    """The provider could not read this page (unreadable image): the page is marked failed."""

    code = "page_unreadable"


@dataclass(frozen=True, slots=True)
class FieldReading:
    """One cell as read by the provider. ``bbox`` = (x, y, width, height), fractions of the page."""

    value: str
    confidence: float | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class PageExtraction:
    """Provider output for one page: candidate rows (field -> reading) and the page text."""

    rows: list[dict[str, FieldReading]] = field(default_factory=list)
    raw_text: str = ""


class ExtractionProvider(Protocol):
    """Pluggable extraction (FR-IMP-024)."""

    name: str

    def extract(self, page_image: bytes, *, language_hints: Sequence[str]) -> PageExtraction:
        """Read one page image. Raise :class:`ExtractionUnavailable` for transient failures and
        :class:`ExtractionFailed` for pages that cannot be read."""
        ...


# --- fake provider (local/ci) ---------------------------------------------------------------


def _png_text_chunks(data: bytes) -> dict[bytes, bytes]:
    """``tEXt`` chunks of a PNG (keyword -> text); empty for anything else."""
    out: dict[bytes, bytes] = {}
    if not data.startswith(PNG_SIGNATURE):
        return out
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if len(body) < length or length > _MAX_SCRIPT_BYTES:
            break
        if kind == b"tEXt" and b"\x00" in body:
            key, _, text = body.partition(b"\x00")
            out[key] = text
        if kind == b"IEND":
            break
        pos += 12 + length
    return out


def fake_script_png(script: Mapping[str, Any]) -> bytes:
    """A minimal valid 1x1 PNG carrying a fake-provider script (tests and local demos only)."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    text = json.dumps(script, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"tEXt", FAKE_SCRIPT_KEYWORD + b"\x00" + text)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + chunk(b"IEND", b"")
    )


def _reading(raw: Any) -> FieldReading | None:
    if isinstance(raw, str):
        return FieldReading(raw)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("value"), str):
        return None
    confidence = raw.get("confidence")
    bbox = raw.get("bbox")
    box: tuple[float, float, float, float] | None = None
    if isinstance(bbox, list | tuple) and len(bbox) == 4:
        box = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return FieldReading(
        raw["value"],
        float(confidence) if isinstance(confidence, int | float) else None,
        box,
    )


class FakeExtractionProvider:
    """Synthetic, deterministic output. Refused in staging/prod (like the dev-noop scanner)."""

    name = "fake"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._refuse()

    def _refuse(self) -> None:
        if self._settings.is_production_like:
            raise ProviderRefused("the fake extraction provider is not allowed in staging/prod")

    def extract(self, page_image: bytes, *, language_hints: Sequence[str]) -> PageExtraction:
        self._refuse()
        script = _png_text_chunks(page_image).get(FAKE_SCRIPT_KEYWORD)
        if script is not None:
            return self._scripted(json.loads(script.decode("ascii")))
        return self._generated(page_image)

    @staticmethod
    def _scripted(script: Mapping[str, Any]) -> PageExtraction:
        fail = script.get("fail")
        if fail == "unavailable":
            raise ExtractionUnavailable("scripted transient failure")
        if fail == "unreadable":
            raise ExtractionFailed("scripted unreadable page")
        rows: list[dict[str, FieldReading]] = []
        for raw_row in script.get("rows", []):
            if not isinstance(raw_row, Mapping):
                continue
            row = {str(k): r for k, v in raw_row.items() if (r := _reading(v)) is not None}
            rows.append(row)
        raw_text = script.get("raw_text", "")
        return PageExtraction(rows=rows, raw_text=raw_text if isinstance(raw_text, str) else "")

    @staticmethod
    def _generated(page_image: bytes) -> PageExtraction:
        # Synthetic names only (app.devtools.names); imported lazily so production code paths
        # never load the generator.
        from app.devtools.names import generate_name  # noqa: PLC0415

        cfg = extraction_config().fake
        seed = int.from_bytes(hashlib.sha256(page_image).digest()[:8], "big")
        rng = random.Random(seed)  # noqa: S311 - synthetic test data, not security
        count = rng.randint(cfg.rows_min, cfg.rows_max)
        base_no = rng.randint(1000, 8999)
        rows: list[dict[str, FieldReading]] = []
        lines: list[str] = []
        height = 0.9 / max(count, 1)

        def conf() -> float:
            return round(rng.uniform(cfg.confidence_min, cfg.confidence_max), 3)

        for i in range(count):
            child = generate_name(rng)
            father = generate_name(rng, gender="m")
            mother = generate_name(rng, gender="f")
            born = dt.date(2009, 1, 1) + dt.timedelta(days=rng.randint(0, 7 * 365))
            admitted = born + dt.timedelta(days=rng.randint(4 * 365, 6 * 365))
            y = round(0.05 + i * height, 4)
            cells = {
                "admission_no": str(base_no + i),
                "admission_date": admitted.isoformat(),
                "full_name": child.register_form,
                "gender": "female" if child.gender == "f" else "male",
                "dob": born.isoformat(),
                "father_name": f"{child.surname} {father.given_name}".upper(),
                "mother_name": f"{child.surname} {mother.given_name}".upper(),
            }
            row: dict[str, FieldReading] = {}
            for col, (key, value) in enumerate(cells.items()):
                box = (round(0.02 + col * 0.14, 4), y, 0.13, round(height * 0.8, 4))
                row[key] = FieldReading(value, conf(), box)
            rows.append(row)
            lines.append(" | ".join(cells.values()))
        return PageExtraction(rows=rows, raw_text="\n".join(lines))


# --- not configured (staging/prod default) --------------------------------------------------


class NotConfiguredProvider:
    """Fails every page with an operator-facing error: choose a provider (M2, by evaluation)."""

    name = "not-configured"

    def extract(self, page_image: bytes, *, language_hints: Sequence[str]) -> PageExtraction:
        raise ProviderNotConfigured(
            "No register-photo extraction provider is configured for this deployment. "
            "Set SOS_EXTRACTION_PROVIDER to an approved provider (docs/06 §4.2)."
        )


def provider_kind(settings: Settings) -> ExtractionProviderKind:
    """The configured kind; unset means ``fake`` locally/in CI and ``not-configured`` elsewhere."""
    if settings.extraction_provider is not None:
        return settings.extraction_provider
    if settings.is_production_like:
        return ExtractionProviderKind.NOT_CONFIGURED
    return ExtractionProviderKind.FAKE


def build_provider(settings: Settings) -> ExtractionProvider:
    kind = provider_kind(settings)
    if kind is ExtractionProviderKind.FAKE:
        return FakeExtractionProvider(settings)
    return NotConfiguredProvider()


__all__ = [
    "FAKE_SCRIPT_KEYWORD",
    "ExtractionFailed",
    "ExtractionProvider",
    "ExtractionUnavailable",
    "FakeExtractionProvider",
    "FieldReading",
    "NotConfiguredProvider",
    "PageExtraction",
    "ProviderNotConfigured",
    "ProviderRefused",
    "build_provider",
    "fake_script_png",
    "provider_kind",
]
