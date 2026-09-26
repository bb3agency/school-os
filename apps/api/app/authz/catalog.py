"""Permission catalog and system role templates (FR-IAM-010, FR-IAM-011; docs/07 §6.2).

Both come from versioned YAML inside the package (``permissions.yaml``, ``roles.yaml``) and are
validated when first loaded: a role may only grant known, non-platform, non-implicit
permissions, and platform permissions are exactly the ``platform.*`` keys. Anything wrong fails
at import/startup, never at request time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Any, Final, Literal

import yaml

Sensitivity = Literal["normal", "sensitive", "critical"]
GrantMode = Literal["school", "scoped", "school_step_up"]

_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_ROLE_RE: Final = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SENSITIVITIES: Final = ("normal", "sensitive", "critical")
_MODES: Final = ("school", "scoped", "school_step_up")

AUTHENTICATED: Final = "session.authenticated"
"""Implicit permission of every active member (routes such as ``GET /me``)."""


class CatalogError(ValueError):
    """The YAML catalog is inconsistent (raised at load time)."""


@dataclass(frozen=True, slots=True)
class PermissionDef:
    key: str
    description: str
    sensitivity: Sensitivity
    step_up: bool
    is_platform: bool
    implicit: bool


@dataclass(frozen=True, slots=True)
class RoleGrant:
    permission: str
    scoped: bool
    step_up: bool


@dataclass(frozen=True, slots=True)
class RoleDef:
    key: str
    name_en: str
    name_te: str
    mfa_required: bool
    membership_ttl: timedelta | None
    grants: tuple[RoleGrant, ...]

    @property
    def permission_keys(self) -> frozenset[str]:
        return frozenset(g.permission for g in self.grants)

    def grant(self, permission: str) -> RoleGrant | None:
        for g in self.grants:
            if g.permission == permission:
                return g
        return None


def _load_yaml(name: str) -> dict[str, Any]:
    raw = yaml.safe_load(resources.files("app.authz").joinpath(name).read_text("utf-8"))
    if not isinstance(raw, dict):
        raise CatalogError(f"{name}: expected a mapping")
    return raw


def _parse_permissions(raw: dict[str, Any]) -> dict[str, PermissionDef]:
    items = raw.get("permissions")
    if not isinstance(items, dict) or not items:
        raise CatalogError("permissions.yaml: 'permissions' must be a non-empty mapping")
    out: dict[str, PermissionDef] = {}
    for key, spec in items.items():
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise CatalogError(f"permissions.yaml: bad key {key!r}")
        if not isinstance(spec, dict):
            raise CatalogError(f"permissions.yaml: {key} must be a mapping")
        unknown = set(spec) - {"description", "sensitivity", "step_up", "is_platform", "implicit"}
        if unknown:
            raise CatalogError(f"permissions.yaml: {key} has unknown fields {sorted(unknown)}")
        description = spec.get("description")
        sensitivity = spec.get("sensitivity", "normal")
        if not isinstance(description, str) or not description.strip():
            raise CatalogError(f"permissions.yaml: {key} needs a description")
        if sensitivity not in _SENSITIVITIES:
            raise CatalogError(f"permissions.yaml: {key} has bad sensitivity")
        is_platform = bool(spec.get("is_platform", False))
        if is_platform != key.startswith("platform."):
            raise CatalogError(f"permissions.yaml: is_platform must equal platform.* for {key}")
        flags = [spec.get(f, False) for f in ("step_up", "is_platform", "implicit")]
        if not all(isinstance(f, bool) for f in flags):
            raise CatalogError(f"permissions.yaml: {key} flags must be booleans")
        out[key] = PermissionDef(
            key=key,
            description=description.strip(),
            sensitivity=sensitivity,
            step_up=bool(spec.get("step_up", False)),
            is_platform=is_platform,
            implicit=bool(spec.get("implicit", False)),
        )
    return out


def _parse_roles(raw: dict[str, Any], permissions: dict[str, PermissionDef]) -> dict[str, RoleDef]:
    items = raw.get("roles")
    if not isinstance(items, dict) or not items:
        raise CatalogError("roles.yaml: 'roles' must be a non-empty mapping")
    out: dict[str, RoleDef] = {}
    for key, spec in items.items():
        if not isinstance(key, str) or not _ROLE_RE.match(key) or key.startswith("platform"):
            raise CatalogError(f"roles.yaml: bad role key {key!r}")
        if not isinstance(spec, dict):
            raise CatalogError(f"roles.yaml: {key} must be a mapping")
        grants_raw = spec.get("grants")
        if not isinstance(grants_raw, dict):
            raise CatalogError(f"roles.yaml: {key}.grants must be a mapping")
        grants: list[RoleGrant] = []
        for perm, mode in grants_raw.items():
            pdef = permissions.get(perm)
            if pdef is None:
                raise CatalogError(f"roles.yaml: {key} grants unknown permission {perm}")
            if pdef.is_platform or pdef.implicit:
                raise CatalogError(f"roles.yaml: {key} may not grant {perm}")
            if mode not in _MODES:
                raise CatalogError(f"roles.yaml: {key}.{perm} has bad mode {mode!r}")
            step_up = mode == "school_step_up"
            if step_up != pdef.step_up:
                raise CatalogError(
                    f"roles.yaml: {key}.{perm} step-up disagrees with permissions.yaml"
                )
            grants.append(RoleGrant(permission=perm, scoped=mode == "scoped", step_up=step_up))
        ttl_days = spec.get("membership_ttl_days")
        if ttl_days is not None and (not isinstance(ttl_days, int) or ttl_days < 1):
            raise CatalogError(f"roles.yaml: {key}.membership_ttl_days must be a positive int")
        name_en, name_te = spec.get("name_en"), spec.get("name_te")
        if not isinstance(name_en, str) or not isinstance(name_te, str):
            raise CatalogError(f"roles.yaml: {key} needs name_en and name_te")
        out[key] = RoleDef(
            key=key,
            name_en=name_en,
            name_te=name_te,
            mfa_required=bool(spec.get("mfa_required", False)),
            membership_ttl=timedelta(days=ttl_days) if ttl_days else None,
            grants=tuple(grants),
        )
    return out


@lru_cache(maxsize=1)
def permission_catalog() -> MappingProxyType[str, PermissionDef]:
    """Every permission (tenant, implicit and platform), keyed by permission key."""
    return MappingProxyType(_parse_permissions(_load_yaml("permissions.yaml")))


@lru_cache(maxsize=1)
def system_roles() -> MappingProxyType[str, RoleDef]:
    """The system role templates, keyed by role key (FR-IAM-010)."""
    return MappingProxyType(_parse_roles(_load_yaml("roles.yaml"), dict(permission_catalog())))


def tenant_permission(key: str) -> PermissionDef:
    """The catalog entry for a permission usable by tenant routes, else ``CatalogError``."""
    pdef = permission_catalog().get(key)
    if pdef is None:
        raise CatalogError(f"unknown permission {key!r}")
    if pdef.is_platform:
        raise CatalogError(f"{key!r} is a platform permission; use require_platform()")
    return pdef


def implicit_permissions() -> frozenset[str]:
    return frozenset(k for k, p in permission_catalog().items() if p.implicit)


def mfa_roles() -> frozenset[str]:
    """Role keys whose holders must present the MFA claim (FR-IAM-002)."""
    return frozenset(k for k, r in system_roles().items() if r.mfa_required)
