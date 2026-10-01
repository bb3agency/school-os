"""Bilingual explanation texts and correction routes (docs/02 §5-6, FR-DQ-001, FR-DQ-006).

``config/explanations.yaml`` holds three sections of codes, each with an English and a Telugu
sentence for office staff:

- ``match_classes``: ``NM-EXACT`` ... ``NM-MISSING`` (one per name-match class),
- ``routes``: the suggested correction routes (``ROUTE-SCHOOL-CR``, ``ROUTE-UIDAI``,
  ``ROUTE-UDISE``, ``ROUTE-BOARD``, ``ROUTE-APAAR``),
- ``rules``: the explanation template of each DQ rule (``DQ-001`` ...) and of rule variants
  for a second situation of the same rule (``DQ-005-UNVERIFIED``).

Templates may contain ``{placeholders}``; English and Telugu of one code must use the same set.
Callers fill them with labels or codes (a profile name, a field label, a class name), never with
raw personal data: findings show masked values next to the sentence. Pure module, no I/O beyond
reading the packaged YAML once.
"""

from __future__ import annotations

import functools
import re
import string
from enum import StrEnum
from importlib import resources
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.core.textnorm import has_telugu


class Language(StrEnum):
    EN = "en"
    TE = "te"


ROUTE_CODES: Final[tuple[str, ...]] = (
    "ROUTE-SCHOOL-CR",
    "ROUTE-UIDAI",
    "ROUTE-UDISE",
    "ROUTE-BOARD",
    "ROUTE-APAAR",
)

_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_MATCH_CODE: Final = re.compile(r"^NM-[A-Z]+$")
_ROUTE_CODE: Final = re.compile(r"^ROUTE-[A-Z]+(?:-[A-Z]+)*$")
# A rule's own code (DQ-005) or a variant of it for a second situation (DQ-005-UNVERIFIED).
_RULE_CODE: Final = re.compile(r"^DQ-\d{3}(?:-[A-Z]+)?$")


class UnknownExplanation(LookupError):
    """No text is configured for this code."""


class TemplateParamsError(ValueError):
    """Render parameters do not match the template's placeholders."""


def placeholders_of(template: str) -> frozenset[str]:
    """Names of the ``{placeholders}`` in ``template``; rejects anything but plain names."""
    names: set[str] = set()
    for _literal, name, spec, conversion in string.Formatter().parse(template):
        if name is None:
            continue
        if not _IDENTIFIER.match(name) or spec or conversion:
            raise ValueError(f"placeholder {{{name}}} must be a plain lowercase name")
        names.add(name)
    return frozenset(names)


class ExplanationText(BaseModel):
    """One code's English and Telugu sentence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    en: str
    te: str

    @field_validator("en", "te")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explanation text must not be empty")
        return value

    @model_validator(mode="after")
    def _bilingual(self) -> ExplanationText:
        if not has_telugu(self.te):
            raise ValueError("the Telugu text must be written in Telugu script")
        if placeholders_of(self.en) != placeholders_of(self.te):
            raise ValueError("English and Telugu texts must use the same placeholders")
        return self

    @property
    def placeholders(self) -> frozenset[str]:
        return placeholders_of(self.en)

    def text(self, language: Language) -> str:
        return self.en if language is Language.EN else self.te


def _check_codes(section: dict[str, ExplanationText], pattern: re.Pattern[str]) -> None:
    for code in section:
        if not pattern.match(code):
            raise ValueError(f"malformed explanation code {code!r}")


class ExplanationCatalog(BaseModel):
    """All explanation texts; look codes up with :meth:`get` / :meth:`render`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    match_classes: dict[str, ExplanationText]
    routes: dict[str, ExplanationText]
    rules: dict[str, ExplanationText]

    @model_validator(mode="after")
    def _codes(self) -> ExplanationCatalog:
        _check_codes(self.match_classes, _MATCH_CODE)
        _check_codes(self.routes, _ROUTE_CODE)
        _check_codes(self.rules, _RULE_CODE)
        missing = [code for code in ROUTE_CODES if code not in self.routes]
        if missing:
            raise ValueError(f"missing correction routes: {missing}")
        for code in self.match_classes:
            if self.match_classes[code].placeholders:
                raise ValueError(f"{code}: match-class texts take no placeholders")
        return self

    def has(self, code: str) -> bool:
        return code in self.match_classes or code in self.routes or code in self.rules

    def get(self, code: str) -> ExplanationText:
        for section in (self.match_classes, self.routes, self.rules):
            if code in section:
                return section[code]
        raise UnknownExplanation(code)

    def render(self, code: str, language: Language | str, /, **params: str | int) -> str:
        """The sentence for ``code`` in ``language`` with every placeholder filled.

        Parameters must match the placeholders exactly (none missing, none extra), so a
        template change cannot silently drop or leak a value.
        """
        entry = self.get(code)
        expected = entry.placeholders
        given = frozenset(params)
        if given != expected:
            raise TemplateParamsError(
                f"{code}: expected parameters {sorted(expected)}, got {sorted(given)}"
            )
        template = entry.text(Language(language))
        return template.format_map({k: str(v) for k, v in params.items()})

    def bilingual(self, code: str, /, **params: str | int) -> dict[Language, str]:
        return {lang: self.render(code, lang, **params) for lang in Language}


def parse_catalog(raw: Any) -> ExplanationCatalog:
    return ExplanationCatalog.model_validate(raw)


@functools.cache
def load_explanations() -> ExplanationCatalog:
    """The packaged catalog (``app/dq/config/explanations.yaml``), validated once."""
    text = resources.files("app.dq").joinpath("config/explanations.yaml").read_text("utf-8")
    return parse_catalog(yaml.safe_load(text))
