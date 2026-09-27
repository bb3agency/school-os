"""Search query parsing (FR-STU-010, US-302). Pure functions; SQL lives in the repository.

A query such as ``"venkat sai 9b"`` is split into tokens:

- section tokens: a class number with a section letter (``9b``, ``9-B``, ``10a``) or a roman
  class code with a separator (``IX-B``, ``ix/b``, ``UKG-A``);
- class tokens: a bare class number 1-12 (``9``);
- admission-number tokens: three or more characters containing a digit (``2019/0457``);
- everything else is part of the name text (English or Telugu script), matched against the
  student's names and parent names by trigram word similarity on comparison keys
  (Telugu is transliterated with ``core.textnorm``) and on phonetic keys.

A bare roman numeral (``V``, ``X``) is treated as a name token: it is also a common initial.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final

import yaml

from app.core.textnorm import comparison_key, phonetic_key

_ROMAN: Final[tuple[str, ...]] = (
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
)  # fmt: skip
_SECTION_DIGIT_RE: Final = re.compile(r"^(1[0-2]|[1-9])[-/.]?([A-Za-z])$")
_SECTION_CODE_RE: Final = re.compile(
    r"^(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I|NUR|LKG|UKG)[-/.]([A-Z])$", re.IGNORECASE
)
_CLASS_DIGIT_RE: Final = re.compile(r"^(1[0-2]|[1-9])$")
_ADMISSION_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/._-]{2,31}$")
_SPLIT_RE: Final = re.compile(r"[\s,;]+")
MAX_QUERY_LENGTH: Final = 200
MAX_TOKENS: Final = 12


@dataclass(frozen=True, slots=True)
class SearchConfig:
    name_threshold: float
    parent_weight: float
    admission_exact_bonus: float
    max_offset: int


@lru_cache(maxsize=1)
def search_config() -> SearchConfig:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.students").joinpath("search.yaml").read_text("utf-8")
    )
    return SearchConfig(
        name_threshold=float(raw["name_threshold"]),
        parent_weight=float(raw["parent_weight"]),
        admission_exact_bonus=float(raw["admission_exact_bonus"]),
        max_offset=int(raw["max_offset"]),
    )


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    name_text: str
    class_codes: frozenset[str]
    sections: frozenset[tuple[str, str]]  # (class code, section name upper-cased)
    admission_terms: tuple[str, ...]

    @property
    def name_key(self) -> str:
        return comparison_key(self.name_text) if self.name_text else ""

    @property
    def name_phonetic(self) -> str:
        return phonetic_key(self.name_text) if self.name_text else ""

    @property
    def has_structure(self) -> bool:
        return bool(self.class_codes or self.sections)


def class_code_for_number(number: int) -> str:
    return _ROMAN[number - 1]


def parse_query(query: str) -> ParsedQuery:
    text = unicodedata.normalize("NFC", query).strip()[:MAX_QUERY_LENGTH]
    names: list[str] = []
    classes: set[str] = set()
    sections: set[tuple[str, str]] = set()
    admission: list[str] = []
    for token in [t for t in _SPLIT_RE.split(text) if t][:MAX_TOKENS]:
        if m := _SECTION_DIGIT_RE.match(token):
            sections.add((class_code_for_number(int(m.group(1))), m.group(2).upper()))
        elif m := _SECTION_CODE_RE.match(token):
            sections.add((m.group(1).upper(), m.group(2).upper()))
        elif m := _CLASS_DIGIT_RE.match(token):
            classes.add(class_code_for_number(int(m.group(1))))
        elif any(ch.isdigit() for ch in token) and _ADMISSION_RE.match(token):
            admission.append(token.upper())
        else:
            names.append(token)
    return ParsedQuery(
        name_text=" ".join(names),
        class_codes=frozenset(classes),
        sections=frozenset(sections),
        admission_terms=tuple(admission),
    )


def translit_key(names: list[str | None]) -> str | None:
    """Phonetic keys of every name spelling a student is known by (search column)."""
    keys: list[str] = []
    for name in names:
        if not name:
            continue
        key = phonetic_key(name)
        if key and key not in keys:
            keys.append(key)
    return " | ".join(keys) if keys else None
