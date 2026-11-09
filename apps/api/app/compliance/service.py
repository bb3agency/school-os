"""School compliance certificate register (FC-01; FR-COMP-001..FR-COMP-030; docs/05 §16.9).

The SCHOOL's own certificates (fire NOC, building safety, recognition, affiliation, water and
sanitation): no personal data, no new data class.

- **Catalogue (FR-COMP-001, FR-COMP-002).** ``catalogue.yaml`` lists the kinds with a typical
  validity and the boards they apply to (``verified: false``). The register shows every kind
  that applies to the school's boards, ``missing`` until one is recorded, plus any ``other``
  certificate the school names itself. The school always types the real dates.
- **Renewal history (FR-COMP-004, FR-COMP-005).** Recording a kind the school already holds is
  its renewal: the old row becomes ``superseded`` (kept), the new one ``current``. A row
  recorded by mistake is ``removed``; when it was a renewal, the one it replaced is current
  again. At most one current row per catalogue kind (partial unique index).
- **Evidence (FR-COMP-006).** A copy in Documents the caller can see (through
  ``documents.service``); deleting the document leaves the row without a copy.
- **State (FR-COMP-010).** ``valid`` / ``expiring`` (within ``expiring_days``) / ``expired`` /
  ``no_expiry`` from the "valid until" date and today in IST (:func:`config.state`).
- **Reminders (FR-COMP-011..FR-COMP-013).** Daily, in-app only (staff, never parents): at the
  lead days before "valid until" and once it lapses (then every ``expired_every_days``), to the
  renewal owner and the holders of ``compliance.manage``. Idempotent per row and lead day.
- **CBSE disclosure (FR-COMP-020..FR-COMP-022).** :func:`disclosure_certificates` tells
  ``app.disclosure`` which current certificate backs each Appendix IX item, and its state.
- **Audit (FR-COMP-007).** ``compliance.certificate.recorded`` / ``.updated`` / ``.removed`` in
  the same transaction: ids, kind, dates and changed field names (never typed texts).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.compliance import config as cfg
from app.compliance import repository as repo
from app.compliance.models import ComplianceCertificate as Row
from app.compliance.schemas import (
    CertificateCreate,
    CertificateDetailOut,
    CertificateUpdate,
    ComplianceCertificateOut,
    ComplianceOwnerOut,
    DocumentRefOut,
    KindOut,
    RegisterCountsOut,
    RegisterOut,
    RegisterRowOut,
    RegisterSummaryOut,
    State,
)
from app.core import purge as purging
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.identity import service as identity
from app.notifications import service as notifications
from app.tenancy import service as tenancy

READ: Final = "compliance.read"
MANAGE: Final = "compliance.manage"
RESOURCE: Final = "compliance_certificate"
TEMPLATE: Final = "compliance.certificate.expiring"
IST: Final = ZoneInfo("Asia/Kolkata")
MANAGERS = notifications.PermissionSelector(MANAGE)
# A certificate the school named itself is announced without its typed name (no free text in
# notifications).
OTHER_NOTICE_LABEL: Final = "A certificate the school added"
EDITABLE: Final = (
    "label",
    "reference_no",
    "issued_by",
    "valid_from",
    "valid_until",
    "document_id",
    "owner_membership_id",
    "notes",
)

log = get_logger(__name__)


class CertificateExpired(ValidationFailed):
    """Publishing CBSE Appendix IX with an expired certificate when the catalogue says
    ``disclosure: refuse`` (FR-COMP-022)."""

    code = "certificate_expired"


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    row: Row,
    extra: Mapping[str, Any] | None = None,
    *,
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=RESOURCE,
        resource_id=row.id,
        summary={
            "kind": row.kind,
            "valid_until": row.valid_until.isoformat() if row.valid_until else None,
            "document_id": str(row.document_id) if row.document_id else None,
            **dict(extra or {}),
        },
        actor_type="system" if system else "user",
        request_id=None if system else _request_id(),
    )


def today(session: Session) -> dt.date:
    """Today in IST from the database clock (FR-COMP-010)."""
    return repo.now(session).astimezone(IST).date()


def _boards(session: Session) -> list[str]:
    boards, _ = tenancy.school_boards(session)
    return boards


def _kind_label(row: Row) -> str:
    if row.kind == cfg.OTHER and row.label:
        return row.label
    kind = cfg.catalogue().kinds.get(row.kind)
    return kind.label if kind is not None else row.kind


def _state(row: Row, day: dt.date) -> State:
    return cfg.state(row.valid_until, day, expiring_days=cfg.catalogue().expiring_days)


@dataclass(slots=True)
class _Lookups:
    """Names and document titles for a list of rows (one query each)."""

    owners: dict[uuid.UUID, str]
    active: set[uuid.UUID]
    recorders: dict[uuid.UUID, str]
    titles: dict[uuid.UUID, str | None]


def _lookups(session: Session, ctx: UserContext, rows: Iterable[Row]) -> _Lookups:
    rows = list(rows)
    owner_ids = {r.owner_membership_id for r in rows if r.owner_membership_id}
    owners = identity.member_display_names(session, owner_ids) if owner_ids else {}
    active = {m for m in owner_ids if identity.is_active_member(session, m)}
    recorder_ids = [r.recorded_by for r in rows]
    found = identity.members_for_users(session, recorder_ids) if recorder_ids else {}
    recorders = {u: name for u, (_m, name) in found.items()}
    doc_ids = [r.document_id for r in rows if r.document_id is not None]
    visible = documents.visible_document_ids(session, ctx, doc_ids) if doc_ids else set()
    titles: dict[uuid.UUID, str | None] = {}
    for doc_id in doc_ids:
        titles[doc_id] = (
            documents.get_document(session, ctx, doc_id).title if doc_id in visible else None
        )
    return _Lookups(owners=owners, active=active, recorders=recorders, titles=titles)


def _out(row: Row, day: dt.date, names: _Lookups) -> ComplianceCertificateOut:
    owner = None
    if row.owner_membership_id is not None:
        owner = ComplianceOwnerOut(
            membership_id=row.owner_membership_id,
            display_name=names.owners.get(row.owner_membership_id),
            active=row.owner_membership_id in names.active,
        )
    document = None
    if row.document_id is not None:
        document = DocumentRefOut(id=row.document_id, title=names.titles.get(row.document_id))
    return ComplianceCertificateOut(
        id=row.id,
        kind=row.kind,
        kind_label=_kind_label(row),
        label=row.label,
        reference_no=row.reference_no,
        issued_by=row.issued_by,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        state=_state(row, day),
        days_left=(row.valid_until - day).days if row.valid_until else None,
        document=document,
        owner=owner,
        notes=row.notes,
        status=row.status,
        superseded_by=row.superseded_by,
        recorded_by_name=names.recorders.get(row.recorded_by),
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )


# --- reads ----------------------------------------------------------------------------------


def _kinds_out(boards: list[str]) -> list[KindOut]:
    cat = cfg.catalogue()
    return [
        KindOut(
            kind=key,
            label=kind.label,
            authority=kind.authority,
            typical_validity_months=kind.typical_validity_months,
            note=kind.note,
            disclosure_items=list(kind.disclosure_items) if "CBSE" in boards else [],
            applies=key != cfg.OTHER and kind.applies_to(boards),
        )
        for key, kind in cat.kinds.items()
    ]


_ORDER: Final = {"expired": 0, "missing": 1, "expiring": 2, "valid": 3, "no_expiry": 4}


def register(session: Session, ctx: UserContext) -> RegisterOut:
    """The register (``compliance.read``; FR-COMP-003): every catalogue kind that applies to
    the school's boards (``missing`` until recorded), every current certificate, and counts.
    Most urgent first: expired, missing, expiring, valid, no expiry."""
    cat = cfg.catalogue()
    day = today(session)
    boards = _boards(session)
    expected = cat.kinds_for(boards)
    rows = repo.current_rows(session)
    names = _lookups(session, ctx, rows)
    out: list[RegisterRowOut] = []
    held = {r.kind for r in rows}
    for row in rows:
        cert = _out(row, day, names)
        out.append(
            RegisterRowOut(
                kind=row.kind, kind_label=cert.kind_label, state=cert.state, certificate=cert
            )
        )
    for key, kind in expected.items():
        if key not in held:
            out.append(
                RegisterRowOut(kind=key, kind_label=kind.label, state="missing", certificate=None)
            )
    keys = list(cat.kinds)
    out.sort(
        key=lambda r: (
            _ORDER[r.state],
            r.certificate.valid_until
            if r.certificate and r.certificate.valid_until
            else dt.date.max,
            keys.index(r.kind) if r.kind in keys else len(keys),
            r.kind_label,
        )
    )
    counts = {s: sum(1 for r in out if r.state == s) for s in _ORDER}
    return RegisterOut(
        rows=out,
        counts=RegisterCountsOut(**counts),
        kinds=_kinds_out(boards),
        expiring_days=cat.expiring_days,
        verified=cat.verified,
        today=day,
        owners=_owner_choices(session) if ctx.has(MANAGE) else [],
    )


def _owner_choices(session: Session) -> list[ComplianceOwnerOut]:
    return sorted(
        (
            ComplianceOwnerOut(
                membership_id=m.membership_id, display_name=m.display_name, active=True
            )
            for m in identity.active_members(session)
        ),
        key=lambda o: (o.display_name or "").casefold(),
    )


def summary(session: Session, ctx: UserContext) -> RegisterSummaryOut:
    """Counts only, for the school dashboard (``compliance.read``; FR-COMP-014)."""
    cat = cfg.catalogue()
    day = today(session)
    rows = repo.current_rows(session)
    states = [_state(r, day) for r in rows]
    held = {r.kind for r in rows}
    missing = sum(1 for k in cat.kinds_for(_boards(session)) if k not in held)
    return RegisterSummaryOut(
        expired=states.count("expired"), expiring=states.count("expiring"), missing=missing
    )


def _load(session: Session, certificate_id: uuid.UUID, *, lock: bool = False) -> Row:
    row = repo.get(session, certificate_id, lock=lock)
    if row is None:
        raise NotFound("Certificate not found")
    return row


def get_certificate(
    session: Session, ctx: UserContext, certificate_id: uuid.UUID
) -> CertificateDetailOut:
    """One certificate with the earlier ones of its kind (``compliance.read``; FR-COMP-005).
    Another school's: 404."""
    row = _load(session, certificate_id)
    older = repo.history(session, row)
    day = today(session)
    names = _lookups(session, ctx, [row, *older])
    base = _out(row, day, names)
    return CertificateDetailOut(**base.model_dump(), history=[_out(r, day, names) for r in older])


