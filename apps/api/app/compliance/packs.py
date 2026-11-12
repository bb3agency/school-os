"""Inspection readiness packs (FC-07 merged with FO-12; FR-COMP-024..FR-COMP-045; docs/05 §16.9).

Before a DEO/MEO, APSERMC, CBSE or fire department visit the office sees, per authority, what
SchoolOS already holds and what is missing, then makes the pack: a printable index (cover,
contents, the state of every item) plus the files it lists, which the office prints from their
own screens (certificate copies, register print views, the Appendix IX page, the privacy notice).

- **Definitions (FR-COMP-024, FR-COMP-025).** ``packs_catalogue.yaml`` (``verified: false``):
  packs per authority for the school's boards; certificate items follow their kind's boards.
- **Readiness (FR-COMP-026..FR-COMP-031).** Live, ``compliance.read``: certificate items take
  the FC-01 register's state (``ready`` valid or no expiry, ``expiring``, ``expired``,
  ``missing``, ``no_copy`` when held without a copy in Documents); registers are ``ready``
  (they print from SchoolOS records); the Appendix IX item is ``ready`` while a snapshot is
  live; the privacy notice ``ready`` once a parents' notice was recorded; the DPDP checklist
  ``ready`` when every applicable item is done, else ``incomplete``; evidence SchoolOS does not
  hold yet (FC-04, FC-05, FC-09) is ``not_tracked``, never a failure.
- **Making a pack (FR-COMP-032..FR-COMP-037).** ``compliance.manage``: the items' states are
  frozen in ``ops.inspection_packs`` with the academic year, visit date and the registers the
  office chose (student lists: only when chosen, and only by holders of ``register.read``;
  registers not chosen print as ``left_out``). Audited in the same transaction
  (``compliance.inspection_pack.generated``: pack key, version, counts, register keys).
- **Index (FR-COMP-038..FR-COMP-040).** ``compliance.read``: an A4 HTML page with one hashed
  style block (no scripts, links or images), school name, authority, date, the "unverified"
  note and the contents with states and dates; counts only, no personal data.
- **Log (FR-COMP-041, FR-COMP-042).** The packs made, newest first; another school's pack is 404.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from functools import cached_property
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.compliance import config as cfg
from app.compliance import packs_page as page
from app.compliance import packs_repository as packs_repo
from app.compliance import repository as repo
from app.compliance.models import ComplianceCertificate
from app.compliance.packs_config import Pack, PackItem, pack_catalogue
from app.compliance.packs_models import InspectionPack
from app.compliance.packs_schemas import (
    InspectionPackCountsOut,
    InspectionPackCreate,
    InspectionPackDetailOut,
    InspectionPackItemOut,
    InspectionPackOut,
    InspectionPackReadinessOut,
    InspectionReadinessOut,
    ItemState,
)
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.disclosure import service as disclosure
from app.identity import service as identity
from app.privacy import readiness_service as dpdp
from app.privacy import service as privacy
from app.tenancy import service as tenancy

READ: Final = "compliance.read"
MANAGE: Final = "compliance.manage"
REGISTER_READ: Final = "register.read"
RESOURCE: Final = "inspection_pack"
ACTION: Final = "compliance.inspection_pack.generated"
IST: Final = ZoneInfo("Asia/Kolkata")

ATTENTION: Final = frozenset({"expiring", "no_copy", "incomplete"})
MISSING: Final = frozenset({"expired", "missing"})

log = get_logger(__name__)

# A bad pack file stops the API at start-up (FR-COMP-024).
pack_catalogue()


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _boards(session: Session) -> list[str]:
    boards, _ = tenancy.school_boards(session)
    return boards


class _Sources:
    """What SchoolOS holds today, each read at most once per request (no personal data)."""

    def __init__(self, session: Session, ctx: UserContext) -> None:
        self.session = session
        self.ctx = ctx
        self.today = repo.now(session).astimezone(IST).date()
        self.certificates: dict[str, ComplianceCertificate] = {
            r.kind: r for r in repo.current_rows(session) if r.kind != cfg.OTHER
        }

    @cached_property
    def disclosure(self) -> tuple[uuid.UUID, dt.datetime] | None:
        return disclosure.live_snapshot_published_at(self.session)

    @cached_property
    def notice(self) -> tuple[uuid.UUID, dt.date] | None:
        parents = [n for n in privacy.list_notices(self.session) if n.audience == "parents"]
        latest = max(parents, key=lambda n: (n.issued_on, str(n.id)), default=None)
        return (latest.id, latest.issued_on) if latest is not None else None

    @cached_property
    def dpdp(self) -> tuple[int, int]:
        out = dpdp.readiness(self.session, self.ctx)
        return out.done, out.applicable


def _certificate_item(item: PackItem, src: _Sources) -> InspectionPackItemOut:
    kind = item.kind or ""
    row = src.certificates.get(kind)
    base = _base(item)
    if row is None:
        return base.model_copy(update={"state": "missing", "kind": item.kind})
    cert_state = cfg.state(row.valid_until, src.today, expiring_days=cfg.catalogue().expiring_days)
    state: ItemState
    if cert_state == "expired":
        state = "expired"
    elif row.document_id is None:
        state = "no_copy"
    elif cert_state == "expiring":
        state = "expiring"
    else:
        state = "ready"
    return base.model_copy(
        update={
            "state": state,
            "kind": item.kind,
            "certificate_id": row.id,
            "valid_until": row.valid_until,
            "has_copy": row.document_id is not None,
        }
    )


def _base(item: PackItem) -> InspectionPackItemOut:
    return InspectionPackItemOut(
        key=item.key,
        label=item.label,
        source=item.source,
        state="ready",
        fix_by=item.fix_by,
        note=item.note,
        feature=item.feature,
    )


def _item(  # noqa: PLR0911 - one answer per source reads best
    item: PackItem, src: _Sources, chosen: frozenset[str] | None
) -> InspectionPackItemOut:
    """One item's state now (FR-COMP-026..FR-COMP-031). ``chosen``: the registers picked when
    making a pack (None in the live view)."""
    base = _base(item)
    if item.source == "certificate":
        return _certificate_item(item, src)
    if item.source == "register":
        included = None if chosen is None else item.key in chosen
        state: ItemState = "left_out" if included is False else "ready"
        return base.model_copy(
            update={"state": state, "register_view": item.register_view, "included": included}
        )
    if item.source == "disclosure":
        live = src.disclosure
        if live is None:
            return base.model_copy(update={"state": "missing"})
        return base.model_copy(update={"snapshot_id": live[0], "published_at": live[1]})
    if item.source == "privacy_notice":
        notice = src.notice
        if notice is None:
            return base.model_copy(update={"state": "missing"})
        return base.model_copy(update={"notice_id": notice[0], "issued_on": notice[1]})
    if item.source == "dpdp_readiness":
        done, applicable = src.dpdp
        state = "ready" if done >= applicable else "incomplete"
        return base.model_copy(update={"state": state, "done": done, "applicable": applicable})
    return base.model_copy(update={"state": "not_tracked"})


def counts(items: Sequence[InspectionPackItemOut]) -> InspectionPackCountsOut:
    states = [i.state for i in items]
    return InspectionPackCountsOut(
        ready=states.count("ready"),
        attention=sum(1 for s in states if s in ATTENTION),
        missing=sum(1 for s in states if s in MISSING),
        not_tracked=states.count("not_tracked"),
        left_out=states.count("left_out"),
    )


def _pack_items(
    pack: Pack, boards: Sequence[str], src: _Sources, chosen: frozenset[str] | None
) -> list[InspectionPackItemOut]:
    return [_item(i, src, chosen) for i in pack.items_for(boards)]


def readiness(session: Session, ctx: UserContext) -> InspectionReadinessOut:
    """Every pack for the school's boards with each item's state now (``compliance.read``;
    FR-COMP-026)."""
    cat = pack_catalogue()
    boards = _boards(session)
    src = _Sources(session, ctx)
    packs: list[InspectionPackReadinessOut] = []
    for key, pack in cat.packs_for(boards).items():
        items = _pack_items(pack, boards, src, None)
        packs.append(
            InspectionPackReadinessOut(
                key=key,
                label=pack.label,
                authority=pack.authority,
                note=pack.note,
                items=items,
                counts=counts(items),
            )
        )
    return InspectionReadinessOut(
        verified=cat.verified, source=cat.source, version=cat.version, packs=packs
    )


def _year(session: Session, year_id: uuid.UUID | None) -> tuple[uuid.UUID | None, str | None]:
    if year_id is None:
        current = tenancy.get_current_academic_year(session)
        return (current.id, current.label) if current is not None else (None, None)
    try:
        year = tenancy.get_academic_year(session, year_id)
    except NotFound:
        raise ValidationFailed(
            [_error("academic_year_id", "academic_year_not_found")], "Academic year not found."
        ) from None
    return year.id, year.label


def _year_labels(session: Session, rows: Sequence[InspectionPack]) -> dict[uuid.UUID, str]:
    wanted = {r.academic_year_id for r in rows if r.academic_year_id is not None}
    if not wanted:
        return {}
    return {y.id: y.label for y in tenancy.list_academic_years(session) if y.id in wanted}


def _names(session: Session, rows: Sequence[InspectionPack]) -> dict[uuid.UUID, str]:
    users = list({r.generated_by for r in rows})
    found = identity.members_for_users(session, users) if users else {}
    return {u: name for u, (_m, name) in found.items()}


def _out(
    row: InspectionPack, years: dict[uuid.UUID, str], names: dict[uuid.UUID, str]
) -> InspectionPackOut:
    return InspectionPackOut(
        id=row.id,
        pack_key=row.pack_key,
        pack_label=row.pack_label,
        authority=row.authority,
        pack_version=row.pack_version,
        verified=row.verified,
        academic_year_id=row.academic_year_id,
        academic_year_label=years.get(row.academic_year_id) if row.academic_year_id else None,
        visit_on=row.visit_on,
        registers=list(row.registers),
        counts=InspectionPackCountsOut.model_validate(row.counts),
        generated_at=row.generated_at,
        generated_by_name=names.get(row.generated_by),
    )


def _detail(session: Session, row: InspectionPack) -> InspectionPackDetailOut:
    base = _out(row, _year_labels(session, [row]), _names(session, [row]))
    items = [InspectionPackItemOut.model_validate(i) for i in row.items]
    return InspectionPackDetailOut(**base.model_dump(), items=items)


def generate(
    session: Session, ctx: UserContext, data: InspectionPackCreate
) -> InspectionPackDetailOut:
    """Make a pack (``compliance.manage``; FR-COMP-032..FR-COMP-037): freeze each item's state,
    the year, the visit date and the registers chosen; audit in the same transaction."""
    cat = pack_catalogue()
    boards = _boards(session)
    pack = cat.packs_for(boards).get(data.pack_key)
    if pack is None:
        raise ValidationFailed([_error("pack_key", "unknown_pack")], "Unknown inspection pack.")
    items_for = pack.items_for(boards)
    register_keys = {i.key for i in items_for if i.source == "register"}
    unknown = [k for k in data.registers if k not in register_keys]
    if unknown:
        raise ValidationFailed(
            [_error("registers", "unknown_register")], "That register is not in this pack."
        )
    if data.registers and not ctx.has(REGISTER_READ):
        # Registers list students: only holders of register.read may put them in a pack
        # (FR-COMP-034); the print views ask for the MFA step-up themselves.
        raise Forbidden("Choosing registers needs permission to print registers.")
    year_id, _label = _year(session, data.academic_year_id)
    src = _Sources(session, ctx)
    chosen = frozenset(data.registers)
    items = _pack_items(pack, boards, src, chosen)
    totals = counts(items)
    row = packs_repo.insert(
        session,
        id=new_id(),
        tenant_id=repo.current_tenant_id(session),
        pack_key=data.pack_key,
        pack_version=cat.version,
        pack_label=pack.label,
        authority=pack.authority,
        verified=cat.verified,
        academic_year_id=year_id,
        visit_on=data.visit_on,
        registers=sorted(chosen),
        items=[i.model_dump(mode="json") for i in items],
        counts=totals.model_dump(),
        generated_by=ctx.user_id,
    )
    audit.record(
        session,
        action=ACTION,
        resource_type=RESOURCE,
        resource_id=row.id,
        summary={
            "pack_key": data.pack_key,
            "pack_version": cat.version,
            "academic_year_id": str(year_id) if year_id else None,
            "registers": sorted(chosen),
            **totals.model_dump(),
        },
        request_id=_request_id(),
    )
    log.info("compliance.inspection_pack.generated", pack_key=data.pack_key, count=len(items))
    return _detail(session, row)


def _load(session: Session, pack_id: uuid.UUID) -> InspectionPack:
    row = packs_repo.get(session, pack_id)
    if row is None:
        raise NotFound("Inspection pack not found")
    return row


def list_packs(session: Session, ctx: UserContext) -> list[InspectionPackOut]:
    """The packs made, newest first (``compliance.read``; FR-COMP-041)."""
    del ctx
    rows = packs_repo.recent(session, pack_catalogue().log_limit)
    years, names = _year_labels(session, rows), _names(session, rows)
    return [_out(r, years, names) for r in rows]


def get_pack(session: Session, ctx: UserContext, pack_id: uuid.UUID) -> InspectionPackDetailOut:
    """One pack as it was made (``compliance.read``; FR-COMP-042). Another school's: 404."""
    del ctx
    return _detail(session, _load(session, pack_id))


def index_page(session: Session, ctx: UserContext, pack_id: uuid.UUID) -> str:
    """The pack's printable index (``compliance.read``; FR-COMP-038..FR-COMP-040)."""
    detail = get_pack(session, ctx, pack_id)
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    return page.render(
        page.IndexData(
            school_name=tenant.name,
            school_address=head.address_en,
            pack=detail,
            printed_at=dt.datetime.now(dt.UTC),
        )
    )


__all__ = [
    "ACTION",
    "MANAGE",
    "READ",
    "counts",
    "generate",
    "get_pack",
    "index_page",
    "list_packs",
    "readiness",
]
