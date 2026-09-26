"""Synthetic AP name generator and variants (docs/02 §6, docs/12 §3; NFR-MNT-002)."""

from __future__ import annotations

import random
import unicodedata

import pytest

from app.devtools import names
from app.devtools.names import NameVariant, PersonName, VariantClass

EXAMPLE = PersonName(surname="Kommineni", given=("Venkata", "Sai"), gender="m")


def _is_telugu(text: str) -> bool:
    letters = [c for c in text if not c.isspace()]
    return bool(letters) and all("ఀ" <= c <= "౿" for c in letters)


def _by_class(items: list[NameVariant]) -> dict[VariantClass, list[str]]:
    out: dict[VariantClass, list[str]] = {}
    for item in items:
        out.setdefault(item.variant_class, []).append(item.text)
    return out


def test_docs02_s6_canonical_is_surname_first() -> None:
    assert EXAMPLE.canonical == "Kommineni Venkata Sai"
    assert EXAMPLE.initials_form == "K. Venkata Sai"
    assert EXAMPLE.given_first == "Venkata Sai Kommineni"
    assert EXAMPLE.register_form == "KOMMINENI VENKATA SAI"
    assert EXAMPLE.telugu == "కొమ్మినేని వెంకట సాయి"


def test_docs02_s6_example_variants_cover_every_class() -> None:
    got = _by_class(names.variants(EXAMPLE))
    assert set(got) == set(VariantClass)
    assert got[VariantClass.SPACING] == ["Kommineni VenkataSai"]
    assert got[VariantClass.INITIALS] == ["K. Venkata Sai", "K Venkata Sai"]
    assert set(got[VariantClass.SPELLING]) == {
        "Komineni Venkata Sai",
        "Kommineni Venkat Sai",
        "Kommineni Venkatta Sai",
        "Kommineni Venkata Saai",
    }
    assert got[VariantClass.ORDER] == ["Venkata Sai Kommineni"]
    assert got[VariantClass.SCRIPT] == ["కొమ్మినేని వెంకట సాయి"]


def test_docs02_s6_variants_are_distinct_nfc_and_differ_from_canonical() -> None:
    rng = random.Random(11)
    for _ in range(300):
        name = names.generate_name(rng)
        items = names.variants(name)
        texts = [v.text for v in items]
        assert name.canonical not in texts
        assert len(texts) == len(set(texts))
        for text in [name.canonical, *texts]:
            assert unicodedata.normalize("NFC", text) == text
            assert text == text.strip()
            assert "  " not in text


def test_docs02_s6_generated_names_cover_every_variant_class() -> None:
    rng = random.Random(5)
    seen: set[VariantClass] = set()
    for _ in range(200):
        seen.update(v.variant_class for v in names.variants(names.generate_name(rng)))
    assert seen == set(VariantClass)


def test_docs02_s6_every_generated_name_has_structural_variants() -> None:
    """SPACING, INITIALS and ORDER always apply (two given tokens and a surname)."""
    rng = random.Random(8)
    for _ in range(200):
        classes = {v.variant_class for v in names.variants(names.generate_name(rng))}
        assert {VariantClass.SPACING, VariantClass.INITIALS, VariantClass.ORDER} <= classes


def test_docs02_s6_spelling_variants_come_from_the_dictionary() -> None:
    rng = random.Random(3)
    for _ in range(200):
        name = names.generate_name(rng)
        tokens = (name.surname, *name.given)
        for item in names.variants(name):
            if item.variant_class is not VariantClass.SPELLING:
                continue
            parts = item.text.split(" ")
            assert len(parts) == len(tokens)
            diffs = [(a, b) for a, b in zip(tokens, parts, strict=True) if a != b]
            assert len(diffs) == 1
            original, alt = diffs[0]
            assert alt in names.SPELLING_VARIANTS[original]


def test_docs02_s6_script_variant_is_telugu_and_telugu_only_names_always_have_one() -> None:
    rng = random.Random(21)
    for _ in range(200):
        name = names.generate_name(rng, telugu_only=True)
        assert name.telugu is not None
        assert _is_telugu(name.telugu)
        scripts = [v for v in names.variants(name) if v.variant_class is VariantClass.SCRIPT]
        assert [v.text for v in scripts] == [name.telugu]


def test_docs02_s6_names_without_telugu_forms_have_no_script_variant() -> None:
    name = PersonName(surname="Bollineni", given=("Venkata", "Sai"), gender="m")
    assert name.telugu is None
    assert VariantClass.SCRIPT not in {v.variant_class for v in names.variants(name)}


def test_docs12_s3_generation_is_deterministic_per_seed() -> None:
    a = [names.generate_with_variants(random.Random(42)) for _ in range(3)]
    b = [names.generate_with_variants(random.Random(42)) for _ in range(3)]
    assert a == b
    many_a = [names.generate_name(random.Random(s)).canonical for s in range(50)]
    many_b = [names.generate_name(random.Random(s)).canonical for s in range(50)]
    assert many_a == many_b
    assert len(set(many_a)) > 25  # enough spread for realistic rosters


def test_docs02_s6_gender_and_token_rules() -> None:
    rng = random.Random(9)
    for gender in ("f", "m"):
        for _ in range(100):
            name = names.generate_name(rng, gender=gender)
            assert name.gender == gender
            assert name.surname in names.SURNAMES
            assert name.given[0] in names.GIVEN_FIRST[gender]
            assert name.given[1] in names.GIVEN_SECOND[gender]
            assert name.given[0] != name.given[1]


def test_word_lists_have_telugu_for_a_subset_and_valid_spelling_keys() -> None:
    every_token = set(names.SURNAMES)
    for lists in (names.GIVEN_FIRST, names.GIVEN_SECOND):
        for tokens in lists.values():
            every_token.update(tokens)
    assert set(names.SPELLING_VARIANTS) <= every_token
    assert set(names.TELUGU) <= every_token
    assert set(names.TELUGU) < every_token  # a subset only: SCRIPT variants are partial
    for alts in names.SPELLING_VARIANTS.values():
        assert alts
    for value in names.TELUGU.values():
        assert _is_telugu(value)


def test_person_name_requires_parts() -> None:
    with pytest.raises(ValueError, match="surname"):
        PersonName(surname="", given=("Sai",), gender="m")
    with pytest.raises(ValueError, match="surname"):
        PersonName(surname="Kotha", given=(), gender="f")