# --- writes ---------------------------------------------------------------------------------


def _check_texts(data: CertificateCreate | CertificateUpdate) -> list[dict[str, str]]:
    errors = []
    for field in ("label", "reference_no", "issued_by", "notes"):
        value = getattr(data, field, None)
        if value and contains_full_aadhaar(value):
            errors.append(_error(field, "aadhaar_full_number_rejected"))
    return errors


def _check_refs(
    session: Session, ctx: UserContext, data: CertificateCreate | CertificateUpdate
) -> list[dict[str, str]]:
    errors = []
    if data.document_id is not None and not (
        documents.is_visible(session, ctx, data.document_id)
        and documents.evidence_exists(session, data.document_id)
    ):
        errors.append(_error("document_id", "document_not_found"))
    if data.owner_membership_id is not None and not identity.is_active_member(
        session, data.owner_membership_id
    ):
        errors.append(_error("owner_membership_id", "owner_not_active"))
    return errors


def _check_label(kind: str, label: str | None) -> list[dict[str, str]]:
    if kind == cfg.OTHER and (label is None or len(label) < 3):
        return [_error("label", "label_required")]
    if kind != cfg.OTHER and label is not None:
        return [_error("label", "label_not_allowed")]
    return []


def _values(data: CertificateCreate | CertificateUpdate, kind: str) -> dict[str, Any]:
    return {
        "label": data.label if kind == cfg.OTHER else None,
        "reference_no": data.reference_no,
        "issued_by": data.issued_by,
        "valid_from": data.valid_from,
        "valid_until": data.valid_until,
        "document_id": data.document_id,
        "owner_membership_id": data.owner_membership_id,
        "notes": data.notes,
    }


