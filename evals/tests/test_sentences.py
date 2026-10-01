"""The harness's per-sentence citation metrics (docs/06 §9 rule 3, §13.2; FR-KB-005, FR-KB-007)."""

from __future__ import annotations

from datetime import date

import pytest

from sos_evals import sentences
from sos_evals.sentences import figures, is_factual, score, split

SOURCE = sentences.figures("Circular 12/2026. Fees of ₹12,500 are due on 15/10/2026 at 10:30.")


def test_split_ends_at_terminators_and_line_breaks_in_any_script() -> None:
    text = (
        "Fees are due on 15/10/2026. Pay at the office!\n- Bring the receipt\n"
        "పరీక్షలు సోమవారం। ఫీజు ₹500."
    )
    assert [s.text for s in split(text)] == [
        "Fees are due on 15/10/2026.",
        "Pay at the office!",
        "- Bring the receipt",
        "పరీక్షలు సోమవారం।",
        "ఫీజు ₹500.",
    ]
    assert all(text[s.start : s.end] == s.text for s in split(text))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fees are due on 15/10/2026.", True),
        ("Fees are not due on Sunday.", True),
        ("Here is what the circular says:", False),
        ("Not found in school records you can access.", False),
        ("I could not find the fee for 2026.", True),
        ("| --- | --- |", False),
        ("1. Pay at the office.", True),
        ("1. Here are the dates:", False),
    ],
)
def test_factual_sentences(text: str, expected: bool) -> None:
    assert is_factual(text) is expected


def test_figures_are_dates_times_amounts_and_long_numbers() -> None:
    assert figures("Due 15/10/2026 at 10:30, ₹1,25,000 for 9A, page 3, year 2026.") == {
        ("date", 15, 10, 2026),
        ("time", 10, 30, 0),
        ("number", 125000, 0, 0),
        ("number", 2026, 0, 0),
    }
    assert figures("౧౫/౧౦/౨౦౨౬") == {("date", 15, 10, 2026)}  # Telugu digits
    assert figures("cut short at 20…") == frozenset()  # a truncated snippet token
    assert sentences.date_figure(date(2026, 9, 22)) == ("date", 22, 9, 2026)


def test_a_cited_sentence_with_its_figures_in_the_source_is_supported() -> None:
    (only,) = score([("Fees of ₹12,500 are due on 15/10/2026. [1]", [SOURCE])])
    assert (only.factual, only.cited, only.supported, only.high_severity) == (
        True,
        True,
        True,
        False,
    )


def test_markers_are_not_facts_and_are_removed_before_splitting() -> None:
    scored = score([("Here is the fee circular: [1]", [SOURCE])])
    assert [s.factual for s in scored] == [False]


def test_an_uncited_sentence_is_unsupported_and_high_severity_with_a_figure() -> None:
    scored = score(
        [
            ("Fees are due on 15/10/2026. [1]", [SOURCE]),
            (" Results come on 30/10/2026.", []),
            (" The office can help.", []),
        ]
    )
    assert [(s.supported, s.high_severity) for s in scored] == [
        (True, False),
        (False, True),
        (False, False),
    ]


def test_a_cited_sentence_writing_a_figure_its_source_does_not_is_high_severity() -> None:
    (only,) = score([("Fees are due on 16/10/2026. [1]", [SOURCE])])
    assert only.cited
    assert not only.supported
    assert only.high_severity


def test_a_native_citation_covers_every_sentence_of_its_segment() -> None:
    scored = score([("Fees are due on 15/10/2026. Pay at 10:30. [1]", [SOURCE])])
    assert all(s.supported for s in scored)
