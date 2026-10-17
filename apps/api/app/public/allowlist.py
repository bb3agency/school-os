"""The public route allowlist (``public_routes.yaml``; ADR-0047 decision 4, FR-PUB-003).

Pure module: loads the allowlist and checks a list of mounted routes against it. Used by the
route-enumeration test (with the real app, and with small synthetic apps to prove the check
fails for unlisted or wrongly guarded routes).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.public.config import HandleKind, WebhookProvider

GuardKind = Literal["public_handle", "webhook_signature", "meta_verify_token"]
PUBLIC_PREFIX: Final = "/api/v1/public/"
WEBHOOK_PREFIX: Final = "/api/v1/webhooks/"
_PREFIX: Final[dict[GuardKind, str]] = {
    "public_handle": PUBLIC_PREFIX,
    "webhook_signature": WEBHOOK_PREFIX,
    "meta_verify_token": WEBHOOK_PREFIX,
}


class PublicRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    guard: GuardKind
    kinds: tuple[HandleKind, ...] = ()
    provider: WebhookProvider | None = None

    @model_validator(mode="after")
    def _shape(self) -> PublicRoute:
        if (self.guard == "public_handle") != bool(self.kinds):
            raise ValueError("kinds are listed exactly for public_handle routes")
        if (self.guard == "webhook_signature") != (self.provider is not None):
            raise ValueError("provider is listed exactly for webhook_signature routes")
        return self


class PublicRoutes(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    routes: dict[str, PublicRoute]

    @model_validator(mode="after")
    def _keys(self) -> PublicRoutes:
        for key, route in self.routes.items():
            method, _, path = key.partition(" ")
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError(f"{key!r}: not 'METHOD /path'")
            if not path.startswith(_PREFIX[route.guard]):
                raise ValueError(
                    f"{key!r}: a {route.guard} route must be under {_PREFIX[route.guard]}"
                )
        return self


@lru_cache(maxsize=1)
def public_routes() -> PublicRoutes:
    text = resources.files("app.public").joinpath("public_routes.yaml").read_text("utf-8")
    return PublicRoutes.model_validate(yaml.safe_load(text))


@dataclass(frozen=True, slots=True)
class MountedRoute:
    method: str
    path: str
    guards: tuple[Any, ...]
    """Every dependency of the route that carries ``sos_permission`` (route guards)."""


def is_public_guard(guard: object) -> bool:
    return isinstance(getattr(guard, "sos_public_guard", None), str)


def problems(mounted: Iterable[MountedRoute], allowlist: PublicRoutes | None = None) -> list[str]:
    """Violations of the public allowlist (empty when everything matches)."""
    allowed = (allowlist or public_routes()).routes
    seen: set[str] = set()
    found: list[str] = []
    for route in mounted:
        key = f"{route.method} {route.path}"
        public = [g for g in route.guards if is_public_guard(g)]
        under = route.path.startswith((PUBLIC_PREFIX, WEBHOOK_PREFIX))
        if not public:
            if under:
                found.append(f"{key}: public or webhook path without a public guard")
            continue
        seen.add(key)
        if len(route.guards) != 1:
            found.append(f"{key}: {len(route.guards)} guards (a public route has exactly one)")
            continue
        guard = public[0]
        entry = allowed.get(key)
        if entry is None:
            found.append(f"{key}: public guard on a route missing from public_routes.yaml")
            continue
        if guard.sos_public_guard != entry.guard:
            found.append(f"{key}: guard {guard.sos_public_guard} but listed as {entry.guard}")
            continue
        if entry.guard == "public_handle" and tuple(guard.sos_handle_kinds) != entry.kinds:
            found.append(f"{key}: handle kinds differ from public_routes.yaml")
        if entry.guard == "webhook_signature" and guard.sos_provider != entry.provider:
            found.append(f"{key}: provider differs from public_routes.yaml")
        if not route.path.startswith(_PREFIX[entry.guard]):
            found.append(f"{key}: {entry.guard} guard outside {_PREFIX[entry.guard]}")
    for key in sorted(set(allowed) - seen):
        found.append(f"{key}: listed in public_routes.yaml but not mounted with a public guard")
    return found


__all__ = [
    "PUBLIC_PREFIX",
    "WEBHOOK_PREFIX",
    "GuardKind",
    "MountedRoute",
    "PublicRoute",
    "PublicRoutes",
    "is_public_guard",
    "problems",
    "public_routes",
]
