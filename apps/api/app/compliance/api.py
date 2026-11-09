"""Compliance certificate register routes (docs/09 "Compliance certificates"; FC-01;
FR-COMP-001..FR-COMP-030).

Reads declare ``require(compliance.read)``, writes ``require(compliance.manage)``, school-wide.
Writes to an existing certificate take ``If-Match``; recording one accepts ``Idempotency-Key``.
Another school's certificate is 404 (RLS). These are the school's own certificates: no
personal data.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep, IfMatch, etag
from app.compliance import service
from app.compliance.schemas import (
    CertificateCreate,
    CertificateDetailOut,
    CertificateOut,
    CertificateUpdate,
    RegisterOut,
    RegisterSummaryOut,
)

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])

Reader = Annotated[UserContext, Depends(require(service.READ, scope="school"))]
Manager = Annotated[UserContext, Depends(require(service.MANAGE, scope="school"))]


def _etag[M: CertificateOut](response: Response, item: M) -> M:
    response.headers["ETag"] = etag(item.version)
    return item


@router.get("/certificates", response_model=RegisterOut)
def get_register(ctx: Reader, db: TenantDB) -> RegisterOut:
    """The register: every certificate kind that applies to the school's boards (``missing``
    until recorded), every current certificate with its state (valid, expiring, expired, no
    expiry), counts and the catalogue (``verified: false``). Permission ``compliance.read``."""
    return service.register(db, ctx)


@router.get("/summary", response_model=RegisterSummaryOut)
def get_summary(ctx: Reader, db: TenantDB) -> RegisterSummaryOut:
    """Counts of expired, expiring and missing certificates for the school dashboard
    (permission ``compliance.read``)."""
    return service.summary(db, ctx)


@router.post("/certificates", response_model=CertificateOut, status_code=201)
def record_certificate(
    ctx: Manager, db: TenantDB, body: CertificateCreate, idem: IdempotencyDep
) -> Response:
    """Record a certificate; for a kind the school already holds this is its renewal (the old
    one is kept as history). Permission ``compliance.manage``. Errors (422): ``unknown_kind``,
    ``label_required``, ``label_not_allowed``, ``renews_not_allowed``, ``renews_not_found``,
    ``document_not_found``, ``owner_not_active``, ``aadhaar_full_number_rejected``; 409 when
    another renewal won the race. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.record(db, ctx, body),
        headers=lambda item: {
            "Location": f"/api/v1/compliance/certificates/{item.id}",
            "ETag": etag(item.version),
        },
    )


@router.get("/certificates/{certificate_id}", response_model=CertificateDetailOut)
def get_certificate(
    ctx: Reader, db: TenantDB, certificate_id: uuid.UUID, response: Response
) -> CertificateDetailOut:
    """One certificate with the earlier ones of its kind (permission ``compliance.read``).
    ``ETag`` for ``If-Match``."""
    return _etag(response, service.get_certificate(db, ctx, certificate_id))


@router.put("/certificates/{certificate_id}", response_model=CertificateOut)
def update_certificate(
    *,
    ctx: Manager,
    db: TenantDB,
    certificate_id: uuid.UUID,
    body: CertificateUpdate,
    version: IfMatch,
    response: Response,
) -> CertificateOut:
    """Correct the current certificate (all editable fields; permission ``compliance.manage``,
    ``If-Match``). Changing "valid until" restarts its reminders. Errors: 409
    ``certificate_not_current``, 412 stale ``If-Match``, 422 as when recording."""
    return _etag(response, service.update(db, ctx, certificate_id, body, expected_version=version))


@router.post("/certificates/{certificate_id}:remove", response_model=CertificateOut)
def remove_certificate(
    ctx: Manager, db: TenantDB, certificate_id: uuid.UUID, version: IfMatch, response: Response
) -> CertificateOut:
    """Remove a certificate recorded by mistake (permission ``compliance.manage``,
    ``If-Match``); it stays in the history as removed, and the one it renewed is current
    again. Errors: 409 ``certificate_not_current``, 412 stale ``If-Match``."""
    return _etag(response, service.remove(db, ctx, certificate_id, expected_version=version))
