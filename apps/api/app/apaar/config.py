"""APAAR consent register configuration (``config.yaml`` next to this module; ADR-0039).

Pure module (no database or web): loads and validates the packaged YAML once. The parent form's
texts exist in English and Telugu; which one a school prints is the school's setting (owner
decision D9), independent of ``SOS_TELUGU_ENABLED`` (the staff UI stays English).
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from importlib import resources
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.core.languages import contains_telugu

FormLanguage = Literal["en", "te"]

FORM_KEYS: Final = frozenset(
    {
        "title",
        "subtitle",
        "student_details",
        "name",
        "admission_no",
        "class_section",
        "dob",
        "pen",
        "what",
        "voluntary",
        "board",
        "data",
        "withdraw",
        "choice",
        "give",
        "refuse",
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


class LegalBasis(_Model):
    refusal: str = Field(min_length=20, max_length=1000)
    source_urls: tuple[HttpUrl, ...] = Field(min_length=1)
    secondary_source: bool
    verified: bool


class ApaarConfig(_Model):
    version: int = Field(ge=1)
    form_version: str = Field(pattern=r"^[0-9A-Za-z.-]{1,32}$")
    legal_basis: LegalBasis
    note_max_length: int = Field(ge=1, le=1000)
    earliest_decision: dt.date
    max_forms_per_print: int = Field(ge=1, le=500)
    form: dict[FormLanguage, dict[str, str]]

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
        if not all(contains_telugu(value["te"][k]) for k in ("title", "voluntary", "refuse")):
            raise ValueError("form.te must be written in Telugu script")
        return value

    def text(self, language: FormLanguage, key: str) -> str:
        return self.form[language][key].replace("{form_version}", self.form_version)


def parse_config(raw: object) -> ApaarConfig:
    return ApaarConfig.model_validate(raw)


@lru_cache(maxsize=1)
def load_config() -> ApaarConfig:
    raw = yaml.safe_load(resources.files("app.apaar").joinpath("config.yaml").read_text("utf-8"))
    return parse_config(raw)


__all__ = ["FORM_KEYS", "ApaarConfig", "FormLanguage", "LegalBasis", "load_config", "parse_config"]
