"""Tally connector routes (docs/09 Tally connector; M6; ADR-0032 Proposed; FR-TALLY-001..010).

Behind the per-school flag ``tally.connector.enabled`` (default off): with the flag off every
route below answers 404.

- ``router`` (``/api/v1/tally/*``): staff routes, each with ``require(...)``/``require_any(...)``.
  Enrolment codes and revocation need ``tally.device.manage`` with step-up; groups and links
  ``tally.configure``; dues ``finance.read`` (school-wide).
- ``agent_router`` (``/api/v1/edge/tally/*``): the edge agent, machine-authenticated by
  ``require_edge_agent_enrolment()`` / ``require_edge_agent_signature()`` (ADR-0032 §3). No user
  session; outbound from the office PC only.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import Cursor, IfMatch, Limit, Page, etag
from app.core.crypto import KeyWrapper
from app.tally import service
from app.tally.agent_auth import (
    AgentCaller,
    EnrolmentCaller,
    get_agent_key_wrapper,
    require_edge_agent_enrolment,
    require_edge_agent_signature,
)
from app.tally.schemas import (
    AgentConfigOut,
    CatalogIn,
    CatalogOut,
    ConnectorStatus,
    DeviceOut,
    DuesPage,
    EnrolIn,
    EnrolmentCodeCreate,
    EnrolmentCodeOut,
    EnrolOut,
    GroupOut,
    GroupSelectionIn,
    KeyRotationOut,
    LinkFilter,
    LinkIn,
    PartyDetail,
    PartyOut,
    PartySearchIn,
    SyncIn,
    SyncOut,
)

router = APIRouter(prefix="/api/v1/tally", tags=["tally"])
agent_router = APIRouter(prefix="/api/v1/edge/tally", tags=["tally-agent"])

StatusReader = Annotated[
    UserContext,
    Depends(require_any(service.READ, service.DEVICE_MANAGE, service.CONFIGURE)),
]
DeviceReader = Annotated[UserContext, Depends(require(service.DEVICE_MANAGE, scope="school"))]
DeviceManager = Annotated[
    UserContext, Depends(require(service.DEVICE_MANAGE, scope="school", step_up=True))
]
Configurer = Annotated[UserContext, Depends(require(service.CONFIGURE, scope="school"))]
FeeReader = Annotated[UserContext, Depends(require(service.READ, scope="school"))]
Wrapper = Annotated[KeyWrapper, Depends(get_agent_key_wrapper)]


# --- staff: connector, devices, enrolment codes ---------------------------------------------------


@router.get("/status", response_model=ConnectorStatus)
def get_status(ctx: StatusReader, db: TenantDB) -> ConnectorStatus:
    """The connector at a glance: active agents (and whether one is silent), last sync, Tally
    as-of date, company, selected groups, ledgers linked and unlinked; the totals due only for
    school-wide ``finance.read`` holders. 404 when the connector is off for the school."""
    return service.status(db, ctx)


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(ctx: DeviceReader, db: TenantDB) -> list[DeviceOut]:
    """Enrolled Tally agents, active first, with last call, last sync and version
    (``tally.device.manage``). The ETag of each is ``W/"<version>"`` for revocation."""
    return service.list_devices(db, ctx)


@router.post("/enrolment-codes", response_model=EnrolmentCodeOut, status_code=201)
def create_enrolment_code(
    ctx: DeviceManager, db: TenantDB, body: EnrolmentCodeCreate
) -> EnrolmentCodeOut:
    """A one-time code for enrolling the Tally agent on the office PC (``tally.device.manage``,
    recent MFA sign-in). Shown once; SchoolOS keeps only its hash; valid 30 minutes. 409
    ``too_many_devices``."""
    return service.create_enrolment_code(db, ctx, body)


@router.post("/devices/{device_id}/revoke", response_model=DeviceOut)
def revoke_device(
    ctx: DeviceManager,
    db: TenantDB,
    *,
    device_id: uuid.UUID,
    version: IfMatch,
    response: Response,
) -> DeviceOut:
    """Revoke a Tally agent at once (``tally.device.manage``, recent MFA sign-in; ``If-Match``).
    Its keys are erased; synced data stays until offboarding. 409 ``already_revoked``."""
    out = service.revoke_device(db, ctx, device_id, version)
    response.headers["ETag"] = etag(out.version)
    return out


# --- staff: groups and ledger links ---------------------------------------------------------------


@router.get("/groups", response_model=list[GroupOut])
def list_groups(ctx: Configurer, db: TenantDB) -> list[GroupOut]:
    """Ledger groups the agent reported (names only), selected ones marked
    (``tally.configure``)."""
    return service.list_groups(db, ctx)


@router.put("/groups/selection", response_model=list[GroupOut])
def select_groups(ctx: Configurer, db: TenantDB, body: GroupSelectionIn) -> list[GroupOut]:
    """Choose the groups whose party ledgers the agent may send; replaces the selection
    (``tally.configure``). The server refuses snapshots with any other group. 422
    ``unknown_group``."""
    return service.select_groups(db, ctx, body)


@router.get("/parties", response_model=Page[PartyOut])
def list_parties(
    ctx: Configurer,
    db: TenantDB,
    link: Annotated[LinkFilter, Query(description="all, linked or unlinked")] = "all",
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[PartyOut]:
    """Ledgers of the last snapshot by name, with the students they are linked to
    (``tally.configure``). Filter by name with ``POST /tally/parties/search`` (no names in
    URLs)."""
    return service.list_parties(db, ctx, link=link, limit=limit, cursor=cursor)


@router.post("/parties/search", response_model=Page[PartyOut])
def search_parties(
    ctx: Configurer,
    db: TenantDB,
    body: PartySearchIn,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[PartyOut]:
    """Ledgers whose name contains ``query`` (case-insensitive), optionally only linked or
    unlinked (``tally.configure``). A POST so ledger names never appear in URLs."""
    return service.list_parties(
        db, ctx, link=body.link, query=body.query, limit=limit, cursor=cursor
    )


@router.get("/parties/{party_id}", response_model=PartyDetail)
def get_party(ctx: Configurer, db: TenantDB, party_id: uuid.UUID) -> PartyDetail:
    """One ledger with its links and up to five suggested students whose name, admission number
    or class appears in the ledger name (suggestions only; a person links)."""
    return service.get_party(db, ctx, party_id)


@router.post("/parties/{party_id}/links", response_model=PartyOut, status_code=201)
def link_party(ctx: Configurer, db: TenantDB, party_id: uuid.UUID, body: LinkIn) -> PartyOut:
    """Link the ledger to a student (``tally.configure``); idempotent. 409 ``party_gone``; 422
    ``student_id`` ``not_found``."""
    return service.link_party(db, ctx, party_id, body.student_id)


@router.delete("/parties/{party_id}/links/{student_id}", status_code=204)
def unlink_party(
    ctx: Configurer, db: TenantDB, party_id: uuid.UUID, student_id: uuid.UUID
) -> Response:
    """Remove a ledger's link to a student (``tally.configure``)."""
    service.unlink_party(db, ctx, party_id, student_id)
    return Response(status_code=204)


