"""Property tests for name matching (docs/12 §5: order invariance, idempotent normalisation).

FR-DQ-003. Strategies build AP-style names from the synthetic word lists in
``app.devtools.names`` (plus arbitrary text for the "never raises" properties).
"""

from __future__ import annotations

import random

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.devtools.names import GIVEN_FIRST, GIVEN_SECOND, SURNAMES, TELUGU
from app.dq.matching import MatchClass, classify, match_key

WORDS = sorted({*SURNAMES, *GIVEN_FIRST["m"], *GIVEN_FIRST["f"], *GIVEN_SECOND["m"]})
TELUGU_WORDS = sorted(TELUGU)

word = st.sampled_from(WORDS)
initial = st.sampled_from("ABCDGKLMNPRSTVY").map(lambda c: c + ".")
token = st.one_of(word, word, word, initial)
name_tokens = st.lists(token, min_size=1, max_size=5)
names = name_tokens.map(" ".join)
telugu_names = st.lists(st.sampled_from(TELUGU_WORDS), min_size=1, max_size=4)

# Arbitrary text: Latin, Telugu block, punctuation, spaces, zero-width and odd code points.
wild = st.text(
    alphabet=st.one_of(
        st.characters(min_codepoint=0x0C00, max_codepoint=0x0C7F),
        st.sampled_from(list("abcKLM .-_,/‌‍\t")),
        st.characters(),
    ),
    max_size=40,
)

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@SETTINGS
@given(names)
def test_FR_DQ_003_a_name_is_exact_to_itself(a: str) -> None:
    assert classify(a, a).match_class is MatchClass.EXACT


@SETTINGS
@given(names, names)
def test_FR_DQ_003_classification_is_symmetric(a: str, b: str) -> None:
    forward, backward = classify(a, b), classify(b, a)
    assert forward.match_class is backward.match_class
    assert forward.similarity == backward.similarity


@SETTINGS
@given(name_tokens, st.randoms(use_true_random=False))
def test_FR_DQ_003_permutations_are_order_or_exact(tokens: list[str], rnd: random.Random) -> None:
    shuffled = list(tokens)
    rnd.shuffle(shuffled)
    result = classify(" ".join(tokens), " ".join(shuffled))
    expected = MatchClass.EXACT if shuffled == tokens else MatchClass.ORDER
    if shuffled != tokens and match_key(" ".join(shuffled)) == match_key(" ".join(tokens)):
        expected = MatchClass.EXACT  # e.g. two equal tokens swapped
    assert result.match_class is expected


@SETTINGS
@given(telugu_names, st.randoms(use_true_random=False))
def test_telugu_script_matches_its_latin_form_in_any_order(
    tokens: list[str], rnd: random.Random
) -> None:
    latin = " ".join(tokens)
    shuffled = list(tokens)
    rnd.shuffle(shuffled)
    telugu = " ".join(TELUGU[t] for t in shuffled)
    result = classify(latin, telugu)
    assert result.match_class in (MatchClass.EXACT, MatchClass.ORDER)
    assert result.details["script"] == "cross"


@SETTINGS
@given(wild)
def test_normalisation_is_idempotent(text: str) -> None:
    once = match_key(text)
    assert match_key(once) == once


@SETTINGS
@given(wild, wild)
def test_FR_DQ_003_never_raises_and_stays_in_range(a: str, b: str) -> None:
    result = classify(a, b)
    assert result.match_class in MatchClass
    assert 0.0 <= result.similarity <= 1.0
    assert result.explanation_code == f"NM-{result.match_class.value}"
    assert classify(b, a).match_class is result.match_class


@SETTINGS
@given(wild)
def test_empty_or_self(text: str) -> None:
    expected = MatchClass.MISSING if not match_key(text) else MatchClass.EXACT
    assert classify(text, text).match_class is expected
    assert classify(text, None).match_class is MatchClass.MISSING
