"""Load, validate and render versioned prompt files (docs/06 §10).

File format (``<id>.v<version>.txt`` in this package)::

    ---
    id: answer_system
    version: 1
    model_config_key: answer        # a role in knowledge/config/models.yaml
    changelog:
      - "1 (2026-09-27): ..."
    ---
    Prompt text with {placeholders}.

A new behaviour is a new version file (the old one stays for comparison runs). Rendering needs
exactly the template's placeholders, so a prompt never ships with a literal ``{school_name}``
and never takes an unplanned value (such as a student record) through a side door. Values are
display strings; the gateway still redacts everything it sends (invariant 4).
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.knowledge.domain import ModelRole

PROMPTS_DIR: Final = Path(__file__).resolve().parent
_FILE: Final = re.compile(r"^(?P<id>[a-z][a-z0-9_]{0,63})\.v(?P<version>[1-9][0-9]{0,3})\.txt$")
_HEADER: Final = re.compile(r"\A---\n(?P<header>.*?\n)---\n(?P<body>.*)\Z", re.DOTALL)


class PromptHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: int = Field(ge=1)
    model_config_key: ModelRole
    changelog: tuple[str, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    header: PromptHeader
    text: str

    @property
    def placeholders(self) -> frozenset[str]:
        return frozenset(
            name for _, name, _, _ in string.Formatter().parse(self.text) if name is not None
        )

    def render(self, **values: str) -> str:
        expected = self.placeholders
        missing = sorted(expected - set(values))
        unexpected = sorted(set(values) - expected)
        if missing:
            raise ValueError(f"prompt {self.header.id} v{self.header.version}: missing {missing}")
        if unexpected:
            raise ValueError(
                f"prompt {self.header.id} v{self.header.version}: unexpected {unexpected}"
            )
        return self.text.format(**values)


def available(directory: Path = PROMPTS_DIR) -> list[tuple[str, int]]:
    """``(id, version)`` of every prompt file, sorted."""
    found = []
    for path in directory.glob("*.txt"):
        m = _FILE.fullmatch(path.name)
        if m:
            found.append((m["id"], int(m["version"])))
    return sorted(found)


def load_prompt(prompt_id: str, version: int, *, directory: Path = PROMPTS_DIR) -> PromptTemplate:
    return _load(prompt_id, version, directory)


@lru_cache(maxsize=32)
def _load(prompt_id: str, version: int, directory: Path) -> PromptTemplate:
    path = directory / f"{prompt_id}.v{version}.txt"
    if not _FILE.fullmatch(path.name) or not path.is_file():
        raise LookupError(f"no prompt {prompt_id} v{version}")
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = _HEADER.match(raw)
    if m is None:
        raise ValueError(f"{path.name}: missing '---' header")
    try:
        header = PromptHeader.model_validate(yaml.safe_load(m["header"]))
    except ValidationError as exc:
        raise ValueError(f"{path.name}: invalid header: {exc}") from exc
    if (header.id, header.version) != (prompt_id, version):
        raise ValueError(f"{path.name}: header id/version disagree with the file name")
    return PromptTemplate(header=header, text=m["body"].strip("\n"))


__all__ = ["PromptHeader", "PromptTemplate", "available", "load_prompt"]
