"""Configuration of the consent register (``consent_purposes.yaml``; ADR-0049).

Pure module (no database or web): loads and validates the packaged YAML once.
"""

from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from importlib import resources
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.languages import contains_telugu

FormLanguage = Literal["en", "te"]
KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

FORM_KEYS: Final = frozenset(
    {
        "title",
        "subtitle",
        "student_details",
        "name",
        "admission_no",
        "class_section",
        "voluntary",
        "withdraw",
        "agree",
        "disagree",
        "version",
        "parent_name",
        "relationship",
        "father",
        "mother",
        "guardian",
        "signature",
        "date",
        "office",
        "received_on",
        "recorded_by",
        "footer",
    }
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Suggestion(_Model):
    label_en: str = Field(min_length=2, max_length=120)
    label_te: str | None = Field(default=None, min_length=2, max_length=120)
    description: str = Field(min_length=10, max_length=1000)


class ConsentConfig(_Model):
    version: int = Field(ge=1)
    form_layout_version: str = Field(pattern=r"^[0-9A-Za-z.-]{1,32}$")
    note_max_length: int = Field(ge=1, le=500)
    earliest_decision: dt.date
    max_forms_per_print: int = Field(ge=1, le=500)
    max_active_purposes: int = Field(ge=1, le=40)
    suggestions: dict[str, Suggestion]
    form: dict[FormLanguage, dict[str, str]]

    @field_validator("suggestions")
    @classmethod
    def _keys(cls, value: dict[str, Suggestion]) -> dict[str, Suggestion]:
        bad = [k for k in value if not KEY_RE.match(k)]
        if bad:
            raise ValueError(f"suggestion keys must match {KEY_RE.pattern}: {bad}")
        return value

    @field_validator("form")
    @classmethod
    def _complete(
        cls, value: dict[FormLanguage, dict[str, str]]
    ) -> dict[FormLanguage, dict[str, str]]:
        if set(value) != {"en", "te"}:
            raise ValueError("form needs exactly the en and te texts")
        for language, texts in value.items():
            if set(texts) != FORM_KEYS:
                missing = sorted(FORM_KEYS - set(texts))
                extra = sorted(set(texts) - FORM_KEYS)
                raise ValueError(f"form.{language}: missing {missing}, unknown {extra}")
            if any(not t.strip() for t in texts.values()):
                raise ValueError(f"form.{language}: empty text")
        if not all(contains_telugu(value["te"][k]) for k in ("title", "voluntary", "disagree")):
            raise ValueError("form.te must be written in Telugu script")
        return value

    def text(self, language: FormLanguage, key: str) -> str:
        return self.form[language][key]


def parse_config(raw: object) -> ConsentConfig:
    return ConsentConfig.model_validate(raw)


@lru_cache(maxsize=1)
def load_config() -> ConsentConfig:
    raw = yaml.safe_load(
        resources.files("app.privacy").joinpath("consent_purposes.yaml").read_text("utf-8")
    )
    return parse_config(raw)


__all__ = ["FORM_KEYS", "KEY_RE", "ConsentConfig", "FormLanguage", "Suggestion", "load_config"]
