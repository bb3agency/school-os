"""Voyage AI embeddings over HTTPS with ``httpx`` (no SDK; ADR-0006, docs/06 §4.6, docs/08 §1).

Disabled unless ``SOS_KB_PROVIDER_MODE=live`` and ``embeddings.yaml`` selects a ``voyage``
candidate with a model: the composition root passes :func:`build_voyage_provider` to
``embeddings.select_embeddings_provider`` as the ``"voyage"`` factory, and selection refuses
while nothing is selected. Endpoint and timeouts come from ``embeddings.yaml`` ``voyage``; the
model ID and dimensions from the selected candidate (invariant 13); the key from
``Settings.embeddings_api_key`` (an organization API key, invariant 10).

One provider call is one HTTP request (``POST /v1/embeddings``, ``truncation: false`` so an
over-long chunk fails instead of being silently cut). Retries are **not** done here: failures
are raised as :class:`VoyageEmbeddingsError` with ``retryable`` (timeouts, connection errors,
HTTP 408, 429 and 5xx) and ``retry_after_s`` (from ``Retry-After``), and ``TenantEmbedder`` applies
the one retry policy (``embeddings.yaml`` ``retry``). Errors carry status codes and error types
only: never the request texts, the response body or the key. Nothing here logs.

Texts sent are chunk text and questions, already Aadhaar-redacted by the caller (invariant 4).
Voyage is a sub-processor outside India (docs/08 §1): selecting it needs the sub-processor
register, school notice and DPIA refresh first.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import Settings
from app.knowledge.config.embeddings import Candidate, VoyageHttp
from app.knowledge.domain import InputType

PROVIDER = "voyage"
_RETRYABLE_STATUS = frozenset({408, 429})
_MAX_TEXTS = 1000
"""Voyage's per-request limit; ``batching.max_texts`` is lower."""


class VoyageEmbeddingsError(RuntimeError):
    """A failed Voyage call; ``retryable`` and ``retry_after_s`` follow ``is_transient``."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after_s = retry_after_s


class VoyageEmbeddingsProvider:
    """:class:`app.knowledge.interfaces.EmbeddingsProvider` for one Voyage model."""

    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        api_key: str,
        http: VoyageHttp,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Voyage embeddings need SOS_EMBEDDINGS_API_KEY")
        self._model = model
        self._dimensions = dimensions
        self._endpoint = http.endpoint
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                http.read_timeout_s, connect=http.connect_timeout_s, pool=http.connect_timeout_s
            ),
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

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def close(self) -> None:
        self._client.close()

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > _MAX_TEXTS:
            raise ValueError(f"at most {_MAX_TEXTS} texts per Voyage request")
        payload = {
            "input": list(texts),
            "model": self._model,
            "input_type": input_type,
            "output_dimension": self._dimensions,
            "output_dtype": "float",
            "truncation": False,
        }
        try:
            response = self._client.post(self._endpoint, json=payload)
        except httpx.TimeoutException:
            raise VoyageEmbeddingsError("voyage: timeout", retryable=True) from None
        except httpx.TransportError as exc:
            raise VoyageEmbeddingsError(
                f"voyage: transport error {type(exc).__name__}", retryable=True
            ) from None
        if response.status_code != httpx.codes.OK:
            status = response.status_code
            raise VoyageEmbeddingsError(
                f"voyage: HTTP {status}",
                retryable=status in _RETRYABLE_STATUS or status >= 500,
                status=status,
                retry_after_s=_retry_after(response.headers.get("Retry-After")),
            )
        return self._parse(response, len(texts))

    def _parse(self, response: httpx.Response, count: int) -> list[list[float]]:
        try:
            body: Any = response.json()
            data = body["data"]
            rows = sorted(data, key=lambda row: int(row["index"]))
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
            indices = [int(row["index"]) for row in rows]
        except (ValueError, KeyError, TypeError):
            raise VoyageEmbeddingsError("voyage: malformed response", retryable=False) from None
        if indices != list(range(count)):
            raise VoyageEmbeddingsError(
                f"voyage: {len(indices)} embeddings for {count} texts", retryable=False
            )
        for vector in vectors:
            if len(vector) != self._dimensions or not all(math.isfinite(x) for x in vector):
                raise VoyageEmbeddingsError(
                    f"voyage: expected {self._dimensions}-dim finite vectors", retryable=False
                )
        return vectors


def _retry_after(value: str | None) -> float | None:
    """Seconds from a numeric ``Retry-After`` (HTTP-date values are ignored)."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def build_voyage_provider(
    candidate: Candidate,
    *,
    settings: Settings,
    http: VoyageHttp,
    transport: httpx.BaseTransport | None = None,
) -> VoyageEmbeddingsProvider:
    """The ``"voyage"`` factory for ``select_embeddings_provider`` (bind settings and http)."""
    if candidate.provider != PROVIDER:
        raise ValueError(f"not a voyage candidate: {candidate.provider!r}")
    if candidate.model is None:
        raise ValueError("the voyage candidate has no model (ADR-0006 evaluation pending)")
    key = settings.embeddings_api_key
    if key is None or not key.get_secret_value().strip():
        raise ValueError("Voyage embeddings need SOS_EMBEDDINGS_API_KEY")
    return VoyageEmbeddingsProvider(
        model=candidate.model,
        dimensions=candidate.dimensions,
        api_key=key.get_secret_value(),
        http=http,
        transport=transport,
    )
