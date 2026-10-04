"""Tally connector: enrolment, devices, catalog, snapshots, party links and fee dues (M6;
ADR-0032 Proposed; FR-TALLY-001..010; docs/05 §7.4, docs/09 Tally connector).

Everything is behind the per-school flag ``tally.connector.enabled`` (``config.yaml``; unknown
flag = off): with the flag off every school route answers 404 and :func:`fee_tool_available`
is false, so ``get_fee_dues`` is not offered to the model.

- **Owner** (``tally.device.manage``, step-up): one-time enrolment codes (stored as SHA-256),
  device list, revocation (keys erased at once).
- **Accountant, principal, owner** (``tally.configure``): which ledger groups the agent sends,
  and party <-> student links. SchoolOS suggests candidate students; a person links (the AI never
  does, invariant 9).
- **Finance readers** (``finance.read``, school-wide grants only): connector status with totals,
  the fee dues list, and the read functions behind ``get_fee_dues``: only parties LINKED to a
  student the caller can see are read, filtered in SQL (invariant 8).
- **Agent** (device-signed, :mod:`app.tally.agent_auth`): enrolment, configuration, catalog,
  idempotent snapshots (``UNIQUE (device, batch)``: a repeat is answered from the stored result),
  key rotation.

Audit in the same transaction with IDs and counts only; logs carry IDs, counts and codes, never
ledger names or amounts (invariant 5). Ledger names and balances are C2 personal data with the
``finance.read`` restriction (ADR-0032 §5).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.core import feature_flags
from app.core import purge as purging
from app.core.crypto import KeyWrapper
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    PreconditionFailed,
    Unauthenticated,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.records import RecordTable
from app.notifications import service as notifications
from app.students import service as students
from app.students.schemas import SearchFilters, StudentSummary
from app.tally import repository as repo
from app.tally.agent_auth import AgentCaller, EnrolmentCaller
from app.tally.config import rules, version_tuple
from app.tally.models import Device, Group, Party
from app.tally.schemas import (
    MAX_AMOUNT,
    AgentConfigOut,
    CatalogIn,
    CatalogOut,
    ConnectorStatus,
    DeviceOut,
    DuesPage,
    DuesTotals,
    EnrolIn,
    EnrolmentCodeCreate,
    EnrolmentCodeOut,
    EnrolOut,
    GroupOut,
    GroupSelectionIn,
    KeyRotationOut,
    LinkedStudentOut,
    LinkFilter,
    PartyDetail,
    PartyOut,
    StudentDuesOut,
    SyncIn,
    SyncOut,
)
from app.tenancy import service as tenancy

log = get_logger(__name__)

READ: Final = "finance.read"
DEVICE_MANAGE: Final = "tally.device.manage"
CONFIGURE: Final = "tally.configure"
SILENT_TEMPLATE: Final = "tally.agent_silent"

CODE_ALPHABET: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""32 unambiguous characters (no I, O, 0, 1): 12 of them are 60 bits."""
CODE_LENGTH: Final = 12
KEY_ID_ALPHABET: Final = "abcdefghijklmnopqrstuvwxyz"
"""Letters only: key ids appear in audit summaries, which reject long digit runs."""
SECRET_BYTES: Final = 32
ZERO: Final = Decimal("0.00")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --- flag and permissions -------------------------------------------------------------------------


def connector_enabled(session: Session, tenant_id: uuid.UUID) -> bool:
    """The school's ``tally.connector.enabled`` flag (default off, ADR-0032 §8)."""
    return feature_flags.is_enabled(rules().flag, tenant_id, session=session)


def _require_on(session: Session, ctx: UserContext) -> None:
    if not connector_enabled(session, ctx.tenant_id):
        raise NotFound()


def can_read_fees(ctx: UserContext) -> bool:
    """``finance.read`` held school-wide (scoped finance grants see nothing, ADR-0032 §5)."""
    return ctx.has(READ) and ctx.scope_for(READ).school_wide


def fee_tool_available(session: Session, ctx: UserContext) -> bool:
    """Whether ``get_fee_dues`` may be offered to this caller now (permission AND flag)."""
    return can_read_fees(ctx) and connector_enabled(session, ctx.tenant_id)


# --- codes and keys -------------------------------------------------------------------------------


