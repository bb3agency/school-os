"""The answer loop and its server-side checks, with a scripted model (docs/06 §5, §9, §15).

No database and no provider: a scripted :class:`LlmGateway` plays the model, fake tools return
fixed ``search_result`` blocks. Covers FR-KB-005 (citations validated: unknown source, text not
in the block, dropped), FR-KB-007 / invariant 8 ("not found in school records" when nothing
valid supports the answer), §9 rule 3 (too many uncited facts -> search-only), §9 rule 5 and
SEC-019 (no external links or HTML in the answer), FR-KB-011 (budget exhausted -> search-only
with the reason), SEC-020 (tools offered only with their permission, at most 3 rounds, the
12k-token context budget) and invariant 9 (a failing tool is reported, nothing else happens).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from app.authz.context import Scopes, UserContext
from app.core.redaction import verhoeff_check_digit
from app.knowledge.answer import (
    AnswerEngine,
    detect_language,
    normalise,
    sanitise,
)
from app.knowledge.config.llm import load_llm_config
from app.knowledge.domain import (
    AnswerSegment,
    Citation,
    ConversationItem,
    Metering,
    ModelRole,
    ModelTurn,
    RankedChunk,
    SearchResultBlock,
    ToolCall,
    ToolOutcome,
    ToolResultsMessage,
    ToolSpec,
    Usage,
)
from app.knowledge.gateway.errors import BudgetExhausted, GatewayMisuse
from app.knowledge.prompts.registry import load_prompt
from app.knowledge.tools.documents import DocumentSearch
from app.knowledge.tools.registry import OfferedTool

DOC = uuid.UUID("0190f000-0000-7000-8000-0000000000d1")
SOURCE = f"sos://doc/{DOC}/v2#p1"
OTHER = f"sos://doc/{uuid.UUID(int=7)}/v1#p1"
TEXT = "Quarterly examinations begin on 22/09/2026 at 09:30. Students carry hall tickets."
CONFIG = load_llm_config()
PROMPT = load_prompt("answer_system", 1)


def ctx(*permissions: str) -> UserContext:
    return UserContext(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        roles=frozenset({"office_staff"}),
        permissions=frozenset(permissions),
        scopes=Scopes(school=True),
        mfa=True,
        auth_time=None,
    )


READER = ctx("kb.ask", "document.read", "student.read_basic")


def turn(
    *segments: AnswerSegment, calls: Sequence[ToolCall] = (), model: str = "claude-sonnet-5"
) -> ModelTurn:
    return ModelTurn(
        model=model,
        stop_reason="tool_use" if calls else "end_turn",
        segments=tuple(segments),
        tool_calls=tuple(calls),
        usage=Usage(100, 20),
    )


def search_call(n: int = 1) -> ToolCall:
    return ToolCall(call_id=f"toolu_{n:02d}", name="search_documents", arguments={"query": "x"})


@dataclass
class ScriptedGateway:
    """Plays the model: returns the scripted turns in order (or raises)."""

    turns: list[ModelTurn | Exception]
    seen_tools: list[list[str]] = field(default_factory=list)
    conversations: list[list[ConversationItem]] = field(default_factory=list)

    def run_turn(
        self,
        metering: Metering,
        role: ModelRole,
        system: str,
        conversation: Sequence[ConversationItem],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        assert role == "answer"
        assert metering.feature == "ask"
        assert metering.query_id is not None
        self.seen_tools.append([t.name for t in tools])
        self.conversations.append(list(conversation))
        nxt = self.turns.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def generate_json(self, *args: Any, **kwargs: Any) -> Mapping[str, object]:
        raise AssertionError("not used by the ask loop")


@dataclass
class FakeTool:
    name: str
    permission: str
    blocks: tuple[SearchResultBlock, ...] = ()
    error: Exception | None = None
    runs: int = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="synthetic tool for tests",
            input_schema={"type": "object"},
            permission=self.permission,
        )

    def allowed(self, c: UserContext) -> bool:
        return c.has(self.permission)

    def run(
        self, session: Any, c: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        self.runs += 1
        if self.error is not None:
            raise self.error
        return ToolOutcome(call_id=call_id, blocks=self.blocks)


@dataclass
class FakeSearch:
    chunks: list[RankedChunk] = field(default_factory=list)

    def search(self, session: Any, c: UserContext, query: str, **kwargs: Any) -> list[RankedChunk]:
        return list(self.chunks)


def chunk(source: str = SOURCE, content: str = TEXT) -> RankedChunk:
    return RankedChunk(
        chunk_id=uuid.uuid4(),
        document_id=DOC,
        version_id=uuid.uuid4(),
        version_no=2,
        page_from=1,
        page_to=1,
        doc_type="circular",
        title="Quarterly examination timetable (revised)",
        issued_on=None,
        content=content,
        score=0.5,
        source=source,
    )


def engine(
    gateway: ScriptedGateway,
    tools: Sequence[FakeTool] = (),
    search: FakeSearch | None = None,
) -> AnswerEngine:
    return AnswerEngine(
        gateway=gateway,
        tools={t.name: cast(OfferedTool, t) for t in tools},
        search=cast(DocumentSearch, search or FakeSearch()),
        config=CONFIG,
        prompt=PROMPT,
    )


def search_tool(*blocks: SearchResultBlock) -> FakeTool:
    return FakeTool(
        "search_documents",
        "document.read",
        blocks or (SearchResultBlock(SOURCE, "Circular · Timetable (p.1)", TEXT),),
    )


def run(
    eng: AnswerEngine, question: str = "When do quarterly exams begin?", c: UserContext = READER
) -> Any:
    return eng.run(cast(Any, object()), c, question, query_id=uuid.uuid4(), school_name="Synthetic")


def cited(text: str, source: str = SOURCE, cited_text: str = TEXT) -> AnswerSegment:
    return AnswerSegment(text, (Citation(source, cited_text),))


def test_FR_KB_005_valid_citation_is_kept_and_numbered() -> None:
    gw = ScriptedGateway([turn(calls=[search_call()]), turn(cited("Exams begin on 22/09/2026."))])
    answer = run(engine(gw, [search_tool()]))
    assert answer.status == "answered"
    assert answer.mode == "full"
    assert answer.route == "documents"
    assert answer.segments[0].text.endswith("[1]")
    assert [(c.index, c.source) for c in answer.cited] == [(1, SOURCE)]
    assert answer.provided == (SOURCE,)
    assert answer.model_ids == ("claude-sonnet-5",)
    assert (answer.input_tokens, answer.output_tokens) == (200, 40)


def test_FR_KB_005_citation_to_a_source_not_given_is_dropped_and_answer_is_not_found() -> None:
    gw = ScriptedGateway(
        [turn(calls=[search_call()]), turn(cited("Exams begin on 22/09/2026.", source=OTHER))]
    )
    answer = run(engine(gw, [search_tool()]))
    assert answer.status == "not_found"
    assert answer.refused
    assert answer.cited == ()
    assert answer.citations_dropped == 1
    assert answer.text == CONFIG.answer_checks.not_found.en


def test_FR_KB_005_cited_text_must_be_in_the_block() -> None:
    fabricated = "Quarterly examinations begin on 01/01/2027"
    gw = ScriptedGateway(
        [turn(calls=[search_call()]), turn(cited("Exams begin 01/01/2027.", cited_text=fabricated))]
    )
    answer = run(engine(gw, [search_tool()]))
    assert answer.status == "not_found"
    assert answer.citations_dropped == 1


def test_FR_KB_005_cited_text_matches_after_whitespace_normalisation() -> None:
    spaced = "Quarterly   examinations\nbegin on 22/09/2026"
    gw = ScriptedGateway(
        [turn(calls=[search_call()]), turn(cited("Exams begin on 22/09/2026.", cited_text=spaced))]
    )
    assert run(engine(gw, [search_tool()])).status == "answered"


def test_FR_KB_007_no_tool_results_answers_not_found_in_the_question_language() -> None:
    gw = ScriptedGateway([turn(AnswerSegment("ఈత కొలను 2026లో తెరుస్తారు."))])
    answer = run(engine(gw, [search_tool()]), question="పాఠశాల ఈత కొలను ఎప్పుడు తెరుస్తారు?")
    assert answer.status == "not_found"
    assert answer.language == "te"
    assert answer.text == CONFIG.answer_checks.not_found.te


def test_FR_KB_006_code_mixed_telugu_question_gets_the_telugu_not_found() -> None:
    gw = ScriptedGateway([turn(AnswerSegment("Nothing."))])
    answer = run(engine(gw, [search_tool()]), question="9B ఫీల్డ్ ట్రిప్ ఎప్పుడు?")
    assert answer.language == "mixed"
    assert answer.text == CONFIG.answer_checks.not_found.te


def test_docs_06_s9_rule3_mostly_uncited_facts_fall_back_to_search_only() -> None:
    gw = ScriptedGateway(
        [
            turn(calls=[search_call()]),
            turn(
                cited("Exams begin on 22/09/2026."),
                AnswerSegment("Fees are due on 05/10/2026."),
                AnswerSegment("Results come on 30/10/2026."),
            ),
        ]
    )
    answer = run(engine(gw, [search_tool()]))
    assert answer.status == "search_only"
    assert answer.mode == "search_only"
    assert answer.segments == ()
    assert [c.source for c in answer.cited] == [SOURCE]
    assert answer.uncited_factual == 2


def test_SEC_019_external_links_and_html_are_removed_from_the_answer() -> None:
    gw = ScriptedGateway(
        [
            turn(calls=[search_call()]),
            turn(
                cited(
                    "Exams begin on 22/09/2026. <script>x()</script> See [here](https://evil.example)"
                    " or www.evil.example and [the circular](sos://doc/x)."
                )
            ),
        ]
    )
    text = run(engine(gw, [search_tool()])).text
    assert "http" not in text
    assert "www." not in text
    assert "<script>" not in text
    assert "(sos://doc/x)" in text


def test_invariant_4_aadhaar_in_model_output_is_masked() -> None:
    body = "73920184556"
    number = body + verhoeff_check_digit(body)  # synthetic, Verhoeff-valid
    spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
    out = sanitise(f"Number {spaced} is on file.")
    assert number not in out.replace(" ", "")
    assert number[-4:] in out


def test_FR_KB_011_budget_exhausted_answers_search_only_with_the_reason() -> None:
    gw = ScriptedGateway([BudgetExhausted("used up")])
    answer = run(engine(gw, [search_tool()], FakeSearch([chunk()])))
    assert answer.status == "search_only"
    assert answer.mode == "search_only"
    assert answer.error_code == "ai_budget_exhausted"
    assert answer.message_key == "kb.errors.budget"
    assert [c.source for c in answer.cited] == [SOURCE]
    assert answer.segments == ()


def test_gateway_misuse_is_a_bug_not_a_fallback() -> None:
    gw = ScriptedGateway([GatewayMisuse("programming error")])
    with pytest.raises(GatewayMisuse):
        run(engine(gw, [search_tool()]))


def test_SEC_020_tools_are_offered_only_with_their_permission() -> None:
    records = FakeTool("find_students", "student.read_basic")
    docs = search_tool()
    gw = ScriptedGateway([turn(AnswerSegment("Nothing."))])
    run(engine(gw, [records, docs]), c=ctx("kb.ask", "document.read"))
    assert gw.seen_tools == [["search_documents"]]


def test_SEC_020_at_most_three_tool_rounds() -> None:
    gw = ScriptedGateway([turn(calls=[search_call(i)]) for i in range(1, 5)])
    tool = search_tool()
    answer = run(engine(gw, [tool]))
    assert tool.runs == CONFIG.limits.max_tool_rounds + 1
    assert gw.turns == []
    assert answer.status == "not_found"


def test_SEC_020_tool_results_are_trimmed_to_the_context_budget() -> None:
    limits = CONFIG.limits
    budget = limits.tool_result_context_tokens * limits.chars_per_token_estimate
    big = "word " * (budget // 10)
    blocks = tuple(
        SearchResultBlock(f"sos://doc/{uuid.UUID(int=i)}/v1#p1", "T", big) for i in range(1, 5)
    )
    gw = ScriptedGateway([turn(calls=[search_call()]), turn(AnswerSegment("Nothing."))])
    run(engine(gw, [search_tool(*blocks)]))
    sent = [i for i in gw.conversations[-1] if isinstance(i, ToolResultsMessage)]
    total = sum(len(b.title) + len(b.text) for m in sent for o in m.outcomes for b in o.blocks)
    assert 0 < total <= budget


def test_docs_06_s15_a_failing_tool_is_reported_to_the_model() -> None:
    broken = FakeTool("search_documents", "document.read", error=ConnectionError("down"))
    gw = ScriptedGateway([turn(calls=[search_call()]), turn(AnswerSegment("Could not search."))])
    answer = run(engine(gw, [broken]))
    results = [i for i in gw.conversations[-1] if isinstance(i, ToolResultsMessage)]
    assert results[0].outcomes[0].is_error
    assert answer.tools[0].error
    assert answer.status == "not_found"


def test_invariant_9_unknown_tool_call_runs_nothing() -> None:
    call = ToolCall(call_id="toolu_01", name="find_students", arguments={"query": "x"})
    records = FakeTool("find_students", "student.read_basic")
    gw = ScriptedGateway([turn(calls=[call]), turn(AnswerSegment("Nothing."))])
    run(engine(gw, [records]), c=ctx("kb.ask"))
    assert records.runs == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("When is sports day?", "en"),
        ("తాగునీటి ట్యాంకుల శుభ్రత ఎప్పుడు?", "te"),
        ("DEO circular లో exam timings ఏమిటి?", "mixed"),
    ],
)
def test_FR_KB_006_language_by_script(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_normalise_is_nfc_casefold_and_collapses_whitespace() -> None:
    assert normalise("  A\n\tB  ") == "a b"
