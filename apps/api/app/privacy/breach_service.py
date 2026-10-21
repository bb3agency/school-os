"""The school's breach register (R3-1B; ADR-0050; US-2802..US-2804; FR-DPDP-032..038, PRV-008).

- **Two origins (FR-DPDP-032, FR-DPDP-033, FR-DPDP-038).** ``school``: a breach the school
  records itself (a lost register, a mis-sent message); ``processor_notice``: SchoolOS's notice,
  pulled from the control plane by ``app.privacy.breach_sync`` every minute on the shared
  platform (break-glass pattern: read through ``app.platform.service``, stored here with
  :func:`store_processor_notice`, reported back after commit; idempotent per incident; a
  control-plane outage only delays it), or recorded by hand on a dedicated host. Only
  ``breach_sync`` imports the control plane. The owner and principal get an urgent in-app
  notification.
- **Texts are C3 (invariant 5).** The description and containment steps are encrypted with the
  school's DEK (AAD ``ops.breach_records|<column>|<id>``; re-encrypted on key rotation) and never
  logged or audited; audit summaries carry ids, origins, codes, counts and field names only.
- **Duties and clocks (FR-DPDP-034, FR-DPDP-035).** Board initial notice (overdue from T0 + 24 h,
  a SchoolOS default), Board detailed report (T0 + 72 h), affected people notified (without
  delay); each records when and how. :func:`send_reminders` reminds the owner and principal at
  T0 + 24 h and T0 + 60 h while the detailed report is not recorded, once per breach and
  reminder (``ops.breach_reminders``).
- **Drafts, not sending (FR-DPDP-036, FR-DPDP-037).** The family notice and the Board outline
  are filled from the record and the school's privacy contact (``breach_templates.yaml``, the app
  copy of ``docs/legal/breach-runbook.md``). :func:`create_notice_draft` turns the family notice
  into an M4 parent notice draft (``circulars.service``; approval needed); the draft sent to the
  notice writer carries no description, phone number or e-mail address. SchoolOS never sends a
  breach notice by itself.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.circulars import service as circulars
from app.circulars.schemas import NoticeCreate
from app.core import purge as purging
from app.core.config import DeploymentMode, get_settings
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.identity import service as identity
from app.notifications import service as notifications
from app.privacy import breach_repository as repo
from app.privacy.breach_config import NOT_SET, BreachConfig, DraftKey, fill, load_config
from app.privacy.breach_models import BreachRecord
from app.privacy.breach_schemas import (
    AffectedCountsOut,
    AffectedIn,
    AffectedOut,
    BreachCreate,
    BreachDetailOut,
    BreachOut,
    BreachUpdate,
    DraftOut,
    DutyIn,
    DutyKey,
    DutyOut,
    NextDutyOut,
    NoticeDraftOut,
    ProcessorFactsOut,
)
from app.students import crypto
from app.students import rotation as key_rotation
from app.tenancy import service as tenancy

READ: Final = "privacy.breach.read"
MANAGE: Final = "privacy.breach.manage"
NOTICE_DRAFT: Final = "notice.draft"
TABLE: Final = "ops.breach_records"
DESCRIPTION: Final = "description_ciphertext"
CONTAINMENT: Final = "containment_ciphertext"
RESOURCE: Final = "breach_record"
IST: Final = ZoneInfo("Asia/Kolkata")
OWNERS = notifications.PermissionSelector(MANAGE)
_DUTIES: Final[tuple[DutyKey, ...]] = ("board_notice", "board_report", "people_notified")
_DUTY_COLUMNS: Final[dict[str, tuple[str, str]]] = {
    "board_notice": ("board_notice_done_at", "board_notice_method"),
    "board_report": ("board_report_done_at", "board_report_method"),
    "people_notified": ("people_notified_at", "people_notified_method"),
}

log = get_logger(__name__)


def settings() -> BreachConfig:
    return load_config()


def sweep_interval_seconds() -> int:
    return settings().sweep_interval_seconds


def control_plane_here() -> bool:
    """Notices live in the shared deployment's control plane; a dedicated host has none."""
    return get_settings().deployment_mode is DeploymentMode.SHARED


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _audit(
    session: Session, action: str, resource_id: uuid.UUID, summary: Mapping[str, Any]
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type=RESOURCE,
        resource_id=resource_id,
        summary=summary,
        request_id=request_id if isinstance(request_id, str) else None,
    )


