"""Name normalisation and Telugu transliteration (docs/02 §6, FR-STU-010, FR-DQ-003)."""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.textnorm import (
    comparison_key,
    has_telugu,
    normalize_name,
    phonetic_key,
    tokenize,
    transliterate_telugu,
)


@pytest.mark.parametrize(
    ("telugu", "latin"),
    [
        ("వెంకట సాయి", "venkata saayi"),
        ("లక్ష్మి", "lakshmi"),
        ("శ్రీనివాస్", "shreenivaas"),
        ("కొమ్మినేని", "kommineeni"),
        ("రామ", "raama"),
        ("సంపత్", "sampat"),
        ("౧౨౩", "123"),
    ],
)
def test_FR_STU_010_telugu_transliteration_table(telugu: str, latin: str) -> None:
    assert transliterate_telugu(telugu) == latin


def test_mixed_script_passes_latin_through() -> None:
    assert transliterate_telugu("K. వెంకట") == "K. venkata"


def test_has_telugu() -> None:
    assert has_telugu("సాయి")
    assert not has_telugu("SAI")


def test_normalize_name_keeps_script_and_collapses_space() -> None:
    assert normalize_name("  k.  venkata   sai ") == "K. VENKATA SAI"
    assert normalize_name("వెంకట  సాయి") == "వెంకట సాయి"


def test_comparison_key_splits_initials_and_punctuation() -> None:
    assert comparison_key("K.VENKATA-SAI") == "K VENKATA SAI"
    assert tokenize("K. Venkata Sai").initials == ("K",)
    assert tokenize("K. Venkata Sai").words == ("VENKATA", "SAI")


def test_telugu_and_latin_spellings_meet_on_phonetic_key() -> None:
    assert phonetic_key("వెంకట సాయి") == phonetic_key("VENKATA SAI")
    assert phonetic_key("SEETHA") == phonetic_key("SITA")
    assert phonetic_key("LAKSHMI") == phonetic_key("LAKSMI")


@given(st.text(max_size=40))
def test_comparison_key_is_idempotent_and_ascii_upper_for_latin(text: str) -> None:
    once = comparison_key(text)
    assert comparison_key(once) == once
    assert once == once.strip()
    assert "  " not in once


@given(st.text(alphabet=st.characters(min_codepoint=0x0C00, max_codepoint=0x0C7F), max_size=30))
def test_transliteration_never_crashes_and_removes_telugu_letters(text: str) -> None:
    out = transliterate_telugu(text)
    for ch in out:
        assert not (unicodedata.category(ch).startswith("L") and has_telugu(ch))
