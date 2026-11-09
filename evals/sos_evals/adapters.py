"""The only interface between the harness and the system under test.

`RetrievalAdapter` exposes `search_documents`-style ranked retrieval (docs/06 §6) and
`AskAdapter` the full ask pipeline (docs/06 §5). The M2 knowledge module plugs in by
implementing these protocols (in-process through `knowledge.service`, or over the SSE API);
the harness never imports application code and never calls an LLM itself.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol

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


class ModelCall(_Model):
    """One metered model call made while answering (token counts as billed; NFR-CST-003).

    On app runs these are the question's ``kb.llm_calls`` rows (or the live gateway's metering
    events); the stubs record a fixed profile. Never prompt or answer text."""

    role: str
    """The ``models.yaml`` role (``answer``, ``followups``, ...)."""
    model: str | None = None
    """The model billed; None means the role's configured model in ``models.yaml``."""
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    called_on: date | None = None
    """UTC call date, which picks the price in force (NFR-CST-004); None = the run date."""


class AskResult(_Model):
    segments: tuple[AnswerSegment, ...]
    refused: bool
    """True when the system answered "not found in school records" (FR-KB-007)."""
    provided_sources: tuple[str, ...]
    """Sources the tool results gave the model in this request (docs/06 §9 rule 1)."""
    latency_ms: float | None = Field(default=None, ge=0)
    mode: Literal["full", "search_only"] = "full"
    """``search_only``: the segments are the cited passages themselves (no written answer)."""
    language: str | None = None
    """The answer's language as the system reports it (SSE ``meta.language``; ADR-0036)."""
    followups: tuple[str, ...] = ()
    """Follow-up suggestions shown after the answer."""
    title: str | None = None
    """The conversation title shown for the question."""
    calls: tuple[ModelCall, ...] = ()
    """Every model call this question made (cost per answer, NFR-CST-003, FR-KB-046)."""

    @property
    def text(self) -> str:
        return " ".join(segment.text for segment in self.segments)


class RetrievalAdapter(Protocol):
    name: str

    def retrieve(self, question: str, asker: Asker, k: int) -> Retrieved: ...


class AskAdapter(Protocol):
    name: str

    def ask(self, question: str, asker: Asker) -> AskResult: ...
