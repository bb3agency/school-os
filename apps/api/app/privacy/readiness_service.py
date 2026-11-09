"""DPDP readiness checklist, notice given at admission and the privacy notice sent on WhatsApp
(FC-03 items 1 and 3, FP-13; FR-DPDP-053..FR-DPDP-066; docs/08 §12, §16.3; ADR-0048).

**Not legal advice** (owner decision D30): every answer carries ``not_legal_advice: true`` and
every screen says so. The checklist shows what the school has recorded in SchoolOS; it never says
the school complies with the law.

- **Checklist (FR-DPDP-053..057).** Eight items computed from existing records: the privacy
  contact (``tenant.settings.privacy_contact``), the parents' and staff notices recorded with the
  current template version, the DPA signed, the DPIA done, one request closed end to end, the
  breach contact and phone tree, the retention settings reviewed this academic year, and the
  optional-purpose consent forms in use (not applicable without an active purpose). Four items
  have no other record in SchoolOS: the school records a date and optionally the evidence
  document on the checklist (``ops.privacy_readiness_marks``, append-only; the latest counts).
- **Notice given at admission (FR-DPDP-058..060).** Students added after the latest parents'
  notice was issued need their own record (date, channel, which notice issue); counts for the
  whole school, names only of students the reader may see (``student.read_basic`` reach).
- **Send on WhatsApp (FR-DPDP-061..066).** Makes an M4 parent notice from the parents' notice
  (short text from ``readiness.yaml``; drafted and approved through ``circulars.service``, so the
  caller needs ``notice.draft`` and ``notice.approve``), previews and confirms the broadcast
  through ``messaging.sends.service`` (``message.send`` school-wide; utility template
  ``school_notice``), and records the notice issue with channel ``sos_whatsapp``, the parent
  notice id and the broadcast id. The delivery view counts guardians by the furthest status and
  lists the students none of whose guardians got it, for a paper copy (names in reach only).
- **Audit and logs (invariants 5, 7).** Ids, item keys, channels and counts only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Mapping
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.circulars import service as circulars
from app.circulars.schemas import NoticeCreate, NoticeUpdate
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context
from app.documents import service as documents
from app.identity import service as identity
from app.messaging import service as messaging
from app.messaging.sends import service as sends
from app.messaging.sends.schemas import BroadcastConfirmIn, BroadcastPreviewIn
from app.privacy import config as cfg
from app.privacy import consents_service as consents
from app.privacy import readiness_repository as repo
from app.privacy import repository as privacy_repo
from app.privacy import service as privacy
from app.privacy.models import PrivacyNotice
from app.privacy.readiness_config import fill, load_config
from app.privacy.readiness_models import AdmissionNotice, ReadinessMark
from app.privacy.readiness_schemas import (
    AdmissionCountsOut,
    AdmissionNoticeIn,
    AdmissionNoticeOut,
    AdmissionPendingOut,
    AdmissionPendingRowOut,
    AdmissionRecordedOut,
    NoticeDeliveryOut,
    NotReachedRowOut,
    ReadinessChecklistItemOut,
    ReadinessMarkIn,
    ReadinessMarkOut,
    ReadinessOut,
    WhatsAppPreviewIn,
    WhatsAppPreviewOut,
    WhatsAppSendIn,
)
from app.privacy.schemas import PrivacyNoticeOut
from app.students import service as students
from app.tenancy import service as tenancy

READ: Final = privacy.READ
MANAGE: Final = privacy.MANAGE
MESSAGE_SEND: Final = sends.SEND
NOTICE_DRAFT: Final = "notice.draft"
NOTICE_APPROVE: Final = "notice.approve"
IST: Final = ZoneInfo("Asia/Kolkata")
PARENTS_KEY: Final = "notice_parents"
REACHED: Final = frozenset({"delivered", "read"})


def _invalid(field: str, code: str) -> ValidationFailed:
    return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.{code}"}])


def _audit(
    session: Session,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        request_id=request_id if isinstance(request_id, str) else None,
    )


# --- checklist (FR-DPDP-053..057) ---------------------------------------------------------------


def _mark_out(row: ReadinessMark, names: dict[uuid.UUID, str]) -> ReadinessMarkOut:
    return ReadinessMarkOut(
        item=cast(Any, row.item),
        done_on=row.done_on,
        document_id=row.document_id,
        recorded_by_name=names.get(row.recorded_by_membership),
        recorded_at=row.recorded_at,
    )


def _latest_parents_notice(session: Session) -> PrivacyNotice | None:
    rows = privacy_repo.list_notices(session)
    return next((n for n in rows if n.audience == "parents"), None)


def _added_since(session: Session, notice: PrivacyNotice | None) -> dict[uuid.UUID, dt.datetime]:
    if notice is None:
        return {}
    # Families admitted after the day the school-wide notice was issued.
    start = dt.datetime.combine(notice.issued_on + dt.timedelta(days=1), dt.time(), tzinfo=IST)
    return students.active_students_added_since(session, start)


def _admission_counts(session: Session) -> tuple[PrivacyNotice | None, list[uuid.UUID]]:
    notice = _latest_parents_notice(session)
    added = _added_since(session, notice)
    given = repo.admission_notices(session, list(added))
    return notice, [sid for sid in added if sid not in given]


def _year_start(session: Session, today: dt.date) -> dt.date:
    year = tenancy.get_current_academic_year(session)
    if year is not None:
        return year.starts_on
    return today - dt.timedelta(days=365)


def _notices_item(session: Session) -> tuple[str, dict[str, Any]]:
    issued = privacy_repo.list_notices(session)
    detail: dict[str, Any] = {}
    done = True
    for key in cfg.TEMPLATE_KEYS:
        template = cfg.notice_template(key)
        last = next((n for n in issued if n.audience == template.audience), None)
        detail[f"{template.audience}_current_version"] = template.template_version
        detail[f"{template.audience}_issued_version"] = last.template_version if last else None
        detail[f"{template.audience}_issued_on"] = last.issued_on.isoformat() if last else None
        if last is None or last.template_version != template.template_version:
            done = False
    return ("done" if done else "todo"), detail


def readiness(session: Session, ctx: UserContext) -> ReadinessOut:
    """The DPDP readiness checklist (FR-DPDP-053; ``privacy.read``). Not legal advice."""
    del ctx
    config = load_config()
    today = privacy.today(session)
    marks = repo.latest_marks(session)
    names = identity.member_display_names(
        session, {m.recorded_by_membership for m in marks.values()}
    )
    items: list[ReadinessChecklistItemOut] = []
    for item in config.items:
        detail: dict[str, Any] = {}
        mark = marks.get(item.key)
        status = "todo"
        if item.kind == "setting":
            values = privacy.school_values(session)
            has_name = bool(values.get("privacy_contact_name"))
            has_phone = bool(values.get("privacy_contact_phone"))
            has_email = bool(values.get("privacy_contact_email"))
            detail = {"has_name": has_name, "has_phone": has_phone, "has_email": has_email}
            status = "done" if has_name and (has_phone or has_email) else "todo"
        elif item.kind == "notices":
            status, detail = _notices_item(session)
        elif item.kind == "mark":
            if mark is not None:
                detail = {"done_on": mark.done_on.isoformat()}
                status = "done"
                if item.key == "retention_reviewed":
                    start = _year_start(session, today)
                    detail["year_starts_on"] = start.isoformat()
                    status = "done" if mark.done_on >= start else "todo"
        elif item.kind == "requests":
            closed = repo.closed_request_count(session)
            detail = {"closed_requests": closed}
            status = "done" if closed > 0 else "todo"
        else:  # consents
            purposes = [p for p in consents.list_purposes(session).data if p.active]
            answered = sum(1 for p in purposes if p.answered > 0)
            detail = {"active_purposes": len(purposes), "purposes_answered": answered}
            if not purposes:
                status = "not_applicable"
            else:
                status = "done" if answered == len(purposes) else "todo"
        items.append(
            ReadinessChecklistItemOut(
                key=item.key,
                status=cast(Any, status),
                detail=detail,
                mark=_mark_out(mark, names) if mark is not None else None,
                source=item.source,
            )
        )
    notice, pending = _admission_counts(session)
    added = _added_since(session, notice)
    return ReadinessOut(
        done=sum(1 for i in items if i.status == "done"),
        applicable=sum(1 for i in items if i.status != "not_applicable"),
        items=items,
        admission=AdmissionCountsOut(
            since=notice.issued_on if notice else None,
            added_since=len(added),
            without_notice=len(pending),
        ),
    )


def record_mark(session: Session, ctx: UserContext, data: ReadinessMarkIn) -> ReadinessMarkOut:
    """Record that the school did one readiness step (FR-DPDP-054; ``privacy.request.manage``).
    Append-only; the latest counts. Errors (422): ``date_in_future``, ``not_found``
    (document). Audit ``privacy.readiness.recorded`` (item, whether a document is attached)."""
    if data.done_on > privacy.today(session):
        raise _invalid("done_on", "date_in_future")
    if data.document_id is not None and not documents.evidence_exists(session, data.document_id):
        raise _invalid("document_id", "not_found")
    row = repo.insert_mark(
        session,
        {
            "id": new_id(),
            "tenant_id": ctx.tenant_id,
            "item": data.item,
            "done_on": data.done_on,
            "document_id": data.document_id,
            "recorded_by": ctx.user_id,
            "recorded_by_membership": ctx.membership_id,
        },
    )
    _audit(
        session,
        "privacy.readiness.recorded",
        "privacy_readiness_mark",
        row.id,
        {"item": data.item, "has_document": data.document_id is not None},
    )
    names = identity.member_display_names(session, {ctx.membership_id})
    return _mark_out(row, names)


# --- notice given at admission (FR-DPDP-058..060) ----------------------------------------------


def admission_pending(session: Session, ctx: UserContext) -> AdmissionPendingOut:
    """Students added after the latest parents' notice without their own notice record
    (FR-DPDP-059; ``privacy.read``): the count for the whole school, names only of those in the
    reader's ``student.read_basic`` reach (at most ``admission_list_limit``)."""
    notice, pending = _admission_counts(session)
    added = _added_since(session, notice)
    visible = students.summaries(session, ctx, pending)
    rows = [
        AdmissionPendingRowOut(
            student_id=sid,
            display_name=visible[sid].display_name,
            admission_no=visible[sid].admission_no,
            class_section=visible[sid].class_section,
            added_on=added[sid].astimezone(IST).date(),
        )
        for sid in pending
        if sid in visible
    ]
    return AdmissionPendingOut(
        since=notice.issued_on if notice else None,
        without_notice=len(pending),
        hidden=len(pending) - len(rows),
        items=rows[: load_config().admission_list_limit],
    )


