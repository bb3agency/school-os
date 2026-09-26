"""Search query parsing (FR-STU-010, US-302 AC1). Pure unit tests."""

from __future__ import annotations

import pytest

from app.core.textnorm import phonetic_key
from app.students.search import parse_query, translit_key


@pytest.mark.parametrize(
    ("query", "name", "sections", "classes", "admission"),
    [
        ("venkat sai 9b", "venkat sai", {("IX", "B")}, set(), ()),
        ("VENKAT SAI IX-B", "VENKAT SAI", {("IX", "B")}, set(), ()),
        ("10a lakshmi", "lakshmi", {("X", "A")}, set(), ()),
        ("ukg/a", "", {("UKG", "A")}, set(), ()),
        ("9 ravi", "ravi", set(), {"IX"}, ()),
        ("2019/0457", "", set(), set(), ("2019/0457",)),
        ("k. v ramana", "k. v ramana", set(), set(), ()),  # bare roman V is an initial
        ("వెంకట సాయి 9b", "వెంకట సాయి", {("IX", "B")}, set(), ()),
        ("13b", "", set(), set(), ("13B",)),  # no class 13: an admission-number fragment
    ],
)
def test_FR_STU_010_query_tokens(
    query: str, name: str, sections: set[tuple[str, str]], classes: set[str], admission: tuple[str, ...]
) -> None:
    parsed = parse_query(query)
    assert parsed.name_text == name
    assert set(parsed.sections) == sections
    assert set(parsed.class_codes) == classes
    assert parsed.admission_terms == admission


def test_FR_STU_010_telugu_query_meets_latin_name_on_phonetic_key() -> None:
    telugu = parse_query("వెంకట సాయి")
    assert telugu.name_key.isascii()
    assert telugu.name_phonetic == phonetic_key("Venkata Sai")


def test_translit_key_dedupes_spellings() -> None:
    key = translit_key(["K. Venkata Sai", "K Venkata Sai", None, "Kommineni Venkatasai"])
    assert key is not None
    assert key.count("|") == 1
