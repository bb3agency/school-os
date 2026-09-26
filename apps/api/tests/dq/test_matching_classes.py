"""Name match classes, table-driven (docs/02 §6, FR-DQ-003; docs/12 §5 "Name matching").

Synthetic AP-style names only (CLAUDE.md invariant 11). Each case states the expected class of
``classify(a, b)``; the reverse direction is checked too (the result is symmetric).
"""

from __future__ import annotations

import json

import pytest

from app.dq.matching import MatchClass, Thresholds, classify, match_key

EXACT, ORDER, SPACING = MatchClass.EXACT, MatchClass.ORDER, MatchClass.SPACING
INITIALS, VARIANT, TYPO = MatchClass.INITIALS, MatchClass.VARIANT, MatchClass.TYPO
DIFFERENT, MISSING = MatchClass.DIFFERENT, MatchClass.MISSING

CASES: list[tuple[str | None, str | None, MatchClass]] = [
    # EXACT: equal after normalisation (case, spaces, dots, hyphens) or transliteration
    ("KOMMINENI VENKATA SAI", "Kommineni  Venkata Sai", EXACT),
    ("K. VENKATA SAI", "K VENKATA SAI", EXACT),
    ("K.VENKATA SAI", "k. venkata sai", EXACT),
    ("SAI-KRISHNA", "SAI KRISHNA", EXACT),
    ("CH. VENKATA RAO", "CH VENKATA RAO", EXACT),
    ("వెంకట సాయి", "VENKATA SAI", EXACT),
    ("కొమ్మినేని వెంకట సాయి", "KOMMINENI VENKATA SAI", EXACT),
    ("వెంకట  సాయి", "వెంకట సాయి", EXACT),
    ("మోహన్", "MOHAN", EXACT),
    ("కృష్ణ", "KRISHNA", EXACT),
    ("సీత", "SEETHA", EXACT),
    # ORDER: same tokens, different order (surname first vs last)
    ("VENKATA SAI KOMMINENI", "KOMMINENI VENKATA SAI", ORDER),
    ("RAVI KUMAR REDDY", "REDDY RAVI KUMAR", ORDER),
    ("సాయి వెంకట", "VENKATA SAI", ORDER),
    ("VENKATA SAI K.", "K. VENKATA SAI", ORDER),
    # SPACING: equal after removing spaces (also combined with a different order)
    ("KOMMINENI VENKATASAI", "KOMMINENI VENKATA SAI", SPACING),
    ("VENKATASAI", "VENKATA SAI", SPACING),
    ("NAGESWARARAO", "NAGESWARA RAO", SPACING),
    ("SRILAKSHMI", "SRI LAKSHMI", SPACING),
    ("VENKATASAI KOMMINENI", "KOMMINENI VENKATA SAI", SPACING),
    ("వెంకటసాయి", "VENKATA SAI", SPACING),
    # INITIALS: an initial on one side stands for a full word on the other
    ("K. VENKATA SAI", "KOMMINENI VENKATA SAI", INITIALS),
    ("K VENKATA SAI", "KOMMINENI VENKATA SAI", INITIALS),
    ("VENKATA SAI K", "KOMMINENI VENKATA SAI", INITIALS),
    ("VENKATA SAI K.", "KOMMINENI VENKATA SAI", INITIALS),
    ("K VENKATA SAI", "VENKATA SAI KOMMINENI", INITIALS),
    ("VENKATASAI K", "KOMMINENI VENKATA SAI", INITIALS),
    ("K. V. SAI", "KOMMINENI VENKATA SAI", INITIALS),
    ("CH. VENKATA RAO", "CHOWDARY VENKATA RAO", INITIALS),
    ("కె. వెంకట సాయి", "KOMMINENI VENKATA SAI", INITIALS),
    ("K. VENKATA SAI", "కొమ్మినేని వెంకట సాయి", INITIALS),
    # VARIANT: variant dictionary or phonetic key (plus any earlier kind)
    ("SRI LAKSHMI", "SREE LAXMI", VARIANT),
    ("KOMMINENI VENKAT SAI", "KOMMINENI VENKATA SAI", VARIANT),
    ("KOMINENI VENKATA SAI", "KOMMINENI VENKATA SAI", VARIANT),
    ("SHAIK MOHAMMED", "SK MD", VARIANT),
    ("RAMAIAH", "RAMAYYA", VARIANT),
    ("GOWTHAM", "GAUTAM", VARIANT),
    ("SATYANARAYANA", "SATHYANARAYAN", VARIANT),
    ("CHOWDARY", "CHAUDHARI", VARIANT),
    ("BHAVANI", "BAVANI", VARIANT),
    ("Y. SRINIVASA RAO", "YARLAGADDA SREENIVAS RAO", VARIANT),
    ("LAXMI", "లక్ష్మి", VARIANT),
    ("LAXMI SAI", "సాయి లక్ష్మి", VARIANT),
    ("VENKAT SAI", "సాయి వెంకట", ORDER),  # across scripts VENKAT and VENKATA share a key
    # TYPO: close enough on Jaro-Winkler or trigram similarity
    ("KOMMINENI VENKATA SAI", "KOMMINENI VENKTA SAI", TYPO),
    ("SRINIVASULU", "SRINIVASLU", TYPO),
    ("CHAITANYA", "CHAITANAY", TYPO),
    ("K. VENKATA SAI", "KOMMINENI VENKATTS SAI", TYPO),
    # DIFFERENT: anything else, including gender markers and different name forms
    ("RAVI KUMAR", "RAJU KUMAR", DIFFERENT),
    ("KOMMINENI VENKATA SAI", "YARLAGADDA LAKSHMI DIVYA", DIFFERENT),
    ("RAVI KUMAR", "RAVI KUMARI", DIFFERENT),
    ("KRISHNA", "KRISHNAN", DIFFERENT),
    ("SRINIVASA RAO", "SRINIVASULU RAO", DIFFERENT),
    ("K. V. S.", "KOMMINENI VENKATA SAI", DIFFERENT),
    ("K. SAI", "V. SAI", DIFFERENT),
    ("VENKATA SAI", "KOMMINENI VENKATA SAI", DIFFERENT),
    # MISSING: no name on one or both sides
    (None, "SAI", MISSING),
    ("", "SAI", MISSING),
    ("   ", "SAI", MISSING),
    (". -", "SAI", MISSING),
    (None, None, MISSING),
    ("‌", "‌", MISSING),
]


