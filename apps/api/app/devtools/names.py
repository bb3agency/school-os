"""Synthetic Andhra Pradesh names and their register-style variants (docs/02 §6, docs/12 §3).

SYNTHETIC ONLY (CLAUDE.md invariant 11). The word lists below are common Telugu given names and
house names (surnames), combined at random. They are not taken from any record, and no
combination refers to a real person.

How AP names look (docs/02 §6): the surname/house name usually comes first ("Kommineni Venkata
Sai"), registers often reduce it to an initial ("K. Venkata Sai"), multi-part given names are
written with or without a space ("VenkataSai"), and the same name is spelt several ways
("Venkata"/"Venkat"/"Venkatta", "Sri"/"Sree"/"Shri", "Lakshmi"/"Laxmi").

Public API (the M1 data-quality rules and their tests reuse it; keep it stable):

- :class:`PersonName` - surname + given tokens; ``canonical`` is surname-first, title case.
- :func:`generate_name` - a random :class:`PersonName` from a ``random.Random``.
- :func:`variants` - every variant of a name, each tagged with a :class:`VariantClass`.
- :func:`generate_with_variants` - ``(canonical, variants)`` in one call.
- :data:`SPELLING_VARIANTS` - the token spelling dictionary (canonical -> alternatives).

Variant classes and the match class each one should produce in the name matcher
(docs/02 §6 "Match classes"):

=============  ===========================================  ==========================
VariantClass   Example (canonical "Kommineni Venkata Sai")   Expected matcher class
=============  ===========================================  ==========================
SPACING        "Kommineni VenkataSai"                        SPACING
INITIALS       "K. Venkata Sai", "K Venkata Sai"             INITIALS
SPELLING       "Kommineni Venkat Sai", "Komineni Venkata     VARIANT
               Sai"
ORDER          "Venkata Sai Kommineni"                       ORDER
SCRIPT         "కొమ్మినేని వెంకట సాయి"                          EXACT after transliteration
                                                             (step 3 of the pipeline)
=============  ===========================================  ==========================

Every variant is NFC-normalised and differs from the canonical form. Word lists are part of the
dataset version (``v1``): change them only together with a new dataset version so that test
expectations tied to ``v1`` stay stable.
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

Gender = Literal["f", "m"]
GENDERS: Final[tuple[Gender, Gender]] = ("f", "m")


class VariantClass(StrEnum):
    """How a variant differs from the canonical (surname-first) form."""

    SPACING = "SPACING"
    INITIALS = "INITIALS"
    SPELLING = "SPELLING"
    ORDER = "ORDER"
    SCRIPT = "SCRIPT"


@dataclass(frozen=True, slots=True)
class NameVariant:
    text: str
    variant_class: VariantClass


# --- word lists (dataset v1) -----------------------------------------------------------------

SURNAMES: Final[tuple[str, ...]] = (
    "Addepalli",
    "Bhimavarapu",
    "Bollineni",
    "Chintalapudi",
    "Duvvuri",
    "Garikapati",
    "Gorantla",
    "Gudivada",
    "Jonnalagadda",
    "Kanumuri",
    "Kommineni",
    "Kotha",
    "Madala",
    "Mandava",
    "Pinnamaneni",
    "Pothula",
    "Ravuri",
    "Tummala",
    "Vemuri",
    "Yarlagadda",
)

# First given token (often a compound prefix) and second given token, per gender.
GIVEN_FIRST: Final[dict[Gender, tuple[str, ...]]] = {
    "m": (
        "Venkata",
        "Sai",
        "Srinivasa",
        "Rama",
        "Satya",
        "Naga",
        "Surya",
        "Siva",
        "Hari",
        "Lakshmana",
    ),
    "f": (
        "Sri",
        "Lakshmi",
        "Naga",
        "Sai",
        "Venkata",
        "Durga",
        "Satya",
        "Padma",
        "Sita",
        "Rama",
    ),
}
GIVEN_SECOND: Final[dict[Gender, tuple[str, ...]]] = {
    "m": (
        "Sai",
        "Krishna",
        "Ravi",
        "Kumar",
        "Prasad",
        "Mohan",
        "Chandra",
        "Teja",
        "Chaitanya",
        "Rao",
    ),
    "f": (
        "Lakshmi",
        "Divya",
        "Kavya",
        "Priya",
        "Ramya",
        "Swathi",
        "Bhavani",
        "Anusha",
        "Vani",
        "Jyothi",
    ),
}

# Spelling alternatives seen in registers and certificates (docs/02 §6 step 4 examples).
SPELLING_VARIANTS: Final[dict[str, tuple[str, ...]]] = {
    "Venkata": ("Venkat", "Venkatta"),
    "Sai": ("Saai",),
    "Sri": ("Sree", "Shri"),
    "Lakshmi": ("Laxmi", "Lakshmy"),
    "Srinivasa": ("Sreenivasa", "Srinivas"),
    "Satya": ("Sathya",),
    "Siva": ("Shiva",),
    "Lakshmana": ("Laxmana",),
    "Krishna": ("Krishnaa",),
    "Prasad": ("Prasadh",),
    "Swathi": ("Swati",),
    "Jyothi": ("Jyoti",),
    "Surya": ("Suriya",),
    "Kommineni": ("Komineni",),
    "Yarlagadda": ("Yaralagadda",),
    "Pothula": ("Potula",),
    "Tummala": ("Thummala",),
    "Vemuri": ("Vemoori",),
    "Gorantla": ("Gorantala",),
    "Madala": ("Maadala",),
}

# Telugu script for a subset of tokens; names made only of these get a SCRIPT variant.
TELUGU: Final[dict[str, str]] = {
    "Venkata": "వెంకట",
    "Sai": "సాయి",
    "Srinivasa": "శ్రీనివాస",
    "Rama": "రామ",
    "Krishna": "కృష్ణ",
    "Satya": "సత్య",
    "Naga": "నాగ",
    "Surya": "సూర్య",
    "Ravi": "రవి",
    "Siva": "శివ",
    "Hari": "హరి",
    "Kumar": "కుమార్",
    "Prasad": "ప్రసాద్",
    "Mohan": "మోహన్",
    "Chandra": "చంద్ర",
    "Teja": "తేజ",
    "Sri": "శ్రీ",
    "Lakshmi": "లక్ష్మి",
    "Durga": "దుర్గ",
    "Padma": "పద్మ",
    "Sita": "సీత",
    "Divya": "దివ్య",
    "Kavya": "కావ్య",
    "Priya": "ప్రియ",
    "Ramya": "రమ్య",
    "Swathi": "స్వాతి",
    "Bhavani": "భవాని",
    "Anusha": "అనూష",
    "Vani": "వాణి",
    "Kommineni": "కొమ్మినేని",
    "Yarlagadda": "యార్లగడ్డ",
    "Vemuri": "వేమూరి",
    "Tummala": "తుమ్మల",
    "Gorantla": "గోరంట్ల",
    "Pothula": "పోతుల",
    "Mandava": "మండవ",
    "Kotha": "కొత్త",
    "Ravuri": "రావూరి",
    "Addepalli": "అద్దేపల్లి",
    "Garikapati": "గరికపాటి",
    "Jonnalagadda": "జొన్నలగడ్డ",
    "Madala": "మాదల",
    "Chintalapudi": "చింతలపూడి",
}


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


@dataclass(frozen=True, slots=True)
class PersonName:
    """A synthetic AP name: ``surname`` (house name) and one or more ``given`` tokens."""

    surname: str
    given: tuple[str, ...]
    gender: Gender

    def __post_init__(self) -> None:
        if not self.surname or not self.given or any(not t for t in self.given):
            raise ValueError("a name needs a surname and at least one given token")

    @property
    def canonical(self) -> str:
        """Surname-first, title case, single spaces: ``"Kommineni Venkata Sai"``."""
        return _nfc(" ".join((self.surname, *self.given)))

    @property
    def given_name(self) -> str:
        return _nfc(" ".join(self.given))

    @property
    def initials_form(self) -> str:
        """Surname as an initial with a dot: ``"K. Venkata Sai"``."""
        return _nfc(f"{self.surname[0]}. {self.given_name}")

    @property
    def given_first(self) -> str:
        """Given names first, surname last: ``"Venkata Sai Kommineni"``."""
        return _nfc(f"{self.given_name} {self.surname}")

    @property
    def telugu(self) -> str | None:
        """Surname-first in Telugu script, or ``None`` if a token has no Telugu form here."""
        tokens = (self.surname, *self.given)
        if not all(t in TELUGU for t in tokens):
            return None
        return _nfc(" ".join(TELUGU[t] for t in tokens))

    @property
    def register_form(self) -> str:
        """Upper-case surname-first, as typed in many admission registers."""
        return self.canonical.upper()


def generate_name(
    rng: random.Random, *, gender: Gender | None = None, telugu_only: bool = False
) -> PersonName:
    """A random name with a surname and two given tokens.

    ``telugu_only`` restricts tokens to those with a Telugu form, so :attr:`PersonName.telugu`
    is never ``None``. The result depends only on ``rng``'s state (deterministic per seed).
    """
    g: Gender = gender if gender is not None else rng.choice(GENDERS)

    def pick(options: tuple[str, ...], exclude: str | None = None) -> str:
        pool = [o for o in options if o != exclude and (not telugu_only or o in TELUGU)]
        return rng.choice(pool)

    surname = pick(SURNAMES)
    first = pick(GIVEN_FIRST[g])
    second = pick(GIVEN_SECOND[g], exclude=first)
    return PersonName(surname=surname, given=(first, second), gender=g)


def _dedupe(canonical: str, items: list[NameVariant]) -> list[NameVariant]:
    seen = {canonical}
    out: list[NameVariant] = []
    for item in items:
        if item.text not in seen:
            seen.add(item.text)
            out.append(item)
    return out


def variants(name: PersonName) -> list[NameVariant]:
    """All variants of ``name`` in a stable order: SPACING, INITIALS, SPELLING, ORDER, SCRIPT.

    - SPACING: adjacent given tokens joined (needs two or more given tokens).
    - INITIALS: surname reduced to an initial, with and without the dot.
    - SPELLING: one token replaced by each alternative in :data:`SPELLING_VARIANTS`.
    - ORDER: given names first, surname last.
    - SCRIPT: the name in Telugu script, when every token has a Telugu form.

    A class is absent only when it cannot apply (e.g. no spelling alternatives for any token).
    """
    out: list[NameVariant] = []
    given = name.given
    for i in range(len(given) - 1):
        joined = (*given[:i], given[i] + given[i + 1], *given[i + 2 :])
        out.append(NameVariant(_nfc(" ".join((name.surname, *joined))), VariantClass.SPACING))
    out.append(NameVariant(name.initials_form, VariantClass.INITIALS))
    out.append(NameVariant(_nfc(f"{name.surname[0]} {name.given_name}"), VariantClass.INITIALS))
    tokens = (name.surname, *given)
    for i, token in enumerate(tokens):
        for alt in SPELLING_VARIANTS.get(token, ()):
            spelt = (*tokens[:i], alt, *tokens[i + 1 :])
            out.append(NameVariant(_nfc(" ".join(spelt)), VariantClass.SPELLING))
    out.append(NameVariant(name.given_first, VariantClass.ORDER))
    telugu = name.telugu
    if telugu is not None:
        out.append(NameVariant(telugu, VariantClass.SCRIPT))
    return _dedupe(name.canonical, out)


def generate_with_variants(
    rng: random.Random, *, gender: Gender | None = None, telugu_only: bool = False
) -> tuple[str, list[NameVariant]]:
    """``(canonical, variants)`` for a newly generated name (see :func:`variants`)."""
    name = generate_name(rng, gender=gender, telugu_only=telugu_only)
    return name.canonical, variants(name)
