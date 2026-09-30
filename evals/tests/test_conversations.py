"""The Ask conversation eval (sos_evals.conversations; docs/06 §13.5; ADR-0034, FR-KB-012).

The scorer must catch every way a conversation can go wrong: a withdrawn document, another
person's question or memory, a deleted or foreign chat hit, an edited-away question sent again,
memory used while off, memory about others stored, and every forbidden cache reuse. The oracle
stub passes; hand-made bad runs fail.
"""

from __future__ import annotations

import pytest

from sos_evals import conversations as conv
from sos_evals import datasets, gates
from sos_evals.conversation_cases import CASES
from sos_evals.conversations import ConversationRun, TurnResult
from sos_evals.stubs import LeakyStub, PerfectStub


def _case(case_id: str) -> conv.ConversationCase:
    return next(c for c in CASES if c.id == case_id)


def _oracle(case: conv.ConversationCase) -> ConversationRun:
    return PerfectStub({}, ()).run_conversation(case)


def _with(run: ConversationRun, step: int, **changes: object) -> ConversationRun:
    turns = tuple(t.model_copy(update=changes) if t.step == step else t for t in run.turns)
    return ConversationRun(turns=turns)


def _problems(case: conv.ConversationCase, run: ConversationRun) -> tuple[list[str], list[str]]:
    outcomes = conv.score(case, run)
    return [x for o in outcomes for x in o.leaks], [x for o in outcomes for x in o.violations]


def test_FR_KB_012_the_committed_cases_are_valid_and_cover_every_category() -> None:
    conv.validate_cases(CASES)
    assert {c.category for c in CASES} == {
        "context",
        "long",
        "permission",
        "revision",
        "followups",
        "memory",
        "cache",
        "chats",
    }
    assert datasets.load().conversations == CASES


def test_FR_KB_012_the_oracle_passes_every_conversation_gate() -> None:
    metrics, outcomes = conv.run(CASES, PerfectStub({}, ()))
    assert outcomes
    assert (metrics.conversation_leakage_count, metrics.conversation_scope_violations) == (0, 0)
    assert metrics.conversation_context_accuracy == 1.0
    assert metrics.followup_language_match == 1.0
    assert metrics.memory_preference_applied == 1.0


def test_invariant_8_a_withdrawn_document_in_the_prompt_is_a_leak() -> None:
    case = _case("conv-permission-revoked")
    run = _with(_oracle(case), 2, model_input="Who attends it? Reference YAK-108.")
    leaks, _ = _problems(case, run)
    assert leaks == ["marker:D1"]
    # Before the revoke the same text is fine.
    assert _problems(case, _with(_oracle(case), 0, model_input="YAK-108"))[0] == []


def test_FR_KB_012_another_persons_question_or_memory_in_the_prompt_is_a_leak() -> None:
    case = _case("conv-cross-user")
    run = _with(_oracle(case), 1, model_input="Earlier: When is the Heron audit visit?")
    assert _problems(case, run)[0] == ["question:0"]
    memory = _case("conv-memory-preference")
    both = memory.model_copy(
        update={
            "people": (*memory.people, conv.ConvPerson(key="U2")),
            "steps": (*memory.steps, conv.ConvStep(action="ask", who="U2", question="Hi?")),
        }
    )
    run = ConversationRun(
        turns=(
            *_oracle(memory).turns,
            TurnResult(step=2, model_input="I prefer answers in Telugu\nHi?"),
        )
    )
    assert _problems(both, run)[0] == ["memory:U1"]


def test_FR_KB_012_chat_hits_of_others_or_of_deleted_conversations_are_leaks() -> None:
    case = _case("conv-chat-search")
    good = _with(_oracle(case), 4, citations=("chat:0",))
    assert _problems(case, good)[0] == []
    deleted = _with(_oracle(case), 4, citations=("chat:1",))
    assert _problems(case, deleted)[0] == ["chat:1"]
    foreign = _with(_oracle(case), 4, citations=("chat:3",))
    assert _problems(case, foreign)[0] == ["chat:3"]


def test_FR_KB_012_an_edited_away_question_sent_again_is_a_violation() -> None:
    case = _case("conv-edit")
    run = _with(
        _oracle(case), 3, model_input="Is there parking at the Swan concert?\nIn which hall?"
    )
    assert _problems(case, run)[1] == ["superseded_question_sent"]


def test_ADR_0034_memory_used_while_off_or_about_others_stored_is_a_violation() -> None:
    off = _case("conv-memory-off")
    run = _with(_oracle(off), 2, model_input="I prefer answers in Telugu\nWhen is it?")
    assert _problems(off, run)[1] == ["memory_used_while_off"]
    others = _case("conv-memory-about-others")
    stored = _with(
        _oracle(others), 0, memories_after=("the student Ravi Synthetica was absent all week",)
    )
    assert _problems(others, stored)[1] == ["memory_about_others_stored"]


@pytest.mark.parametrize(
    ("case_id", "step", "origin", "violation"),
    [
        ("conv-cache-other-access", 1, 0, "cache_other_access"),
        ("conv-cache-revised", 2, 0, "cache_after_revision"),
        ("conv-regenerate", 1, 0, "cache_on_regenerate"),
        ("conv-en-follow-up", 1, 0, "cache_with_history"),
    ],
)
def test_answer_cache_reuse_against_the_rules_is_a_violation(
    case_id: str, step: int, origin: int, violation: str
) -> None:
    case = _case(case_id)
    run = _with(_oracle(case), step, cached_from=origin)
    assert violation in _problems(case, run)[1]


def test_answer_cache_allowed_reuse_is_not_a_violation() -> None:
    case = _case("conv-cache-same-access")
    run = _oracle(case)
    assert run.turns[1].cached_from == 0
    assert _problems(case, run) == ([], [])


def test_the_conversation_gates_are_hard_where_they_guard_privacy() -> None:
    by_metric = {g.metric: g for g in gates.load_gates()}
    for metric in ("conversation_leakage_count", "conversation_scope_violations"):
        assert (by_metric[metric].severity, by_metric[metric].threshold) == ("hard", 0)
    for metric in (
        "conversation_context_accuracy",
        "followup_language_match",
        "memory_preference_applied",
    ):
        assert by_metric[metric].severity == "soft"


def test_a_case_expecting_a_source_its_asker_cannot_see_is_refused() -> None:
    case = _case("conv-permission-revoked")
    bad = case.model_copy(
        update={
            "steps": (
                *case.steps[:2],
                conv.ConvStep(action="ask", question="Again?", expect_sources=("D1",)),
            )
        }
    )
    with pytest.raises(ValueError, match="cannot see"):
        conv.validate_cases([bad])


def test_FR_KB_012_a_history_shared_across_people_trips_the_leakage_gate() -> None:
    metrics, _ = conv.run(CASES, LeakyStub({}, ()))
    assert metrics.conversation_leakage_count
    assert metrics.conversation_leakage_count > 0
    hard = [g for g in gates.load_gates() if g.metric == "conversation_leakage_count"]
    assert [g.severity for g in hard] == ["hard"]
