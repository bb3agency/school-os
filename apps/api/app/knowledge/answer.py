"""The answer loop and the server-side output checks of "Ask the school" (docs/06 §5, §9, §15).

:class:`AnswerEngine.run` turns one question into an :class:`Answer`:

1. **Tool-use loop** on the ``answer`` role through the :class:`LlmGateway` (at most
   ``max_tool_rounds`` rounds, ADR-0008). Only the tools the caller may use are offered; each runs
   under the caller's ``UserContext`` in the request's ``tenant_session`` (RLS, scopes, C3
   rules). Tool results reach the model only as citable passages (``search_result`` blocks on
   Anthropic, numbered passages cited with ``[n]`` markers on Gemini; ADR-0033), trimmed to the
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
from collections.abc import Generator, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal, overload
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
    HistoryTurn,
    Locale,
    Metering,
    ModelTurn,
    SearchResultBlock,
    StatusStep,
    StepUpdate,
    TextDelta,
    ToolCall,
    ToolOutcome,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
)
from app.knowledge.gateway.errors import GatewayError, InvalidModelOutput
from app.knowledge.interfaces import LlmGateway, StreamingLlmGateway
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


CHAT_SEARCH_TOOL: Final = "search_my_conversations"


def step_for(tool: str) -> StatusStep:
    """The ``status`` step code of a tool (docs/06 §5.1)."""
    if tool == SEARCH_TOOL:
        return "searching_documents"
    if tool == CHAT_SEARCH_TOOL:
        return "searching_chats"
    return "reading_records"


@dataclass(frozen=True, slots=True)
class AskContext:
    """What a question carries besides itself (docs/06 §5 prompt layout; ADR-0034): the recent
    turns of the caller's conversation (answers only where every cited source is still visible),
    the rolling summary (only when its sources are still visible), the caller's confirmed memory
    items, and the standalone rewrite of a follow-up (sent as the question; the original goes
    with it and decides the answer's language). All context only, never evidence: citations are
    still validated against this request's tool results alone."""

    turns: tuple[HistoryTurn, ...] = ()
    summary: str | None = None
    memory: tuple[str, ...] = ()
    standalone: str | None = None

    @property
    def has_history(self) -> bool:
        return bool(self.turns or self.summary)


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
        """The segments, each stripped, joined with one space (the ``final`` event text;
        ``token`` events carry the same stripped segments, docs/06 §5.1)."""
        return " ".join(t for s in self.segments if (t := s.text.strip()))


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


@dataclass
class Progress:
    """What a streamed answer has shown and used so far (docs/06 §5.1).

    The service records it when the client goes away before the answer is complete
    (``kb.queries`` status ``cancelled``): the preview text shown, tool runs, sources given to
    the model and the model turns (ids and tokens)."""

    shown: list[str] = field(default_factory=list)
    runs: list[ToolRun] = field(default_factory=list)
    provided: _Provided = field(default_factory=_Provided)
    turns: list[ModelTurn] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.shown)


class PreviewSanitiser:
    """The §9 rule 5 output rules for streamed preview text.

    Holds back the unfinished last word, an HTML tag without its ``>`` and a markdown link
    without its ``)``, then applies :func:`sanitise` to whole words only, so a link or tag is
    never shown in pieces (the gateway has already masked Aadhaar numbers across deltas).
    Beyond ``max_pending`` held characters it emits what it has, sanitised."""

    def __init__(self, max_pending: int, progress: Progress | None = None) -> None:
        self._max = max_pending
        self._pending = ""
        self.progress = progress or Progress()

    def feed(self, text: str) -> str:
        self._pending += text
        cut = self._safe_cut(self._pending)
        if cut == 0 and len(self._pending) > self._max:
            cut = len(self._pending)
        out, self._pending = self._pending[:cut], self._pending[cut:]
        return sanitise(out) if out else ""

    def flush(self) -> str:
        out, self._pending = self._pending, ""
        return sanitise(out) if out else ""

    @staticmethod
    def _safe_cut(text: str) -> int:
        cut = max(text.rfind(c) for c in " \n\t") + 1
        head = text[:cut]
        tag = head.rfind("<")
        if tag >= 0 and head.rfind(">") < tag:
            cut = tag
        link = head[:cut].rfind("[")
        if link >= 0:
            rest = head[link:cut]
            close = rest.find("]")
            if close < 0 or (rest[close + 1 : close + 2] == "(" and ")" not in rest[close:]):
                cut = link
        return cut


