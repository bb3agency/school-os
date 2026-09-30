"""Offline deterministic reranker (``SOS_KB_PROVIDER_MODE=fake``: local, CI, offline evals).

A stand-in for a cross-encoder that looks at the question and each passage **together**, which
the fused first stage does not: the vector branch compares two independent (here hashed, not
semantic) vectors and RRF only sees ranks. Score of a passage for a question:

- every question word found in the passage counts its inverse document frequency over the
  candidate set (rare words matter, words every candidate shares do not); words with a digit
  (dates, amounts, reference numbers) count double;
- a question word not found exactly counts half its weight when a passage word shares most of
  its character trigrams (inflections, transliteration variants, OCR noise);
- every pair of consecutive question words found next to each other adds half the smaller
  weight of the two (phrases).

No meaning, no translation, no learned weights: offline numbers with it measure the pipeline
(what is sent, fallback, ordering, leakage), not a real reranker's quality (docs/06 §13.5).
"""

from __future__ import annotations

import itertools
import math
import re
import unicodedata
from collections.abc import Sequence
from typing import Final

FAKE_MODEL: Final = "sos-fake-lexical-rerank-v1"
_WORD: Final = re.compile(r"[\w\u0c00-\u0c7f]+")  # Telugu signs are not \w in re
_STOP: Final = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "be", "of", "to", "in", "on", "at", "for", "by",
        "and", "or", "what", "when", "which", "who", "how", "does", "do", "did", "will", "with",
        "from", "this", "that", "it", "its", "our", "about", "me", "tell", "please", "school",
    }
)  # fmt: skip
_FUZZY_MIN: Final = 0.5


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFC", text).casefold()
    return [w for w in _WORD.findall(folded) if len(w) >= 2 and w not in _STOP]


def _trigrams(word: str) -> frozenset[str]:
    padded = f" {word} "
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def _similar(word: str, others: frozenset[str]) -> bool:
    if len(word) < 4:
        return False
    mine = _trigrams(word)
    for other in others:
        if len(other) < 4:
            continue
        theirs = _trigrams(other)
        if len(mine & theirs) / len(mine | theirs) >= _FUZZY_MIN:
            return True
    return False


class FakeReranker:
    """:class:`app.knowledge.interfaces.Reranker`, deterministic and offline."""

    name = "fake"
    model = FAKE_MODEL

    def rerank(self, query: str, passages: Sequence[str], *, timeout_s: float) -> list[float]:
        del timeout_s  # no network
        question = _words(query)
        if not question or not passages:
            return [0.0] * len(passages)
        docs = [_words(p) for p in passages]
        sets = [frozenset(d) for d in docs]
        n = len(passages)
        weight = {}
        for w in dict.fromkeys(question):
            df = sum(1 for s in sets if w in s)
            idf = math.log(1 + n / (1 + df))
            weight[w] = idf * (2.0 if any(ch.isdigit() for ch in w) else 1.0)
        scores = []
        for words, present in zip(docs, sets, strict=True):
            score = 0.0
            for w in dict.fromkeys(question):
                if w in present:
                    score += weight[w]
                elif _similar(w, present):
                    score += weight[w] / 2
            pairs = set(itertools.pairwise(words))
            for a, b in itertools.pairwise(question):
                if (a, b) in pairs:
                    score += min(weight[a], weight[b]) / 2
            scores.append(round(score, 6))
        return scores


__all__ = ["FAKE_MODEL", "FakeReranker"]