@pytest.mark.parametrize(("a", "b", "expected"), CASES)
def test_FR_DQ_003_match_class_table(a: str | None, b: str | None, expected: MatchClass) -> None:
    forward = classify(a, b)
    backward = classify(b, a)
    assert forward.match_class is expected, (a, b, dict(forward.details))
    assert backward.match_class is expected
    assert forward.explanation_code == f"NM-{expected.value}"
    assert forward.similarity == backward.similarity
    assert 0.0 <= forward.similarity <= 1.0


@pytest.mark.parametrize(("a", "b", "expected"), CASES)
def test_details_never_contain_the_names(
    a: str | None, b: str | None, expected: MatchClass
) -> None:
    """details are safe to store with findings and to log: positions and codes only."""
    serialised = json.dumps(classify(a, b).details, ensure_ascii=False)
    for value in (a, b):
        for token in match_key(value or "").replace(".", "").split():
            if len(token) >= 3:
                assert token not in serialised.upper()
        if value and any(ch.isalpha() for ch in value) and len(value.strip()) > 2:
            assert value.strip() not in serialised


def test_exact_and_similarity_bounds() -> None:
    assert classify("SAI", "SAI").similarity == 1.0
    assert classify(None, "SAI").similarity == 0.0
    # whole-name similarity is informative only: shared surname, different given name
    result = classify("RAVI KUMAR", "RAJU KUMAR")
    assert result.similarity >= 0.9
    assert result.match_class is DIFFERENT


def test_cross_script_is_reported() -> None:
    assert classify("వెంకట సాయి", "VENKATA SAI").details["script"] == "cross"
    assert classify("VENKATA SAI", "VENKATA SAI").details["script"] == "same"


