"""Privacy configuration files (R3-1A; ADR-0048 decisions 1, 4 and 6; FR-DPDP-001, FR-DPDP-008,
FR-DPDP-017).

Pure module (no database or web): loads and validates the packaged YAML once.

- ``record_classes.yaml``: every class of record SchoolOS keeps for a school, with owner module,
  tables, classification, retention (as docs/05 §13), basis and erasure rule.
- ``subjects.yaml``: the subject-report and erasure registry (contributors, ``no_personal_data``).
- ``templates/*.yaml``: the app copies of the school-facing notice and letter templates (same
  text and version as ``docs/legal/``).
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.subjects import SubjectKind

Erasure = Literal[
    "allowed",
    "allowed_after_retention",
    "refused_legal_obligation",
    "refused_official_record",
    "no_personal_data",
]
Classification = Literal["C0", "C1", "C2", "C3"]
TemplateKey = Literal["notice_parents", "notice_staff"]
LetterKey = Literal[
    "acknowledgement",
    "access",
    "correction",
    "erasure",
    "nomination",
    "consent_withdrawn",
    "grievance",
]
TEMPLATE_KEYS: Final[tuple[TemplateKey, ...]] = ("notice_parents", "notice_staff")
LETTER_KEYS: Final[tuple[LetterKey, ...]] = (
    "acknowledgement",
    "access",
    "correction",
    "erasure",
    "nomination",
    "consent_withdrawn",
    "grievance",
)
_KEY: Final = r"^[a-z][a-z0-9_]{1,40}$"
_TABLE: Final = re.compile(r"^[a-z]+\.[a-z_]+$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecordClass(_Model):
    label: str = Field(min_length=3, max_length=80)
    owner: str = Field(pattern=_KEY)
    tables: tuple[str, ...] = Field(min_length=1)
    classification: Classification
    docs_row: str = Field(min_length=3)
    retention: str = Field(min_length=3)
    basis: str = Field(min_length=10, max_length=300)
    erasure: Erasure
    source: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def _tables(self) -> RecordClass:
        for table in self.tables:
            if not _TABLE.match(table):
                raise ValueError(f"bad table name {table!r}")
        return self


class RecordClasses(_Model):
    version: int = Field(ge=1)
    classes: dict[str, RecordClass]

    @model_validator(mode="after")
    def _unique_tables(self) -> RecordClasses:
        seen: dict[str, str] = {}
        for key, cls in self.classes.items():
            if not re.match(_KEY, key):
                raise ValueError(f"bad class key {key!r}")
            for table in cls.tables:
                if table in seen:
                    raise ValueError(f"{table} is in {seen[table]} and {key}")
                seen[table] = key
        return self

    def of_owner(self, owner: str) -> dict[str, RecordClass]:
        return {k: c for k, c in self.classes.items() if c.owner == owner}


class Contributor(_Model):
    subjects: tuple[SubjectKind, ...] = Field(min_length=1)
    classes: tuple[str, ...] = Field(min_length=1)
    eraser: bool = False


class SubjectsRegistry(_Model):
    version: int = Field(ge=1)
    contributors: dict[str, Contributor]
    no_personal_data: dict[str, str]

    @model_validator(mode="after")
    def _disjoint(self) -> SubjectsRegistry:
        both = set(self.contributors) & set(self.no_personal_data)
        if both:
            raise ValueError(f"listed twice: {sorted(both)}")
        for module, reason in self.no_personal_data.items():
            if len(reason.strip()) < 20:
                raise ValueError(f"{module}: give a reason")
        return self


class NoticeTemplate(_Model):
    key: TemplateKey
    template_version: str = Field(pattern=r"^[0-9]{1,3}\.[0-9]{1,3}$")
    audience: Literal["parents", "staff"]
    source: str
    placeholders: tuple[str, ...]
    body_markdown: str = Field(min_length=100)


class Letter(_Model):
    title: str
    body: str = Field(min_length=20)


class LetterTemplates(_Model):
    template_version: str = Field(pattern=r"^[0-9]{1,3}\.[0-9]{1,3}$")
    source: str
    placeholders: tuple[str, ...]
    letters: dict[LetterKey, Letter]


def _read(name: str) -> object:
    return yaml.safe_load(resources.files("app.privacy").joinpath(name).read_text("utf-8"))


@lru_cache(maxsize=1)
def record_classes() -> RecordClasses:
    out = RecordClasses.model_validate(_read("record_classes.yaml"))
    registry = subjects_registry()
    for module, entry in registry.contributors.items():
        for key in entry.classes:
            cls = out.classes.get(key)
            if cls is None or cls.owner != module:
                raise ValueError(f"subjects.yaml: {module} reports class {key} it does not own")
            if cls.erasure in ("allowed",) and not entry.eraser and cls.owner == module:
                raise ValueError(f"class {key} allows erasure but {module} has no eraser")
    return out


@lru_cache(maxsize=1)
def subjects_registry() -> SubjectsRegistry:
    return SubjectsRegistry.model_validate(_read("subjects.yaml"))


@lru_cache(maxsize=2)
def notice_template(key: TemplateKey) -> NoticeTemplate:
    out = NoticeTemplate.model_validate(_read(f"templates/{key}.yaml"))
    if out.key != key:
        raise ValueError(f"templates/{key}.yaml has key {out.key}")
    return out


@lru_cache(maxsize=1)
def letter_templates() -> LetterTemplates:
    out = LetterTemplates.model_validate(_read("templates/letters.yaml"))
    if set(out.letters) != set(LETTER_KEYS):
        raise ValueError("templates/letters.yaml must have every letter")
    return out


__all__ = [
    "LETTER_KEYS",
    "TEMPLATE_KEYS",
    "Contributor",
    "Erasure",
    "LetterKey",
    "LetterTemplates",
    "NoticeTemplate",
    "RecordClass",
    "RecordClasses",
    "SubjectsRegistry",
    "TemplateKey",
    "letter_templates",
    "notice_template",
    "record_classes",
    "subjects_registry",
]
