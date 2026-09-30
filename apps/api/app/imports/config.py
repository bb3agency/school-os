"""Typed view of ``app/imports/config.yaml`` (limits, synonyms, required fields; §6.13)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from app.core.languages import shown_texts


@dataclass(frozen=True, slots=True)
class Limits:
    max_file_bytes: int
    max_rows: int
    max_columns: int
    max_cell_chars: int
    header_search_rows: int
    header_min_text_cells: int
    xlsx_max_uncompressed_bytes: int
    xlsx_max_compression_ratio: int
    xlsx_max_members: int
    max_scanned_rows: int


@dataclass(frozen=True, slots=True)
class ImportConfig:
    limits: Limits
    suggest_threshold: int
    revert_window_hours: int
    raw_file_retention_days: int
    creating_sources: frozenset[str]
    required_always: tuple[str, ...]
    required_create: tuple[str, ...]
    required_create_by_source: Mapping[str, tuple[str, ...]]
    text_max_length: Mapping[str, int]
    synonyms: Mapping[str, tuple[str, ...]]
    enum_synonyms: Mapping[str, Mapping[str, tuple[str, ...]]]
    class_aliases: Mapping[str, tuple[str, ...]]
    class_noise_words: tuple[str, ...]
    sheet_watermark_texts: Mapping[str, str] = field(default_factory=dict)

    @property
    def sheet_watermark(self) -> str:
        """The download watermark in the languages shown (English only while Telugu is
        hidden, ADR-0036)."""
        return shown_texts(self.sheet_watermark_texts)

    def max_length(self, key: str) -> int:
        return int(self.text_max_length.get(key, self.text_max_length["default"]))


def _strs(values: Any) -> tuple[str, ...]:
    return tuple(str(v) for v in (values or ()))


def parse_config(raw: Mapping[str, Any]) -> ImportConfig:
    limits = raw["limits"]
    required = raw["required"]
    return ImportConfig(
        limits=Limits(**{k: int(v) for k, v in limits.items()}),
        suggest_threshold=int(raw["mapping"]["suggest_threshold"]),
        revert_window_hours=int(raw["revert_window_hours"]),
        raw_file_retention_days=int(raw["raw_file_retention_days"]),
        creating_sources=frozenset(_strs(raw["creating_sources"])),
        required_always=_strs(required["always"]),
        required_create=_strs(required["create"]),
        required_create_by_source={
            str(k): _strs(v) for k, v in (required.get("create_by_source") or {}).items()
        },
        text_max_length={str(k): int(v) for k, v in raw["text_max_length"].items()},
        synonyms={str(k): _strs(v) for k, v in raw["synonyms"].items()},
        enum_synonyms={
            str(attr): {str(value): _strs(words) for value, words in options.items()}
            for attr, options in raw["enum_synonyms"].items()
        },
        class_aliases={str(k): _strs(v) for k, v in raw["class_aliases"].items()},
        class_noise_words=_strs(raw["class_noise_words"]),
        sheet_watermark_texts={
            str(k): str(v)
            for k, v in ((raw.get("sheet") or {}).get("export_watermark") or {}).items()
        },
    )


@lru_cache(maxsize=1)
def import_config() -> ImportConfig:
    text = resources.files("app.imports").joinpath("config.yaml").read_text("utf-8")
    return parse_config(yaml.safe_load(text))