def _admission_out(row: AdmissionNotice, names: dict[uuid.UUID, str]) -> AdmissionNoticeOut:
    return AdmissionNoticeOut(
        student_id=row.student_id,
        given_on=row.given_on,
        channel=cast(Any, row.channel),
        notice_id=row.notice_id,
        recorded_by_name=names.get(row.recorded_by_membership),
        recorded_at=row.recorded_at,
    )


def get_admission_notice(
    session: Session, ctx: UserContext, student_id: uuid.UUID
) -> AdmissionNoticeOut:
    """The notice record of one student (FR-DPDP-058; ``privacy.read``; 404 outside the
    reader's reach or without a record)."""
    students.ensure_in_scope(session, ctx, student_id)
    row = repo.admission_notices(session, [student_id]).get(student_id)
    if row is None:
        raise NotFound("No notice record for this student")
    names = identity.member_display_names(session, {row.recorded_by_membership})
    return _admission_out(row, names)


def record_admission_notices(
    session: Session, ctx: UserContext, data: AdmissionNoticeIn
) -> AdmissionRecordedOut:
    """Record that the families of these students got the privacy notice at admission
    (FR-DPDP-058, FR-DPDP-060; ``privacy.request.manage``). Every student must be in the
    caller's reach (404 otherwise); students with a record already are counted, not changed
    (append-only). ``notice_id`` defaults to the latest parents' notice issue. Errors (422):
    ``date_in_future``, ``no_parents_notice``, ``not_found`` (notice). Audit
    ``privacy.admission_notice.recorded`` (channel and counts)."""
    if data.given_on > privacy.today(session):
        raise _invalid("given_on", "date_in_future")
    wanted = list(dict.fromkeys(data.student_ids))
    for sid in wanted:
        students.ensure_in_scope(session, ctx, sid)
    if data.notice_id is not None:
        notice = repo.get_notice(session, data.notice_id)
        if notice is None or notice.audience != "parents":
            raise _invalid("notice_id", "not_found")
    else:
        notice = _latest_parents_notice(session)
        if notice is None:
            raise _invalid("notice_id", "no_parents_notice")
    existing = repo.admission_notices(session, wanted)
    new = [sid for sid in wanted if sid not in existing]
    repo.insert_admission_notices(
        session,
        [
            {
                "id": new_id(),
                "tenant_id": ctx.tenant_id,
                "student_id": sid,
                "given_on": data.given_on,
                "channel": data.channel,
                "notice_id": notice.id,
                "recorded_by": ctx.user_id,
                "recorded_by_membership": ctx.membership_id,
            }
            for sid in new
        ],
    )
    _audit(
        session,
        "privacy.admission_notice.recorded",
        "privacy_notice",
        notice.id,
        {"channel": data.channel, "recorded": len(new), "already_recorded": len(existing)},
    )
    return AdmissionRecordedOut(recorded=len(new), already_recorded=len(existing))


