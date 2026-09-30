"""English first, Telugu hidden (ADR-0036): the scoring of the switch-off pass."""

from __future__ import annotations

from datetime import date

from sos_evals import english_first as ef
from sos_evals.adapters import AnswerSegment, AskResult
from sos_evals.circulars import CircularResult, SuggestedDeadline
from sos_evals.conversations import ConversationRun, TurnResult


def _ask(text: str, **kw: object) -> AskResult:
    return AskResult(segments=(AnswerSegment(text=text),), refused=False, provided_sources=(), **kw)


def test_an_english_answer_with_english_language_passes() -> None:
    shown = ef.ask_shown(_ask("Exams begin on 22/09/2026.", language="en", followups=("More?",)))
    outcome = ef._outcome("x", "ask", shown)
    assert outcome.telugu_fields == ()
    assert ef.is_english("Exams begin on 22/09/2026.")


def test_telugu_prose_a_telugu_language_or_a_missing_language_counts() -> None:
    assert ef._outcome("x", "ask", ef.ask_shown(_ask("పరీక్షలు", language="en"))).telugu_fields == (
        "answer",
    )
    assert ef._outcome("x", "ask", ef.ask_shown(_ask("Exams.", language="te"))).telugu_fields == (
        "language",
    )
    assert ef._outcome("x", "ask", ef.ask_shown(_ask("Exams."))).telugu_fields == ("language",)
    telugu_followup = ef.ask_shown(_ask("Exams.", language="en", followups=("ఇంకా?",)))
    assert ef._outcome("x", "ask", telugu_followup).telugu_fields == ("followup[0]",)


def test_search_only_passages_are_not_the_answer_and_not_english() -> None:
    """Cited passages are the school's own words (not counted), but they are not an answer."""
    result = _ask("పరీక్షలు 22/09/2026న.", language="en", mode="search_only")
    assert ef._outcome("x", "ask", ef.ask_shown(result)).telugu_fields == ()
    assert not (result.mode == "full" and ef.is_english(result.text))


def test_circular_quotes_are_evidence_but_titles_and_summaries_count() -> None:
    reading = CircularResult(
        deadlines=(
            SuggestedDeadline(
                due_on=date(2026, 10, 20), quote="20 అక్టోబర్ 2026 లోగా", title="Submit rolls"
            ),
        ),
        summary_en="Submit the nominal rolls by 20/10/2026.",
    )
    assert ef._outcome("c", "circular", ef.circular_shown(reading)).telugu_fields == ()
    telugu = reading.model_copy(update={"summary_te": "సమర్పించండి", "subject": "విషయం"})
    assert set(ef._outcome("c", "circular", ef.circular_shown(telugu)).telugu_fields) == {
        "summary_te",
        "subject",
    }


def test_a_probe_that_shows_nothing_fails() -> None:
    assert ef._outcome("c", "circular", ef.circular_shown(CircularResult())).telugu_fields == (
        "nothing",
    )
    assert ef._outcome("n", "notice", ef.notice_shown(ef.NoticeResult())).telugu_fields == (
        "nothing",
    )


def test_notice_telugu_fields_must_be_empty() -> None:
    notice = ef.NoticeResult(title_en="Notice", body_en="Sports day.", body_te="క్రీడా దినోత్సవం")
    assert ef._outcome("n", "notice", ef.notice_shown(notice)).telugu_fields == ("body_te",)


def test_conversation_turns_count_text_followups_memory_title_and_language() -> None:
    run = ConversationRun(
        turns=(
            TurnResult(
                step=0,
                text="Fair on 14/11/2026.",
                followups=("మరిన్ని?",),
                memory=(("suggested", "Prefers Telugu"),),
                title="కెస్ట్రెల్",
                language="mixed",
            ),
        )
    )
    bad = ef._outcome("v", "conversation", ef.conversation_shown(run)).telugu_fields
    assert set(bad) == {"step[0].followup", "step[0].title", "language"}


def test_aggregate_and_empty_runs() -> None:
    assert ef.aggregate(()).english_first_telugu_outputs is None  # not measured: gate fails
    outcomes = (
        ef.EnglishFirstOutcome(id="a", kind="ask", fields=3, telugu_fields=(), english_answer=True),
        ef.EnglishFirstOutcome(
            id="b", kind="ask", fields=3, telugu_fields=("answer",), english_answer=False
        ),
    )
    m = ef.aggregate(outcomes)
    assert (m.english_first_items, m.english_first_fields) == (2, 6)
    assert m.english_first_telugu_outputs == 1
    assert m.english_first_english_answer_rate == 0.5
