"""Deleting one school's data at offboarding (FR-PLT-005; ADR-0029; docs/16 §5.5).

The control plane drives the steps through :mod:`app.tenancy.service` (lifecycle "offboard",
ADR-0020): :func:`inventory` → :func:`purge` → :func:`verify` → :func:`destroy_keys`, and a year
later :func:`purge_expired_audit_chain`. Every step runs in the school's own ``tenant_session``
as ``sos_app`` (RLS), is idempotent and returns counts only.

- Each tenant module owns its tables and registers a :class:`TenantDataOwner` (its
  ``tenant_data_counts`` / ``purge_tenant_data``, optionally a ``prepare`` step that runs as
  ``sos_app`` before the role switch) and, for files, a :class:`TenantObjectOwner`. The order
  comes from ``offboarding.yaml``; a missing owner stops the purge before anything is deleted.
- The row purge is ONE transaction: counts (as ``sos_app``), prepare steps, deferred keys,
  ``SET LOCAL ROLE sos_purger`` + flag, every owner's deletes, back to ``sos_app`` and the school
  audit event ``tenant.data_purged``. Either every row goes or none does.
- The database decides whether the purge may run: ``sos_purger`` sees nothing unless the school
  is ``offboarding`` (two-person approved) and the flag names it (``core.tenant_purge_allowed``).
- :func:`verify` counts every catalog table with ``tenant_id`` (except the retained audit chain
  and the keys), so a table no module registered is found; :func:`destroy_keys` refuses while
  any row or file remains (fail closed).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core import purge as purging
from app.core.config import get_settings
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.tenancy import repository as repo
from app.tenancy.schemas import (
    TenantDataCounts,
    TenantKeysDestroyed,
    TenantPurgeResult,
)

log = get_logger(__name__)

Counter = Callable[[Session], dict[str, int]]
Purger = Callable[[Session], dict[str, int]]
Preparer = Callable[[Session], int]


@dataclass(frozen=True, slots=True)
class TenantDataOwner:
    """A module's school tables: ``count`` and ``prepare`` run as ``sos_app``; ``purge`` runs as
    ``sos_purger`` (only ``DELETE`` on the module's own tables). ``prepare`` returns how many rows
    it changed (for example profiles cleared)."""

    name: str
    count: Counter
    purge: Purger
    prepare: Preparer | None = None


@dataclass(frozen=True, slots=True)
class TenantObjectOwner:
    """A module's files under ``t/<tenant_id>/``: ``count`` and ``purge`` are idempotent."""

    name: str
    count: Callable[[uuid.UUID], int]
    purge: Callable[[uuid.UUID], int]


KeysDestroyedHook = Callable[[uuid.UUID], None]

DATA_OWNERS: dict[str, TenantDataOwner] = {}
OBJECT_OWNERS: dict[str, TenantObjectOwner] = {}
KEYS_DESTROYED_HOOKS: list[KeysDestroyedHook] = []


class OffboardingConfig(BaseModel):
    """``app/tenancy/offboarding.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    purge_order: tuple[str, ...] = Field(min_length=1)
    object_owners: tuple[str, ...]
    deferred_constraints: tuple[str, ...]
    retained: dict[str, str]
    keys_table: str


@lru_cache(maxsize=1)
def config() -> OffboardingConfig:
    raw: Any = yaml.safe_load(
        resources.files("app.tenancy").joinpath("offboarding.yaml").read_text("utf-8")
    )
    return OffboardingConfig.model_validate(raw)


def register_data_owner(owner: TenantDataOwner) -> None:
    """Called by each module at import time; re-registering the same name replaces it."""
    if owner.name not in config().purge_order:
        raise ValueError(f"{owner.name} is not in offboarding.yaml purge_order")
    DATA_OWNERS[owner.name] = owner


def register_object_owner(owner: TenantObjectOwner) -> None:
    if owner.name not in config().object_owners:
        raise ValueError(f"{owner.name} is not in offboarding.yaml object_owners")
    OBJECT_OWNERS[owner.name] = owner


def _owners() -> tuple[list[TenantDataOwner], list[TenantObjectOwner]]:
    cfg = config()
    missing = [n for n in cfg.purge_order if n not in DATA_OWNERS]
    missing += [n for n in cfg.object_owners if n not in OBJECT_OWNERS]
    if missing:
        log.error("tenancy.offboarding.owner_missing", owners=",".join(missing))
        raise Conflict(
            "The deletion job is not fully configured in this process.", code="purge_owner_missing"
        )
    return [DATA_OWNERS[n] for n in cfg.purge_order], [OBJECT_OWNERS[n] for n in cfg.object_owners]


def _session(tenant_id: uuid.UUID, engine: Engine | None) -> Any:
    return tenant_session(
        tenant_id,
        statement_timeout_ms=get_settings().worker_statement_timeout_ms,
        engine=engine,
    )


def _require_status(session: Session, *statuses: str) -> None:
    tenant = repo.get_own_tenant(session)
    if tenant is None:
        raise NotFound("Tenant not found")
    if tenant.status not in statuses:
        raise Conflict(
            f"A school that is {tenant.status} cannot be deleted.", code="not_offboarding"
        )


def _by_owner(counts: dict[str, dict[str, int]]) -> dict[str, int]:
    return {name: sum(tables.values()) for name, tables in counts.items()}


def inventory(tenant_id: uuid.UUID, *, engine: Engine | None = None) -> TenantDataCounts:
    """Rows per category (owning module) and files of an ``offboarding`` school, before deletion."""
    owners, objects = _owners()
    with _session(tenant_id, engine) as s:
        _require_status(s, "offboarding")
        rows = _by_owner({o.name: o.count(s) for o in owners})
    return TenantDataCounts(rows=rows, objects=sum(o.count(tenant_id) for o in objects))


def purge(tenant_id: uuid.UUID, *, engine: Engine | None = None) -> TenantPurgeResult:
    """Delete every row of the school (one transaction), then every file. Idempotent."""
    owners, objects = _owners()
    cfg = config()
    with _session(tenant_id, engine) as s:
        _require_status(s, "offboarding")
        counts = {o.name: o.count(s) for o in owners}
        prepared = {o.name: o.prepare(s) for o in owners if o.prepare is not None}
        profiles = prepared.get("identity", 0)
        for constraint in cfg.deferred_constraints:
            purging.defer_constraint(s, constraint)
        with purging.purge_role(s, tenant_id, flag="app.purge_tenant"):
            for owner in owners:
                owner.purge(s)
        rows = _by_owner(counts)
        if any(rows.values()) or any(prepared.values()):  # a retry after a commit finds nothing
            audit.record(
                s,
                action="tenant.data_purged",
                resource_type="tenant",
                resource_id=tenant_id,
                summary={"rows": rows, "profiles_cleared": profiles},
                actor_type="system",
            )
    deleted_objects = sum(o.purge(tenant_id) for o in objects)
    log.info(
        "tenancy.offboarding.purged",
        tenant_id=str(tenant_id),
        rows=sum(rows.values()),
        objects=deleted_objects,
    )
    return TenantPurgeResult(rows=rows, profiles_cleared=profiles, objects_deleted=deleted_objects)


def verify(tenant_id: uuid.UUID, *, engine: Engine | None = None) -> TenantDataCounts:
    """What is left of the school: rows per table (catalog-driven; retained audit chain and keys
    excluded) and files. Empty ``rows`` and ``objects == 0`` mean the purge is complete."""
    _owners_ignored, objects = _owners()
    cfg = config()
    with _session(tenant_id, engine) as s:
        rows = purging.remaining_rows(s, exclude=(*cfg.retained, cfg.keys_table))
    return TenantDataCounts(rows=rows, objects=sum(o.count(tenant_id) for o in objects))


def destroy_keys(tenant_id: uuid.UUID, *, engine: Engine | None = None) -> TenantKeysDestroyed:
    """Crypto-shredding: delete every wrapped key of the school once nothing else remains.

    Refuses (``409 data_remaining``) while :func:`verify` finds rows or files. Idempotent: a
    school without keys returns ``count = 0``. Audit (school chain, system):
    ``tenant.keys_destroyed`` with the key versions and key id, never key material.
    """
    remaining = verify(tenant_id, engine=engine)
    if remaining.rows or remaining.objects:
        raise Conflict("School data remains; the keys are kept.", code="data_remaining")
    keys_table = config().keys_table
    with _session(tenant_id, engine) as s:
        _require_status(s, "offboarding")
        keys = repo.list_tenant_keys(s)
        with purging.purge_role(s, tenant_id, flag="app.purge_tenant"):
            deleted = purging.delete_rows(s, (keys_table,))[keys_table]
        if repo.list_tenant_keys(s):  # pragma: no cover - the database refused silently
            raise Conflict("The keys could not be destroyed.", code="keys_remaining")
        if keys:
            audit.record(
                s,
                action="tenant.keys_destroyed",
                resource_type="tenant",
                resource_id=tenant_id,
                summary={
                    "key_versions": [k.key_version for k in keys],
                    "key_id": keys[-1].kms_key_arn,
                },
                actor_type="system",
            )
    for hook in KEYS_DESTROYED_HOOKS:
        hook(tenant_id)
    return TenantKeysDestroyed(
        count=deleted, key_versions=[k.key_version for k in keys] if deleted else []
    )


def purge_expired_audit_chain(tenant_id: uuid.UUID, *, engine: Engine | None = None) -> int:
    """Delete a ``deleted`` school's audit chain once its retention ended (ADR-0029 decision 2).

    The database refuses any event younger than 365 days (the whole transaction then rolls back,
    and the caller retries later). Returns the number of events deleted (0 when already gone).
    """
    with _session(tenant_id, engine) as s:
        _require_status(s, "deleted")
        with purging.purge_role(s, tenant_id, flag="app.purge_audit"):
            deleted = purging.delete_rows(s, ("audit.events", "audit.chain_heads"))
    return deleted["audit.events"]


__all__ = [
    "DATA_OWNERS",
    "KEYS_DESTROYED_HOOKS",
    "OBJECT_OWNERS",
    "TenantDataOwner",
    "TenantObjectOwner",
    "config",
    "destroy_keys",
    "inventory",
    "purge",
    "purge_expired_audit_chain",
    "register_data_owner",
    "register_object_owner",
    "verify",
]
