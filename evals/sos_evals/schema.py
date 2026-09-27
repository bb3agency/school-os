"""Dataset schema for the evaluation corpus and question sets (docs/06 §13.1).

Two JSONL shapes live in `evals/datasets/`:

- `corpus.jsonl`: one `CorpusItem` per citable source (document page or record field), with the
  visibility (tenant + ACL) the real retrieval filter applies in SQL (docs/06 §6 `<ALLOWED>`).
- `<category>.jsonl`: one `EvalItem` per question, with the asker's role and scope, the sources
  the answer must come from, and whether the right behaviour is a refusal.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Locale = Literal["en", "te", "mixed"]
"""Language style of a question: English, Telugu script, or code-mixed Telugu-English."""

Category = Literal[
    "records",
    "documents",
    "mixed_lang",
    "temporal",
    "unanswerable",
    "permissions",
    "adversarial",
]
CATEGORIES: tuple[Category, ...] = (
    "records",
    "documents",
    "mixed_lang",
    "temporal",
    "unanswerable",
    "permissions",
    "adversarial",
)

Role = Literal[
    "owner",
    "principal",
    "office_admin",
    "office_staff",
    "accountant",
    "exam_coordinator",
    "class_teacher",
    "teacher",
    "auditor_readonly",
]
"""Tenant system roles (apps/api/app/authz/roles.yaml)."""

SOURCE_URI = re.compile(r"^sos://(doc|student|finding|change|verified)/[0-9a-f-]{36}(/|#|$)")
"""docs/06 §8: stable internal URIs that never contain names or values."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Asker(_Model):
    """Who asks: the tenant, the role and the section/class scope the role is limited to."""

    tenant: str = Field(min_length=1)
    role: Role
    sections: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()


class Acl(_Model):
    """Denormalised ACL keys of a chunk (docs/06 §6): any overlap with the asker grants access."""

    roles: tuple[Role, ...] = ()
    sections: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()


class CorpusItem(_Model):
    source: str
    tenant: str = Field(min_length=1)
    kind: Literal["document", "record"]
    doc_type: str
    title: str
    locale: Locale
    issued_on: date | None = None
    is_latest: bool = True
    acl: Acl
    content: str = Field(min_length=1)
    marker: str = Field(pattern=r"^MK-[0-9A-F]{6}$")
    """A unique token inside `content`; seeing it in an answer proves the content reached it."""
    injection_canaries: tuple[str, ...] = ()
    """Strings an injected instruction in `content` asks the model to output."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not SOURCE_URI.match(self.source):
            raise ValueError(f"source is not a sos:// URI: {self.source!r}")
        if self.marker not in self.content:
            raise ValueError(f"marker {self.marker} does not occur in the content of {self.source}")
        for canary in self.injection_canaries:
            if canary not in self.content:
                raise ValueError(f"canary {canary!r} does not occur in the content")
        return self


class EvalItem(_Model):
    id: str = Field(pattern=r"^[a-z_]+-[0-9]{3}$")
    category: Category
    question: str = Field(min_length=1)
    locale: Locale
    asker: Asker
    expected_sources: tuple[str, ...] = ()
    """Sources a correct answer retrieves and cites; empty when the right answer is a refusal."""
    expect_refusal: bool = False
    """True when the answer must be "not found in school records" (unanswerable or forbidden)."""
    reference_answer: str | None = None
    leakage_probe: bool = False
    """The question targets content the asker must not see (cross-section, cross-role, tenant)."""
    probe_sources: tuple[str, ...] = ()
    """For leakage probes: the sources that hold the answer and that the asker cannot see."""
    injection: bool = False
    """A document the asker can see carries an embedded instruction (prompt injection)."""
    fast: bool = False
    """Member of the fast subset `make eval` runs on pull requests."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.expect_refusal and self.expected_sources:
            raise ValueError(f"{self.id}: a refusal item has no expected sources")
        if not self.expect_refusal and not self.expected_sources:
            raise ValueError(f"{self.id}: an answerable item needs expected sources")
        if self.leakage_probe and not (self.expect_refusal and self.probe_sources):
            raise ValueError(f"{self.id}: a leakage probe expects a refusal and names its probes")
        if self.probe_sources and not self.leakage_probe:
            raise ValueError(f"{self.id}: probe_sources only belong to leakage probes")
        if not self.id.startswith(f"{self.category}-"):
            raise ValueError(f"{self.id}: id must start with the category {self.category!r}")
        return self
