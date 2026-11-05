"""Typed view of ``app/ops/config.yaml`` (outbox retention, lead decision D51; §6.13)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class OpsConfig:
    outbox_delivered_retention_days: int


def parse_config(raw: Mapping[str, Any]) -> OpsConfig:
    days = int(raw["outbox"]["delivered_retention_days"])
    if days < 1:
        raise ValueError("outbox.delivered_retention_days must be at least 1")
    return OpsConfig(outbox_delivered_retention_days=days)


@lru_cache(maxsize=1)
def load_config() -> OpsConfig:
    text = resources.files("app.ops").joinpath("config.yaml").read_text("utf-8")
    return parse_config(yaml.safe_load(text))
