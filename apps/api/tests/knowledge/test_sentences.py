"""Sentence splitting and the factual-sentence rule (docs/06 §9 rule 3; FR-KB-005, FR-KB-007).

Pure unit tests of :mod:`app.knowledge.sentences` with the rules of ``models.yaml``
(``answer_checks.sentences``, invariant 13): numbers, abbreviations and initials never end a
sentence; lists and tables in the restricted markdown are one unit per line; Telugu text splits
on the same script-neutral rules; a sentence is factual unless it is markup only, a short lead-in,
a configured connective or a "not found" sentence, and a digit always makes it factual (negation
does not make a claim non-factual).
"""

from __future__ import annotations

import pytest

from app.knowledge.config.llm import load_llm_config
from app.knowledge.sentences import Sentence, body, is_factual, split_sentences

RULES = load_llm_config().answer_checks.sentences


def texts(text: str) -> list[str]:
    return [s.text for s in split_sentences(text, RULES)]


def test_spans_point_into_the_text() -> None:
    text = "  Fees are due on 15/10/2026.  Pay at the office!\nBring the receipt?"
    found = split_sentences(text, RULES)
    assert [s.text for s in found] == [
        "Fees are due on 15/10/2026.",
        "Pay at the office!",
        "Bring the receipt?",
    ]
    assert all(text[s.start : s.end] == s.text for s in found)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Decimals, amounts with grouping and times never end a sentence.
        ("The fee is ₹12,500.50 per term. Pay by 10.30 am.", 2),
        ("Rc. No. 12/2026 sets the fee at Rs. 1,25,000. It is due in June.", 2),
        # Initials are part of a name; a class numeral may end a sentence.
        ("Sri K. Ramesh is the class teacher. He teaches maths.", 2),
        ("The trip is for Class X. Fees are due on 05/10/2026.", 2),
        ("Exams are at 9 a.m. on Monday. Bring pencils.", 2),
        ("Exams start at 9 a.m. Students bring pencils.", 2),
        ("Is the fee due? Yes, on 15/10/2026! Pay online.", 3),
        ("Wait for it... The result comes on 30/10/2026.", 2),
        ("(Fees are due on 15/10/2026.) Pay at the office.", 2),
        ('He said "fees are due." Pay at the office.', 2),
    ],
)
def test_sentence_boundaries(text: str, expected: int) -> None:
    assert len(texts(text)) == expected, texts(text)


def test_telugu_text_splits_on_the_same_rules() -> None:
    text = "పరీక్షలు 22/09/2026న ప్రారంభమవుతాయి. హాల్ టికెట్లు తీసుకురండి। ఫీజు ₹500."
    assert texts(text) == [
        "పరీక్షలు 22/09/2026న ప్రారంభమవుతాయి.",
        "హాల్ టికెట్లు తీసుకురండి।",
        "ఫీజు ₹500.",
    ]


def test_a_telugu_sentence_after_a_dot_is_not_mistaken_for_lower_case() -> None:
    # Telugu has no letter case: the lower-case rule ("9 a.m. on Monday") must not join these.
    assert len(texts("Exams start at 9 a.m. పరీక్షలు సోమవారం.")) == 2


def test_lists_are_one_unit_per_line_and_the_list_number_is_not_a_sentence_end() -> None:
    text = (
        "Fees for this term:\n1. Tuition fee is ₹12,500.\n"
        "2. Bus fee is ₹3,000. Pay by June.\n- Late fee applies."
    )
    found = split_sentences(text, RULES)
    assert [s.text for s in found] == [
        "Fees for this term:",
        "1. Tuition fee is ₹12,500.",
        "2. Bus fee is ₹3,000.",
        "Pay by June.",
        "- Late fee applies.",
    ]
    assert [s.kind for s in found] == ["prose", "list_item", "list_item", "prose", "list_item"]
    assert body(found[1]) == "Tuition fee is ₹12,500."


def test_tables_are_one_unit_per_row() -> None:
    text = "| Class | Fee |\n|---|---:|\n| IX | ₹12,500. Due June. |\n| X | ₹13,000 |"
    found = split_sentences(text, RULES)
    assert [s.kind for s in found] == ["table_header", "table_rule", "table_row", "table_row"]
    assert found[2].text == "| IX | ₹12,500. Due June. |"
    assert body(found[3]) == "X ₹13,000"


def factual(text: str) -> bool:
    (sentence,) = split_sentences(text, RULES)
    return is_factual(sentence, RULES)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fees are due on 15/10/2026.", True),  # a date
        ("The fee is ₹500.", True),  # an amount
        ("Fees are due on Friday.", True),  # a claim without digits is still a claim
        ("Fees are not due on Sunday.", True),  # negation is a claim too
        ("The school has no record of a late fee.", True),
        ("Yes.", True),
        ("Here is what the fee circular says:", False),  # a short lead-in
        ("Fees for 2026 are as follows:", True),  # a lead-in with a figure states a fact
        ("In summary.", False),  # a configured connective
        ("However,", False),
        ("I could not find this in the school records you can access.", False),  # not found
        ("Not found in school records you can access.", False),
        ("I could not find the fee for 2026.", True),  # a figure makes it factual
        ("**", False),  # markup only
        ("---", False),
        ("పరీక్షలు 22/09/2026న ప్రారంభమవుతాయి.", True),
        ("పరీక్షలు సోమవారం ప్రారంభమవుతాయి.", True),
    ],
)
def test_factual_rule(text: str, expected: bool) -> None:
    assert factual(text) is expected


def test_list_numbers_and_table_markup_do_not_make_a_line_factual() -> None:
    header, rule, row = split_sentences("| Class | Fee |\n|---|---|\n| IX | ₹12,500 |", RULES)
    assert not is_factual(header, RULES)
    assert not is_factual(rule, RULES)
    assert is_factual(row, RULES)
    (item,) = split_sentences("1. Here is the fee circular:", RULES)
    assert not is_factual(item, RULES)


def test_the_configured_not_found_sentence_is_not_factual() -> None:
    first: Sentence = split_sentences(load_llm_config().answer_checks.not_found.en, RULES)[0]
    assert not is_factual(first, RULES)


def test_empty_and_blank_text_has_no_sentences() -> None:
    assert split_sentences("", RULES) == ()
    assert split_sentences(" \n\t\n", RULES) == ()