# --- staff: fee dues ------------------------------------------------------------------------------


@router.get("/dues", response_model=DuesPage)
def list_dues(ctx: FeeReader, db: TenantDB, limit: Limit = 50, cursor: Cursor = None) -> DuesPage:
    """Students with fee dues from the linked Tally ledgers, highest first, with the school
    totals and the Tally as-of date (``finance.read``, school-wide). Unlinked ledgers count only
    in the totals."""
    return service.list_dues(db, ctx, limit=limit, cursor=cursor)


# --- edge agent (device-signed; ADR-0032 §3) ------------------------------------------------------


@agent_router.post("/enrol", response_model=EnrolOut, status_code=201)
def enrol(
    caller: Annotated[EnrolmentCaller, Depends(require_edge_agent_enrolment())],
    wrapper: Wrapper,
    body: EnrolIn,
) -> EnrolOut:
    """Exchange the owner's one-time code for a device credential (returned once). Headers:
    ``X-SOS-Tenant``, ``X-SOS-Timestamp``, ``X-SOS-Nonce``. 401 for a wrong, used or expired
    code; 429 after 10 attempts per school per hour."""
    return service.enrol(caller, body, wrapper=wrapper)


@agent_router.get("/config", response_model=AgentConfigOut)
def agent_config(
    caller: Annotated[AgentCaller, Depends(require_edge_agent_signature("config"))],
) -> AgentConfigOut:
    """The company and ledger groups to read, the sync interval and the minimum agent version."""
    return service.agent_config(caller)


@agent_router.put("/catalog", response_model=CatalogOut)
def put_catalog(
    caller: Annotated[AgentCaller, Depends(require_edge_agent_signature("catalog"))],
    body: CatalogIn,
) -> CatalogOut:
    """The ledger groups of the company open in Tally (names only), so the accountant can choose."""
    return service.put_catalog(caller, body)


@agent_router.post("/syncs", response_model=SyncOut)
def post_sync(
    caller: Annotated[AgentCaller, Depends(require_edge_agent_signature("sync"))],
    body: SyncIn,
) -> SyncOut:
    """One complete snapshot of the party ledgers under the selected groups. Idempotent by
    ``batch_id`` (a repeat returns the first result with ``repeat: true``). 409
    ``no_groups_selected`` / ``agent_outdated``; 422 ``group_not_selected``."""
    return service.accept_sync(caller, body)


@agent_router.post("/key-rotation", response_model=KeyRotationOut)
def rotate_key(
    caller: Annotated[AgentCaller, Depends(require_edge_agent_signature("key_rotation"))],
    wrapper: Wrapper,
) -> KeyRotationOut:
    """A new device key, returned once; the old one works until the new one is first used."""
    return service.rotate_key(caller, wrapper=wrapper)