def test_missing_side_is_reported_from_the_callers_view() -> None:
    assert classify(None, "SAI").details == {"missing": "a"}
    assert classify("SAI", "").details == {"missing": "b"}
    assert classify(None, None).details == {"missing": "both"}


def test_initials_details_point_at_positions() -> None:
    result = classify("VENKATA SAI K", "KOMMINENI VENKATA SAI")
    assert result.details["reordered"] is True
    pairs = result.details["pairs"]
    assert {"a": [2], "b": [0], "kind": "initial"} in pairs
    assert {"a": [0], "b": [1], "kind": "same"} in pairs
    # the same pairs, seen from the other side
    reverse = classify("KOMMINENI VENKATA SAI", "VENKATA SAI K")
    assert {"a": [0], "b": [2], "kind": "initial"} in reverse.details["pairs"]
    assert reverse.details["tokens"] == {"a": 3, "b": 3}


def test_spacing_details_show_joined_tokens() -> None:
    result = classify("KOMMINENI VENKATASAI", "KOMMINENI VENKATA SAI")
    assert result.details["pairs"] == [
        {"a": [0], "b": [0], "kind": "same"},
        {"a": [1], "b": [1, 2], "kind": "joined"},
    ]


def test_variant_details_say_dictionary_or_phonetic() -> None:
    by_dictionary = classify("LAKSHMI", "LAXMI").details["pairs"][0]
    by_phonetic = classify("BHAVANI", "BAVANI").details["pairs"][0]
    assert by_dictionary["via"] == "dictionary"
    assert by_phonetic["via"] == "phonetic"


def test_typo_reports_which_metric_fired() -> None:
    jw = classify("SRINIVASULU", "SRINIVASLU")
    assert jw.details["typo_metric"] == "jaro_winkler"
    # a prefix mistake defeats Jaro-Winkler's prefix bonus; trigrams still see the overlap
    loose = Thresholds(jaro_winkler_min=0.99, trigram_min=0.5)
    tri = classify("VENKATESWARLU", "BENKATESWARLU", thresholds=loose)
    assert tri.match_class is TYPO
    assert tri.details["typo_metric"] == "trigram"
    both = classify("SRINIVASULU", "SRINIVASLU", thresholds=Thresholds(trigram_min=0.6))
    assert both.details["typo_metric"] == "both"


def test_thresholds_are_configurable() -> None:
    strict = Thresholds(jaro_winkler_min=0.99, trigram_min=0.99)
    assert classify("SRINIVASULU", "SRINIVASLU", thresholds=strict).match_class is DIFFERENT
    assert classify("SRINIVASULU", "SRINIVASLU").match_class is TYPO


def test_require_full_word_can_be_relaxed() -> None:
    relaxed = Thresholds(require_full_word=False)
    assert classify("K. V. S.", "KOMMINENI VENKATA SAI").match_class is DIFFERENT
    result = classify("K. V. S.", "KOMMINENI VENKATA SAI", thresholds=relaxed)
    assert result.match_class is INITIALS


def test_digraph_initials_need_a_dot_and_come_from_config() -> None:
    assert classify("CH. RAO", "CHOWDARY RAO").match_class is INITIALS
    assert classify("CH RAO", "CHOWDARY RAO").match_class is DIFFERENT  # "CH" is a word here
    no_digraphs = Thresholds(digraph_initials=())
    assert classify("CH. RAO", "CHOWDARY RAO", thresholds=no_digraphs).match_class is DIFFERENT


def test_long_names_fall_back_to_whole_string_checks() -> None:
    few = Thresholds(max_tokens=3)
    long_a = "A1 B2 C3 D4 SAI"
    assert classify(long_a, "A1 B2 C3 D4SAI", thresholds=few).match_class is SPACING
    result = classify(long_a, "A1 B2 C3 D4 SAAI", thresholds=few)
    assert result.match_class is DIFFERENT
    assert result.details["reason"] == "token_limit"


def test_match_key_shows_initials_and_transliterates() -> None:
    assert match_key("  k.venkata-sai ") == "K. VENKATA SAI"
    assert match_key("కె. వెంకట CH. Sai") == "K. VENKATA CH. SAI"
    assert match_key("కొమ్మినేని") == "KOMMINENI"