# --- send the privacy notice on WhatsApp (FR-DPDP-061..066) ------------------------------------


def _need_sender(ctx: UserContext) -> None:
    """The M4 path needs drafting and approving parent notices and sending school-wide."""
    if not (ctx.has(NOTICE_DRAFT) and ctx.has(NOTICE_APPROVE)):
        raise Forbidden()
    if not ctx.has(MESSAGE_SEND) or not ctx.scope_for(MESSAGE_SEND).school_wide:
        raise Forbidden()


def _whatsapp_text(session: Session) -> tuple[str, str, str]:
    """(template version, title, body) of the parent notice made from the parents' notice."""
    config = load_config().whatsapp_notice
    template = cfg.notice_template(PARENTS_KEY)
    school = privacy.school_values(session)
    values = {
        "school_name": tenancy.school_display_name(session),
        "privacy_contact_name": school.get("privacy_contact_name") or "the principal",
        "template_version": template.template_version,
    }
    title = fill(config.title_en, values)[:120]
    body = " ".join(fill(config.body_en, values).split())[:1500]
    return template.template_version, title, body


def _same_text(session: Session, ctx: UserContext, notice_id: uuid.UUID) -> bool:
    _, title, body = _whatsapp_text(session)
    try:
        out = circulars.get_notice(session, ctx, notice_id)
    except NotFound:
        return False
    return out.status == "approved" and out.title_en == title and out.body_en == body


