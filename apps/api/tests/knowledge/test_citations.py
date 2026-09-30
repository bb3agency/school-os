"""Provider-neutral passage markers (ADR-0033; docs/06 §7, §9; FR-KB-005, FR-KB-007).

Pure unit tests of :mod:`app.knowledge.gateway.citations`: passages are numbered across tool
rounds; ``[n]`` markers become citations of THIS request's passages only; a statement whose
numbers its passages do not contain loses its markers (uncited, so the answer falls back); the
cited text is always a sentence copied from the passage; streamed text never shows a marker.
"""

from __future__ import annotations

import pytest

from app.knowledge.domain import (
    AssistantMessage,
    ConversationItem,
    ModelTurn,
    SearchResultBlock,
    ToolCall,
    ToolOutcome,
    ToolResultsMessage,
    Usage,
    UserMessage,
)
from app.knowledge.gateway.citations import (
    MarkerStripper,
    Passage,
    number_passages,
    numbers_in,
    segments_from_markers,
    supporting_sentence,
)

A = SearchResultBlock(
    "sos://doc/a/v1#p1", "Circular 12/2026", "Fees are due on 15/10/2026. Pay at the office."
)
B = SearchResultBlock("sos://doc/b/v1#p1", "Timetable", "Class X exams start at 09:30.")
PASSAGES = (Passage(1, A), Passage(2, B))


def mark(text: str, require: bool = True) -> list[tuple[str, list[str]]]:
    marked = segments_from_markers(text, PASSAGES, require_numbers=require)
    return [(s.text, [c.source for c in s.citations]) for s in marked.segments]


def test_passages_are_numbered_in_conversation_order_across_rounds() -> None:
    call = ToolCall("c1", "search_documents", {})
    conversation: list[ConversationItem] = [
        UserMessage("q"),
        AssistantMessage(ModelTurn("m", "tool_use", (), (call,), Usage(1, 1))),
        ToolResultsMessage((ToolOutcome("c1", (A,)), ToolOutcome("c2", ()))),
        AssistantMessage(ModelTurn("m", "tool_use", (), (call,), Usage(1, 1))),
        ToolResultsMessage((ToolOutcome("c3", (B,)),)),
    ]
    assert number_passages(conversation) == PASSAGES


def test_FR_KB_005_markers_and_trailing_punctuation() -> None:
    assert mark("Fees are due on 15/10/2026 [1]. Exams start at 09:30 [2].") == [
        ("Fees are due on 15/10/2026.", [A.source]),
        (" Exams start at 09:30.", [B.source]),
    ]
    assert mark("Pay at the office [1][2], or online.") == [
        ("Pay at the office,", [A.source, B.source]),
        (" or online.", []),
    ]
    assert mark("Pay at the office [1, 2].") == [("Pay at the office.", [A.source, B.source])]


def test_FR_KB_005_a_marker_for_no_passage_of_this_request_is_dropped() -> None:
    marked = segments_from_markers("Pay at the office [3].", PASSAGES, require_numbers=True)
    assert [(s.text, s.citations) for s in marked.segments] == [("Pay at the office.", ())]
    assert marked.dropped == 1


@pytest.mark.parametrize(
    ("text", "cited"),
    [
        ("Fees are due on 15/10/2026 [1].", True),
        ("Fees are due on 16/10/2026 [1].", False),  # a date the passage does not write
        ("Circular 12 of 2026 says so [1].", True),  # numbers of the title count
        ("Class 10 exams start at 9:30 [2].", True),  # Roman X and 09 read as numbers
        ("Class 10 exams start at 9:30 [1].", False),  # right fact, wrong passage
        ("Fees of ₹12,500 are due [1].", False),
    ],
)
def test_FR_KB_007_statement_numbers_must_be_in_the_cited_passages(text: str, cited: bool) -> None:
    ((_, sources),) = mark(text)
    assert bool(sources) is cited
    ((_, loose),) = mark(text, require=False)
    assert loose  # the rule is the only thing that drops these


def test_numbers_in_reads_indian_grouping_unicode_digits_and_roman_classes() -> None:
    assert numbers_in("₹1,25,000 and 07") == {125000, 7}
    assert numbers_in("౧౨ తేదీ") == {12}  # Telugu digits
    assert numbers_in("Class IX", roman=True) == {9}
    assert numbers_in("Class IX") == frozenset()


def test_FR_KB_005_cited_text_is_the_best_sentence_of_the_passage() -> None:
    assert (
        supporting_sentence("When are fees due? 15/10/2026", A.text)
        == "Fees are due on 15/10/2026."
    )
    assert supporting_sentence("Where do I pay?", A.text) == "Pay at the office."
    assert supporting_sentence("unrelated", A.text) == "Fees are due on 15/10/2026."
    for claim in ("x", "office", "15"):
        assert supporting_sentence(claim, A.text) in A.text


def test_FR_TALLY_008_every_figure_of_the_statement_is_inside_the_cited_text() -> None:
    dues = "Fee dues for SV-1. Term 1: 12,500.00. Transport: 3,000.00. Total due: 15,500.00."
    claim = "Total due is 15,500.00 (term fee 12,500.00, transport 3,000.00)"
    cited = supporting_sentence(claim, dues)
    assert cited == "Term 1: 12,500.00. Transport: 3,000.00. Total due: 15,500.00."
    assert cited in dues


def test_FR_KB_008_streamed_text_never_shows_a_marker() -> None:
    stripper = MarkerStripper()
    pieces = ["Fees are due [", "1", "]. Pay at", " the office [2][", "1].", " Done [x]."]
    shown = "".join(stripper.feed(p) for p in pieces) + stripper.flush()
    assert shown == "Fees are due. Pay at the office. Done [x]."