def record(session: Session, ctx: UserContext, data: CertificateCreate) -> ComplianceCertificateOut:
    """Record a certificate (``compliance.manage``; FR-COMP-004). A catalogue kind the school
    already holds is renewed: the current one is kept as ``superseded``. For ``other``,
    ``renews_id`` names the one it renews. Errors (422): ``unknown_kind``, ``label_required``,
    ``label_not_allowed``, ``renews_not_allowed``, ``renews_not_found``, ``document_not_found``,
    ``owner_not_active``, ``aadhaar_full_number_rejected``; a renewal racing another: 409."""
    cat = cfg.catalogue()
    errors: list[dict[str, str]] = []
    if data.kind not in cat.kinds:
        errors.append(_error("kind", "unknown_kind"))
    errors += _check_label(data.kind, data.label)
    if data.renews_id is not None and data.kind != cfg.OTHER:
        errors.append(_error("renews_id", "renews_not_allowed"))
    errors += _check_texts(data)
    errors += _check_refs(session, ctx, data)
    previous: Row | None = None
    if not errors:
        if data.kind != cfg.OTHER:
            previous = repo.current_of_kind(session, data.kind, lock=True)
        elif data.renews_id is not None:
            previous = repo.get(session, data.renews_id, lock=True)
            if previous is None or previous.kind != cfg.OTHER or previous.status != repo.CURRENT:
                errors.append(_error("renews_id", "renews_not_found"))
    if errors:
        raise ValidationFailed(errors)
    row_id = new_id()
    try:
        with session.begin_nested():
            if previous is not None:
                # The old row leaves "current" first (one current row per kind); its pointer
                # to the renewal is checked at commit (deferred FK).
                repo.set_values(
                    session,
                    previous.id,
                    {
                        "status": "superseded",
                        "superseded_by": row_id,
                        "superseded_at": repo.now(session),
                    },
                )
            row = repo.insert(
                session,
                id=row_id,
                tenant_id=repo.current_tenant_id(session),
                kind=data.kind,
                recorded_by=ctx.user_id,
                **_values(data, data.kind),
            )
    except IntegrityError:
        raise Conflict(
            "Someone recorded this certificate at the same time. Reload and try again.",
            code="conflict",
        ) from None
    _audit(
        session,
        "compliance.certificate.recorded",
        row,
        {"renews_id": str(previous.id) if previous else None},
    )
    day = today(session)
    return _out(row, day, _lookups(session, ctx, [row]))


