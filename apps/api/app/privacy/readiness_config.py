"""Configuration of the DPDP readiness checklist (``readiness.yaml``; FC-03, FP-13;
FR-DPDP-053..FR-DPDP-066).

Pure module (no database or web): loads and validates the packaged YAML once.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

ItemKey = Literal[
    "privacy_contact",
    "notices",
    "dpa_signed",
    "dpia_done",
    "test_request",
    "breach_contact",
    "retention_reviewed",
    "consent_forms",
]
MarkItem = Literal["dpa_signed", "dpia_done", "breach_contact", "retention_reviewed"]
ItemKind = Literal["setting", "notices", "mark", "requests", "consents"]
MARK_ITEMS: Final[tuple[str, ...]] = (
    "dpa_signed",
    "dpia_done",
    "breach_contact",
    "retention_reviewed",
)
PLACEHOLDERS: Final = frozenset({"school_name", "privacy_contact_name", "template_version"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Item(_Model):
    key: ItemKey
    kind: ItemKind
    source: str = Field(min_length=5, max_length=300)


class WhatsAppNotice(_Model):
    title_en: str = Field(min_length=5, max_length=100)
    body_en: str = Field(min_length=20, max_length=1300)


class ReadinessConfig(_Model):
    version: int = Field(ge=1)
    items: tuple[Item, ...]
    admission_list_limit: int = Field(ge=10, le=1000)
    whatsapp_notice: WhatsAppNotice

    @field_validator("items")
    @classmethod
    def _items(cls, v: tuple[Item, ...]) -> tuple[Item, ...]:
        keys = [i.key for i in v]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate checklist item")
        marks = {i.key for i in v if i.kind == "mark"}
        if marks != set(MARK_ITEMS):
            raise ValueError(f"mark items must be exactly {MARK_ITEMS}")
        return v


@lru_cache(maxsize=1)
def load_config() -> ReadinessConfig:
    raw = resources.files("app.privacy").joinpath("readiness.yaml").read_text("utf-8")
    return ReadinessConfig.model_validate(yaml.safe_load(raw))


def fill(template: str, values: dict[str, str]) -> str:
    """Replace ``{name}`` placeholders (only the known ones; braces stay otherwise)."""
    out = template
    for key in PLACEHOLDERS:
        out = out.replace("{" + key + "}", values.get(key, ""))
    return out