def _make_parent_notice(session: Session, ctx: UserContext) -> uuid.UUID:
    _, title, body = _whatsapp_text(session)
    draft = circulars.create_notice(session, ctx, NoticeCreate(source="blank"))
    edited = circulars.update_notice(
        session, ctx, draft.id, NoticeUpdate(title_en=title, body_en=body), draft.version
    )
    approved = circulars.approve_notice(session, ctx, edited.id, edited.version)
    return approved.id


def _all_sections(session: Session) -> list[uuid.UUID]:
    year = tenancy.get_current_academic_year(session)
    if year is None:
        raise _invalid("section_ids", "no_current_year")
    sections = [s.id for s in tenancy.list_sections(session, academic_year_id=year.id)]
    if not sections:
        raise _invalid("section_ids", "no_sections")
    return sections


def whatsapp_preview(
    session: Session, ctx: UserContext, data: WhatsAppPreviewIn
) -> WhatsAppPreviewOut:
    """Make the parent notice from the parents' privacy notice (or reuse the one an earlier
    preview made) and preview its broadcast: recipients, guardians left out and the Meta cost
    (FR-DPDP-061, FR-DPDP-062; ``privacy.request.manage`` with ``notice.draft``,
    ``notice.approve`` and ``message.send`` school-wide: 403 otherwise). Nothing is sent. The
    parent notice is audited by M4 (``notice.drafted``, ``notice.approved``). Errors: 404
    ``module_unavailable`` (messaging off); 409 ``notice_not_current`` (a reused notice whose
    text is no longer the current one); 422 ``section_ids``."""
    _need_sender(ctx)
    messaging.ensure_available()
    version, title, body = _whatsapp_text(session)
    if data.parent_notice_id is not None:
        if not _same_text(session, ctx, data.parent_notice_id):
            raise Conflict(
                "The privacy notice text has changed. Preview again without the old notice.",
                code="notice_not_current",
            )
        notice_id = data.parent_notice_id
    else:
        notice_id = _make_parent_notice(session, ctx)
    sections = list(data.section_ids) if data.section_ids else _all_sections(session)
    preview = sends.preview_broadcast(
        session, ctx, notice_id, BroadcastPreviewIn(section_ids=sections)
    )
    return WhatsAppPreviewOut(
        parent_notice_id=notice_id,
        template_version=version,
        title=title,
        body=body,
        preview=preview,
    )


