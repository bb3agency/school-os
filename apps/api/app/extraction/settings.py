"""Extraction configuration loaded from ``config.yaml`` in this package (CLAUDE.md §6.13)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class FakeConfig:
    rows_min: int
    rows_max: int
    confidence_min: float
    confidence_max: float


@dataclass(frozen=True, slots=True)
class RedactionConfig:
    """PRV-016 image redaction (see ``config.yaml`` ``redaction``)."""

    padding_px: int
    padding_ratio: float
    max_pixels: int
    jpeg_quality: int


@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    version: int
    fields: tuple[str, ...]
    language_hints: tuple[str, ...]
    documents_per_batch: int
    rows_per_page: int
    value_max_length: int
    accepted_mime_types: tuple[str, ...]
    redaction: RedactionConfig
    fake: FakeConfig


def _redaction(raw: dict[str, Any]) -> RedactionConfig:
    cfg = RedactionConfig(
        padding_px=int(raw["padding_px"]),
        padding_ratio=float(raw["padding_ratio"]),
        max_pixels=int(raw["max_pixels"]),
        jpeg_quality=int(raw["jpeg_quality"]),
    )
    if not (
        0 <= cfg.padding_px <= 50
        and 0.0 <= cfg.padding_ratio <= 1.0
        and 1 <= cfg.max_pixels <= 100_000_000
        and 50 <= cfg.jpeg_quality <= 95
    ):
        raise ValueError("extraction redaction settings out of range")
    return cfg


def _parse(raw: dict[str, Any]) -> ExtractionConfig:
    limits = raw["limits"]
    fake = raw["fake"]
    redaction = raw["redaction"]
    fields = tuple(str(f) for f in raw["fields"])
    if len(set(fields)) != len(fields) or not fields:
        raise ValueError("extraction fields must be unique and non-empty")
    return ExtractionConfig(
        version=int(raw["version"]),
        fields=fields,
        language_hints=tuple(str(h) for h in raw["language_hints"]),
        documents_per_batch=int(limits["documents_per_batch"]),
        rows_per_page=int(limits["rows_per_page"]),
        value_max_length=int(limits["value_max_length"]),
        accepted_mime_types=tuple(str(m) for m in raw["accepted_mime_types"]),
        redaction=_redaction(redaction),
        fake=FakeConfig(
            rows_min=int(fake["rows_min"]),
            rows_max=int(fake["rows_max"]),
            confidence_min=float(fake["confidence_min"]),
            confidence_max=float(fake["confidence_max"]),
        ),
    )


@lru_cache(maxsize=1)
def extraction_config() -> ExtractionConfig:
    text = resources.files("app.extraction").joinpath("config.yaml").read_text("utf-8")
    return _parse(yaml.safe_load(text))
