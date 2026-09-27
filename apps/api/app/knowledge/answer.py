"""The answer loop and the server-side output checks of "Ask the school" (docs/06 §5, §9, §15).

:class:`AnswerEngine.run` turns one question into an :class:`Answer`:

1. **Tool-use loop** on the ``answer`` role through the :class:`LlmGateway` (at most
   ``max_tool_rounds`` rounds, ADR-0008). Only the tools the caller may use are offered; each runs
   under the caller's ``UserContext`` in the request's ``tenant_session`` (RLS, scopes, C3
   rules). Tool results reach the model only as ``search_result`` blocks, trimmed to the
   docs/06 §12 context budget. A failing tool is reported to the model as an error result.
2. **Citation validation** (§9 rules 1-2, FR-KB-005): a citation is kept only when its
   ``source`` is one of the blocks given to the model in THIS request (so it is visible to the
   caller and, for documents, the latest version: blocks are built only from ACL-filtered,
   ``is_latest`` retrieval and scoped record reads) and its ``cited_text`` is a
   whitespace-normalised substring of that block's text. Invalid citations are dropped.
3. **Grounding** (invariant 8, FR-KB-007): an answer with no valid citation is replaced by the
   configured "not found in school records" text; when more than
   ``max_uncited_factual_fraction`` of the factual segments (those with digits, or with
   citations) have no valid citation, the answer is replaced by the search-only view of the
   passages the model was given (§9 rule 3).
4. **Output sanitising** (§9 rule 5, SEC-019): HTML tags and any link other than ``sos://``
   are removed from the model's text; Aadhaar numbers are masked (the gateway also masks).
5. **Fallback** (§12, §15; FR-KB-011): a gateway refusal that allows it (budget exhausted,
   school switch off, rate limit, outage) answers search-only: ranked, cited passages without
   generated prose.

Nothing here logs or returns question or answer text to logs (invariant 5); the service
encrypts both into ``kb.queries``.
"""

from __future__ import annotations

import dataclasses
import re
import time
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.core.redaction import mask_aadhaar
from app.knowledge import sources
from app.knowledge.config.llm import LlmConfig
from app.knowledge.domain import (
    AnswerSegment,
    AskMode,
    AssistantMessage,
    Citation,
    ConversationItem,
    Locale,
    Metering,
    ModelTurn,
    SearchResultBlock,
    ToolCall,
    ToolOutcome,
    ToolResultsMessage,
    UserMessage,
)
from app.knowledge.gateway.errors import GatewayError
from app.knowledge.interfaces import LlmGateway
from app.knowledge.prompts.registry import PromptTemplate
from app.knowledge.tools.documents import NAME as SEARCH_TOOL
from app.knowledge.tools.documents import DocumentSearch, to_block
from app.knowledge.tools.registry import OfferedTool, offered

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

log = get_logger(__name__)

IST: Final = ZoneInfo("Asia/Kolkata")
FALLBACK_RESULTS: Final = 5
SNIPPET_CHARS: Final = 300

Status = Literal["answered", "not_found", "refused", "search_only", "error"]
Route = Literal["tools", "documents", "both", "refused"]

_TELUGU: Final = re.compile(r"[ఀ-౿]")
_LATIN: Final = re.compile(r"[A-Za-z]")
_DIGIT: Final = re.compile(r"\d")
_WS: Final = re.compile(r"\s+")
_TAG: Final = re.compile(r"<[^>\n]{0,500}>")
_MD_LINK: Final = re.compile(r"\[([^\]\n]{0,500})\]\((?!sos://)[^)\s]{0,2000}\)")
_BARE_LINK: Final = re.compile(r"(?:\b(?:https?|ftp)://|\bwww\.)[^\s<>()\[\]]*", re.IGNORECASE)


def detect_language(text: str) -> Locale:
    """Script-based style of a question (FR-KB-006): Telugu, English or code-mixed."""
    telugu = bool(_TELUGU.search(text))
    latin = bool(_LATIN.search(text))
    if telugu and latin:
        return "mixed"
    return "te" if telugu else "en"


