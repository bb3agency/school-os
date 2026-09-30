"""Voyage AI reranking over HTTPS with ``httpx`` (no SDK; docs/06 §6, §13.6; ADR-0035 Proposed).

Disabled unless ``SOS_KB_PROVIDER_MODE=live`` AND ``retrieval.yaml`` ``rerank.provider`` (or
``SOS_KB_RERANK``) is ``voyage`` AND ``rerank.voyage.model`` names the model an evaluation chose:
the composition root passes :func:`build_voyage_reranker` to ``rerank.select_reranker`` as the
``"voyage"`` factory. Same vendor, organization key (``SOS_EMBEDDINGS_API_KEY``) and data
category as the Voyage embeddings candidate (docs/08 §1: document chunk text and questions), but
a new purpose: enabling it needs the sub-processor register entry updated and schools notified.

One call is one request: ``POST rerank.voyage.endpoint`` with ``query``, ``documents`` (the
candidates' text: ONLY passages that already passed the caller's ACL predicate in SQL,
Aadhaar-masked by ingestion and again by the retriever; invariants 4 and 8), ``model``,
``truncation: true`` (Voyage cuts an over-long passage instead of failing the call; the retriever
already caps each passage at ``max_passage_chars``). The response's ``data[].index`` /
``relevance_score`` become one score per passage, in order. Timeout = the caller's latency
budget. No retries (the retriever falls back to the RRF order at once), nothing logged here,
errors carry status codes only: never the query, passages, body or key.

API shape as documented by Voyage (``/v1/rerank``, models ``rerank-2.5`` / ``rerank-2.5-lite``);
re-check against the provider's reference before the live evaluation (docs/06 §13.6).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import Settings
from app.knowledge.config.retrieval import Rerank

PROVIDER = "voyage"
MAX_DOCUMENTS = 1000
"""Voyage's documented per-request limit; ``rerank.candidates`` is at most 150."""


class VoyageRerankError(RuntimeError):
    """A failed rerank call (status code or error type only)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class VoyageReranker:
    """:class:`app.knowledge.interfaces.Reranker` for one Voyage rerank model."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        endpoint: str,
        connect_timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Voyage reranking needs SOS_EMBEDDINGS_API_KEY")
        self._model = model
        self._endpoint = endpoint
        self._connect = connect_timeout_s
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def model(self) -> str:
        return self._model

    def close(self) -> None:
        self._client.close()

    def rerank(self, query: str, passages: Sequence[str], *, timeout_s: float) -> list[float]:
        if not passages:
            return []
        if len(passages) > MAX_DOCUMENTS:
            raise ValueError(f"at most {MAX_DOCUMENTS} passages per Voyage rerank request")
        payload = {
            "query": query,
            "documents": list(passages),
            "model": self._model,
            "truncation": True,
        }
        connect = min(self._connect, timeout_s)
        timeout = httpx.Timeout(timeout_s, connect=connect, pool=connect)
        try:
            response = self._client.post(self._endpoint, json=payload, timeout=timeout)
        except httpx.TimeoutException:
            raise VoyageRerankError("voyage rerank: timeout") from None
        except httpx.TransportError as exc:
            raise VoyageRerankError(
                f"voyage rerank: transport error {type(exc).__name__}"
            ) from None
        if response.status_code != httpx.codes.OK:
            raise VoyageRerankError(
                f"voyage rerank: HTTP {response.status_code}", status=response.status_code
            )
        return self._parse(response, len(passages))

    @staticmethod
    def _parse(response: httpx.Response, count: int) -> list[float]:
        try:
            body: Any = response.json()
            rows = body["data"]
            scores: dict[int, float] = {}
            for row in rows:
                index, score = int(row["index"]), float(row["relevance_score"])
                if not 0 <= index < count or index in scores or not math.isfinite(score):
                    raise ValueError("bad row")
                scores[index] = score
        except (ValueError, KeyError, TypeError):
            raise VoyageRerankError("voyage rerank: malformed response") from None
        if len(scores) != count:
            raise VoyageRerankError(f"voyage rerank: {len(scores)} scores for {count} passages")
        return [scores[i] for i in range(count)]


def build_voyage_reranker(
    config: Rerank, *, settings: Settings, transport: httpx.BaseTransport | None = None
) -> VoyageReranker:
    """The ``"voyage"`` factory for ``rerank.select_reranker`` (bind settings)."""
    if config.provider != PROVIDER:
        raise ValueError(f"not a voyage rerank configuration: {config.provider!r}")
    if config.voyage.model is None:
        raise ValueError("rerank.voyage.model is null (the evaluation has not chosen one)")
    key = settings.embeddings_api_key
    if key is None or not key.get_secret_value().strip():
        raise ValueError("Voyage reranking needs SOS_EMBEDDINGS_API_KEY")
    return VoyageReranker(
        model=config.voyage.model,
        api_key=key.get_secret_value(),
        endpoint=config.voyage.endpoint,
        connect_timeout_s=config.voyage.connect_timeout_s,
        transport=transport,
    )


__all__ = ["VoyageRerankError", "VoyageReranker", "build_voyage_reranker"]
