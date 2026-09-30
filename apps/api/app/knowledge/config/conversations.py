"""``conversations.yaml``: Ask conversations, memory and the answer cache (docs/06 §5; ADR-0033).

Invariant 13: prompt ids/versions, limits, thresholds and the fixed reply texts are read from here;
the roles' models and output caps from ``models.yaml``. Pure (no I/O beyond this package's file).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field, field_validator

from app.knowledge.config._base import CONFIG_DIR, ConfigModel, read_yaml
from app.knowledge.config.circulars import PromptRef

PATH: Final = CONFIG_DIR / "conversations.yaml"


class Titles(ConfigModel):
    derived_max_chars: int = Field(ge=20, le=120)
    max_chars: int = Field(ge=20, le=200)


class Revisions(ConfigModel):
    max_revisable_messages: int = Field(ge=1, le=50)


class QueryRewrite(ConfigModel):
    enabled: bool
    prompt: PromptRef
    max_chars: int = Field(ge=50, le=1000)


class SummaryConfig(ConfigModel):
    prompt: PromptRef
    max_chars: int = Field(ge=200, le=4000)
    max_input_chars: int = Field(ge=1000, le=50_000)


class FollowupsConfig(ConfigModel):
    prompt: PromptRef
    max_questions: int = Field(ge=0, le=5)
    max_chars: int = Field(ge=20, le=300)
    max_answer_chars: int = Field(ge=200, le=8000)


class Reply(ConfigModel):
    en: str = Field(min_length=5, max_length=400)
    te: str = Field(min_length=5, max_length=400)


class MemoryReplies(ConfigModel):
    saved: Reply
    refused: Reply
    unavailable: Reply
    memory_off: Reply
    full: Reply


class MemoryConfig(ConfigModel):
    screen_prompt: PromptRef
    max_items: int = Field(ge=1, le=200)
    max_chars: int = Field(ge=20, le=500)
    pending_ttl_hours: int = Field(ge=1, le=24 * 7)
    remember_prefixes: tuple[str, ...] = Field(min_length=1)
    replies: MemoryReplies

    @field_validator("remember_prefixes")
    @classmethod
    def _lower(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if any(p != p.casefold().strip() or not p for p in v):
            raise ValueError("remember_prefixes are lower case, without surrounding spaces")
        return v


class AnswerCache(ConfigModel):
    enabled: bool
    ttl_hours: int = Field(ge=1, le=24 * 30)


class ConversationsConfig(ConfigModel):
    version: int = Field(ge=1)
    titles: Titles
    revisions: Revisions
    query_rewrite: QueryRewrite
    summary: SummaryConfig
    followups: FollowupsConfig
    memory: MemoryConfig
    answer_cache: AnswerCache


def load_conversations_config(path: Path | None = None) -> ConversationsConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> ConversationsConfig:
    return ConversationsConfig.model_validate(read_yaml(path))


__all__ = [
    "AnswerCache",
    "ConversationsConfig",
    "FollowupsConfig",
    "MemoryConfig",
    "load_conversations_config",
]
