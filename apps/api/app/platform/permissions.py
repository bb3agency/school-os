"""Platform permissions and operator role matrix (docs/16 §6, docs/07 §6.5; FR-PLT-028, SEC-027).

Permission keys and step-up flags come from the single catalog ``app/authz/permissions.yaml``
(``is_platform: true`` entries, seeded into ``core.permissions`` by 0004_authz_seed). This
module adds only the operator-role matrix and the two-person list from ``roles.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final

import yaml

from app.authz.catalog import permission_catalog

PLATFORM_ROLES: Final[tuple[str, ...]] = (
    "platform_owner",
    "platform_engineer",
    "support_agent",
    "billing_admin",
    "platform_viewer",
)

# Routes any active operator may call (me, dashboard, lists of announcements and break-glass
# requests, own jobs) are guarded by the one platform permission EVERY platform role holds, so
# each route still names a real catalog key (route-enumeration test). A test pins that every
# role in roles.yaml grants it.
ANY_OPERATOR: Final = "platform.tenants.read"


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
        resources.files("app.platform").joinpath("roles.yaml").read_text("utf-8")
    )
    two_person = set(raw.get("two_person") or [])
    perms = {
        key: PermissionSpec(key, spec.step_up, key in two_person)
        for key, spec in permission_catalog().items()
        if spec.is_platform
    }
    unknown_two_person = two_person - set(perms)
    if unknown_two_person:
        raise ValueError(f"two_person names unknown permissions {sorted(unknown_two_person)}")
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