@contextmanager
def _affected_errors() -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        message = str(exc.orig) if exc.orig is not None else str(exc)
        for column, field in (
            ("breach_affected_student_fk", "student_ids"),
            ("breach_affected_guardian_fk", "guardian_ids"),
            ("breach_affected_membership_fk", "membership_ids"),
        ):
            if column in message:
                raise ValidationFailed([_error(field, "not_found")]) from exc
        raise


# --- texts --------------------------------------------------------------------------------------


def _encrypt(
    session: Session, breach_id: uuid.UUID, column: str, value: str | None
) -> bytes | None:
    if not value:
        return None
    blob, _ = crypto.encrypt_value(session, value, table=TABLE, column=column, row_id=breach_id)
    return blob


def _decrypt(session: Session, row: BreachRecord, column: str) -> str | None:
    blob = getattr(row, column)
    if blob is None:
        return None
    try:
        return crypto.decrypt_value(session, bytes(blob), table=TABLE, column=column, row_id=row.id)
    except crypto.CryptoError:  # pragma: no cover - tampered or moved ciphertext
        log.error("privacy.breach_text_unreadable", resource_id=row.id, error_code="crypto")
        return None


def _refuse_aadhaar(values: Mapping[str, str | None]) -> None:
    bad = [k for k, v in values.items() if v and contains_full_aadhaar(v)]
    if bad:
        raise ValidationFailed(
            [_error(k, "aadhaar_full_number_rejected") for k in sorted(bad)],
            detail="Don't enter Aadhaar numbers. Enter only the last 4 digits.",
        )


# --- shaping and clocks -------------------------------------------------------------------------


def _due(row: BreachRecord, key: str) -> dt.datetime | None:
    clocks = settings().clocks
    if key == "board_notice":
        return row.noticed_at + dt.timedelta(hours=clocks.board_notice_hours)
    if key == "board_report":
        return row.noticed_at + dt.timedelta(hours=clocks.board_report_hours)
    return None


def _duties(row: BreachRecord, at: dt.datetime) -> list[DutyOut]:
    out = []
    for key in _DUTIES:
        done_col, method_col = _DUTY_COLUMNS[key]
        done_at = getattr(row, done_col)
        due = _due(row, key)
        out.append(
            DutyOut(
                key=key,
                due_at=due,
                done_at=done_at,
                method=getattr(row, method_col),
                overdue=done_at is None and due is not None and at > due,
                default_rule=key == "board_notice",
            )
        )
    return out


def _next_duty(row: BreachRecord, duties: Sequence[DutyOut], at: dt.datetime) -> NextDutyOut | None:
    if row.status == "closed":
        return None
    open_duties = [d for d in duties if d.done_at is None]
    if not open_duties:
        return None
    timed = sorted((d for d in open_duties if d.due_at is not None), key=lambda d: d.due_at or at)
    duty = timed[0] if timed else open_duties[0]
    hours = None
    if duty.due_at is not None:
        hours = math.floor((duty.due_at - at).total_seconds() / 3600)
    return NextDutyOut(key=duty.key, due_at=duty.due_at, hours_left=hours)


def _facts(row: BreachRecord) -> ProcessorFactsOut | None:
    return ProcessorFactsOut.model_validate(row.processor_facts) if row.processor_facts else None


def _base(row: BreachRecord, at: dt.datetime) -> dict[str, Any]:
    duties = _duties(row, at)
    return {
        "id": row.id,
        "origin": row.origin,
        "platform_incident_id": row.platform_incident_id,
        "noticed_at": row.noticed_at,
        "categories": list(row.categories),
        "approx_children": row.approx_children,
        "approx_guardians": row.approx_guardians,
        "approx_staff": row.approx_staff,
        "status": row.status,
        "duties": duties,
        "next_duty": _next_duty(row, duties, at),
        "processor_facts": _facts(row),
        "closed_at": row.closed_at,
        "created_at": row.created_at,
        "version": row.version,
    }


