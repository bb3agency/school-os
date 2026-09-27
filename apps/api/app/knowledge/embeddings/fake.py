"""Deterministic offline embeddings for local/CI and the eval stubs (ADR-0006; SEC-020).

Vectors are hashed bags of features: whole words plus character trigrams of each word, on the
NFC-normalised, case-folded text. Each feature lands in one of ``dimensions`` buckets with a
sign (both from ``blake2b``, which is not salted per process), and the sum is L2-normalised.
Texts that share words or word fragments therefore get a high cosine similarity, in any script
(Telugu vowel signs are kept inside words), and the same text always gives the same vector in
every process. It is not a language model: it knows nothing about meaning or translation, so
retrieval quality measured with it says nothing about a real provider.

No network, no randomness, no logging. ``input_type`` is ignored so queries and documents share
one space (a real provider may differ; the cache keeps them apart anyway).
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections.abc import Sequence

from app.knowledge.domain import InputType

_WORD_WEIGHT = 1.0
_TRIGRAM_WEIGHT = 0.5
_EMPTY_FEATURE = "\x00empty"


def _words(text: str) -> list[str]:
    """Split on anything that is not a letter, combining mark or number (keeps Telugu matras)."""
    norm = unicodedata.normalize("NFC", text).casefold()
    kept = "".join(ch if unicodedata.category(ch)[0] in "LMN" else " " for ch in norm)
    return kept.split()


def _features(text: str) -> list[tuple[str, float]]:
    features: list[tuple[str, float]] = []
    for word in _words(text):
        features.append(("w:" + word, _WORD_WEIGHT))
        padded = f" {word} "
        features.extend(("t:" + padded[i : i + 3], _TRIGRAM_WEIGHT) for i in range(len(padded) - 2))
    return features or [(_EMPTY_FEATURE, 1.0)]


class FakeEmbeddingsProvider:
    """:class:`app.knowledge.interfaces.EmbeddingsProvider` without a network."""

    def __init__(self, *, model: str, dimensions: int) -> None:
        if dimensions < 2:
            raise ValueError("dimensions must be at least 2")
        self._model = model
        self._dimensions = dimensions

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        del input_type  # one shared space for queries and documents
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for feature, weight in _features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            sign = 1.0 if value & 1 else -1.0
            vector[(value >> 1) % self._dimensions] += sign * weight
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0.0:  # every feature cancelled out: fall back to a fixed unit vector
            vector[0] = 1.0
            return vector
        return [x / norm for x in vector]
