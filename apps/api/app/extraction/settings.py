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
class ExtractionConfig:
    version: int
    fields: tuple[str, ...]
    language_hints: tuple[str, ...]
    documents_per_batch: int
    rows_per_page: int
    value_max_length: int
    accepted_mime_types: tuple[str, ...]
    fake: FakeConfig


def _parse(raw: dict[str, Any]) -> ExtractionConfig:
    limits = raw["limits"]
    fake = raw["fake"]
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
