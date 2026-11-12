"""Inspection readiness pack definitions (``packs_catalogue.yaml``; FC-07, FO-12;
FR-COMP-024, FR-COMP-025).

Pure module (no database or web): the packs per authority with their items, each mapped to a
source SchoolOS holds today (a certificate kind of ``catalogue.yaml``, a register print view,
the CBSE Appendix IX page, the parents' privacy notice, the DPDP readiness checklist) or marked
``not_tracked`` (evidence a later feature adds: FC-04, FC-05, FC-09). ``verified: false``: the
lists are a SchoolOS proposal from secondary sources. A bad file stops the API at start-up.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from importlib import resources
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.compliance import config as certificates

KEY: Final = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
FEATURE: Final = re.compile(r"^F[A-Z]-\d{2}$")

Source = Literal[
    "certificate", "register", "disclosure", "privacy_notice", "dpdp_readiness", "not_tracked"
]
RegisterKey = Literal["admission_withdrawal", "transfer_certificates", "certificates"]
SOURCES: Final[tuple[str, ...]] = (
    "certificate",
    "register",
    "disclosure",
    "privacy_notice",
    "dpdp_readiness",
    "not_tracked",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackItem(_Model):
    key: str
    label: str = Field(min_length=3, max_length=120)
    source: Source
    kind: str | None = None
    register_view: RegisterKey | None = None
    feature: str | None = None
    fix_by: str = Field(min_length=3, max_length=60)
    note: str | None = Field(default=None, min_length=3, max_length=300)

    @model_validator(mode="after")
    def _source(self) -> PackItem:
        if not KEY.fullmatch(self.key):
            raise ValueError(f"bad item key {self.key!r}")
        if (self.source == "certificate") != (self.kind is not None):
            raise ValueError(f"{self.key}: a kind exactly for a certificate item")
        if (self.source == "register") != (self.register_view is not None):
            raise ValueError(f"{self.key}: a register_view exactly for a register item")
        if (self.source == "not_tracked") != (self.feature is not None):
            raise ValueError(f"{self.key}: a feature exactly for a not_tracked item")
        if self.feature is not None and not FEATURE.fullmatch(self.feature):
            raise ValueError(f"{self.key}: bad feature id {self.feature!r}")
        if self.kind is not None:
            kinds = certificates.catalogue().kinds
            if self.kind not in kinds or self.kind == certificates.OTHER:
                raise ValueError(f"{self.key}: unknown certificate kind {self.kind!r}")
        return self

    def applies_to(self, boards: Sequence[str]) -> bool:
        """A certificate item follows its kind's boards; every other item applies."""
        if self.kind is None:
            return True
        return certificates.catalogue().kinds[self.kind].applies_to(boards)


class Pack(_Model):
    label: str = Field(min_length=3, max_length=120)
    authority: str = Field(min_length=3, max_length=200)
    boards: tuple[str, ...] = ()
    note: str = Field(min_length=3, max_length=300)
    items: tuple[PackItem, ...] = Field(min_length=1, max_length=60)

    @field_validator("boards")
    @classmethod
    def _boards(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [b for b in value if b not in certificates.BOARDS]
        if unknown:
            raise ValueError(f"unknown boards {unknown}")
        return value

    @model_validator(mode="after")
    def _unique(self) -> Pack:
        keys = [i.key for i in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("item keys repeat")
        return self

    def applies_to(self, boards: Sequence[str]) -> bool:
        return not self.boards or any(b in self.boards for b in boards)

    def items_for(self, boards: Sequence[str]) -> tuple[PackItem, ...]:
        return tuple(i for i in self.items if i.applies_to(boards))


class PackCatalogue(_Model):
    version: int = Field(ge=1)
    verified: bool
    source: str = Field(min_length=10)
    log_limit: int = Field(ge=1, le=500)
    packs: dict[str, Pack] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys(self) -> PackCatalogue:
        for key in self.packs:
            if not KEY.fullmatch(key):
                raise ValueError(f"bad pack key {key!r}")
        return self

    def packs_for(self, boards: Sequence[str]) -> dict[str, Pack]:
        """The packs that apply to a school with ``boards`` (FR-COMP-025)."""
        return {k: p for k, p in self.packs.items() if p.applies_to(boards)}


def parse(raw: object) -> PackCatalogue:
    return PackCatalogue.model_validate(raw)


@lru_cache(maxsize=1)
def pack_catalogue() -> PackCatalogue:
    raw = yaml.safe_load(
        resources.files("app.compliance").joinpath("packs_catalogue.yaml").read_text("utf-8")
    )
    return parse(raw)


__all__ = [
    "SOURCES",
    "Pack",
    "PackCatalogue",
    "PackItem",
    "RegisterKey",
    "Source",
    "pack_catalogue",
    "parse",
]
