"""``tools.yaml``: the read-only record-tool whitelist, permissions and caps (docs/06 §7).

ADR-0008 fixes the whitelist; adding a tool is a deliberate change with tests and evals, so the
loader refuses any tool name outside :data:`WHITELIST`. Permissions are checked against the
authz catalog by tests (``tests/knowledge/test_config.py``); this module stays pure.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field, model_validator

from app.knowledge.config._base import CONFIG_DIR, KEY_PATTERN, ConfigModel, read_yaml

PATH: Final = CONFIG_DIR / "tools.yaml"
PERMISSION_PATTERN: Final = r"^[a-z][a-z_]*(\.[a-z][a-z_]*)+$"

WHITELIST: Final = frozenset(
    {
        "find_students",
        "get_student_facts",
        "get_value_history",
        "count_students",
        "list_findings",
        "list_documents",
        "search_documents",
    }
)
"""ADR-0008 / docs/06 §7. The only tools the answer model may call."""


class ToolConfig(ConfigModel):
    description: str | None = Field(default=None, min_length=20, max_length=1000)
    """What the model is told the tool does (a prompt text: versioned here, invariant 13).
    Tools without one are not offered to the model yet."""
    permission: str = Field(pattern=PERMISSION_PATTERN)
    """Required to call the tool at all; the service still checks scope per object."""
    sensitive_permission: str | None = Field(default=None, pattern=PERMISSION_PATTERN)
    """Also required for C3 fields, which are masked unless held AND explicitly asked for."""
    actor_names_permission: str | None = Field(default=None, pattern=PERMISSION_PATTERN)
    """Also required to see who made a change (else actors are anonymised)."""
    max_results: int | None = Field(default=None, ge=1, le=100)
    max_fields: int | None = Field(default=None, ge=1, le=20)
    fields: tuple[str, ...] = ()
    """The enum of fields the tool accepts (never free-form)."""
    small_cell_min: int | None = Field(default=None, ge=1, le=100)
    """Counts below this are suppressed for sensitive breakdowns."""
    latest_terms: tuple[str, ...] = ()
    """Words that make a search prefer the latest documents (recency boost, docs/06 §6)."""

    @model_validator(mode="after")
    def _field_keys(self) -> ToolConfig:
        bad = [f for f in self.fields if not re.fullmatch(KEY_PATTERN, f)]
        if bad:
            raise ValueError(f"fields must be keys: {bad}")
        if self.max_fields is not None and self.fields and self.max_fields > len(self.fields):
            raise ValueError("max_fields exceeds the number of fields")
        return self

    def permissions(self) -> tuple[str, ...]:
        extra = (self.sensitive_permission, self.actor_names_permission)
        return (self.permission, *(p for p in extra if p is not None))


class ToolsConfig(ConfigModel):
    version: int = Field(ge=1)
    tools: dict[str, ToolConfig]

    @model_validator(mode="after")
    def _whitelist(self) -> ToolsConfig:
        extra = sorted(set(self.tools) - WHITELIST)
        missing = sorted(WHITELIST - set(self.tools))
        if extra or missing:
            raise ValueError(
                f"tools must be exactly the ADR-0008 whitelist (extra {extra}, missing {missing})"
            )
        return self


def load_tools_config(path: Path | None = None) -> ToolsConfig:
    return _load(path or PATH)


@lru_cache(maxsize=4)
def _load(path: Path) -> ToolsConfig:
    return ToolsConfig.model_validate(read_yaml(path))
