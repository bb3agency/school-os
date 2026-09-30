"""Pure value types shared by the knowledge packages and exposed through ``knowledge.service``.

No I/O, no database, no web: every subpackage (including the pure ``chunking``) may import this
module. Types are frozen so a value handed to the gateway or a tool cannot be widened on the way
(the LLM never receives data the user cannot see, invariant 8). Names and shapes follow docs/06:
``SearchResultBlock`` is a citable passage (a Messages API ``search_result`` block, or a numbered
passage on Gemini; §7), ``Citation`` /
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

ModelRole = Literal[
    "answer",
    "router",
    "metadata",
    "translation",
    "extraction",
    "circular",
    "notice",
    "followups",
    "summary",
    "memory_screen",
    "query_rewrite",
    "eval_judge",
]
"""Keys of ``knowledge/config/models.yaml`` ``roles`` (docs/06 §4.4, §4.10, §5, §6, §10, §12)."""

Feature = Literal[
    "ask", "metadata", "translation", "extraction", "circulars", "notices", "embeddings", "eval"
]
"""What a metered model call was for (FR-KB-009, NFR-CST-001: spend per tenant and feature)."""

AskMode = Literal["full", "search_only"]
"""``search_only``: ranked, cited snippets without generated prose (budget, outage; §12, §15)."""

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal"]

BlockKind = Literal["heading", "paragraph", "list_item", "table", "page_break"]

SourceKind = Literal[
    "doc", "student", "finding", "change", "verified", "count", "fee", "conversation"
]


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
    """A passage the answer model may see and cite: the only way content reaches it. The gateway
    sends it as a Messages API ``search_result`` block (Anthropic) or as a numbered passage that
    the model cites with ``[n]`` markers (Gemini; ADR-0033, docs/06 §7)."""

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
    signature: str | None = field(default=None, compare=False, repr=False)
    """Opaque provider state the gateway must send back with this call on the next request
    (a Gemini thought signature; ADR-0033). Not model text: never shown, logged or stored."""


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
class TextDelta:
    """A piece of answer text as the model generates it (streaming, docs/06 §5.1).

    Already Aadhaar-masked by the gateway; NOT validated: citations are checked only on the
    complete :class:`ModelTurn` that ends every stream (FR-KB-005)."""

    text: str


TurnEvent = TextDelta | ModelTurn
"""What :meth:`~app.knowledge.interfaces.StreamingLlmGateway.stream_turn` yields: text deltas,
then exactly one complete :class:`ModelTurn`."""


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """One earlier turn of the SAME user's conversation (docs/06 §5 conversation rules)."""

    question: str
    answer: str | None = None
    """The checked answer (``[n]`` markers removed), only when every source it cited is still
    visible to the caller now; else None (invariant 8). Context only, never evidence."""


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str
    earlier_questions: tuple[str, ...] = ()
    """Earlier questions of the SAME user's session, oldest first (FR-KB-012; docs/06 §5
    conversation rules). Context only: never earlier answers or tool results, so every turn
    re-retrieves under the caller's current permissions (invariant 8). Superseded by
    ``earlier_turns`` when that is given."""
    earlier_turns: tuple[HistoryTurn, ...] = ()
    """Recent turns of the SAME user's conversation, oldest first (ADR-0034): questions and
    their checked, still-visible answers. Context only; every question is searched afresh."""
    summary: str | None = None
    """Rolling summary of the conversation's older turns (context only, never evidence)."""
    memory: tuple[str, ...] = ()
    """The user's confirmed memory items (ADR-0034): how to answer, never evidence. The wire
    format puts them in a system block right after the static prompt (a stable, cacheable
    prefix)."""
    asked_as: str | None = None
    """The question as the user wrote it, when ``text`` is its standalone rewrite (docs/06
    §5 query rewrite): shown to the model so it answers in the user's language."""


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
    question: str = ""
    """Empty only with ``regenerate_of`` (the stored question is asked again)."""
    session_id: uuid.UUID | None = None
    """The browser's Ask session (the legacy name of ``conversation_id``). It names one of the
    caller's conversations or starts a new one; another user's never (FR-KB-012)."""
    conversation_id: uuid.UUID | None = None
    """One of the caller's conversations (404 for anyone else's or a deleted one)."""
    regenerate_of: uuid.UUID | None = None
    """Answer that question again (``question`` is then ignored); it becomes superseded."""
    edit_of: uuid.UUID | None = None
    """``question`` replaces that question; it and every later message become superseded."""


