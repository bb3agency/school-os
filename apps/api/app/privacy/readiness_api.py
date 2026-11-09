"""DPDP readiness routes: the checklist, the notice given at admission and the privacy notice sent
on WhatsApp (docs/09 "R3: privacy"; FC-03, FP-13; FR-DPDP-053..FR-DPDP-066). Not legal advice.

Reads declare ``privacy.read`` (names of students only within the reader's ``student.read_basic``
reach); writes declare ``privacy.request.manage``. Sending on WhatsApp also needs
``notice.draft``, ``notice.approve`` and ``message.send`` school-wide, checked in the service.
No personal data in URLs.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep
from app.privacy import readiness_service as service
from app.privacy.readiness_schemas import (
    AdmissionNoticeIn,
    AdmissionNoticeOut,
    AdmissionPendingOut,
    AdmissionRecordedOut,
    NoticeDeliveryOut,
    ReadinessMarkIn,
    ReadinessMarkOut,
    ReadinessOut,
    WhatsAppPreviewIn,
    WhatsAppPreviewOut,
    WhatsAppSendIn,
)
from app.privacy.schemas import PrivacyNoticeOut

router = APIRouter(prefix="/api/v1", tags=["privacy"])

Reader = Annotated[UserContext, Depends(require(service.READ))]
Manager = Annotated[UserContext, Depends(require(service.MANAGE))]
SchoolManager = Annotated[UserContext, Depends(require(service.MANAGE, scope="school"))]

_UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    404: {"description": "``module_unavailable`` on a dedicated host or with messaging off."}
}


@router.get("/privacy/readiness", response_model=ReadinessOut)
def readiness(ctx: Reader, db: TenantDB) -> ReadinessOut:
    """The DPDP readiness checklist computed from the school's records: privacy contact,
    notices with the current template version, DPA, DPIA, one request closed, breach contact,
    retention reviewed this academic year, consent forms; and how many students added after the
    parents' notice have no notice record (FR-DPDP-053, FR-DPDP-059; permission
    ``privacy.read``). Not legal advice."""
    return service.readiness(db, ctx)


@router.post("/privacy/readiness/marks", response_model=ReadinessMarkOut, status_code=201)
def record_mark(
    ctx: Manager, db: TenantDB, body: ReadinessMarkIn, idem: IdempotencyDep
) -> Response:
    """Record a readiness step SchoolOS cannot see elsewhere (DPA signed, DPIA done, breach
    contact and phone tree, retention reviewed): the date and optionally the evidence document
    (FR-DPDP-054; permission ``privacy.request.manage``; ``Idempotency-Key``). Append-only.
    Errors (422): ``date_in_future``, ``not_found``."""
    return idem.run(db, body, lambda: service.record_mark(db, ctx, body))


@router.get("/privacy/admission-notices/pending", response_model=AdmissionPendingOut)
def admission_pending(ctx: Reader, db: TenantDB) -> AdmissionPendingOut:
    """Students added after the latest parents' notice without a notice record: the count for
    the school, the list of those you may see (FR-DPDP-059; permission ``privacy.read``)."""
    return service.admission_pending(db, ctx)


@router.get("/privacy/admission-notices/{student_id}", response_model=AdmissionNoticeOut)
def get_admission_notice(ctx: Reader, db: TenantDB, student_id: uuid.UUID) -> AdmissionNoticeOut:
    """When and how one student's family got the privacy notice at admission (FR-DPDP-058;
    permission ``privacy.read``; 404 outside your reach or without a record)."""
    return service.get_admission_notice(db, ctx, student_id)


@router.post("/privacy/admission-notices", response_model=AdmissionRecordedOut, status_code=201)
def record_admission_notices(
    ctx: Manager, db: TenantDB, body: AdmissionNoticeIn, idem: IdempotencyDep
) -> Response:
    """Record that these students' families got the privacy notice: date, channel and notice
    issue (FR-DPDP-058, FR-DPDP-060; permission ``privacy.request.manage``;
    ``Idempotency-Key``). A student with a record keeps it. Errors: 404 (a student outside your
    reach); 422 ``date_in_future``, ``no_parents_notice``, ``not_found``."""
    return idem.run(db, body, lambda: service.record_admission_notices(db, ctx, body))


@router.post(
    "/privacy/notices/whatsapp/preview", response_model=WhatsAppPreviewOut, responses=_UNAVAILABLE
)
def whatsapp_preview(
    ctx: SchoolManager, db: TenantDB, body: WhatsAppPreviewIn, response: Response
) -> WhatsAppPreviewOut:
    """Make the parent notice from the parents' privacy notice and preview sending it on
    WhatsApp: recipients, guardians left out, estimated Meta cost (FR-DPDP-061, FR-DPDP-062;
    permission ``privacy.request.manage`` with ``notice.draft``, ``notice.approve`` and
    ``message.send``). Nothing is sent. Errors: 403; 409 ``notice_not_current``; 422
    ``section_ids``."""
    response.headers["Cache-Control"] = "no-store"
    return service.whatsapp_preview(db, ctx, body)


@router.post(
    "/privacy/notices/whatsapp",
    response_model=PrivacyNoticeOut,
    status_code=201,
    responses=_UNAVAILABLE,
)
def whatsapp_send(
    ctx: SchoolManager, db: TenantDB, body: WhatsAppSendIn, idem: IdempotencyDep
) -> Response:
    """Send the previewed privacy notice on WhatsApp (utility template ``school_notice``) and
    record the notice issue with the broadcast (FR-DPDP-063, FR-DPDP-064; same permissions as
    the preview; ``Idempotency-Key``). Errors: 409 ``notice_not_current``, ``preview_invalid``,
    ``preview_expired``, ``preview_stale`` and the sending blocks."""
    return idem.run(
        db,
        body,
        lambda: service.whatsapp_send(db, ctx, body),
        headers=lambda out: {"Location": f"/api/v1/privacy/notices/{out.id}/delivery"},
    )


@router.get(
    "/privacy/notices/{notice_id}/delivery",
    response_model=NoticeDeliveryOut,
    responses=_UNAVAILABLE,
)
def notice_delivery(ctx: Reader, db: TenantDB, notice_id: uuid.UUID) -> NoticeDeliveryOut:
    """Who the privacy notice sent on WhatsApp reached: guardians by delivery status and the
    students to give a paper copy to (FR-DPDP-065, FR-DPDP-066; permission ``privacy.read``;
    names only of students you may see). 404 for a notice not sent on WhatsApp."""
    return service.notice_delivery(db, ctx, notice_id)