def normalise_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def code_hash(code: str) -> bytes:
    return hashlib.sha256(normalise_code(code).encode("ascii")).digest()


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _grouped(code: str) -> str:
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


def _new_key(tenant_id: uuid.UUID, wrapper: KeyWrapper) -> tuple[str, bytes, str]:
    """(key id, wrapped secret, secret as base64url). The plaintext is returned once."""
    raw = secrets.token_bytes(SECRET_BYTES)
    key_id = "tdk-" + "".join(secrets.choice(KEY_ID_ALPHABET) for _ in range(20))
    wrapped = wrapper.wrap(raw, tenant_id=tenant_id)
    return key_id, wrapped, base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


# --- owner: enrolment codes and devices -----------------------------------------------------------


def create_enrolment_code(
    session: Session, ctx: UserContext, data: EnrolmentCodeCreate
) -> EnrolmentCodeOut:
    """A one-time code for ``sos-tally-agent enrol`` (FR-TALLY-001). Shown once; only its hash is
    stored; valid ``code_ttl_minutes``. 409 ``too_many_devices`` when the school already has the
    maximum of active agents."""
    _require_on(session, ctx)
    cfg = rules().enrolment
    if repo.count_active_devices(session) >= cfg.max_active_devices:
        raise Conflict(
            f"At most {cfg.max_active_devices} Tally agents can be active. Revoke one first.",
            code="too_many_devices",
        )
    code = _new_code()
    now = _now()
    row = repo.insert_code(
        session,
        {
            "id": new_id(),
            "code_hash": code_hash(code),
            "device_name": data.device_name,
            "created_by": ctx.user_id,
            "created_at": now,
            "expires_at": now + dt.timedelta(minutes=cfg.code_ttl_minutes),
        },
    )
    audit.record(
        session,
        action="tally.enrolment_code.created",
        resource_type="tally_enrolment_code",
        resource_id=row.id,
        summary={"valid_minutes": cfg.code_ttl_minutes},
    )
    return EnrolmentCodeOut(
        id=row.id, code=_grouped(code), device_name=row.device_name, expires_at=row.expires_at
    )


def _silent_before(now: dt.datetime) -> dt.datetime:
    return now - dt.timedelta(hours=rules().silence.notify_after_hours)


def _outdated(version: str | None) -> bool:
    if version is None:
        return False
    return version_tuple(version) < version_tuple(rules().sync.min_agent_version)


def _device_out(device: Device, now: dt.datetime) -> DeviceOut:
    last = device.last_seen_at or device.enrolled_at
    return DeviceOut(
        id=device.id,
        name=device.name,
        status=device.status,
        agent_version=device.agent_version,
        platform=device.platform,
        tally_product=device.tally_product,
        enrolled_at=device.enrolled_at,
        revoked_at=device.revoked_at,
        last_seen_at=device.last_seen_at,
        last_sync_at=device.last_sync_at,
        silent=device.status == "active" and last < _silent_before(now),
        outdated=device.status == "active" and _outdated(device.agent_version),
        version=device.version,
    )


def list_devices(session: Session, ctx: UserContext) -> list[DeviceOut]:
    _require_on(session, ctx)
    now = _now()
    return [_device_out(d, now) for d in repo.list_devices(session)]


def revoke_device(
    session: Session, ctx: UserContext, device_id: uuid.UUID, expected_version: int
) -> DeviceOut:
    """Revoke an agent (FR-TALLY-002): both keys are erased at once; synced data stays."""
    _require_on(session, ctx)
    device = repo.get_device(session, device_id, lock=True)
    if device is None:
        raise NotFound("Tally agent not found")
    if device.version != expected_version:
        raise PreconditionFailed()
    if device.status == "revoked":
        raise Conflict("This Tally agent is already revoked.", code="already_revoked")
    now = _now()
    device = repo.update_device(
        session,
        device_id,
        {
            "status": "revoked",
            "key_id": None,
            "key_ciphertext": None,
            "next_key_id": None,
            "next_key_ciphertext": None,
            "rotation_started_at": None,
            "revoked_by": ctx.user_id,
            "revoked_at": now,
        },
    )
    audit.record(
        session,
        action="tally.device.revoked",
        resource_type="tally_device",
        resource_id=device_id,
        summary={},
    )
    log.info("tally.device.revoked", resource_type="tally_device", resource_id=device_id)
    return _device_out(device, now)


