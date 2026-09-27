"""The only interface between the harness and the system under test.

`RetrievalAdapter` exposes `search_documents`-style ranked retrieval (docs/06 §6) and
`AskAdapter` the full ask pipeline (docs/06 §5). The M2 knowledge module plugs in by
implementing these protocols (in-process through `knowledge.service`, or over the SSE API);
the harness never imports application code and never calls an LLM itself.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from sos_evals.schema import Asker


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Retrieved(_Model):
    sources: tuple[str, ...]
    """Source URIs, best first, after the permission filter and fusion."""
    latency_ms: float | None = Field(default=None, ge=0)
    """Measured by the adapter; the runner uses its own wall clock when None."""


class Citation(_Model):
    source: str
    cited_text: str


class AnswerSegment(_Model):
    """A text block of the answer with its citations (Messages API citations shape)."""

    text: str
    citations: tuple[Citation, ...] = ()


class AskResult(_Model):
    segments: tuple[AnswerSegment, ...]
    refused: bool
    """True when the system answered "not found in school records" (FR-KB-007)."""
    provided_sources: tuple[str, ...]
    """Sources the tool results gave the model in this request (docs/06 §9 rule 1)."""
    latency_ms: float | None = Field(default=None, ge=0)

    @property
    def text(self) -> str:
        return " ".join(segment.text for segment in self.segments)


class RetrievalAdapter(Protocol):
    name: str

    def retrieve(self, question: str, asker: Asker, k: int) -> Retrieved: ...


class AskAdapter(Protocol):
    name: str

    def ask(self, question: str, asker: Asker) -> AskResult: ...