StatusStep = Literal[
    "understanding", "searching_documents", "reading_records", "searching_chats", "writing"
]
"""Progress codes of the ``status`` SSE event (docs/06 §5.1): codes only, never text."""


@dataclass(frozen=True, slots=True)
class StepUpdate:
    """Progress of the answer loop (a tool about to run, or its result count)."""

    step: StatusStep
    tool: str | None = None
    count: int | None = None


@dataclass(frozen=True, slots=True)
class MetaEvent:
    event: ClassVar[str] = "meta"
    query_id: uuid.UUID
    language: Locale
    mode: AskMode
    conversation_id: uuid.UUID | None = None
    """The conversation the question belongs to (a new one when none was named)."""
    title: str | None = None
    """The conversation's title (for a new one: the question cut at a word boundary)."""
    cached: bool = False
    """True when this is an exact repeat answered from the answer cache (docs/06 §5)."""
    cached_from: uuid.UUID | None = None
    """The earlier question whose checked answer is reused (only when ``cached``)."""
    summarized: bool = False
    """Older messages of the conversation reached the model as its rolling summary only."""


@dataclass(frozen=True, slots=True)
class StatusEvent:
    """Progress while the answer is prepared (docs/06 §5.1): a step code, the tool's code and
    how many documents or records it returned. Never text."""

    event: ClassVar[str] = "status"
    step: StatusStep
    tool: str | None = None
    count: int | None = None


@dataclass(frozen=True, slots=True)
class FollowupsEvent:
    """Up to 3 short follow-up questions in the answer's language, after ``final`` (empty in
    search-only mode, when the budget is used up or when the answer was not found)."""

    event: ClassVar[str] = "followups"
    questions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    """A memory item (ADR-0034): ``saved`` (the user said "remember that ...") or
    ``suggested`` (pending until the user confirms it; expires after 24 hours)."""

    event: ClassVar[str] = "memory"
    action: Literal["saved", "suggested"]
    item_id: uuid.UUID
    text: str


@dataclass(frozen=True, slots=True)
class TokenEvent:
    event: ClassVar[str] = "token"
    text: str
    """One whole validated answer segment (with its ``[n]`` markers), without surrounding
    whitespace: join token texts with ONE space to get ``final.text`` (docs/06 §5.1). Only
    ``delta`` texts carry their own whitespace and are appended verbatim."""


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
    status: str = "answered"
    """How the question ended (M2 wave 5, additive): ``answered``, ``not_found``, ``refused``,
    ``search_only`` or ``error`` (the stored query status; ``error`` = the stream failed)."""
    mode: AskMode = "full"
    """The final mode (``meta.mode`` is sent before the answer and may still say ``full``)."""


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    event: ClassVar[str] = "error"
    type: str
    """e.g. ``budget_exhausted``; a code, never free text."""
    message_key: str
    """An i18n key (``kb.errors.budget``); the UI renders it in en/te."""


@dataclass(frozen=True, slots=True)
class DeltaEvent:
    """Streaming preview (docs/06 §5.1, M2 wave 5): append ``text`` verbatim to the answer
    being shown. NOT yet validated; the ``final`` event replaces it."""

    event: ClassVar[str] = "delta"
    text: str


@dataclass(frozen=True, slots=True)
class FinalEvent:
    """The validated answer (docs/06 §5.1): show ``text`` INSTEAD of every ``delta`` received
    so far, and ignore the ``token`` events that follow (they repeat it for older clients).
    ``replaced`` is true when validation changed what was streamed (citations dropped, "not
    found", search-only fallback), beyond adding the ``[n]`` citation markers."""

    event: ClassVar[str] = "final"
    text: str
    replaced: bool
    status: str
    """``answered``, ``not_found``, ``refused`` or ``search_only`` (the stored query status)."""
    mode: AskMode
    summarized: bool = False
    """As ``meta.summarized`` (repeated for clients that read only the final event)."""


AskEvent = (
    MetaEvent
    | TokenEvent
    | CitationEvent
    | DoneEvent
    | ErrorEvent
    | DeltaEvent
    | FinalEvent
    | StatusEvent
    | FollowupsEvent
    | MemoryEvent
)