def _detail(session: Session, row: BreachRecord) -> BreachDetailOut:
    at = repo.now(session)
    counts = repo.affected_counts(session, [row.id]).get(row.id, (0, 0, 0))
    name = None
    if row.recorded_by is not None:
        member = identity.members_for_users(session, {row.recorded_by}).get(row.recorded_by)
        name = member[1] if member is not None else None
    return BreachDetailOut(
        **_base(row, at),
        description=_decrypt(session, row, DESCRIPTION),
        containment=_decrypt(session, row, CONTAINMENT),
        close_reason=row.close_reason,
        affected=AffectedCountsOut(students=counts[0], guardians=counts[1], staff=counts[2]),
        recorded_by_name=name,
    )


def _sort_key(item: BreachOut) -> tuple[int, float, float]:
    closed = item.status == "closed"
    due = item.next_duty.due_at if item.next_duty and item.next_duty.due_at else None
    return (
        1 if closed else 0,
        due.timestamp() if due is not None else math.inf,
        -item.noticed_at.timestamp(),
    )


# --- reads --------------------------------------------------------------------------------------


def list_breaches(session: Session) -> list[BreachOut]:
    """The register (``privacy.breach.read``): open breaches first, by the next duty's clock,
    then closed ones, newest first (FR-DPDP-034)."""
    at = repo.now(session)
    items = [BreachOut(**_base(r, at)) for r in repo.list_records(session)]
    return sorted(items, key=_sort_key)


def _get(session: Session, breach_id: uuid.UUID, *, lock: bool = False) -> BreachRecord:
    row = repo.get(session, breach_id, lock=lock)
    if row is None:
        raise NotFound("Breach not found")
    return row


def get_breach(session: Session, breach_id: uuid.UUID) -> BreachDetailOut:
    """One breach with its texts (decrypted for the reader), duties and affected counts
    (``privacy.breach.read``; another school's id is 404 through RLS)."""
    return _detail(session, _get(session, breach_id))


# --- writes -------------------------------------------------------------------------------------


def _check_time(session: Session, value: dt.datetime, field: str) -> None:
    if value.tzinfo is None:
        raise ValidationFailed([_error(field, "timezone_required")])
    if value > repo.now(session) + dt.timedelta(minutes=5):
        raise ValidationFailed([_error(field, "date_in_future")])


def create_breach(session: Session, ctx: UserContext, data: BreachCreate) -> BreachDetailOut:
    """Record a breach (FR-DPDP-033; ``privacy.breach.manage``, step-up). A ``processor_notice``
    by hand only where no control plane delivers it (422 ``pulled_automatically`` on the shared
    platform). Audit ``privacy.breach.recorded`` (origin, categories, counts; never texts)."""
    _check_time(session, data.noticed_at, "noticed_at")
    if data.origin == "processor_notice" and control_plane_here():
        raise ValidationFailed([_error("origin", "pulled_automatically")])
    _refuse_aadhaar({"description": data.description, "containment": data.containment})
    breach_id = new_id()
    row = repo.insert(
        session,
        id=breach_id,
        origin=data.origin,
        noticed_at=data.noticed_at,
        description_ciphertext=_encrypt(session, breach_id, DESCRIPTION, data.description),
        containment_ciphertext=_encrypt(session, breach_id, CONTAINMENT, data.containment),
        categories=list(data.categories),
        approx_children=data.approx_children,
        approx_guardians=data.approx_guardians,
        approx_staff=data.approx_staff,
        recorded_by=ctx.user_id,
    )
    _audit(
        session,
        "privacy.breach.recorded",
        row.id,
        {
            "origin": row.origin,
            "categories": sorted(row.categories),
            "approx_children": row.approx_children,
            "approx_guardians": row.approx_guardians,
            "approx_staff": row.approx_staff,
            "has_containment": data.containment is not None,
        },
    )
    log.info("privacy.breach.recorded", resource_type=RESOURCE, resource_id=row.id)
    return _detail(session, row)


def _duty_values(
    session: Session, row: BreachRecord, key: str, duty: DutyIn | None
) -> dict[str, Any]:
    if duty is None:
        return {}
    _check_time(session, duty.done_at, f"{key}.done_at")
    if duty.done_at < row.noticed_at - dt.timedelta(minutes=5):
        raise ValidationFailed([_error(f"{key}.done_at", "before_noticed")])
    done_col, method_col = _DUTY_COLUMNS[key]
    return {done_col: duty.done_at, method_col: duty.method}