def normalise(text: str) -> str:
    """NFC, casefolded, whitespace collapsed: the comparison form for cited text (§9 rule 2)."""
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def sanitise(text: str) -> str:
    """Restricted output (§9 rule 5): no HTML, no links except ``sos://``; Aadhaar masked."""
    text = _TAG.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _BARE_LINK.sub("", text)
    return mask_aadhaar(text)


def is_factual(segment: AnswerSegment) -> bool:
    """§9 rule 3 heuristic: numbers and dates are facts; a cited segment states one too."""
    return bool(segment.citations) or bool(_DIGIT.search(segment.text))


@dataclass(frozen=True, slots=True)
class CitedSource:
    index: int
    source: str
    title: str
    snippet: str


@dataclass(frozen=True, slots=True)
class ToolRun:
    name: str
    results: int
    error: bool


@dataclass(frozen=True, slots=True)
class Answer:
    """What the service streams, stores (encrypted) and audits (counts only)."""

    language: Locale
    mode: AskMode
    status: Status
    route: Route
    segments: tuple[AnswerSegment, ...]
    cited: tuple[CitedSource, ...]
    provided: tuple[str, ...]
    """Sources given to the model in this request (docs/06 §9 rule 1), in order."""
    tools: tuple[ToolRun, ...] = ()
    error_code: str | None = None
    message_key: str | None = None
    model_ids: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    citations_dropped: int = 0
    uncited_factual: int = 0

    @property
    def refused(self) -> bool:
        return self.status in ("not_found", "refused")

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments if s.text)


@dataclass
class _Provided:
    """Blocks given to the model, by source (several chunks may share a page source)."""

    by_source: dict[str, list[SearchResultBlock]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    def add(self, block: SearchResultBlock) -> None:
        if block.source not in self.by_source:
            self.by_source[block.source] = []
            self.order.append(block.source)
        self.by_source[block.source].append(block)

    def supports(self, citation: Citation) -> bool:
        blocks = self.by_source.get(citation.source)
        if not blocks:
            return False  # rule 1: not among this request's sources
        try:
            sources.parse(citation.source)
        except ValueError:
            return False
        cited = normalise(citation.cited_text)
        if not cited:
            return False
        return any(cited in normalise(b.text) for b in blocks)  # rule 2

    def first(self, source: str) -> SearchResultBlock:
        return self.by_source[source][0]


def _snippet(text: str) -> str:
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1].rstrip() + "…"


def validate(
    segments: Sequence[AnswerSegment], provided: _Provided
) -> tuple[list[AnswerSegment], int]:
    """Keep only valid citations (§9 rules 1-2) and sanitise the text (§9 rule 5)."""
    out: list[AnswerSegment] = []
    dropped = 0
    for seg in segments:
        good = tuple(c for c in seg.citations if provided.supports(c))
        dropped += len(seg.citations) - len(good)
        text = sanitise(seg.text)
        if text.strip() or good:
            out.append(AnswerSegment(text=text, citations=good))
    return out, dropped


def cited_sources(
    segments: Sequence[AnswerSegment], provided: _Provided
) -> tuple[list[AnswerSegment], list[CitedSource]]:
    """Number the cited sources in order of first use and mark segments with ``[n]``."""
    numbers: dict[str, int] = {}
    cited: list[CitedSource] = []
    marked: list[AnswerSegment] = []
    for seg in segments:
        refs: list[int] = []
        for c in seg.citations:
            if c.source not in numbers:
                numbers[c.source] = len(numbers) + 1
                block = provided.first(c.source)
                cited.append(
                    CitedSource(numbers[c.source], c.source, block.title, _snippet(c.cited_text))
                )
            if numbers[c.source] not in refs:
                refs.append(numbers[c.source])
        text = seg.text
        if refs:
            text = text.rstrip() + " " + "".join(f"[{n}]" for n in refs)
        marked.append(dataclasses.replace(seg, text=text))
    return marked, cited