# --- status ---------------------------------------------------------------------------------------


def status(session: Session, ctx: UserContext) -> ConnectorStatus:
    """The connector at a glance; money totals only for school-wide ``finance.read``."""
    _require_on(session, ctx)
    now = _now()
    devices = [d for d in repo.list_devices(session) if d.status == "active"]
    selected = repo.selected_groups(session)
    counts = repo.party_counts(session)
    latest = repo.latest_sync(session)
    fees = can_read_fees(ctx)
    total: Decimal | None = None
    if fees:
        total = sum(
            (t for t in _student_totals(repo.linked_balances(session, None)).values() if t > 0),
            ZERO,
        )
    return ConnectorStatus(
        devices_active=len(devices),
        silent=any(_device_out(d, now).silent for d in devices),
        last_sync_at=latest.received_at if latest else None,
        as_of=counts.as_of,
        company=selected[0].company if selected else repo.latest_company(session),
        groups_selected=len(selected),
        parties=counts.parties,
        parties_linked=counts.linked,
        parties_unlinked=counts.unlinked,
        total_due=total,
        unlinked_due=counts.unlinked_due if fees else None,
    )


# --- groups ---------------------------------------------------------------------------------------


def _group_out(group: Group) -> GroupOut:
    return GroupOut(
        id=group.id,
        company=group.company,
        name=group.name,
        parent=group.parent,
        present=group.present,
        selected=group.selected,
        version=group.version,
    )


def list_groups(session: Session, ctx: UserContext) -> list[GroupOut]:
    """The ledger groups the agent reported (names only), selected ones marked."""
    _require_on(session, ctx)
    return [_group_out(g) for g in repo.list_groups(session)]


def _field_error(field: str, code: str) -> ValidationFailed:
    return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.{code}"}])


def select_groups(session: Session, ctx: UserContext, data: GroupSelectionIn) -> list[GroupOut]:
    """Choose the groups whose party ledgers the agent may send (FR-TALLY-004). Replaces the
    selection; all groups must be present groups of ``company``."""
    _require_on(session, ctx)
    wanted = set(data.group_ids)
    groups = {g.id: g for g in repo.list_groups(session)}
    for group_id in wanted:
        group = groups.get(group_id)
        if group is None or group.company != data.company or not group.present:
            raise _field_error("group_ids", "unknown_group")
    repo.set_selection(session, data.company, wanted, ctx.user_id, _now())
    audit.record(
        session,
        action="tally.groups.selected",
        resource_type="tally_group",
        summary={"group_ids": sorted(str(g) for g in wanted), "count": len(wanted)},
    )
    return [_group_out(g) for g in repo.list_groups(session)]


# --- parties and links ----------------------------------------------------------------------------


def _linked_out(summary: StudentSummary) -> LinkedStudentOut:
    return LinkedStudentOut(
        student_id=summary.id,
        display_name=summary.display_name,
        admission_no=summary.admission_no,
        class_section=summary.class_section,
    )


def _party_outs(session: Session, ctx: UserContext, parties: Sequence[Party]) -> list[PartyOut]:
    links = repo.links_of(session, [p.id for p in parties])
    everyone = {sid for ids in links.values() for sid in ids}
    visible = students.summaries(session, ctx, everyone)
    return [
        PartyOut(
            id=p.id,
            ledger_name=p.ledger_name,
            group_name=p.group_name,
            closing_balance=p.closing_balance,
            as_of=p.as_of,
            present=p.present,
            links=[_linked_out(visible[s]) for s in links.get(p.id, []) if s in visible],
        )
        for p in parties
    ]


