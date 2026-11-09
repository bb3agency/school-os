"""Add-on fee configuration (``addons.yaml``; FM-02; FR-FEE-081..100; invariant 13).

Pure: reads and validates the versioned file next to this module; no database. The GST note is
marked ``verified: false`` until a chartered accountant has reviewed it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH: Final = Path(__file__).with_name("addons.yaml")
HEAD_KINDS: Final = frozenset({"tuition", "admission", "exam", "books", "transport", "other"})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Limits(_Frozen):
    max_templates_per_year: int = Field(ge=1, le=500)
    max_bulk_students: int = Field(ge=1, le=20000)
    max_picked_students: int = Field(ge=1, le=2000)


class GstNote(_Frozen):
    verified: bool
    source: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=500)


class AddonsConfig(_Frozen):
    version: int = Field(ge=1)
    head_kinds: tuple[str, ...] = Field(min_length=1)
    change_approval: Literal["reduction_only"]
    limits: Limits
    gst_note: GstNote

    @model_validator(mode="after")
    def _kinds(self) -> AddonsConfig:
        if set(self.head_kinds) - HEAD_KINDS:
            raise ValueError("unknown head kind")
        if "tuition" in self.head_kinds:
            raise ValueError("tuition is charged by the class plan, never as an add-on")
        return self


@lru_cache(maxsize=1)
def load_config() -> AddonsConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return AddonsConfig.model_validate(raw)


__all__ = ["CONFIG_PATH", "AddonsConfig", "load_config"]