class AnswerEngine:
    def __init__(
        self,
        *,
        gateway: LlmGateway,
        tools: dict[str, OfferedTool],
        search: DocumentSearch,
        config: LlmConfig,
        prompt: PromptTemplate,
        final_k: int = FALLBACK_RESULTS,
    ) -> None:
        self._gateway = gateway
        self._tools = tools
        self._search = search
        self._config = config
        self._prompt = prompt
        self._final_k = final_k

    # --- prompt -------------------------------------------------------------------------------

    def system_prompt(self, ctx: UserContext, school_name: str) -> str:
        roles = ", ".join(sorted(r.replace("_", " ") for r in ctx.roles)) or "staff member"
        scope = (
            "the whole school"
            if ctx.scope_for("kb.ask").school_wide
            else "their assigned classes and sections"
        )
        return self._prompt.render(
            school_name=school_name,
            date_ist=datetime.now(IST).strftime("%d/%m/%Y"),
            role_display=roles,
            scope_display=scope,
        )

    # --- the loop -----------------------------------------------------------------------------

    def run(
        self,
        session: Session,
        ctx: UserContext,
        question: str,
        *,
        query_id: uuid.UUID,
        school_name: str,
    ) -> Answer:
        language = detect_language(question)
        tools = offered(self._tools, ctx)
        by_name = {t.spec.name: t for t in tools}
        specs = [t.spec for t in tools]
        limits = self._config.limits
        budget = limits.tool_result_context_tokens * limits.chars_per_token_estimate
        used = 0
        provided = _Provided()
        runs: list[ToolRun] = []
        turns: list[ModelTurn] = []
        conversation: list[ConversationItem] = [UserMessage(question)]
        metering = Metering(tenant_id=ctx.tenant_id, feature="ask", query_id=query_id)
        system = self.system_prompt(ctx, school_name)
        try:
            for _ in range(limits.max_tool_rounds + 1):
                turn = self._gateway.run_turn(metering, "answer", system, conversation, specs)
                turns.append(turn)
                if not turn.tool_calls:
                    break
                outcomes: list[ToolOutcome] = []
                for call in turn.tool_calls:
                    outcome = self._run_tool(session, ctx, by_name, call)
                    kept: list[SearchResultBlock] = []
                    for block in outcome.blocks:
                        size = len(block.title) + len(block.text)
                        if used + size > budget:
                            continue  # docs/06 §12: at most 12k tokens of tool results
                        used += size
                        kept.append(block)
                        provided.add(block)
                    outcome = dataclasses.replace(outcome, blocks=tuple(kept))
                    runs.append(ToolRun(call.name, len(kept), outcome.is_error))
                    outcomes.append(outcome)
                conversation += [AssistantMessage(turn), ToolResultsMessage(tuple(outcomes))]
        except GatewayError as exc:
            if not exc.search_only:
                raise
            return self._fallback(
                session,
                ctx,
                question=question,
                language=language,
                turns=turns,
                runs=runs,
                error=(exc.code, exc.message_key),
            )
        final = turns[-1] if turns and not turns[-1].tool_calls else None
        segments, dropped = validate(final.segments if final else (), provided)
        base = self._base(language, turns, runs, provided, dropped)
        factual = [s for s in segments if is_factual(s)]
        uncited = [s for s in factual if not s.citations]
        if not any(s.citations for s in segments):
            return dataclasses.replace(
                base,
                status="not_found",
                segments=(AnswerSegment(self._not_found(language)),),
                uncited_factual=len(uncited),
            )
        threshold = self._config.answer_checks.max_uncited_factual_fraction
        if factual and len(uncited) / len(factual) > threshold:
            log.warning(
                "kb.answer.uncited_fallback",
                tenant_id=ctx.tenant_id,
                resource_type="kb_query",
                resource_id=query_id,
                count=len(uncited),
            )
            return self._search_only_from(base, provided, uncited=len(uncited))
        marked, cited = cited_sources(segments, provided)
        return dataclasses.replace(
            base,
            status="answered",
            segments=tuple(marked),
            cited=tuple(cited),
            uncited_factual=len(uncited),
        )

    def _run_tool(
        self,
        session: Session,
        ctx: UserContext,
        by_name: dict[str, OfferedTool],
        call: ToolCall,
    ) -> ToolOutcome:
        tool = by_name.get(call.name)
        if tool is None:
            return ToolOutcome(call_id=call.call_id, blocks=(), is_error=True)
        try:
            return tool.run(session, ctx, call.call_id, call.arguments)
        except SQLAlchemyError:
            raise  # the transaction is broken: fail the question, never answer from half a read
        except Exception as exc:
            log.warning(
                "kb.tool.failed",
                tenant_id=ctx.tenant_id,
                action=call.name,
                error_type=type(exc).__name__,
            )
            return ToolOutcome(call_id=call.call_id, blocks=(), is_error=True)

    # --- results ------------------------------------------------------------------------------

    def _not_found(self, language: Locale) -> str:
        # Telugu for any question written with Telugu script (FR-KB-006; code-mixed accepts either).
        texts = self._config.answer_checks.not_found
        return texts.en if language == "en" else texts.te

    @staticmethod
    def _route(runs: Sequence[ToolRun]) -> Route:
        names = {r.name for r in runs}
        docs = SEARCH_TOOL in names
        records = bool(names - {SEARCH_TOOL})
        if docs and records:
            return "both"
        if docs:
            return "documents"
        return "tools" if records else "refused"

    def _base(
        self,
        language: Locale,
        turns: Sequence[ModelTurn],
        runs: Sequence[ToolRun],
        provided: _Provided,
        dropped: int,
    ) -> Answer:
        return Answer(
            language=language,
            mode="full",
            status="answered",
            route=self._route(runs),
            segments=(),
            cited=(),
            provided=tuple(provided.order),
            tools=tuple(runs),
            model_ids=tuple(sorted({t.model for t in turns})),
            input_tokens=sum(t.usage.input_tokens for t in turns),
            output_tokens=sum(t.usage.output_tokens for t in turns),
            citations_dropped=dropped,
        )

    def _search_only_from(self, base: Answer, provided: _Provided, *, uncited: int) -> Answer:
        cited = [
            CitedSource(
                i, source, provided.first(source).title, _snippet(provided.first(source).text)
            )
            for i, source in enumerate(provided.order[: self._final_k], start=1)
        ]
        return dataclasses.replace(
            base,
            mode="search_only",
            status="search_only",
            segments=(),
            cited=tuple(cited),
            uncited_factual=uncited,
        )

    def _fallback(
        self,
        session: Session,
        ctx: UserContext,
        *,
        question: str,
        language: Locale,
        turns: Sequence[ModelTurn],
        runs: Sequence[ToolRun],
        error: tuple[str, str],
    ) -> Answer:
        """Search-only answer (budget, switch-off, rate limit or outage; docs/06 §15)."""
        fresh = _Provided()
        try:
            for chunk in self._search.search(session, ctx, question, k=self._final_k):
                fresh.add(to_block(chunk))
        except SQLAlchemyError:
            raise
        except Exception as exc:
            log.warning(
                "kb.search_only.failed", tenant_id=ctx.tenant_id, error_type=type(exc).__name__
            )
        base = self._base(language, turns, runs, fresh, 0)
        answer = self._search_only_from(base, fresh, uncited=0)
        return dataclasses.replace(
            answer,
            status="search_only",
            error_code=error[0],
            message_key=error[1],
            tools=tuple(runs),
        )


def elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


__all__ = [
    "Answer",
    "AnswerEngine",
    "CitedSource",
    "ToolRun",
    "cited_sources",
    "detect_language",
    "elapsed_ms",
    "is_factual",
    "normalise",
    "sanitise",
    "validate",
]
