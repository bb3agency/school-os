"""Provision the school on its dedicated host (FR-PLT-003, ADR-0015; docs/16 §13.2 step 4).

Run ON the dedicated host, after ``db-bootstrap`` and ``migrate``, against the host's own database
(settings from the environment, like every other container)::

    sudo scripts/compose.sh run --rm api python -m app.platform.provision_dedicated \\
        --tenant-id <tenant ID from the panel> --code <code> --name "<school name>" \\
        [--boards CISCE] --owner-subject <sub in this host's user pool> \\
        --owner-name "<display name>" --owner-email <email> [--owner-language en|te]

The control plane chose the tenant ID when the operator provisioned the school (docs/16 §5.4);
the host carries it as ``SOS_DEDICATED_TENANT_ID``. The command refuses unless
``SOS_DEPLOYMENT_MODE=dedicated`` and ``--tenant-id`` equals ``SOS_DEDICATED_TENANT_ID``, and it
refuses when the host already holds a different school (one school per host).

Steps (each resumable; a re-run continues where an interrupted run stopped):

1. ``tenancy.register_tenant`` in a ``platform_session`` with that tenant ID (status
   ``provisioning``) + platform event ``tenant.provisioned``;
2. ``tenancy.initialise_tenant``: wrapped DEK + HMAC key (KMS on hosts) and the system roles;
3. ``platform.service.invite_school_owner``: the invited owner (MFA required, ``owner`` role) +
   platform event ``tenant.owner_invite_created`` + school-chain ``tenant.provisioned``;
4. ``tenancy.activate_tenant`` + platform and school-chain ``tenant.activated``.

Platform events are ``actor_type = system``; school-chain events are ``actor_type = platform``.
Only IDs and states are printed (never the owner's name or email).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, TextIO

from pydantic import ValidationError

# Registers the system-role cloning hook in tenancy.POST_PROVISION_HOOKS (as in the worker), so
# step 2 gives the school its roles and the owner invite gets the ``owner`` role.
import app.identity.service  # noqa: F401  isort: skip
from app.core.config import DeploymentMode, Settings, get_settings
from app.core.crypto import KeyWrapper, get_key_wrapper
from app.core.db import context_free_session, platform_session
from app.core.errors import Conflict, DomainError
from app.core.logging import get_logger
from app.platform import service as platform_service
from app.platform.common import SYSTEM, audit_platform, db_errors, tenant_chain
from app.platform.schemas import OwnerIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import TenantProvisionIn

EXIT_OK: Final = 0
EXIT_REFUSED: Final = 1
EXIT_INVALID: Final = 2
TIER: Final = "dedicated"

log = get_logger(__name__)


class Refused(Exception):
    """The host is not in a state where this command may run (reason code only)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Result:
    tenant_id: uuid.UUID
    outcome: str  # created | resumed | already_active
    status: str
    owner_invite: str  # created | pending_role | existing
    owner_membership_id: uuid.UUID | None = None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.platform.provision_dedicated",
        description="Create this dedicated host's school, owner invite and keys (idempotent).",
    )
    p.add_argument("--tenant-id", required=True, help="tenant ID chosen by the control plane")
    p.add_argument("--code", required=True, help="school code (same as in the panel)")
    p.add_argument("--name", required=True, help="school name")
    p.add_argument("--boards", default="", help="comma-separated board codes, e.g. CISCE")
    p.add_argument("--owner-subject", required=True, help="owner's sub in this host's user pool")
    p.add_argument("--owner-name", required=True)
    p.add_argument("--owner-email", required=True)
    p.add_argument("--owner-language", default="en", help="en or te")
    return p


def _tenant_ids(statuses: Sequence[str] | None) -> set[uuid.UUID]:
    with context_free_session() as session:
        return set(tenancy.list_tenant_ids(session, statuses))


def _register(tenant_id: uuid.UUID, school: TenantProvisionIn, settings: Settings) -> None:
    with platform_session() as s, db_errors():
        tenancy.register_tenant(s, school, tenant_id=tenant_id)
        audit_platform(
            s,
            SYSTEM,
            "tenant.provisioned",
            "tenant",
            tenant_id,
            {"tier": TIER, "code": school.code}
            | ({"deployment_id": settings.deployment_id} if settings.deployment_id else {}),
            tenant_id=tenant_id,
        )


