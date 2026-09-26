"""Platform permission catalog and role matrix (docs/16 §6, docs/07 §6.5; FR-PLT-028, SEC-027).

Loaded from ``permissions.yaml`` inside the package (the image does not ship /config).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final

import yaml

PLATFORM_ROLES: Final[tuple[str, ...]] = (
    "platform_owner",
    "platform_engineer",
    "support_agent",
    "billing_admin",
    "platform_viewer",
)

# Pseudo-permission for routes any active operator may call (me, dashboard, lists of
# announcements and break-glass requests). It is not in the catalog and grants nothing else.
ANY_OPERATOR: Final = "platform.any_operator"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    key: str
    step_up: bool
    two_person: bool


@dataclass(frozen=True, slots=True)
class Catalog:
    permissions: dict[str, PermissionSpec]
    roles: dict[str, frozenset[str]]
    step_up_max_age_seconds: int

    def permissions_for(self, roles: set[str] | frozenset[str]) -> frozenset[str]:
        granted: set[str] = set()
        for role in roles:
            granted |= self.roles.get(role, frozenset())
        return frozenset(granted)


def _load() -> Catalog:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.platform").joinpath("permissions.yaml").read_text("utf-8")
    )
    perms = {
        key: PermissionSpec(key, bool(spec["step_up"]), bool(spec["two_person"]))
        for key, spec in raw["permissions"].items()
    }
    for key in perms:
        if not key.startswith("platform."):
            raise ValueError(f"platform permission {key} must start with 'platform.'")
    roles: dict[str, frozenset[str]] = {}
    for role, keys in raw["roles"].items():
        if role not in PLATFORM_ROLES:
            raise ValueError(f"unknown platform role {role}")
        expanded = set(perms) if keys == ["*"] else set(keys)
        unknown = expanded - set(perms)
        if unknown:
            raise ValueError(f"role {role} grants unknown permissions {sorted(unknown)}")
        roles[role] = frozenset(expanded)
    return Catalog(perms, roles, int(raw["step_up_max_age_seconds"]))


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    return _load()
