"""The streamed answer loop (docs/06 §5.1; FR-KB-005, FR-KB-008, FR-KB-011, SEC-019).

No database and no provider: a scripted streaming gateway plays the model (text pieces, then
the whole turn; or a failure after some pieces). Covers: tool rounds complete first and only
the answer turn is shown (a short remark before a tool call is never shown); the streamed
answer is validated exactly like the unstreamed one; a preview that validation changes is
flagged ``replaced`` (dropped citations, "not found"); a failure mid-stream or an exhausted
budget after a tool round degrades to search-only; closing the stream closes the model call; the
preview never shows a link or HTML tag, even split over deltas (§9 rule 5).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import anyio

from app.knowledge.answer import Answer, AnswerEngine, PreviewSanitiser, Progress, sanitise
from app.knowledge.domain import (
    AnswerSegment,
    ConversationItem,
    DeltaEvent,
    Metering,
    ModelRole,
    ModelTurn,
    TextDelta,
    ToolSpec,
    TurnEvent,
    UserMessage,
)
from app.knowledge.gateway.errors import BudgetExhausted, ProviderUnavailable
from app.knowledge.interfaces import StreamingLlmGateway
from app.knowledge.service import was_replaced


def _load() -> ModuleType:
    name = "sos_test_ask_engine_module"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("test_ask_engine.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


E = _load()
_WORDS = re.compile(r"\S+\s*|\s+")


@dataclass
class Fail:
    """Raise ``error`` after ``after`` text pieces of ``turn``."""

    turn: ModelTurn
    after: int
    error: Exception


@dataclass
class StreamingGateway(E.ScriptedGateway):  # type: ignore[misc,name-defined]
    """``ScriptedGateway`` that streams each scripted turn word by word."""

    closed: int = 0
    earlier: list[tuple[str, ...]] = field(default_factory=list)

    def stream_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> Generator[TurnEvent, None, None]:
        first = conversation[0]
        assert isinstance(first, UserMessage)
        self.earlier.append(first.earlier_questions)
        nxt = self.turns[0]
        fail = nxt if isinstance(nxt, Fail) else None
        if fail is not None:
            self.turns[0] = fail.turn
        turn = self.run_turn(metering, role, system, conversation, tools)  # pops, may raise
        try:
            pieces = [p for s in turn.segments for p in _WORDS.findall(s.text)]
            for n, piece in enumerate(pieces):
                if fail is not None and n == fail.after:
                    raise fail.error
                yield TextDelta(piece)
            yield turn
        finally:
            self.closed += 1


def engine(gw: Any, tools: Sequence[Any] = (), search: Any = None) -> AnswerEngine:
    return cast(AnswerEngine, E.engine(gw, tools, search))


def streamed(eng: AnswerEngine, question: str = "When do quarterly exams begin?") -> Any:
    progress = Progress()
    gen = eng.stream(
        cast(Any, object()),
        E.READER,
        question,
        query_id=uuid.uuid4(),
        school_name="Synthetic",
        progress=progress,
        earlier=("What is the exam timetable?",),
    )
    deltas: list[str] = []
    while True:
        try:
            deltas.append(next(gen).text)
        except StopIteration as done:
            answer: Answer = done.value
            return answer, deltas, progress


LONG = (
    "Quarterly examinations begin on 22/09/2026 at 09:30 in every classroom of the school, "
    "and students carry their hall tickets and two pencils to each paper."
)
assert len(LONG) > E.CONFIG.streaming.preview_hold_chars


def script(text: str = LONG) -> list[Any]:
    remark = AnswerSegment("Let me search.")
    return [
        E.turn(remark, calls=[E.search_call()]),
        E.turn(E.cited(text)),
    ]


def test_FR_KB_008_gateway_double_streams() -> None:
    assert isinstance(StreamingGateway([]), StreamingLlmGateway)


def test_FR_KB_008_tool_rounds_first_then_only_the_answer_streams() -> None:
    gw = StreamingGateway(script())
    answer, deltas, progress = streamed(engine(gw, [E.search_tool()]))
    assert len(deltas) > 3
    shown = "".join(deltas)
    assert shown == LONG
    assert "Let me search" not in shown  # held (short) text before a tool call is never shown
    assert progress.text == shown
    assert answer.status == "answered"
    assert not was_replaced(shown, answer)  # only the [1] marker differs
    assert answer.segments[0].text.endswith("[1]")
    assert gw.earlier == [("What is the exam timetable?",)] * 2  # FR-KB-012 context
    unstreamed = E.run(engine(E.ScriptedGateway(script()), [E.search_tool()]))
    assert (answer.status, answer.cited, answer.text) == (
        unstreamed.status,
        unstreamed.cited,
        unstreamed.text,
    )


def test_FR_KB_008_a_short_answer_turn_is_shown_whole_once_the_turn_ends() -> None:
    gw = StreamingGateway(script("Exams begin on 22/09/2026."))
    answer, deltas, _ = streamed(engine(gw, [E.search_tool()]))
    assert "".join(deltas) == "Exams begin on 22/09/2026."
    assert answer.status == "answered"


def test_FR_KB_008_the_last_possible_turn_streams_at_once() -> None:
    rounds = E.CONFIG.limits.max_tool_rounds
    calls = [E.turn(calls=[E.search_call(i)]) for i in range(1, rounds + 1)]
    gw = StreamingGateway([*calls, E.turn(E.cited("Exams begin on 22/09/2026 at 09:30."))])
    _, deltas, _ = streamed(engine(gw, [E.search_tool()]))
    assert len(deltas) > 3  # no hold: no tool round can follow


def test_FR_KB_005_invalid_citation_after_streaming_replaces_the_preview() -> None:
    gw = StreamingGateway(
        [
            E.turn(calls=[E.search_call()]),
            E.turn(E.cited("Exams begin on 01/01/2027.", cited_text="begin on 01/01/2027")),
        ]
    )
    answer, deltas, _ = streamed(engine(gw, [E.search_tool()]))
    assert "".join(deltas) == "Exams begin on 01/01/2027."
    assert answer.status == "not_found"
    assert answer.citations_dropped == 1
    assert was_replaced("".join(deltas), answer)
    assert answer.text == E.CONFIG.answer_checks.not_found.en


def test_NFR_AVL_004_failure_mid_stream_degrades_to_search_only() -> None:
    final = E.turn(E.cited(LONG))
    words = len(LONG.split())
    gw = StreamingGateway(
        [E.turn(calls=[E.search_call()]), Fail(final, words - 3, ProviderUnavailable("down"))]
    )
    answer, deltas, _ = streamed(engine(gw, [E.search_tool()], E.FakeSearch([E.chunk()])))
    assert deltas
    assert LONG.startswith("".join(deltas))
    assert "".join(deltas) != LONG
    assert answer.status == "search_only"
    assert answer.mode == "search_only"
    assert (answer.error_code, answer.message_key) == ("ai_unavailable", "kb.errors.unavailable")
    assert [c.source for c in answer.cited] == [E.SOURCE]
    assert answer.segments == ()
    assert was_replaced("".join(deltas), answer)


def test_FR_KB_011_budget_exhausted_after_a_tool_round_answers_search_only() -> None:
    gw = StreamingGateway([E.turn(calls=[E.search_call()]), BudgetExhausted("used up")])
    answer, deltas, progress = streamed(engine(gw, [E.search_tool()], E.FakeSearch([E.chunk()])))
    assert deltas == []
    assert answer.status == "search_only"
    assert answer.error_code == "ai_budget_exhausted"
    assert [r.name for r in progress.runs] == ["search_documents"]
    assert not was_replaced("", answer)


def test_FR_KB_009_closing_the_stream_closes_the_model_call() -> None:
    gw = StreamingGateway(script())
    progress = Progress()
    gen = engine(gw, [E.search_tool()]).stream(
        cast(Any, object()),
        E.READER,
        "When do quarterly exams begin?",
        query_id=uuid.uuid4(),
        school_name="Synthetic",
        progress=progress,
    )
    assert next(gen).text
    gen.close()
    assert gw.closed == 2  # the tool turn finished; the answer turn was closed
    assert progress.text
    assert [r.name for r in progress.runs] == ["search_documents"]
    assert len(progress.turns) == 1


def test_a_gateway_without_streaming_still_streams_the_final_text_once() -> None:
    answer, deltas, _ = streamed(engine(E.ScriptedGateway(script()), [E.search_tool()]))
    assert "".join(deltas) == LONG
    assert len(deltas) <= 2  # the whole text at once (the last word after the flush)
    assert answer.status == "answered"


def test_SEC_019_preview_never_shows_a_link_or_tag_even_split_over_deltas() -> None:
    text = (
        "Exams begin on 22/09/2026. See [the portal](https://evil.example/login) or "
        'www.evil.example and <a href="https://evil.example">here</a> <b>now</b> today.'
    )
    preview = PreviewSanitiser(2000)
    shown = "".join(preview.feed(ch) for ch in text) + preview.flush()
    assert "http" not in shown
    assert "www." not in shown
    assert "<" not in shown
    assert "the portal" in shown
    assert shown.split() == sanitise(text).split()


def test_preview_holds_at_most_the_configured_pending_characters() -> None:
    preview = PreviewSanitiser(100)
    assert preview.feed("<b " + "x" * 50) == ""  # an unfinished tag is held
    out = preview.feed("x" * 100)
    assert out == "<b " + "x" * 150  # over the limit: emitted (not a tag, so kept as text)


def test_english_first_preview_stops_before_telugu_script() -> None:
    """ADR-0036: while Telugu is hidden no Telugu character is ever streamed; the validated
    ``final`` answer replaces the preview."""
    preview = PreviewSanitiser(2000, english_only=True)
    text = "Exams begin on 22/09/2026. పరీక్షలు 22/09/2026న. More English after."
    shown = "".join(preview.feed(f"{piece} ") for piece in text.split(" ")) + preview.flush()
    assert not any("\u0c00" <= ch <= "\u0c7f" for ch in shown)
    assert shown.startswith("Exams begin on")
    assert "More English" not in shown  # stopped for good
    on = PreviewSanitiser(2000)
    assert "పరీక్షలు" in on.feed(text) + on.flush()  # Telugu switched on: shown


class _ClosingStream:
    """Stands in for ``service.AskStream`` in the route's SSE adapter."""

    def __init__(self) -> None:
        self.closed = 0
        self._events = iter([DeltaEvent(text="Exams "), DeltaEvent(text="begin.")])

    def __next__(self) -> DeltaEvent:
        return next(self._events)

    def close(self) -> None:
        self.closed += 1


def test_FR_KB_008_a_client_disconnect_closes_the_stream() -> None:
    """Starlette cancels the response body when the client goes away; the adapter's
    ``finally`` must then close the stream (which records the question ``cancelled``)."""
    from app.knowledge.api import _sse_stream

    stream = _ClosingStream()

    async def client_leaves_after_one_frame() -> str:
        frames = _sse_stream(cast(Any, stream))
        first = await anext(frames)
        await frames.aclose()
        return first

    first = anyio.run(client_leaves_after_one_frame)
    assert first.startswith("event: delta\n")
    assert stream.closed == 1


def test_FR_KB_008_the_stream_is_closed_after_the_last_event_too() -> None:
    from app.knowledge.api import _sse_stream

    stream = _ClosingStream()

    async def read_all() -> list[str]:
        return [frame async for frame in _sse_stream(cast(Any, stream))]

    assert len(anyio.run(read_all)) == 2
    assert stream.closed == 1
