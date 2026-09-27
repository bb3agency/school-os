"""Column mapping suggestions from English/Telugu headers (FR-IMP-002). Pure.

Headers and synonyms are normalised the same way (NFC, lower case, punctuation removed, spaces
collapsed). A header that equals a synonym scores 100; otherwise the best rapidfuzz
``token_sort_ratio`` against the target's synonyms is used when it reaches the configured
threshold. Each target is suggested for at most one column (highest score wins). The header
signature (SHA-256 of the normalised headers) finds a school's saved template for the same
layout, which is then reused automatically.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from rapidfuzz import fuzz

SPECIAL_TARGETS: Final = ("class", "section", "class_section", "roll_no")
IGNORE: Final = "ignore"
_PUNCT_RE: Final = re.compile(r"[\s._/\\:;,()\[\]{}#&+*|\"-]+")
_APOSTROPHES: Final = re.compile("['\u2019`\u00b4]")


def normalize_header(text: str) -> str:
    value = unicodedata.normalize("NFC", text).casefold()
    value = _APOSTROPHES.sub("", value)
    return " ".join(_PUNCT_RE.sub(" ", value).split())


def header_signature(headers: Sequence[str]) -> str:
    joined = "\x1f".join(normalize_header(h) for h in headers)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Suggestion:
    index: int
    target: str | None
    score: int


def _score(header: str, synonyms: Sequence[str]) -> int:
    if not header:
        return 0
    best = 0
    for synonym in synonyms:
        norm = normalize_header(synonym)
        if norm == header:
            return 100
        best = max(best, round(fuzz.token_sort_ratio(header, norm)))
    return best


def suggest(
    headers: Sequence[str],
    synonyms: Mapping[str, Sequence[str]],
    *,
    allowed_targets: Collection[str],
    threshold: int,
) -> list[Suggestion]:
    """One suggestion per column (``target`` None when nothing reaches ``threshold``)."""
    normalized = [normalize_header(h) for h in headers]
    candidates: list[tuple[int, int, str]] = []  # (score, column, target)
    for target, words in synonyms.items():
        if target not in allowed_targets:
            continue
        for index, header in enumerate(normalized):
            score = _score(header, words)
            if score >= threshold:
                candidates.append((score, index, target))
    # Highest score first; ties go to the left-most column, then to the target name.
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    chosen: dict[int, tuple[str, int]] = {}
    used: set[str] = set()
    for score, index, target in candidates:
        if index in chosen or target in used:
            continue
        chosen[index] = (target, score)
        used.add(target)
    # "Class" + "Section" columns beat a combined "class & section" guess, and vice versa: keep
    # whichever the file actually has, never both kinds.
    targets = {t for t, _ in chosen.values()}
    if "class_section" in targets and ({"class", "section"} & targets):
        drop = "class_section" if {"class", "section"} <= targets else None
        if drop is None:
            drop = "class" if "class" in targets else "section"
        chosen = {i: v for i, v in chosen.items() if v[0] != drop}
    return [
        Suggestion(i, chosen[i][0], chosen[i][1]) if i in chosen else Suggestion(i, None, 0)
        for i in range(len(headers))
    ]


def mapping_from_template(
    headers: Sequence[str], template_mapping: Mapping[str, str], allowed_targets: Collection[str]
) -> dict[str, str]:
    """Apply a saved template (normalised header -> target) to this file's columns."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for index, header in enumerate(headers):
        target = template_mapping.get(normalize_header(header))
        if target is None or target == IGNORE:
            continue
        if target in used or target not in allowed_targets:
            continue
        out[str(index)] = target
        used.add(target)
    return out


def template_payload(headers: Sequence[str], mapping: Mapping[str, str]) -> dict[str, str]:
    """A batch mapping (column index -> target) as a template (normalised header -> target)."""
    out: dict[str, str] = {}
    for key, target in mapping.items():
        index = int(key)
        if 0 <= index < len(headers):
            header = normalize_header(headers[index])
            if header:
                out[header] = target
    return out


__all__ = [
    "IGNORE",
    "SPECIAL_TARGETS",
    "Suggestion",
    "header_signature",
    "mapping_from_template",
    "normalize_header",
    "suggest",
    "template_payload",
]