def update_breach(  # noqa: PLR0912 - one branch per field
    session: Session,
    ctx: UserContext,
    breach_id: uuid.UUID,
    data: BreachUpdate,
    *,
    expected_version: int,
) -> BreachDetailOut:
    """Change facts or status, or record a duty as done (FR-DPDP-034; ``privacy.breach.manage``,
    step-up; ``If-Match``). A closed breach is final (409 ``breach_closed``); closing needs all
    three duties recorded or a ``close_reason`` (409 ``duties_open``). Audit
    ``privacy.breach.updated`` (field names, status, duties done; never texts)."""
    row = _get(session, breach_id, lock=True)
    if row.version != expected_version:
        raise PreconditionFailed("Someone else changed this breach. Reload it.")
    if row.status == "closed":
        raise Conflict("This breach is closed.", code="breach_closed")
    _refuse_aadhaar(
        {
            "description": data.description,
            "containment": data.containment,
            "close_reason": data.close_reason,
        }
    )
    values: dict[str, Any] = {}
    if data.description is not None:
        values[DESCRIPTION] = _encrypt(session, row.id, DESCRIPTION, data.description)
    if data.containment is not None:
        values[CONTAINMENT] = _encrypt(session, row.id, CONTAINMENT, data.containment)
    if data.categories is not None and sorted(data.categories) != sorted(row.categories):
        values["categories"] = list(data.categories)
    for field in ("approx_children", "approx_guardians", "approx_staff"):
        value = getattr(data, field)
        if value is not None and value != getattr(row, field):
            values[field] = value
    duties_done = []
    for key in _DUTIES:
        duty_values = _duty_values(session, row, key, getattr(data, key))
        if duty_values:
            duties_done.append(key)
            values.update(duty_values)
    if data.status is not None and data.status != row.status:
        values["status"] = data.status
        if data.status == "closed":
            pending = [
                k
                for k in _DUTIES
                if values.get(_DUTY_COLUMNS[k][0], getattr(row, _DUTY_COLUMNS[k][0])) is None
            ]
            if pending and data.close_reason is None:
                raise Conflict(
                    "Record the open duties, or give the reason they were not needed.",
                    code="duties_open",
                )
            values["closed_at"] = repo.now(session)
    if data.close_reason is not None:
        values["close_reason"] = data.close_reason
    if not values:
        return _detail(session, row)
    row = repo.update_record(session, breach_id, **values)
    _audit(
        session,
        "privacy.breach.updated",
        row.id,
        {
            "fields": sorted(
                "description" if k == DESCRIPTION else "containment" if k == CONTAINMENT else k
                for k in values
            ),
            "status": row.status,
            "duties_done": duties_done,
        },
    )
    return _detail(session, row)


def get_affected(session: Session, breach_id: uuid.UUID) -> AffectedOut:
    """The affected students, guardians and staff of a breach (ids only)."""
    _get(session, breach_id)
    rows = repo.affected(session, breach_id)
    return AffectedOut(
        breach_id=breach_id,
        student_ids=[r.student_id for r in rows if r.student_id is not None],
        guardian_ids=[r.guardian_id for r in rows if r.guardian_id is not None],
        membership_ids=[r.membership_id for r in rows if r.membership_id is not None],
    )


def set_affected(
    session: Session, ctx: UserContext, breach_id: uuid.UUID, data: AffectedIn
) -> AffectedOut:
    """Replace the list of affected people (FR-DPDP-033; ids of this school only: 422
    ``not_found`` per list otherwise; at most ``max_affected`` in all, 422 ``too_many``). Audit
    ``privacy.breach.affected_set`` (counts only)."""
    row = _get(session, breach_id, lock=True)
    if row.status == "closed":
        raise Conflict("This breach is closed.", code="breach_closed")
    total = len(data.student_ids) + len(data.guardian_ids) + len(data.membership_ids)
    if total > settings().max_affected:
        raise ValidationFailed([_error("student_ids", "too_many")])
    with _affected_errors():
        repo.replace_affected(
            session,
            breach_id,
            student_ids=data.student_ids,
            guardian_ids=data.guardian_ids,
            membership_ids=data.membership_ids,
        )
        session.flush()
    _audit(
        session,
        "privacy.breach.affected_set",
        breach_id,
        {
            "students": len(data.student_ids),
            "guardians": len(data.guardian_ids),
            "staff": len(data.membership_ids),
        },
    )
    return get_affected(session, breach_id)


