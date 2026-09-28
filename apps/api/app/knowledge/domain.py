"""Pure value types shared by the knowledge packages and exposed through ``knowledge.service``.

No I/O, no database, no web: every subpackage (including the pure ``chunking``) may import this
module. Types are frozen so a value handed to the gateway or a tool cannot be widened on the way
(the LLM never receives data the user cannot see, invariant 8). Names and shapes follow docs/06:
``SearchResultBlock`` mirrors the Messages API ``search_result`` block (§7), ``Citation`` /
``AnswerSegment`` the citations shape the eval harness also uses (``evals/sos_evals/adapters.py``),
and the ``*Event`` classes the SSE protocol (§5.1).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar, Literal

Locale = Literal["en", "te", "mixed"]
"""Language style of a question, block or answer (docs/06 §11; FR-KB-006)."""

InputType = Literal["document", "query"]
"""What an embedding is for (ADR-0006: ``embed(texts, input_type)``)."""

ModelRole = Literal["answer", "router", "metadata", "translation", "extraction", "eval_judge"]
"""Keys of ``knowledge/config/models.yaml`` ``roles`` (docs/06 §4.4, §5, §6, §10.2, §12)."""

Feature = Literal["ask", "metadata", "translation", "extraction", "embeddings", "eval"]
"""What a metered model call was for (FR-KB-009, NFR-CST-001: spend per tenant and feature)."""

AskMode = Literal["full", "search_only"]
"""``search_only``: ranked, cited snippets without generated prose (budget, outage; §12, §15)."""

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal"]

BlockKind = Literal["heading", "paragraph", "list_item", "table", "page_break"]

SourceKind = Literal["doc", "student", "finding", "change", "verified"]


# --- ingestion and chunking (docs/06 §4) -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractedBlock:
    """One structural block of a cleaned, Aadhaar-redacted page (docs/06 §4.2-4.3)."""

    kind: BlockKind
    text: str
    level: int | None = None
    """Heading level (1 = top) for ``heading`` blocks."""
    language: Locale | None = None
    table_header: tuple[str, ...] = ()
    """Column headers for ``table`` blocks (repeated when a table is split by row groups)."""


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    page_no: int
    blocks: tuple[ExtractedBlock, ...]
    ocr_confidence: float | None = None
    """None for a text layer; below the threshold the page is flagged ``needs attention``."""


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    tenant_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    pages: tuple[ExtractedPage, ...]


@dataclass(frozen=True, slots=True)
class DocumentContext:
    """Metadata for the contextual chunk header (docs/06 §4.4-4.5); never guessed."""

    doc_type: str
    title: str
    issuer: str | None = None
    reference_no: str | None = None
    issued_on: date | None = None
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class Chunk:
    """A chunk ready for embedding and indexing (``kb.document_chunks`` without ACL columns)."""

    chunk_no: int
    content: str
    """Text shown to users and cited."""
    context_header: str
    """Prepended for embedding and FTS only (docs/06 §4.5)."""
    heading_path: tuple[str, ...]
    page_from: int | None
    page_to: int | None
    token_count: int
    language: Locale | None = None
    is_table: bool = False


# --- retrieval (docs/06 §6) ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchFilters:
    doc_types: frozenset[str] | None = None
    from_date: date | None = None


@dataclass(frozen=True, slots=True)
class AclKeys:
    """The caller's document-visibility keys, applied IN SQL before ranking (FR-KB-002).

    Built from the ``UserContext`` exactly like the documents service's visibility rule
    (docs/05 §6.1): ``sees_all`` for holders of ``document.manage_acl``; otherwise a chunk is
    visible when an ACL entry matches a role, section, class or the membership, and an EMPTY ACL
    only when ``school_wide`` is true. Tenant isolation is RLS, never these keys.

    ``read_sensitive`` (M2 wave 5, additive; default False = fail closed): the caller holds
    ``student.read_sensitive``, so restricted (C3) documents their ACL reaches may be retrieved
    too. Without it retrieval never returns a C3 chunk, whatever the ACL says.
    """

    roles: frozenset[str]
    section_ids: frozenset[uuid.UUID]
    class_ids: frozenset[uuid.UUID]
    membership_id: uuid.UUID
    school_wide: bool
    sees_all: bool = False
    read_sensitive: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    texts: tuple[str, ...]
    """The original query first, then translations (translated-query fusion, docs/06 §6)."""
    vectors: tuple[tuple[float, ...], ...]
    """Query embeddings, one per text, made by the caller."""
    k: int = 12
    filters: SearchFilters = field(default_factory=SearchFilters)
    prefer_latest: bool = False
    """The question implies "latest/current": apply the recency boost."""


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    page_from: int | None
    page_to: int | None
    doc_type: str
    title: str
    issued_on: date | None
    content: str
    score: float
    source: str
    """``sos://doc/{document_id}/v{n}#p{page}`` (:func:`app.knowledge.sources.document_page`)."""


# --- model conversation (docs/06 §5, §7) --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchResultBlock:
    """A Messages API ``search_result`` block: the only way content reaches the answer model."""

    source: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class Citation:
    source: str
    cited_text: str


@dataclass(frozen=True, slots=True)
class AnswerSegment:
    text: str
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A whitelisted read-only tool (Messages API tool format, docs/06 §7)."""

    name: str
    description: str
    input_schema: Mapping[str, object]
    permission: str
    """Checked before the tool runs; the tool's service call checks scope per object."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    call_id: str
    blocks: tuple[SearchResultBlock, ...]
    is_error: bool = False
    """The model is told the tool failed; the answer states partial coverage (docs/06 §15)."""


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelTurn:
    model: str
    stop_reason: StopReason
    segments: tuple[AnswerSegment, ...]
    tool_calls: tuple[ToolCall, ...]
    usage: Usage


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    turn: ModelTurn


@dataclass(frozen=True, slots=True)
class ToolResultsMessage:
    outcomes: tuple[ToolOutcome, ...]


ConversationItem = UserMessage | AssistantMessage | ToolResultsMessage


@dataclass(frozen=True, slots=True)
class Metering:
    """Who pays for a model call (per tenant and feature; FR-KB-009, FR-KB-011)."""

    tenant_id: uuid.UUID
    feature: Feature
    query_id: uuid.UUID | None = None


# --- ask request and SSE events (docs/06 §5, §5.1) ----------------------------------------------


@dataclass(frozen=True, slots=True)
class AskRequest:
    question: str
    session_id: uuid.UUID
    """Conversation context is session-scoped; never shared across users (FR-KB-012)."""


@dataclass(frozen=True, slots=True)
class MetaEvent:
    event: ClassVar[str] = "meta"
    query_id: uuid.UUID
    language: Locale
    mode: AskMode


@dataclass(frozen=True, slots=True)
class TokenEvent:
    event: ClassVar[str] = "token"
    text: str


@dataclass(frozen=True, slots=True)
class CitationEvent:
    event: ClassVar[str] = "citation"
    index: int
    source: str
    title: str
    snippet: str


@dataclass(frozen=True, slots=True)
class DoneEvent:
    event: ClassVar[str] = "done"
    latency_ms: int
    cited_sources: int


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    event: ClassVar[str] = "error"
    type: str
    """e.g. ``budget_exhausted``; a code, never free text."""
    message_key: str
    """An i18n key (``kb.errors.budget``); the UI renders it in en/te."""


AskEvent = MetaEvent | TokenEvent | CitationEvent | DoneEvent | ErrorEvent