def whatsapp_send(session: Session, ctx: UserContext, data: WhatsAppSendIn) -> PrivacyNoticeOut:
    """Send the previewed parent notice and record the notice issue: channel ``sos_whatsapp``,
    the parents' template and version, today, the parent notice and the broadcast
    (FR-DPDP-063, FR-DPDP-064; same permissions as the preview; ``Idempotency-Key``). Errors:
    409 ``notice_not_current`` and every error of the broadcast confirmation
    (``preview_invalid``, ``preview_expired``, ``preview_stale``, ``messaging_paused``...).
    Audit ``privacy.notice.issued`` (channel, broadcast id) besides the broadcast's own."""
    _need_sender(ctx)
    messaging.ensure_available()
    if not _same_text(session, ctx, data.parent_notice_id):
        raise Conflict(
            "The privacy notice text has changed. Preview again.", code="notice_not_current"
        )
    broadcast = sends.confirm_broadcast(
        session,
        ctx,
        data.parent_notice_id,
        BroadcastConfirmIn(preview_token=data.preview_token, section_ids=data.section_ids),
    )
    template = cfg.notice_template(PARENTS_KEY)
    row = privacy_repo.insert_notice(
        session,
        {
            "id": new_id(),
            "tenant_id": ctx.tenant_id,
            "template_key": PARENTS_KEY,
            "template_version": template.template_version,
            "audience": "parents",
            "language": "en",
            "issued_on": privacy.today(session),
            "channel": "sos_whatsapp",
            "document_id": None,
            "parent_notice_id": data.parent_notice_id,
            "broadcast_id": broadcast.id,
            "recorded_by": ctx.user_id,
            "recorded_by_membership": ctx.membership_id,
        },
    )
    _audit(
        session,
        "privacy.notice.issued",
        "privacy_notice",
        row.id,
        {
            "template_key": PARENTS_KEY,
            "template_version": template.template_version,
            "audience": "parents",
            "channel": "sos_whatsapp",
            "has_document": False,
            "broadcast_id": broadcast.id,
            "parent_notice_id": data.parent_notice_id,
            "queued": broadcast.queued,
        },
    )
    return privacy.notice_out(session, row)


def notice_delivery(session: Session, ctx: UserContext, notice_id: uuid.UUID) -> NoticeDeliveryOut:
    """Who the privacy notice sent on WhatsApp reached (FR-DPDP-065, FR-DPDP-066;
    ``privacy.read``): guardians by the furthest status their message reached, and the students
    none of whose guardians got it delivered or read, for a paper copy. Counts for the whole
    broadcast; names only of students in the reader's ``student.read_basic`` reach. 404 for a
    notice record not sent on WhatsApp."""
    row = repo.get_notice(session, notice_id)
    if row is None or row.broadcast_id is None:
        raise NotFound("Notice not found")
    delivery = sends.broadcast_delivery(session, row.broadcast_id)
    if delivery is None:
        raise NotFound("Broadcast not found")
    guardians: Counter[str] = Counter(delivery.guardian_status.values())
    guardians["left_out"] = delivery.left_out
    reached = {g for g, s in delivery.guardian_status.items() if s in REACHED}
    year = tenancy.get_current_academic_year(session)
    enrolled = (
        students.enrolled_in_sections(
            session, academic_year_id=year.id, section_ids=delivery.section_ids
        )
        if year is not None
        else []
    )
    student_ids = list(dict.fromkeys(e.student_id for e in enrolled))
    families: dict[uuid.UUID, set[uuid.UUID]] = {sid: set() for sid in student_ids}
    for brief in students.guardians_of_students(session, student_ids):
        families.setdefault(brief.student_id, set()).add(brief.guardian_id)
    missed = [sid for sid in student_ids if not (families.get(sid, set()) & reached)]
    visible = students.summaries(session, ctx, missed)
    rows = [
        NotReachedRowOut(
            student_id=sid,
            display_name=visible[sid].display_name,
            admission_no=visible[sid].admission_no,
            class_section=visible[sid].class_section,
        )
        for sid in missed
        if sid in visible
    ]
    rows.sort(key=lambda r: (r.class_section or "", r.display_name or ""))
    return NoticeDeliveryOut(
        notice=privacy.notice_out(session, row),
        broadcast_id=row.broadcast_id,
        guardians=dict(guardians),
        students=len(student_ids),
        students_not_reached=len(missed),
        hidden=len(missed) - len(rows),
        not_reached=rows,
    )


__all__ = [
    "admission_pending",
    "get_admission_notice",
    "notice_delivery",
    "readiness",
    "record_admission_notices",
    "record_mark",
    "whatsapp_preview",
    "whatsapp_send",
]