# --- drafts (FR-DPDP-036, FR-DPDP-037) ----------------------------------------------------------


def _contact(session: Session) -> dict[str, str | None]:
    """The school's privacy contact (``tenant.settings.privacy_contact``, R3-1A); unset values
    print ``[not set]``."""
    stored = getattr(tenancy.get_tenant(session).settings, "privacy_contact", None)
    if stored is None:
        return {}
    data = stored if isinstance(stored, Mapping) else getattr(stored, "__dict__", {})
    return {
        "privacy_contact_name": data.get("name"),
        "privacy_contact_phone": data.get("phone"),
        "privacy_contact_email": data.get("email"),
    }


def _ist(value: dt.datetime) -> str:
    return value.astimezone(IST).strftime("%d/%m/%Y %H:%M IST")


def _values(session: Session, row: BreachRecord) -> dict[str, str | None]:
    cfg = settings()
    school = tenancy.get_tenant(session).name
    people = row.approx_children + row.approx_guardians + row.approx_staff
    return {
        "school_short_name": school,
        "school_legal_name": school,
        "noticed_at": _ist(row.noticed_at),
        "what_happened": _decrypt(session, row, DESCRIPTION),
        "data_categories": ", ".join(cfg.category_labels[c] for c in row.categories),
        "approx_people": str(people) if people else None,
        "what_we_did": _decrypt(session, row, CONTAINMENT),
        "reference": str(row.id)[:8],
        **_contact(session),
    }


def draft(session: Session, breach_id: uuid.UUID, key: DraftKey) -> DraftOut:
    """A draft filled from the record and the privacy contact (``privacy.breach.read``). Nothing
    is sent or stored."""
    row = _get(session, breach_id)
    cfg = settings()
    template = cfg.templates[key]
    values = _values(session, row)
    return DraftOut(
        draft=key,
        template_version=cfg.template_version,
        title=fill(template.title, values),
        text=fill(template.body, values),
    )


def _notice_text(session: Session, row: BreachRecord) -> str:
    """The family notice for the notice writer: no description, containment text, phone number
    or e-mail address (they could hold personal data); the person approving fills the gaps."""
    values = _values(session, row)
    values |= {
        "what_happened": "[describe what happened in plain words]",
        "likely_consequences": "[what this may mean for families]",
        "what_we_did": "[what the school has done]",
        "what_you_can_do": "[what families can do]",
        "privacy_contact_phone": "the school office",
        "privacy_contact_email": "in writing to the school",
    }
    if not values.get("privacy_contact_name"):
        values["privacy_contact_name"] = "the principal"
    template = settings().templates["family_notice"]
    return fill(template.title, values) + "\n\n" + fill(template.body, values)


def create_notice_draft(session: Session, ctx: UserContext, breach_id: uuid.UUID) -> NoticeDraftOut:
    """Turn the family notice into an M4 parent notice draft (FR-DPDP-037;
    ``privacy.breach.manage`` with step-up and ``notice.draft``, else 403). The notice is
    drafted, approved and sent like any notice; SchoolOS sends nothing by itself. Audit
    ``privacy.breach.notice_draft_created``."""
    if not ctx.has(NOTICE_DRAFT):
        raise Forbidden("You cannot draft parent notices.")
    row = _get(session, breach_id)
    text = _notice_text(session, row)
    if NOT_SET in text:
        text = text.replace(NOT_SET, "[to fill in]")
    notice = circulars.create_notice(
        session, ctx, NoticeCreate(source="staff_text", text=text[:4000])
    )
    _audit(
        session,
        "privacy.breach.notice_draft_created",
        breach_id,
        {"notice_id": notice.id},
    )
    return NoticeDraftOut(notice_id=notice.id, status=notice.status)


# --- SchoolOS notices (FR-DPDP-032; pulled by app.privacy.breach_sync) ------------------------