def list_parties(
    session: Session,
    ctx: UserContext,
    *,
    link: LinkFilter = "all",
    query: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[PartyOut]:
    """Ledgers of the last snapshot by name (mapping screen, FR-TALLY-006)."""
    _require_on(session, ctx)
    after = None
    raw = decode_cursor(cursor)
    if raw is not None:
        try:
            after = (str(raw["n"]), uuid.UUID(str(raw["i"])))
        except (KeyError, ValueError):
            raise _field_error("cursor", "invalid") from None
    rows = repo.list_parties(
        session,
        repo.PartyFilter(
            linked={"all": None, "linked": True, "unlinked": False}[link],
            query=(query or "").strip()[:100] or None,
            after=after,
            limit=limit,
        ),
    )
    page = rows[:limit]
    more = len(rows) > limit
    next_cursor = (
        encode_cursor({"n": page[-1].ledger_name, "i": str(page[-1].id)}) if more and page else None
    )
    return Page[PartyOut](data=_party_outs(session, ctx, page), next_cursor=next_cursor)


def get_party(session: Session, ctx: UserContext, party_id: uuid.UUID) -> PartyDetail:
    """One ledger with its links and suggested students (suggestions only; a person links)."""
    _require_on(session, ctx)
    party = repo.get_party(session, party_id)
    if party is None:
        raise NotFound("Ledger not found")
    out = _party_outs(session, ctx, [party])[0]
    linked = {link.student_id for link in out.links}
    candidates: list[LinkedStudentOut] = []
    limit = rules().mapping.max_candidates
    if limit:
        try:
            found = students.search(
                session, ctx, SearchFilters(query=party.ledger_name[:100]), limit=limit
            )
            candidates = [_linked_out(s) for s in found.data if s.id not in linked]
        except DomainError:
            candidates = []
    return PartyDetail(**out.model_dump(), candidates=candidates)


def link_party(
    session: Session, ctx: UserContext, party_id: uuid.UUID, student_id: uuid.UUID
) -> PartyOut:
    """Link a ledger to a student (FR-TALLY-006). Idempotent; audited."""
    _require_on(session, ctx)
    party = repo.get_party(session, party_id, lock=True)
    if party is None:
        raise NotFound("Ledger not found")
    if not party.present:
        raise Conflict("This ledger is no longer in Tally's last snapshot.", code="party_gone")
    if student_id not in students.summaries(session, ctx, [student_id]):
        raise _field_error("student_id", "not_found")
    if repo.insert_link(
        session,
        {"id": new_id(), "party_id": party_id, "student_id": student_id, "linked_by": ctx.user_id},
    ):
        audit.record(
            session,
            action="tally.party.linked",
            resource_type="tally_party",
            resource_id=party_id,
            summary={"student_id": student_id},
        )
    return _party_outs(session, ctx, [party])[0]


def unlink_party(
    session: Session, ctx: UserContext, party_id: uuid.UUID, student_id: uuid.UUID
) -> None:
    _require_on(session, ctx)
    if repo.get_party(session, party_id) is None or not repo.delete_link(
        session, party_id, student_id
    ):
        raise NotFound("Link not found")
    audit.record(
        session,
        action="tally.party.unlinked",
        resource_type="tally_party",
        resource_id=party_id,
        summary={"student_id": student_id},
    )


# --- fee dues (screen and get_fee_dues) -----------------------------------------------------------


def _student_totals(balances: Iterable[repo.LinkedBalance]) -> dict[uuid.UUID, Decimal]:
    totals: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for b in balances:
        if b.present:
            totals[b.student_id] += b.closing_balance
    return dict(totals)


def list_dues(
    session: Session, ctx: UserContext, *, limit: int = 50, cursor: str | None = None
) -> DuesPage:
    """Students with dues (sum of their linked ledgers' positive total), highest first, and the
    school totals (FR-TALLY-007). Only students the caller can see are listed."""
    _require_on(session, ctx)
    if not can_read_fees(ctx):
        raise Forbidden()
    balances = [b for b in repo.linked_balances(session, None) if b.present]
    totals = _student_totals(balances)
    visible = students.summaries(session, ctx, totals)
    ledgers: dict[uuid.UUID, int] = defaultdict(int)
    as_of: dict[uuid.UUID, dt.date] = {}
    for b in balances:
        ledgers[b.student_id] += 1
        as_of[b.student_id] = max(as_of.get(b.student_id, b.as_of), b.as_of)
    rows = sorted(
        (
            StudentDuesOut(
                student_id=sid,
                display_name=visible[sid].display_name,
                admission_no=visible[sid].admission_no,
                class_section=visible[sid].class_section,
                total_due=total,
                ledgers=ledgers[sid],
                as_of=as_of[sid],
            )
            for sid, total in totals.items()
            if total > 0 and sid in visible
        ),
        key=lambda r: (-r.total_due, r.display_name or "", str(r.student_id)),
    )
    raw = decode_cursor(cursor) or {}
    offset = raw.get("o", 0)
    if not isinstance(offset, int) or offset < 0:
        raise _field_error("cursor", "invalid")
    page = rows[offset : offset + limit]
    counts = repo.party_counts(session)
    return DuesPage(
        data=page,
        next_cursor=encode_cursor({"o": offset + limit}) if offset + limit < len(rows) else None,
        totals=DuesTotals(
            students_with_dues=len(rows),
            total_due=sum((r.total_due for r in rows), ZERO),
            unlinked_parties=counts.unlinked,
            unlinked_due=counts.unlinked_due,
            as_of=counts.as_of,
        ),
    )


@dataclass(frozen=True, slots=True)
class LedgerDue:
    ledger_name: str
    group_name: str
    closing_balance: Decimal
    as_of: dt.date


@dataclass(frozen=True, slots=True)
class StudentFeeDues:
    """What ``get_fee_dues`` may tell the model about ONE student (linked ledgers only)."""

    student_id: uuid.UUID
    display_name: str | None
    admission_no: str | None
    class_section: str | None
    ledgers: tuple[LedgerDue, ...]
    total_due: Decimal
    as_of: dt.date | None
    synced_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class FeeSummary:
    """Numbers only (no names): the school view of ``get_fee_dues`` without a student."""

    students_with_dues: int
    total_due: Decimal
    unlinked_parties: int
    as_of: dt.date | None
    synced_at: dt.datetime | None


def _tool_guard(session: Session, ctx: UserContext) -> None:
    if not can_read_fees(ctx):
        raise Forbidden()
    _require_on(session, ctx)


def student_fee_dues(session: Session, ctx: UserContext, student_id: uuid.UUID) -> StudentFeeDues:
    """ONE student's linked ledgers (for ``get_fee_dues``). 404 outside the caller's student
    scope; an unlinked ledger is never read (the SQL selects links of this student only)."""
    _tool_guard(session, ctx)
    summary = students.summaries(session, ctx, [student_id]).get(student_id)
    if summary is None:
        raise NotFound("Student not found")
    present = [b for b in repo.linked_balances(session, [student_id]) if b.present]
    return StudentFeeDues(
        student_id=student_id,
        display_name=summary.display_name,
        admission_no=summary.admission_no,
        class_section=summary.class_section,
        ledgers=tuple(
            LedgerDue(b.ledger_name, b.group_name, b.closing_balance, b.as_of) for b in present
        ),
        total_due=sum((b.closing_balance for b in present), ZERO),
        as_of=max((b.as_of for b in present), default=None),
        synced_at=max((b.synced_at for b in present), default=None),
    )


def fee_summary(session: Session, ctx: UserContext) -> FeeSummary:
    """School totals over linked ledgers (for ``get_fee_dues`` without a student)."""
    _tool_guard(session, ctx)
    totals = _student_totals(repo.linked_balances(session, None))
    counts = repo.party_counts(session)
    latest = repo.latest_sync(session)
    due = [t for t in totals.values() if t > 0]
    return FeeSummary(
        students_with_dues=len(due),
        total_due=sum(due, ZERO),
        unlinked_parties=counts.unlinked,
        as_of=counts.as_of,
        synced_at=latest.received_at if latest else None,
    )


# --- agent: enrolment, config, catalog, sync, key rotation ----------------------------------------


def _config_out(session: Session, now: dt.datetime) -> AgentConfigOut:
    selected = repo.selected_groups(session)
    cfg = rules()
    return AgentConfigOut(
        company=selected[0].company if selected else None,
        groups=[g.name for g in selected],
        sync_interval_minutes=cfg.sync.interval_minutes,
        max_parties=cfg.sync.max_parties,
        min_agent_version=cfg.sync.min_agent_version,
        rotate_after_days=cfg.signing.rotate_after_days,
        server_time=now,
    )


def enrol(caller: EnrolmentCaller, data: EnrolIn, *, wrapper: KeyWrapper) -> EnrolOut:
    """Exchange a one-time code for a device credential (FR-TALLY-001). A wrong, used or expired
    code is a plain 401 (nothing stored); the secret is returned once and stored wrapped."""
    now = _now()
    with tenant_session(caller.tenant_id) as session:
        code = repo.lock_code(session, code_hash(data.code))
        if code is None or code.used_at is not None or code.expires_at <= now:
            log.warning("tally.agent.rejected", error_code="bad_code", outcome="rejected")
            raise Unauthenticated("Edge agent request rejected")
        cfg = rules().enrolment
        if repo.count_active_devices(session) >= cfg.max_active_devices:
            raise Conflict("Too many active Tally agents.", code="too_many_devices")
        key_id, wrapped, secret = _new_key(caller.tenant_id, wrapper)
        device = repo.insert_device(
            session,
            {
                "id": new_id(),
                "name": code.device_name,
                "enrolment_code_id": code.id,
                "key_id": key_id,
                "key_ciphertext": wrapped,
                "agent_version": data.agent_version,
                "platform": data.platform,
                "enrolled_by": code.created_by,
                "enrolled_at": now,
                "last_seen_at": now,
            },
        )
        repo.mark_code_used(session, code.id, now)
        audit.record(
            session,
            action="tally.device.enrolled",
            resource_type="tally_device",
            resource_id=device.id,
            summary={"enrolment_code_id": code.id, "key_id": key_id},
            actor_type="system",
        )
        config = _config_out(session, now)
    log.info("tally.device.enrolled", resource_type="tally_device", resource_id=device.id)
    return EnrolOut(device_id=device.id, key_id=key_id, secret=secret, config=config)


def _active_device(session: Session, caller: AgentCaller) -> Device:
    device = repo.get_device(session, caller.device_id, lock=True)
    if device is None or device.status != "active":
        raise Unauthenticated("Edge agent request rejected")
    return device


def _touch(session: Session, device: Device, caller: AgentCaller, now: dt.datetime) -> Device:
    """Last call time and agent version; the first request signed with the next key retires the
    old key (ADR-0032 §2)."""
    values: dict[str, Any] = {"last_seen_at": now}
    if caller.agent_version is not None and caller.agent_version != device.agent_version:
        values["agent_version"] = caller.agent_version
    if caller.used_next_key and device.next_key_id == caller.key_id:
        values.update(
            {
                "key_id": device.next_key_id,
                "key_ciphertext": device.next_key_ciphertext,
                "next_key_id": None,
                "next_key_ciphertext": None,
                "rotation_started_at": None,
            }
        )
        audit.record(
            session,
            action="tally.device.key_promoted",
            resource_type="tally_device",
            resource_id=device.id,
            summary={"key_id": caller.key_id},
            actor_type="system",
        )
    return repo.update_device(session, device.id, values, bump=False)


def agent_config(caller: AgentCaller) -> AgentConfigOut:
    now = _now()
    with tenant_session(caller.tenant_id) as session:
        _touch(session, _active_device(session, caller), caller, now)
        return _config_out(session, now)


def put_catalog(caller: AgentCaller, data: CatalogIn) -> CatalogOut:
    """The agent's list of ledger groups of the open company (names only, FR-TALLY-004)."""
    if len(data.groups) > rules().sync.max_groups:
        raise _field_error("groups", "too_many")
    now = _now()
    with tenant_session(caller.tenant_id) as session:
        device = _touch(session, _active_device(session, caller), caller, now)
        repo.upsert_groups(session, data.company, [(g.name, g.parent) for g in data.groups], now)
        if data.tally_product and data.tally_product != device.tally_product:
            repo.update_device(
                session, device.id, {"tally_product": data.tally_product}, bump=False
            )
        groups = repo.list_groups(session, data.company)
        present = [g for g in groups if g.present]
        selected = sum(1 for g in present if g.selected)
        audit.record(
            session,
            action="tally.catalog.updated",
            resource_type="tally_device",
            resource_id=device.id,
            summary={"groups": len(present), "selected": selected},
            actor_type="system",
        )
    return CatalogOut(groups=len(present), selected=selected)


def _sync_out(sync: Any, *, repeat: bool) -> SyncOut:
    return SyncOut(
        sync_id=sync.id,
        batch_id=sync.batch_id,
        repeat=repeat,
        parties=sync.parties,
        created=sync.created,
        updated=sync.updated,
        missing=sync.missing,
        received_at=sync.received_at,
    )


def _check_snapshot(data: SyncIn, selected: Sequence[Group]) -> str:
    """The company to apply; 422 when the snapshot names anything that is not selected."""
    if not selected:
        raise Conflict("No Tally ledger groups are selected yet.", code="no_groups_selected")
    company = selected[0].company
    if data.company != company:
        raise _field_error("company", "company_not_selected")
    names = {g.name for g in selected}
    if any(g not in names for g in data.groups) or any(p.group not in names for p in data.parties):
        raise _field_error("parties", "group_not_selected")
    if len(data.parties) > rules().sync.max_parties:
        raise _field_error("parties", "too_many")
    guids = [p.guid for p in data.parties if p.guid]
    plain = [p.name for p in data.parties if not p.guid]
    if len(set(guids)) != len(guids) or len(set(plain)) != len(plain):
        raise _field_error("parties", "duplicate_party")
    # The sync record keeps the snapshot's total due in the same numeric(14,2) as one balance.
    if _snapshot_due(data) > MAX_AMOUNT:
        raise _field_error("parties", "total_too_large")
    return company


def _snapshot_due(data: SyncIn) -> Decimal:
    return sum((p.closing_balance for p in data.parties if p.closing_balance > 0), ZERO)


def accept_sync(caller: AgentCaller, data: SyncIn) -> SyncOut:
    """Apply one complete snapshot (FR-TALLY-005). A batch already accepted is answered from its
    stored result and applies nothing. Parties missing from the snapshot are marked not present."""
    now = _now()
    with tenant_session(caller.tenant_id) as session:
        device = _touch(session, _active_device(session, caller), caller, now)
        done = repo.sync_for_batch(session, device.id, data.batch_id)
        if done is not None:
            audit.record(
                session,
                action="tally.sync.repeated",
                resource_type="tally_device",
                resource_id=device.id,
                summary={"sync_id": done.id, "batch_id": data.batch_id},
                actor_type="system",
            )
            return _sync_out(done, repeat=True)
        if _outdated(device.agent_version):
            raise Conflict("Update the Tally agent before it can sync.", code="agent_outdated")
        company = _check_snapshot(data, repo.selected_groups(session))
        stored = repo.parties_of_company(session, company)
        by_guid = {p.guid: p for p in stored if p.guid}
        by_name = {p.ledger_name: p for p in stored if not p.guid}
        creates: list[dict[str, Any]] = []
        updates: list[tuple[Party, dict[str, Any]]] = []
        seen: set[uuid.UUID] = set()
        changed = 0
        for item in data.parties:
            match = by_guid.get(item.guid) if item.guid else None
            if match is None:
                match = by_name.get(item.name)
            if match is None or match.id in seen:
                creates.append(
                    {
                        "id": new_id(),
                        "company": company,
                        "guid": item.guid,
                        "ledger_name": item.name,
                        "group_name": item.group,
                        "closing_balance": item.closing_balance,
                        "as_of": data.as_of,
                        "first_seen_at": now,
                    }
                )
                continue
            seen.add(match.id)
            values = {
                "guid": item.guid or match.guid,
                "ledger_name": item.name,
                "group_name": item.group,
                "closing_balance": item.closing_balance,
                "as_of": data.as_of,
                "present": True,
            }
            if (
                match.ledger_name != item.name
                or match.group_name != item.group
                or match.closing_balance != item.closing_balance
                or not match.present
            ):
                changed += 1
            updates.append((match, values))
        missing = [p.id for p in repo.present_parties(session) if p.id not in seen]
        sync = repo.insert_sync(
            session,
            {
                "id": new_id(),
                "device_id": device.id,
                "batch_id": data.batch_id,
                "company": company,
                "as_of": data.as_of,
                "groups": len(set(data.groups) | {p.group for p in data.parties}),
                "parties": len(data.parties),
                "created": len(creates),
                "updated": changed,
                "missing": len(missing),
                "total_due": _snapshot_due(data),
                "received_at": now,
            },
        )
        repo.insert_parties(session, [{**c, "last_sync_id": sync.id} for c in creates])
        repo.update_parties(
            session,
            [{"id": party.id, **values, "last_sync_id": sync.id} for party, values in updates],
        )
        repo.mark_parties_missing(session, missing, sync.id)
        repo.update_device(
            session,
            device.id,
            {"last_sync_at": now, "silent_notified_at": None},
            bump=False,
        )
        audit.record(
            session,
            action="tally.sync.received",
            resource_type="tally_device",
            resource_id=device.id,
            summary={
                "sync_id": sync.id,
                "batch_id": data.batch_id,
                "parties": len(data.parties),
                "created": len(creates),
                "updated": changed,
                "missing": len(missing),
            },
            actor_type="system",
        )
    log.info(
        "tally.sync.received",
        tenant_id=caller.tenant_id,
        resource_type="tally_device",
        resource_id=caller.device_id,
        count=len(data.parties),
    )
    return _sync_out(sync, repeat=False)


def rotate_key(caller: AgentCaller, *, wrapper: KeyWrapper) -> KeyRotationOut:
    """A new device key, returned once; the old one works until the new one is first used, or
    for ``key_rotation_overlap_days`` at most (FR-TALLY-002)."""
    now = _now()
    with tenant_session(caller.tenant_id) as session:
        device = _touch(session, _active_device(session, caller), caller, now)
        key_id, wrapped, secret = _new_key(caller.tenant_id, wrapper)
        # A rotation already pending keeps its start: rotating again with the old key must not
        # extend the old key's life past the overlap (SEC-030).
        pending = device.next_key_id is not None and device.rotation_started_at is not None
        repo.update_device(
            session,
            device.id,
            {
                "next_key_id": key_id,
                "next_key_ciphertext": wrapped,
                "rotation_started_at": device.rotation_started_at if pending else now,
            },
        )
        audit.record(
            session,
            action="tally.device.key_rotated",
            resource_type="tally_device",
            resource_id=device.id,
            summary={"key_id": key_id},
            actor_type="system",
        )
    return KeyRotationOut(key_id=key_id, secret=secret)


# --- jobs -----------------------------------------------------------------------------------------


def notify_silent_devices(session: Session, *, now: dt.datetime | None = None) -> int:
    """In-app notice to the owner and finance readers when an active agent has not called for
    ``notify_after_hours`` (FR-TALLY-009); once per silence. Only when the flag is on."""
    current = now or _now()
    tenant_id = repo.current_tenant_id(session)
    if not connector_enabled(session, tenant_id):
        return 0
    sent = 0
    hours = rules().silence.notify_after_hours
    for device in repo.silent_devices(session, _silent_before(current)):
        last = device.last_seen_at or device.enrolled_at
        for permission in (DEVICE_MANAGE, READ):
            sent += notifications.notify(
                session,
                tenant_id=tenant_id,
                recipients=notifications.PermissionSelector(permission),
                template_key=SILENT_TEMPLATE,
                params={"device_id": device.id, "hours": hours},
                resource_id=device.id,
                dedupe_key=f"tally-silent:{device.id}:{last.isoformat()}",
            )
        repo.update_device(session, device.id, {"silent_notified_at": current}, bump=False)
        log.warning(
            "tally.agent.silent",
            tenant_id=tenant_id,
            resource_type="tally_device",
            resource_id=device.id,
        )
    return sent


def purge_old_syncs(session: Session, *, now: dt.datetime | None = None) -> int:
    """Sync records (counts only) past ``retention.sync_days`` that no ledger points to."""
    current = now or _now()
    return repo.delete_old_syncs(session, current - dt.timedelta(days=rules().retention.sync_days))


# --- full export and offboarding ------------------------------------------------------------------


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: the connector's tables for the school's full data export (FR-ADM-001; the
    caller checked ``tenant.export_all`` and audits the export). Wrapped keys and code hashes
    are left out."""
    return repo.export_tables(session)


_PURGE = purging.PurgeTables(
    deleted=(
        "ops.tally_party_links",
        "ops.tally_parties",
        "ops.tally_syncs",
        "ops.tally_groups",
        "ops.tally_devices",
        "ops.tally_enrolment_codes",
    ),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only; ADR-0029)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="tally", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "CONFIGURE",
    "DEVICE_MANAGE",
    "READ",
    "FeeSummary",
    "LedgerDue",
    "StudentFeeDues",
    "accept_sync",
    "agent_config",
    "can_read_fees",
    "code_hash",
    "connector_enabled",
    "create_enrolment_code",
    "enrol",
    "export_records",
    "fee_summary",
    "fee_tool_available",
    "get_party",
    "link_party",
    "list_devices",
    "list_dues",
    "list_groups",
    "list_parties",
    "normalise_code",
    "notify_silent_devices",
    "purge_old_syncs",
    "purge_tenant_data",
    "put_catalog",
    "revoke_device",
    "rotate_key",
    "select_groups",
    "status",
    "student_fee_dues",
    "tenant_data_counts",
    "unlink_party",
]