def _show(text: str, progress: Progress) -> Iterator[TextDelta]:
    if text:
        progress.shown.append(text)
        yield TextDelta(text)


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
        earlier: Sequence[str] = (),
        context: AskContext | None = None,
    ) -> Answer:
        """The whole answer at once (the eval harness, the non-streaming service path)."""
        loop = self._loop(
            session,
            ctx,
            question,
            query_id=query_id,
            school_name=school_name,
            earlier=earlier,
            progress=Progress(),
            stream=False,
            context=context,
            steps=False,
        )
        while True:
            try:
                next(loop)
            except StopIteration as done:
                answer: Answer = done.value
                return answer

    @overload
    def stream(
        self,
        session: Session,
        ctx: UserContext,
        question: str,
        *,
        query_id: uuid.UUID,
        school_name: str,
        progress: Progress,
        earlier: Sequence[str] = (),
        context: AskContext | None = None,
        steps: Literal[False] = False,
    ) -> Generator[TextDelta, None, Answer]: ...

    @overload
    def stream(
        self,
        session: Session,
        ctx: UserContext,
        question: str,
        *,
        query_id: uuid.UUID,
        school_name: str,
        progress: Progress,
        earlier: Sequence[str] = (),
        context: AskContext | None = None,
        steps: Literal[True],
    ) -> Generator[TextDelta | StepUpdate, None, Answer]: ...

    def stream(
        self,
        session: Session,
        ctx: UserContext,
        question: str,
        *,
        query_id: uuid.UUID,
        school_name: str,
        progress: Progress,
        earlier: Sequence[str] = (),
        context: AskContext | None = None,
        steps: bool = False,
    ) -> Generator[TextDelta | StepUpdate, None, Answer]:
        """The answer as it is written: tool rounds first, then the final turn's text as
        sanitised preview deltas (docs/06 §5.1); returns the validated :class:`Answer`.

        ``progress`` shows what was streamed and used so far, for a client that goes away
        (closing this generator closes the provider call). With ``steps``, a
        :class:`StepUpdate` is yielded before each tool runs and with its result count."""
        return self._loop(
            session,
            ctx,
            question,
            query_id=query_id,
            school_name=school_name,
            earlier=earlier,
            progress=progress,
            stream=True,
            context=context,
            steps=steps,
        )

    def _loop(
        self,
        session: Session,
        ctx: UserContext,
        question: str,
        *,
        query_id: uuid.UUID,
        school_name: str,
        earlier: Sequence[str],
        progress: Progress,
        stream: bool,
        context: AskContext | None,
        steps: bool,
    ) -> Generator[TextDelta | StepUpdate, None, Answer]:
        language = detect_language(question)
        context = context or AskContext()
        search_text = context.standalone or question
        tools = offered(self._tools, ctx, session)
        by_name = {t.spec.name: t for t in tools}
        specs = [t.spec for t in tools]
        limits = self._config.limits
        budget = limits.tool_result_context_tokens * limits.chars_per_token_estimate
        used = 0
        provided = progress.provided
        runs = progress.runs
        turns = progress.turns
        conversation: list[ConversationItem] = [
            UserMessage(
                search_text,
                earlier_questions=() if context.turns else tuple(earlier),
                earlier_turns=context.turns,
                summary=context.summary,
                memory=context.memory,
                asked_as=question if context.standalone else None,
            )
        ]
        metering = Metering(tenant_id=ctx.tenant_id, feature="ask", query_id=query_id)
        system = self.system_prompt(ctx, school_name)
        preview = PreviewSanitiser(self._config.streaming.preview_max_pending_chars, progress)
        try:
            for round_no in range(limits.max_tool_rounds + 1):
                if stream:
                    last = not specs or round_no >= limits.max_tool_rounds
                    turn = yield from self._streamed_turn(
                        metering, system, conversation, specs, hold=not last, preview=preview
                    )
                else:
                    turn = self._gateway.run_turn(metering, "answer", system, conversation, specs)
                turns.append(turn)
                if not turn.tool_calls:
                    break
                outcomes: list[ToolOutcome] = []
                for call in turn.tool_calls:
                    if steps:
                        yield StepUpdate(step_for(call.name), call.name)
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
                    if steps:
                        yield StepUpdate(step_for(call.name), call.name, len(kept))
                    outcomes.append(outcome)
                conversation += [AssistantMessage(turn), ToolResultsMessage(tuple(outcomes))]
            yield from _show(preview.flush(), progress)
        except GatewayError as exc:
            if not exc.search_only:
                raise
            return self._fallback(
                session,
                ctx,
                question=search_text,
                language=language,
                turns=turns,
                runs=runs,
                error=(exc.code, exc.message_key),
            )
        return self._validated(ctx, query_id, language, turns=turns, runs=runs, provided=provided)

    def _streamed_turn(
        self,
        metering: Metering,
        system: str,
        conversation: Sequence[ConversationItem],
        specs: Sequence[ToolSpec],
        *,
        hold: bool,
        preview: PreviewSanitiser,
    ) -> Generator[TextDelta, None, ModelTurn]:
        """One model turn, its text shown as it arrives. While more tool rounds may follow
        (``hold``), text is held until ``preview_hold_chars`` long, so a short remark before a
        tool call is never shown; the preview is replaced by the validated answer anyway."""
        gateway = self._gateway
        if not isinstance(gateway, StreamingLlmGateway):
            whole = gateway.run_turn(metering, "answer", system, conversation, specs)
            if not whole.tool_calls:
                text = "".join(s.text for s in whole.segments)
                yield from _show(preview.feed(text), preview.progress)
            return whole
        hold_chars = self._config.streaming.preview_hold_chars if hold else 0
        held: list[str] = []
        showing = hold_chars == 0
        turn: ModelTurn | None = None
        with closing(gateway.stream_turn(metering, "answer", system, conversation, specs)) as it:
            for item in it:
                if isinstance(item, ModelTurn):
                    turn = item
                    continue
                if showing:
                    yield from _show(preview.feed(item.text), preview.progress)
                    continue
                held.append(item.text)
                if sum(len(t) for t in held) >= hold_chars:
                    showing = True
                    yield from _show(preview.feed("".join(held)), preview.progress)
                    held.clear()
        if turn is None:
            raise InvalidModelOutput("the model stream ended without a complete turn")
        if held and not turn.tool_calls:
            yield from _show(preview.feed("".join(held)), preview.progress)
        return turn

    def _validated(
        self,
        ctx: UserContext,
        query_id: uuid.UUID,
        language: Locale,
        *,
        turns: Sequence[ModelTurn],
        runs: Sequence[ToolRun],
        provided: _Provided,
    ) -> Answer:
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
    "AskContext",
    "CitedSource",
    "PreviewSanitiser",
    "Progress",
    "ToolRun",
    "cited_sources",
    "detect_language",
    "elapsed_ms",
    "is_factual",
    "normalise",
    "sanitise",
    "step_for",
    "validate",
]