def store_processor_notice(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    severity: str,
    summary: str,
    opened_at: dt.datetime,
    confirmed_at: dt.datetime,
    categories: Sequence[str],
    approx_people: int,
    window_from: dt.datetime | None,
    window_to: dt.datetime | None,
) -> uuid.UUID | None:
    """Store one SchoolOS notice in the register (``origin = processor_notice``), once per
    incident: the new breach id, or None when the school already holds it. T0 is the time two
    operators confirmed the school was affected. Audit ``privacy.breach.notice_received`` (actor
    ``platform``; codes and counts) and an urgent notification to the owner and principal."""
    breach_id = new_id()
    created = repo.insert_notice_if_new(
        session,
        {
            "id": breach_id,
            "origin": "processor_notice",
            "platform_incident_id": incident_id,
            "noticed_at": confirmed_at,
            "description_ciphertext": _encrypt(session, breach_id, DESCRIPTION, summary),
            "categories": list(categories),
            "processor_facts": {
                "severity": severity,
                "approx_people": approx_people,
                "window_from": window_from.isoformat() if window_from else None,
                "window_to": window_to.isoformat() if window_to else None,
                "opened_at": opened_at.isoformat(),
            },
        },
    )
    if created is None:
        return None
    audit.record(
        session,
        action="privacy.breach.notice_received",
        resource_type=RESOURCE,
        resource_id=created,
        summary={
            "platform_incident_id": incident_id,
            "severity": severity,
            "categories": sorted(categories),
            "approx_people": approx_people,
        },
        actor_type="platform",
    )
    notifications.notify(
        session,
        tenant_id=tenant_id,
        recipients=OWNERS,
        template_key="privacy.breach.notice_received",
        params={"breach_id": created},
        resource_id=created,
        dedupe_key=f"privacy.breach.notice_received:{created}",
    )
    return created


# --- reminders (FR-DPDP-035) --------------------------------------------------------------------


def send_reminders(tenant_id: uuid.UUID, *, now: dt.datetime | None = None) -> int:
    """Remind the owner and principal at T0 + 24 h and T0 + 60 h while the detailed Board report
    is not recorded (once per breach and reminder). Returns how many reminders were sent."""
    clocks = settings().clocks.reminders
    sent = 0
    with tenant_session(tenant_id) as session:
        at = now or repo.now(session)
        rows = [
            r
            for r in repo.list_records(session, include_closed=False)
            if r.board_report_done_at is None
        ]
        done = repo.reminders_sent(session, [r.id for r in rows])
        for row in rows:
            for key, hours in (("board_24h", clocks.board_24h), ("report_60h", clocks.report_60h)):
                if (row.id, key) in done or at < row.noticed_at + dt.timedelta(hours=hours):
                    continue
                if not repo.insert_reminder_once(session, row.id, key):
                    continue
                notifications.notify(
                    session,
                    tenant_id=tenant_id,
                    recipients=OWNERS,
                    template_key="privacy.breach.reminder",
                    params={"breach_id": row.id, "hours": hours},
                    resource_id=row.id,
                    dedupe_key=f"privacy.breach.reminder:{row.id}:{key}",
                )
                sent += 1
    return sent


# --- DEK rotation (SEC-012) ---------------------------------------------------------------------


def reencrypt_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """Breach texts under another key version are re-encrypted to ``target_version`` with the
    same associated data."""
    done = 0
    for breach_id, column, blob in repo.stale_ciphertexts(session, target_version, limit):
        new_blob = crypto.reencrypt_value(
            session,
            blob,
            table=TABLE,
            column=column,
            row_id=breach_id,
            key_version=target_version,
            keyring=keyring,
        )
        repo.set_ciphertext(session, breach_id, column, new_blob)
        done += 1
    return done


key_rotation.register_reencryptor("privacy_breach", reencrypt_batch)


# --- full data export and offboarding -----------------------------------------------------------


def export_records(session: Session, *, include_sensitive: bool = False) -> list[RecordTable]:
    """Worker only: the breach register for the school's full data export (FR-DPDP-018;
    ciphertext left out)."""
    return repo.export_tables(session, include_sensitive=include_sensitive)


_PURGE = purging.PurgeTables(
    deleted=("ops.breach_reminders", "ops.breach_affected", "ops.breach_records")
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only; ADR-0029)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(
        name="privacy_breach", count=tenant_data_counts, purge=purge_tenant_data
    )
)

__all__ = [
    "MANAGE",
    "READ",
    "control_plane_here",
    "create_breach",
    "create_notice_draft",
    "draft",
    "export_records",
    "get_affected",
    "get_breach",
    "list_breaches",
    "purge_tenant_data",
    "reencrypt_batch",
    "send_reminders",
    "set_affected",
    "store_processor_notice",
    "sweep_interval_seconds",
    "tenant_data_counts",
    "update_breach",
]