def _check_version(row: Row, expected_version: int) -> None:
    if row.version != expected_version:
        raise PreconditionFailed("The certificate was changed by someone else. Reload it.")


def _require_current(row: Row) -> None:
    if row.status != repo.CURRENT:
        raise Conflict(
            "Only the current certificate can be changed.", code="certificate_not_current"
        )


def update(
    session: Session,
    ctx: UserContext,
    certificate_id: uuid.UUID,
    data: CertificateUpdate,
    *,
    expected_version: int,
) -> ComplianceCertificateOut:
    """Correct the current certificate (``compliance.manage``, ``If-Match``; FR-COMP-008).
    Changing "valid until" restarts its reminders. Errors: 409 ``certificate_not_current``,
    412 stale ``If-Match``, 422 as :func:`record`."""
    row = _load(session, certificate_id, lock=True)
    _check_version(row, expected_version)
    _require_current(row)
    errors = [*_check_label(row.kind, data.label), *_check_texts(data)]
    errors += _check_refs(session, ctx, data)
    if errors:
        raise ValidationFailed(errors)
    values = _values(data, row.kind)
    changed = sorted(k for k, v in values.items() if getattr(row, k) != v)
    if "valid_until" in changed:
        values.update(last_reminded_lead=None, last_reminded_on=None)
    updated = repo.update_row(session, row, values, expected_version=expected_version)
    if updated is None:
        raise PreconditionFailed("The certificate was changed by someone else. Reload it.")
    _audit(session, "compliance.certificate.updated", updated, {"fields": changed})
    return _out(updated, today(session), _lookups(session, ctx, [updated]))


def remove(
    session: Session, ctx: UserContext, certificate_id: uuid.UUID, *, expected_version: int
) -> ComplianceCertificateOut:
    """Remove a certificate recorded by mistake (``compliance.manage``, ``If-Match``;
    FR-COMP-009). It stays in the history as ``removed``; when it renewed an earlier one, that
    one is current again. Errors: 409 ``certificate_not_current``, 412 stale ``If-Match``."""
    row = _load(session, certificate_id, lock=True)
    _check_version(row, expected_version)
    _require_current(row)
    previous = repo.predecessor(session, row.id)
    updated = repo.update_row(
        session,
        row,
        {"status": "removed", "removed_at": repo.now(session), "removed_by": ctx.user_id},
        expected_version=expected_version,
    )
    if updated is None:
        raise PreconditionFailed("The certificate was changed by someone else. Reload it.")
    if previous is not None:
        repo.set_values(
            session,
            previous.id,
            {"status": "current", "superseded_by": None, "superseded_at": None},
        )
    _audit(
        session,
        "compliance.certificate.removed",
        updated,
        {"restored_id": str(previous.id) if previous else None},
    )
    return _out(updated, today(session), _lookups(session, ctx, [updated]))


# --- reminders (FR-COMP-011..FR-COMP-013) -------------------------------------------------------


def _notice_label(row: Row) -> str:
    if row.kind == cfg.OTHER:
        return OTHER_NOTICE_LABEL
    kind = cfg.catalogue().kinds.get(row.kind)
    return kind.label if kind is not None else OTHER_NOTICE_LABEL


