"""Shared helpers for the knowledge config loaders (pure: reads files of this package only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict

CONFIG_DIR: Final = Path(__file__).resolve().parent
KEY_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"
MODEL_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9.\-]{2,99}$"
"""A provider model ID (lower case, digits, dots, hyphens), never a display name."""


class ConfigModel(BaseModel):
    """Frozen, and unknown keys are errors (a typo must not silently fall back to a default)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a mapping at the top level")
    return data
