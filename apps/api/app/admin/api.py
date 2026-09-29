"""School admin console routes: full data export and retention settings (docs/09 Exports,
audit, admin; US-1201, FR-ADM-001, FR-ADM-002, BR-08).

Every route declares its guard. The full export needs ``tenant.export_all`` reaching the whole
school (the owner by default); requesting and downloading it need step-up MFA (428
``step_up_required``). The archive is made in a worker (202); ``/download-url`` gives a
presigned link valid at most 5 minutes while the archive exists (24 hours after it is ready).
These routes stay open for the owner and principal while the school is suspended (docs/16 §5.5;
the route permission still applies). Retention settings need ``tenant.settings.manage``;
changing them needs step-up and ``If-Match``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.admin import service
from app.admin.schemas import (
    RetentionOut,
    RetentionUpdate,
    TenantExportCreate,
    TenantExportDownloadOut,
    TenantExportOut,
)
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, IfMatch, Limit, Page, etag

router = APIRouter(prefix="/api/v1", tags=["admin"])

EXPORT_PATH = "/admin/tenant-export"

ExportMaker = Annotated[
    UserContext, Depends(require(service.EXPORT_ALL, scope="school", step_up=True))
]
ExportReader = Annotated[UserContext, Depends(require(service.EXPORT_ALL, scope="school"))]
RetentionReader = Annotated[UserContext, Depends(require(service.SETTINGS, scope="school"))]
RetentionManager = Annotated[
    UserContext, Depends(require(service.SETTINGS, scope="school", step_up=True))
]


def _headers(out: TenantExportOut) -> dict[str, str]:
    return {"Location": f"/api/v1{EXPORT_PATH}/{out.id}"}


@router.post(EXPORT_PATH, response_model=TenantExportOut, status_code=202)
def request_tenant_export(
    ctx: ExportMaker, db: TenantDB, body: TenantExportCreate, idem: IdempotencyDep
) -> Response:
    """Export all of the school's data (permission ``tenant.export_all``, the owner; recent
    sign-in with MFA, else 428 ``step_up_required``). The archive holds every record table as
    CSV and JSON, every document that passed the virus check and the audit log as CSV; it is
    made in the background (202) and you are notified when it is ready. Restricted (C3) values
    are masked unless ``include_sensitive`` is true (needs ``student.read_sensitive``, else 403
    ``sensitive_not_allowed``); full Aadhaar numbers are never stored or exported. One export
    at a time per school: 409 ``tenant_export_in_progress``. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.request_export(db, ctx, body),
        status_code=202,
        headers=_headers,
    )


@router.get(EXPORT_PATH, response_model=Page[TenantExportOut])
def list_tenant_exports(
    ctx: ExportReader, db: TenantDB, limit: Limit = 50, cursor: Cursor = None
) -> Page[TenantExportOut]:
    """The school's full exports, newest first (``tenant.export_all``): status, who asked,
    row counts and when the archive is deleted."""
    return service.list_exports(db, ctx, limit=limit, cursor=cursor)


@router.get(EXPORT_PATH + "/{tenant_export_id}", response_model=TenantExportOut)
def get_tenant_export(
    ctx: ExportReader, db: TenantDB, tenant_export_id: uuid.UUID
) -> TenantExportOut:
    """One full export: status (``queued``, ``running``, ``ready``, ``failed``, ``expired``),
    counts and expiry (``tenant.export_all``; 404 for unknown ids)."""
    return service.get_export(db, ctx, tenant_export_id)


@router.get(
    EXPORT_PATH + "/{tenant_export_id}/download-url", response_model=TenantExportDownloadOut
)
def get_tenant_export_download_url(
    ctx: ExportMaker, db: TenantDB, tenant_export_id: uuid.UUID
) -> TenantExportDownloadOut:
    """A download link for the archive, valid at most 5 minutes (``tenant.export_all`` and a
    recent sign-in with MFA, 428). The archive itself is deleted 24 hours after it is ready.
    Errors: 409 ``export_not_ready``, ``export_failed``, ``export_expired``; 403
    ``sensitive_not_allowed`` for an export with restricted values if you may not see them.
    Every download is recorded in the audit log."""
    return service.download_url(db, ctx, tenant_export_id)


@router.get("/admin/retention", response_model=RetentionOut)
def get_retention(ctx: RetentionReader, db: TenantDB, response: Response) -> RetentionOut:
    """How long the school keeps each kind of data (permission ``tenant.settings.manage``):
    the school's period, the default and the allowed range per category, and whether a daily
    job deletes it. The ETag is the settings version (0 until first changed)."""
    out = service.get_retention(db, ctx)
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/admin/retention", response_model=RetentionOut)
def update_retention(
    ctx: RetentionManager,
    db: TenantDB,
    body: RetentionUpdate,
    version: IfMatch,
    response: Response,
) -> RetentionOut:
    """Set the retention period in days of the categories a school may change (permission
    ``tenant.settings.manage``, recent sign-in with MFA; ``If-Match`` required, 412 when someone
    else changed them). A category left out goes back to its default. 422 per category:
    ``unknown_category``, ``not_configurable``, ``out_of_bounds``. Recorded in the audit log."""
    out = service.update_retention(db, ctx, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out
