"""Text primitives for chunking: grapheme clusters, token estimates, script-based language ID.

Pure and deterministic. Chunks are cut only at whitespace; a single word longer than a chunk is
cut between grapheme clusters, never inside one, so a Telugu akshara (consonant + virama +
consonant ... + vowel sign) is never broken (docs/06 §4.5, §11).
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterator
from typing import Final

from app.knowledge.config.chunking import TokenEstimate
from app.knowledge.domain import Locale

_ZWNJ: Final = "‌"
_ZWJ: Final = "‍"
_VIRAMA_CLASS: Final = 9
"""Canonical combining class of the Indic viramas (Telugu U+0C4D and its siblings)."""
_LATIN_LIMIT: Final = 0x0250
"""Basic Latin, Latin-1 and Latin Extended-A/B: counted with the Latin token rate."""
_TELUGU: Final = range(0x0C00, 0x0C80)


def _extends(cluster: str, ch: str) -> bool:
    """Whether ``ch`` continues ``cluster`` (UAX #29 GB9, GB9a, GB9c, GB3)."""
    if ch in (_ZWJ, _ZWNJ) or unicodedata.category(ch) in ("Mn", "Mc", "Me"):
        return True
    if "︀" <= ch <= "️":  # variation selectors
        return True
    if cluster == "\r" and ch == "\n":
        return True
    # Indic conjunct: consonant + virama (+ ZWJ/ZWNJ) + consonant stays one cluster.
    tail = cluster.rstrip(_ZWJ + _ZWNJ)
    return (
        bool(tail)
        and unicodedata.combining(tail[-1]) == _VIRAMA_CLASS
        and unicodedata.category(ch) == "Lo"
    )


def graphemes(text: str) -> list[str]:
    """Split ``text`` into grapheme clusters; ``"".join(graphemes(t)) == t``."""
    clusters: list[str] = []
    for ch in text:
        if clusters and _extends(clusters[-1], ch):
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return clusters


def _is_latin(cluster: str) -> bool:
    return ord(cluster[0]) < _LATIN_LIMIT


def _cost(latin_chars: int, other_clusters: int, rate: TokenEstimate) -> int:
    value = latin_chars / rate.latin_chars_per_token
    value += other_clusters / rate.other_graphemes_per_token
    return max(1, math.ceil(value - 1e-9))


def estimate_tokens(word: str, rate: TokenEstimate) -> int:
    """Estimated tokens of one whitespace-free word (at least 1)."""
    latin = other = 0
    for cluster in graphemes(word):
        if _is_latin(cluster):
            latin += len(cluster)
        else:
            other += 1
    return _cost(latin, other, rate)


def count_tokens(text: str, rate: TokenEstimate) -> int:
    """Estimated tokens of ``text``: the sum over its whitespace-separated words."""
    return sum(estimate_tokens(word, rate) for word in text.split())


def split_word(word: str, budget: int, rate: TokenEstimate) -> Iterator[str]:
    """Cut a word into pieces of at most ``budget`` tokens, between grapheme clusters only."""
    piece: list[str] = []
    latin = other = 0
    for cluster in graphemes(word):
        add_latin, add_other = (len(cluster), 0) if _is_latin(cluster) else (0, 1)
        if piece and _cost(latin + add_latin, other + add_other, rate) > budget:
            yield "".join(piece)
            piece, latin, other = [], 0, 0
        piece.append(cluster)
        latin += add_latin
        other += add_other
    if piece:
        yield "".join(piece)


def detect_language(text: str, dominant_share: float) -> Locale | None:
    """``te``/``en`` when that script dominates the letters, ``mixed`` otherwise; None when the
    text has no Telugu or Latin letters (numbers, punctuation)."""
    telugu = latin = 0
    for ch in text:
        if not ch.isalpha() and unicodedata.category(ch) not in ("Mn", "Mc"):
            continue
        code = ord(ch)
        if code in _TELUGU:
            telugu += 1
        elif code < _LATIN_LIMIT:
            latin += 1
    total = telugu + latin
    if total == 0:
        return None
    if telugu / total >= dominant_share:
        return "te"
    if latin / total >= dominant_share:
        return "en"
    return "mixed"


def combine_languages(languages: list[Locale | None]) -> Locale | None:
    """The language of text made of parts in ``languages`` (unknown parts are ignored)."""
    known = {lang for lang in languages if lang is not None}
    if not known:
        return None
    if len(known) == 1:
        return next(iter(known))
    return "mixed"


def normalize_space(text: str) -> str:
    """Collapse every whitespace run to one space and strip (for headers and headings)."""
    return " ".join(text.split())


__all__ = [
    "combine_languages",
    "count_tokens",
    "detect_language",
    "estimate_tokens",
    "graphemes",
    "normalize_space",
    "split_word",
]