def send_reminders(tenant_id: uuid.UUID, *, day: dt.date | None = None) -> int:
    """Daily, one school: in-app reminders of certificates that lapse soon or have lapsed, to
    the renewal owner (when still an active member) and the holders of ``compliance.manage``.
    Idempotent per certificate and lead day (``last_reminded_lead``) and per recipient and day
    (dedupe key). Returns how many certificates were reminded."""
    rules = cfg.catalogue().reminders
    sent = 0
    with tenant_session(tenant_id) as session:
        on = day or today(session)
        for row in repo.due_for_reminder(session):
            due = cfg.due_reminder(
                row.valid_until,
                on,
                lead_days=rules.lead_days,
                expired_every_days=rules.expired_every_days,
                last_lead=row.last_reminded_lead,
                last_on=row.last_reminded_on,
            )
            if due is None:
                continue
            params = {
                "certificate": _notice_label(row),
                "days": due.days,
                "state": "expired" if due.expired else "expiring",
            }
            dedupe = f"{TEMPLATE}:{row.id}:{due.lead}:{on.isoformat()}"
            who: list[notifications.Recipients] = [MANAGERS]
            if row.owner_membership_id is not None and identity.is_active_member(
                session, row.owner_membership_id
            ):
                who.insert(0, [row.owner_membership_id])
            notified = sum(
                notifications.notify(
                    session,
                    tenant_id=tenant_id,
                    recipients=r,
                    template_key=TEMPLATE,
                    params=params,
                    resource_id=row.id,
                    dedupe_key=dedupe,
                )
                for r in who
            )
            repo.mark_reminded(session, row.id, due.lead, on)
            _audit(
                session,
                "compliance.certificate.reminded",
                row,
                {"lead_day": due.lead, "notified": notified},
                system=True,
            )
            sent += 1
    log.info("compliance.reminders_sent", count=sent)
    return sent


# --- CBSE Appendix IX (FR-COMP-020..FR-COMP-022) ------------------------------------------------


@dataclass(frozen=True, slots=True)
class DisclosureCertificate:
    """The current certificate the register holds for one Appendix IX item."""

    certificate_id: uuid.UUID
    kind: str
    kind_label: str
    document_id: uuid.UUID | None
    valid_until: dt.date | None
    state: State


def disclosure_certificates(session: Session) -> dict[str, list[DisclosureCertificate]]:
    """Appendix IX item key -> the current certificates of the kinds that back it (catalogue
    ``disclosure_items``; FR-COMP-020). Items with no current certificate are left out. No
    permission check: ``app.disclosure`` calls it for its own editors and publishers, and only
    school-level facts come back."""
    cat = cfg.catalogue()
    day = today(session)
    by_kind: dict[str, list[Row]] = {}
    for row in repo.current_rows(session):
        by_kind.setdefault(row.kind, []).append(row)
    out: dict[str, list[DisclosureCertificate]] = {}
    for key, kind in cat.kinds.items():
        for item in kind.disclosure_items:
            for row in by_kind.get(key, []):
                out.setdefault(item, []).append(
                    DisclosureCertificate(
                        certificate_id=row.id,
                        kind=row.kind,
                        kind_label=_kind_label(row),
                        document_id=row.document_id,
                        valid_until=row.valid_until,
                        state=_state(row, day),
                    )
                )
    return out


def disclosure_policy() -> cfg.DisclosurePolicy:
    """``warn`` or ``refuse`` when an Appendix IX item's certificate has expired (FR-COMP-022;
    lead decision D85)."""
    return cfg.catalogue().disclosure


def refuse_expired(items: Iterable[str]) -> None:
    """Raise 422 ``certificate_expired`` for the given Appendix IX items (FR-COMP-022)."""
    errors = [
        {
            "field": f"documents.{item}",
            "code": "certificate_expired",
            "message_key": "errors.certificate_expired",
        }
        for item in items
    ]
    if errors:
        raise CertificateExpired(errors, "A certificate on the disclosure has expired.")


# --- full export, offboarding --------------------------------------------------------------------


def export_records(session: Session) -> list[RecordTable]:
    """The school's register for its full data export (FR-COMP-016)."""
    return repo.export_tables(session)


_PURGE = purging.PurgeTables(deleted=("ops.compliance_certificates",))


def tenant_data_counts(session: Session) -> dict[str, int]:
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the school's register (offboarding only; ADR-0029)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="compliance", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "MANAGE",
    "READ",
    "CertificateExpired",
    "DisclosureCertificate",
    "disclosure_certificates",
    "disclosure_policy",
    "export_records",
    "get_certificate",
    "purge_tenant_data",
    "record",
    "refuse_expired",
    "register",
    "remove",
    "send_reminders",
    "summary",
    "tenant_data_counts",
    "today",
    "update",
]
