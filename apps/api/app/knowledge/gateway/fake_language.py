"""How the offline stand-ins follow the English-first prompts (dev and CI only; ADR-0036).

A real model writes English because the prompt says so; the deterministic stand-ins cannot read,
so they look for the rule every English-first prompt carries (:data:`ENGLISH_ONLY`, pinned by
``tests/knowledge/test_prompts.py``) or for a schema without Telugu fields. They then never
write Telugu script: where they would copy a Telugu sentence they write :data:`ENGLISH_STAND_IN`
and cite the passage, as a model that summarises it in English would. The server-side checks
that hold whatever a model writes live in the product code, not here.
"""

from __future__ import annotations

import re
from typing import Final

ENGLISH_ONLY: Final = "Write in English only"
"""The rule sentence of every English-first prompt (answer, follow-ups, circular, notice)."""

ENGLISH_STAND_IN: Final = "The cited passage from the school's records answers this."
"""What a stand-in writes instead of copying a Telugu sentence while Telugu is hidden."""

_TELUGU: Final = re.compile(r"[ఀ-౿]")


def english_only(system: str) -> bool:
    """True when the system prompt asks for English only (Telugu hidden)."""
    return ENGLISH_ONLY in system


def has_telugu(text: str) -> bool:
    return bool(_TELUGU.search(text))


__all__ = ["ENGLISH_ONLY", "ENGLISH_STAND_IN", "english_only", "has_telugu"]
