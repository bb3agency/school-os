"""Variant dictionary: packaged groups, tenant extension, distinct pairs (FR-DQ-003).

docs/02 §6 step 4: "a configurable variant dictionary (e.g. SRI/SREE/SHRI, LAKSHMI/LAXMI,
VENKATA/VENKAT) ... The dictionary is per-tenant extensible."
"""

from __future__ import annotations

import re

import pytest

from app.core.textnorm import phonetic_key
from app.devtools.names import SPELLING_VARIANTS
from app.dq.matching import (
    MatchClass,
    VariantDictionary,
    VariantGroup,
    classify,
    load_variant_dictionary,
)


@pytest.fixture(scope="module")
def vd() -> VariantDictionary:
    return load_variant_dictionary()


def test_FR_DQ_003_at_least_60_groups_beyond_the_phonetic_key(vd: VariantDictionary) -> None:
    useful = [g for g in vd.groups if len({phonetic_key(m) for m in g.members}) > 1]
    assert len(useful) >= 60, len(useful)


def test_groups_are_well_formed(vd: VariantDictionary) -> None:
    ids = [g.id for g in vd.groups]
    assert len(ids) == len(set(ids))
    for group in vd.groups:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", group.id), group.id
        assert len(group.members) >= 2
        for member in group.members:
            assert re.fullmatch(r"[A-Z]+", member), member
    members = [m for g in vd.groups for m in g.members]
    assert len(members) == len(set(members)), "a spelling belongs to one group only"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("SRI", "SREE"),
        ("SHRI", "SHREE"),
        ("LAKSHMI", "LAXMI"),
        ("LAKSMI", "LAXMI"),
        ("VENKATA", "VENKAT"),
        ("VENKATTA", "VENKAT"),
        ("SAI", "SAAI"),
        ("NARAYANA", "NARAYAN"),
        ("REDDY", "REDDI"),
        ("NAIDU", "NAYUDU"),
        ("CHOWDARY", "CHOUDARY"),
        ("CHOUDARY", "CHAUDHARI"),
        ("SATYA", "SATHYA"),
        ("SRINIVAS", "SREENIVAS"),
        ("SRINIVASA", "SRINIVAS"),
        ("PRASAD", "PRASADH"),
        ("SHAIK", "SK"),
        ("MOHAMMED", "MD"),
        ("RAMAIAH", "RAMAYYA"),
        ("VYSHNAVI", "VAISHNAVI"),
    ],
)
def test_FR_DQ_003_common_ap_spellings_are_variants(vd: VariantDictionary, a: str, b: str) -> None:
    assert vd.are_variants(a, b)
    assert vd.are_variants(b, a)
    assert vd.representative(a) == vd.representative(b) or phonetic_key(a) == phonetic_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("KUMAR", "KUMARI"),  # gender marker
        ("KRISHNA", "KRISHNAN"),  # a different name form
        ("NARAYANA", "NARAYANAN"),
        ("SRINIVASA", "SRINIVASULU"),
        ("SAI", "SRI"),
        ("RAVI", "RAJU"),
    ],
)
def test_distinct_names_are_not_variants(vd: VariantDictionary, a: str, b: str) -> None:
    assert not vd.are_variants(a, b)
    assert not vd.are_variants(b, a)


def test_distinct_pairs_are_declared(vd: VariantDictionary) -> None:
    assert vd.is_distinct("KUMAR", "KUMARI")
    assert vd.is_distinct("KUMARI", "KUMAR")
    assert not vd.is_distinct("KUMAR", "RAVI")


def test_every_synthetic_spelling_variant_is_known(vd: VariantDictionary) -> None:
    """The spelling variants the synthetic generator produces all match as VARIANT."""
    for canonical, alternatives in SPELLING_VARIANTS.items():
        for alternative in alternatives:
            assert vd.are_variants(canonical, alternative), (canonical, alternative)


def test_tenant_extension_adds_and_joins_groups(vd: VariantDictionary) -> None:
    assert classify("LACHMI", "LAXMI").match_class is MatchClass.DIFFERENT
    extended = vd.merged([["LAKSHMI", "LACHMI"], ["PEDDIRAJU", "PEDIRAZU"]])
    assert classify("LACHMI", "LAXMI", variants=extended).match_class is MatchClass.VARIANT
    assert extended.are_variants("PEDDIRAJU", "PEDIRAZU")
    lakshmi = next(g for g in extended.groups if "LACHMI" in g.members)
    assert lakshmi.id == "lakshmi"  # joined into the packaged group, which keeps its name
    assert lakshmi.members[0] == "LAKSHMI"
    # the packaged dictionary is unchanged
    assert not vd.are_variants("LACHMI", "LAXMI")
    assert len(extended) == len(vd) + 1


def test_extension_that_joins_a_distinct_pair_is_rejected(vd: VariantDictionary) -> None:
    with pytest.raises(ValueError, match="distinct"):
        vd.merged([["KUMAR", "KUMARI"]])
    with pytest.raises(ValueError, match="distinct"):
        vd.merged([["KRISHNA", "KRISHNAN"]])  # joins the packaged Krishna group


def test_extension_can_declare_new_distinct_pairs(vd: VariantDictionary) -> None:
    assert classify("SAIRAM", "SAIRAMU").match_class is MatchClass.TYPO
    extended = vd.merged([], distinct=[["SAIRAM", "SAIRAMU"]])
    assert classify("SAIRAM", "SAIRAMU", variants=extended).match_class is MatchClass.DIFFERENT


@pytest.mark.parametrize(
    "groups",
    [
        [["LAKSHMI"]],  # one spelling
        [["LAKSHMI", "lakshmi"]],  # the same spelling twice
        [["SRI LAKSHMI", "SRILAKSHMI"]],  # not a single token
        [["", "X"]],
    ],
)
def test_malformed_groups_are_rejected(groups: list[list[str]]) -> None:
    with pytest.raises(ValueError, match=r"spellings|single name tokens"):
        VariantDictionary(groups)


def test_distinct_entries_are_pairs() -> None:
    with pytest.raises(ValueError, match="pairs"):
        VariantDictionary([], distinct=[["A1", "B1", "C1"]])


def test_groups_sharing_a_spelling_are_joined() -> None:
    vd = VariantDictionary(
        [VariantGroup("one", ("ABHI", "ABI")), VariantGroup("two", ("ABHI", "ABBI"))]
    )
    assert len(vd) == 1
    assert vd.groups[0].id == "one"
    assert vd.are_variants("ABI", "ABBI")


def test_from_mapping_matches_packaged_format() -> None:
    vd = VariantDictionary.from_mapping(
        {
            "version": 7,
            "groups": [{"id": "x", "members": ["GOWRI", "GAURI"]}],
            "distinct": [["KUMAR", "KUMARI"]],
        }
    )
    assert vd.version == "7"
    assert vd.are_variants("gowri", "Gauri")
    assert vd.representative("GAURI") == "GOWRI"
    assert vd.representative("UNLISTED") is None
    assert vd.representative("") is None