def _invite_owner(
    tenant_id: uuid.UUID, owner: OwnerIn, key_version: int
) -> tuple[str, uuid.UUID | None]:
    """Invite the first owner; ``existing`` when an earlier run already did (resume)."""
    try:
        with (
            tenant_chain(
                tenant_id, SYSTEM, "tenant.provisioned", {"tier": TIER, "key_version": key_version}
            ),
            platform_session() as s,
            db_errors(),
        ):
            _user_id, membership_id, role_assigned = platform_service.invite_school_owner(
                s,
                tenant_id=tenant_id,
                subject=owner.idp_subject,
                display_name=owner.display_name,
                email=owner.email,
                language=owner.language,
            )
            audit_platform(
                s,
                SYSTEM,
                "tenant.owner_invite_created",
                "membership",
                membership_id,
                {"owner_role_assigned": role_assigned},
                tenant_id=tenant_id,
            )
    except Conflict:
        # core.create_owner_invite refuses once the school has members: the invite exists.
        return "existing", None
    return ("created" if role_assigned else "pending_role"), membership_id


def _activate(tenant_id: uuid.UUID) -> None:
    change = {"from": "provisioning", "to": "active"}
    with (
        tenant_chain(tenant_id, SYSTEM, "tenant.activated", change),
        platform_session() as s,
        db_errors(),
    ):
        tenancy.activate_tenant(s, tenant_id)
        audit_platform(
            s,
            SYSTEM,
            "tenant.activated",
            "tenant",
            tenant_id,
            {**change, "tier": TIER},
            tenant_id=tenant_id,
        )


def provision(
    tenant_id: uuid.UUID,
    school: TenantProvisionIn,
    owner: OwnerIn,
    *,
    settings: Settings,
    wrapper: KeyWrapper,
) -> Result:
    if settings.deployment_mode is not DeploymentMode.DEDICATED:
        raise Refused("not_dedicated")
    configured = settings.dedicated_tenant_id
    try:
        matches = configured is not None and uuid.UUID(configured) == tenant_id
    except ValueError:
        matches = False
    if not matches:
        raise Refused("tenant_mismatch")

    everyone = _tenant_ids(None)
    if everyone - {tenant_id}:
        raise Refused("host_has_other_tenant")
    if tenant_id in _tenant_ids(("active",)):
        return Result(tenant_id, "already_active", "active", "existing")
    outcome = "resumed"
    if tenant_id not in everyone:
        _register(tenant_id, school, settings)
        outcome = "created"
    elif tenant_id not in _tenant_ids(("provisioning",)):
        raise Refused("tenant_not_provisioning")  # suspended, offboarding or deleted

    key_version, _key_id = tenancy.initialise_tenant(tenant_id, wrapper=wrapper)
    invite, membership_id = _invite_owner(tenant_id, owner, key_version)
    if invite == "pending_role":
        raise Refused("owner_role_missing")  # never activate a school without an owner
    _activate(tenant_id)
    log.info("platform.dedicated.provisioned", tenant_id=str(tenant_id), outcome=outcome)
    return Result(tenant_id, outcome, "active", invite, membership_id)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    wrapper: KeyWrapper | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    try:
        tenant_id = uuid.UUID(args.tenant_id)
        school = TenantProvisionIn(
            code=args.code,
            name=args.name,
            boards=[b.strip() for b in args.boards.split(",") if b.strip()],
            plan_tier=TIER,
            deployment_mode=TIER,
        )
        owner = OwnerIn(
            display_name=args.owner_name,
            email=args.owner_email,
            idp_subject=args.owner_subject,
            language=args.owner_language,
        )
    except (ValueError, ValidationError):
        err.write("invalid input: check the tenant ID, code, boards, owner email and language\n")
        return EXIT_INVALID
    settings = settings or get_settings()
    try:
        result = provision(
            tenant_id,
            school,
            owner,
            settings=settings,
            wrapper=wrapper or get_key_wrapper(settings),
        )
    except Refused as exc:
        err.write(f"refused: {exc.code}\n")
        return EXIT_REFUSED
    except DomainError as exc:
        err.write(f"refused: {exc.code}\n")
        return EXIT_REFUSED
    out.write(f"tenant_id={result.tenant_id}\n")
    out.write(f"outcome={result.outcome}\n")
    out.write(f"status={result.status}\n")
    out.write(f"owner_invite={result.owner_invite}\n")
    if result.owner_membership_id is not None:
        out.write(f"owner_membership_id={result.owner_membership_id}\n")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
