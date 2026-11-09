"""Tally voucher export (XML import file) and ledger-vs-Tally check (FM-03; FR-TALLY-011..030;
docs/09 Tally connector; ADR-0032 unchanged: SchoolOS never writes to Tally).

The accountant maps ledgers once (``tally.configure``), downloads a file of vouchers for a period
(``fee.report``, recent MFA sign-in; audited ``fee.report.downloaded`` like every bulk fee
export) and imports it into Tally by hand. Nothing is posted by the edge agent.

- **Party (lead decision D85, PO to confirm).** ``party_mode`` ``student`` (default, the research
  report's choice): each student's own Tally party, i.e. the ONE present ledger linked to the
  student by the connector; a student without exactly one link goes to the receivable ledger.
  ``receivable``: one receivable ledger for every student.
- **Vouchers.** Receipt (Dr the mode's cash or bank ledger, Cr the party) per receipt; Payment
  (Dr the party, Cr the mode's ledger) per approved cancellation and per processed refund: Tally
  refuses cash and bank ledgers in journals by default. With ``include_demand`` on (default off,
  D85): one Journal per student and day for the fee demand (charges, late fees, concessions,
  adjustments): Dr the party, Cr each head's income ledger (late fees and adjustments without a
  head: ``other_income_ledger``), signs reversed for credits.
- **Never implicit.** A ledger that is not mapped refuses the export (422 ``mapping_incomplete``,
  listing what is missing): the file creates no ledger in Tally.
- **Closed days only (FR-FEE-019).** A day of the period with cash vouchers must be closed (409
  ``days_not_closed``), so the cash in a file never changes afterwards.
- **Idempotent.** Each source record (receipt, cancellation, refund, ledger entry) is recorded
  with the export that first carried it. A later export of the same period carries only the new
  ones, unless ``include_exported`` asks for all; repeated vouchers are marked "[Exported before]"
  in their narration and keep their SchoolOS number as voucher number, so a second import shows.
- **Check (FR-TALLY-025..030).** The balances of the linked ledgers (and the receivable ledger)
  in Tally's last snapshot against what SchoolOS says the same students owed on Tally's as-of
  date. Read-only; nothing is corrected.

Logs carry ids and counts only, never ledger names, student names or amounts (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    StepUpRequired,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_logger
from app.fees import service as fees
from app.identity import service as identity
from app.identity.principal import STEP_UP_MAX_AGE
from app.students import service as students
from app.tally import repository as repo
from app.tally import vouchers_repository as vrepo
from app.tally.config import rules
from app.tally.models import ExportSettings, VoucherExport
from app.tally.service import CONFIGURE, READ, connector_enabled
from app.tally.vouchers_schemas import (
    CheckRow,
    HeadMappingOut,
    KindCount,
    LedgerCheckOut,
    MissingLedger,
    ModeMappingOut,
    VoucherExportIn,
    VoucherExportOut,
    VoucherPreviewOut,
    VoucherSettingsIn,
    VoucherSettingsOut,
)
from app.tally.vouchers_xml import Line, Voucher, render

log = get_logger(__name__)

REPORT: Final = "fee.report"
ZERO: Final = Decimal("0.00")
KINDS: Final = ("receipt", "cancellation", "refund", "demand")
REPEAT_MARK: Final = "[Exported before] "
OTHER_INCOME_LABEL: Final = "Late fees and adjustments without a fee head"
MODE_LABELS: Final = {
    "cash": "Cash",
    "cheque": "Cheque",
    "bank_transfer": "Bank transfer",
    "upi_qr": "UPI (school QR)",
    "razorpay": "Online payment",
}


# --- guards ---------------------------------------------------------------------------------------


def _fees_on(session: Session, ctx: UserContext) -> None:
    if not fees.enabled(session, ctx.tenant_id):
        raise fees.ModuleHidden()


def _school_wide(ctx: UserContext, *permissions: str) -> None:
    """Fee data is school-wide only (FR-FEE-024, ADR-0032 §5)."""
    if not any(ctx.has(p) and ctx.scope_for(p).school_wide for p in permissions):
        raise Forbidden()


def _recent_mfa(ctx: UserContext) -> None:
    """Defence in depth for the step-up download: MFA sign-in within 5 minutes, else 428."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def _error(field_name: str, code: str) -> dict[str, str]:
    return {"field": field_name, "code": code, "message_key": f"errors.{code}"}


# --- settings (FR-TALLY-011..015) ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Mapping:
    party_mode: str
    receivable_ledger: str
    mode_ledgers: Mapping[str, str]
    head_ledgers: Mapping[str, str]
    other_income_ledger: str | None
    include_demand: bool
    company: str | None


def _mapping(row: ExportSettings | None) -> _Mapping:
    if row is None:
        d = rules().vouchers.defaults
        return _Mapping(
            party_mode=d.party_mode,
            receivable_ledger=d.receivable_ledger,
            mode_ledgers=dict(d.mode_ledgers),
            head_ledgers={},
            other_income_ledger=None,
            include_demand=d.include_demand,
            company=None,
        )
    return _Mapping(
        party_mode=row.party_mode,
        receivable_ledger=row.receivable_ledger,
        mode_ledgers={str(k): str(v) for k, v in row.mode_ledgers.items()},
        head_ledgers={str(k): str(v) for k, v in row.head_ledgers.items()},
        other_income_ledger=row.other_income_ledger,
        include_demand=row.include_demand,
        company=row.company,
    )


def _settings_out(session: Session, row: ExportSettings | None) -> VoucherSettingsOut:
    m = _mapping(row)
    return VoucherSettingsOut(
        configured=row is not None,
        party_mode="receivable" if m.party_mode == "receivable" else "student",
        receivable_ledger=m.receivable_ledger,
        modes=[
            ModeMappingOut(mode=mode, ledger=m.mode_ledgers.get(mode)) for mode in fees.TALLY_MODES
        ],
        heads=[
            HeadMappingOut(
                head_id=h.id,
                code=h.code,
                name=h.name,
                active=h.active,
                ledger=m.head_ledgers.get(str(h.id)),
            )
            for h in fees.tally_feed_heads(session)
        ],
        other_income_ledger=m.other_income_ledger,
        include_demand=m.include_demand,
        company=m.company,
        links_available=connector_enabled(session, repo.current_tenant_id(session)),
        format_verified=rules().vouchers.verified,
        version=row.version if row is not None else 0,
        updated_at=row.updated_at if row is not None else None,
    )


def get_settings(session: Session, ctx: UserContext) -> VoucherSettingsOut:
    """The mapping (``tally.configure``, or ``fee.report`` to see what an export will use);
    defaults from ``config.yaml`` until it is saved."""
    _fees_on(session, ctx)
    _school_wide(ctx, CONFIGURE, REPORT)
    return _settings_out(session, vrepo.get_settings(session))


def put_settings(
    session: Session, ctx: UserContext, data: VoucherSettingsIn, expected_version: int | None
) -> VoucherSettingsOut:
    """Save the mapping (``tally.configure``; optional ``If-Match``). Head ids must be this
    school's heads (422 ``unknown_head``). Audited ``tally.export_settings.updated``."""
    _fees_on(session, ctx)
    _school_wide(ctx, CONFIGURE)
    known = {h.id for h in fees.tally_feed_heads(session)}
    unknown = [h for h in data.head_ledgers if h not in known]
    if unknown:
        raise ValidationFailed([_error(f"head_ledgers.{h}", "unknown_head") for h in unknown])
    row = vrepo.get_settings(session, lock=True)
    if expected_version is not None and (row.version if row else 0) != expected_version:
        raise PreconditionFailed("This was changed by someone else. Reload it and try again.")
    values: dict[str, Any] = {
        "party_mode": data.party_mode,
        "receivable_ledger": data.receivable_ledger,
        "mode_ledgers": dict(data.mode_ledgers),
        "head_ledgers": {str(k): v for k, v in data.head_ledgers.items()},
        "other_income_ledger": data.other_income_ledger,
        "include_demand": data.include_demand,
        "company": data.company,
        "updated_by": ctx.user_id,
    }
    if row is None:
        row = vrepo.insert_settings(session, {"id": new_id(), **values})
    else:
        values["updated_at"] = dt.datetime.now(dt.UTC)
        row = vrepo.update_settings(session, row.id, values)
    audit.record(
        session,
        action="tally.export_settings.updated",
        resource_type="tally_export_settings",
        resource_id=row.id,
        summary={
            "party_mode": data.party_mode,
            "modes_mapped": len(data.mode_ledgers),
            "heads_mapped": len(data.head_ledgers),
            "include_demand": data.include_demand,
        },
    )
    return _settings_out(session, row)


# --- planning the vouchers ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Planned:
    kind: str
    keys: tuple[str, ...]
    voucher: Voucher
    repeat: bool
    cash_in: Decimal = ZERO
    cash_out: Decimal = ZERO


@dataclass(slots=True)
class Plan:
    vouchers: list[Planned] = field(default_factory=list)
    open_days: list[dt.date] = field(default_factory=list)
    missing: dict[str, str] = field(default_factory=dict)
    on_receivable: set[uuid.UUID] = field(default_factory=set)


def _parties(session: Session, m: _Mapping) -> dict[uuid.UUID, str]:
    """Student -> its own Tally party (party mode ``student``, connector on): the one present
    ledger linked to it. Students with no link or several links are left out (receivable)."""
    if m.party_mode != "student" or not connector_enabled(session, repo.current_tenant_id(session)):
        return {}
    names: dict[uuid.UUID, set[str]] = defaultdict(set)
    for b in repo.linked_balances(session, None):
        if b.present:
            names[b.student_id].add(b.ledger_name)
    return {sid: next(iter(n)) for sid, n in names.items() if len(n) == 1}


def _label(name: str, class_section: str) -> str:
    return f"{name} ({class_section})" if name and class_section else name or "student"


def plan(
    session: Session,
    ctx: UserContext,
    m: _Mapping,
    start: dt.date,
    end: dt.date,
    *,
    include_exported: bool,
) -> Plan:
    feed = fees.tally_feed_for(session, start, end, demand=m.include_demand)
    keys = [f"{c.kind}:{c.id}" for c in feed.cash] + [f"demand:{d.id}" for d in feed.demand]
    done = vrepo.exported_keys(session, keys)
    parties = _parties(session, m)
    types = rules().vouchers.voucher_types
    out = Plan(open_days=list(feed.open_days))

    def party(student_id: uuid.UUID) -> str:
        found = parties.get(student_id)
        if found is None:
            out.on_receivable.add(student_id)
            return m.receivable_ledger
        return found

    def mode_ledger(mode: str) -> str:
        name = m.mode_ledgers.get(mode)
        if not name:
            out.missing[f"mode_ledgers.{mode}"] = MODE_LABELS.get(mode, mode)
            return ""
        return name

    for c in feed.cash:
        key = f"{c.kind}:{c.id}"
        repeat = key in done
        if repeat and not include_exported:
            continue
        p, ml = party(c.student.student_id), mode_ledger(c.mode)
        who = _label(c.student.name, c.student.class_section)
        mark = REPEAT_MARK if repeat else ""
        if c.kind == "receipt":
            narration = f"{mark}SchoolOS fee receipt {c.receipt_number}: {who}"
            lines = (Line(ml, c.amount), Line(p, -c.amount))
            vch, cash_in, cash_out = types.receipt, c.amount, ZERO
        elif c.kind == "cancellation":
            narration = f"{mark}Cancellation of SchoolOS fee receipt {c.receipt_number}: {who}"
            lines = (Line(p, c.amount), Line(ml, -c.amount))
            vch, cash_in, cash_out = types.payment, ZERO, c.amount
        else:
            narration = f"{mark}Refund {c.number} of SchoolOS fee receipt {c.receipt_number}: {who}"
            lines = (Line(p, c.amount), Line(ml, -c.amount))
            vch, cash_in, cash_out = types.payment, ZERO, c.amount
        out.vouchers.append(
            Planned(
                kind=c.kind,
                keys=(key,),
                voucher=Voucher(vch, c.on, c.number, p, narration, lines),
                repeat=repeat,
                cash_in=cash_in,
                cash_out=cash_out,
            )
        )

    if feed.demand:
        _plan_demand(
            session,
            ctx,
            m,
            feed.demand,
            done=done,
            include_exported=include_exported,
            out=out,
            party=party,
        )
    return out


def _plan_demand(
    session: Session,
    ctx: UserContext,
    m: _Mapping,
    entries: Sequence[fees.TallyFeedDemand],
    *,
    done: set[str],
    include_exported: bool,
    out: Plan,
    party: Callable[[uuid.UUID], str],
) -> None:
    """One Journal per student, day and (new | exported before): Dr the party, Cr the income
    ledgers, netted per ledger."""
    heads = {h.id: h for h in fees.tally_feed_heads(session)}
    groups: dict[tuple[uuid.UUID, dt.date, bool], list[fees.TallyFeedDemand]] = defaultdict(list)
    for e in entries:
        repeat = f"demand:{e.id}" in done
        if repeat and not include_exported:
            continue
        groups[(e.student_id, e.on, repeat)].append(e)
    if not groups:
        return
    names = students.summaries(session, ctx, {sid for sid, _, _ in groups})
    types = rules().vouchers.voucher_types
    for (sid, on, repeat), rows in sorted(
        groups.items(), key=lambda kv: (kv[0][1], str(kv[0][0]), kv[0][2])
    ):
        income: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for e in rows:
            if e.head_id is not None:
                ledger = m.head_ledgers.get(str(e.head_id))
                if not ledger:
                    head = heads.get(e.head_id)
                    out.missing[f"head_ledgers.{e.head_id}"] = (
                        f"{head.code} {head.name}" if head else str(e.head_id)
                    )
                    ledger = ""
            else:
                ledger = m.other_income_ledger or ""
                if not ledger:
                    out.missing["other_income_ledger"] = OTHER_INCOME_LABEL
            income[ledger] -= e.amount
        net = -sum(income.values(), ZERO)
        p = party(sid)
        lines = [Line(p, net)] if net != ZERO else []
        lines += [Line(ledger, amount) for ledger, amount in income.items() if amount != ZERO]
        if len(lines) < 2:
            continue
        summary = names.get(sid)
        who = _label(summary.display_name or "", summary.class_section or "") if summary else ""
        mark = REPEAT_MARK if repeat else ""
        narration = f"{mark}SchoolOS fee demand of {on.isoformat()}" + (f": {who}" if who else "")
        out.vouchers.append(
            Planned(
                kind="demand",
                keys=tuple(f"demand:{e.id}" for e in rows),
                voucher=Voucher(
                    types.journal,
                    on,
                    f"FD/{on:%Y%m%d}/{sid.hex[:8]}",
                    p,
                    narration,
                    tuple(lines),
                ),
                repeat=repeat,
            )
        )


# --- preview and export (FR-TALLY-016..024) ------------------------------------------------------


def _check_period(session: Session, data: VoucherExportIn) -> None:
    errors = []
    if data.period_from > data.period_to:
        errors.append(_error("period_to", "before_start"))
    if data.period_to > fees.today(session):
        errors.append(_error("period_to", "in_future"))
    if (data.period_to - data.period_from).days + 1 > rules().vouchers.max_period_days:
        errors.append(_error("period_from", "period_too_long"))
    if errors:
        raise ValidationFailed(errors)


def _counts(p: Plan) -> list[KindCount]:
    return [
        KindCount(
            kind=kind,
            count=sum(1 for v in p.vouchers if v.kind == kind),
            new=sum(1 for v in p.vouchers if v.kind == kind and not v.repeat),
        )
        for kind in KINDS
    ]


def preview(session: Session, ctx: UserContext, data: VoucherExportIn) -> VoucherPreviewOut:
    """What a file for the period would hold (``fee.report``): counts per kind, new and already
    exported, cash in and out, open days and missing ledgers. Nothing is recorded."""
    _fees_on(session, ctx)
    _school_wide(ctx, REPORT)
    _check_period(session, data)
    row = vrepo.get_settings(session)
    m = _mapping(row)
    everything = plan(session, ctx, m, data.period_from, data.period_to, include_exported=True)
    chosen = [v for v in everything.vouchers if data.include_exported or not v.repeat]
    count = len(chosen)
    return VoucherPreviewOut(
        period_from=data.period_from,
        period_to=data.period_to,
        vouchers=len(everything.vouchers),
        new_vouchers=sum(1 for v in everything.vouchers if not v.repeat),
        already_exported=sum(1 for v in everything.vouchers if v.repeat),
        by_kind=_counts(everything),
        cash_in=sum((v.cash_in for v in chosen), ZERO),
        cash_out=sum((v.cash_out for v in chosen), ZERO),
        open_days=everything.open_days,
        missing=[MissingLedger(field=k, label=v) for k, v in sorted(everything.missing.items())],
        students_on_receivable=len(everything.on_receivable) if m.party_mode == "student" else 0,
        ready=(
            row is not None
            and count > 0
            and not everything.open_days
            and not everything.missing
            and count <= rules().vouchers.max_vouchers
        ),
    )


def export(session: Session, ctx: UserContext, data: VoucherExportIn) -> tuple[str, bytes]:
    """The XML file for the period (``fee.report``, recent MFA sign-in). Returns (filename,
    bytes). 409 ``not_configured`` / ``days_not_closed`` / ``nothing_to_export``; 422
    ``mapping_incomplete`` / ``too_many_vouchers``. Records the export and the vouchers it
    carried first; audited ``fee.report.downloaded`` in the same transaction."""
    _fees_on(session, ctx)
    _school_wide(ctx, REPORT)
    _recent_mfa(ctx)
    _check_period(session, data)
    row = vrepo.get_settings(session, lock=True)  # one export at a time per school
    if row is None:
        raise Conflict(
            "Map the Tally ledgers first (Settings > Tally > Voucher export).",
            code="not_configured",
        )
    m = _mapping(row)
    p = plan(
        session, ctx, m, data.period_from, data.period_to, include_exported=data.include_exported
    )
    if p.open_days:
        shown = ", ".join(d.isoformat() for d in p.open_days[:5])
        raise Conflict(
            f"Close these days in the daily collection report first: {shown}"
            + (f" and {len(p.open_days) - 5} more." if len(p.open_days) > 5 else "."),
            code="days_not_closed",
        )
    if p.missing:
        raise ValidationFailed(
            [_error(k, "mapping_incomplete") for k in sorted(p.missing)],
            detail="Some Tally ledgers are not mapped yet. Map them, then export again.",
        )
    if not p.vouchers:
        raise Conflict(
            "Everything in this period is already in an earlier file. Choose 'Include vouchers "
            "already exported' to download them again.",
            code="nothing_to_export",
        )
    if len(p.vouchers) > rules().vouchers.max_vouchers:
        raise ValidationFailed(
            [_error("period_to", "too_many_vouchers")],
            detail="Too many vouchers for one file. Export a shorter period.",
        )
    body = render([v.voucher for v in p.vouchers], company=m.company)
    new = [v for v in p.vouchers if not v.repeat]
    export_row = vrepo.insert_export(
        session,
        {
            "id": new_id(),
            "period_from": data.period_from,
            "period_to": data.period_to,
            "include_exported": data.include_exported,
            "include_demand": m.include_demand,
            "party_mode": m.party_mode,
            "vouchers": len(p.vouchers),
            "new_vouchers": len(new),
            "cash_total": sum((v.cash_in - v.cash_out for v in p.vouchers), ZERO),
            "file_sha256": hashlib.sha256(body).digest(),
            "created_by": ctx.user_id,
        },
    )
    vrepo.record_vouchers(
        session,
        [
            {"id": new_id(), "voucher_key": key, "kind": v.kind, "export_id": export_row.id}
            for v in new
            for key in v.keys
        ],
    )
    audit.record(
        session,
        action="fee.report.downloaded",
        resource_type="tally_voucher_export",
        resource_id=export_row.id,
        summary={
            "report": "tally_vouchers",
            "from": data.period_from.isoformat(),
            "to": data.period_to.isoformat(),
            "vouchers": len(p.vouchers),
            "new_vouchers": len(new),
            "include_exported": data.include_exported,
        },
    )
    log.info(
        "tally.vouchers.exported",
        resource_type="tally_voucher_export",
        resource_id=export_row.id,
    )
    name = f"tally-vouchers-{data.period_from.isoformat()}-to-{data.period_to.isoformat()}.xml"
    return name, body


def list_exports(session: Session, ctx: UserContext) -> list[VoucherExportOut]:
    """Earlier files, newest first (``fee.report`` or ``finance.read``)."""
    _fees_on(session, ctx)
    _school_wide(ctx, REPORT, READ)
    rows = vrepo.list_exports(session, rules().vouchers.history_limit)
    who = identity.members_for_users(session, {r.created_by for r in rows})
    return [_export_out(r, who) for r in rows]


def _export_out(
    r: VoucherExport, who: Mapping[uuid.UUID, tuple[uuid.UUID, str]]
) -> VoucherExportOut:
    return VoucherExportOut(
        id=r.id,
        period_from=r.period_from,
        period_to=r.period_to,
        include_exported=r.include_exported,
        include_demand=r.include_demand,
        party_mode="receivable" if r.party_mode == "receivable" else "student",
        vouchers=r.vouchers,
        new_vouchers=r.new_vouchers,
        cash_total=r.cash_total,
        created_by_name=who[r.created_by][1] if r.created_by in who else None,
        created_at=r.created_at,
    )


# --- ledger-vs-Tally check (FR-TALLY-025..030) ---------------------------------------------------


def _sum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO)


def check(session: Session, ctx: UserContext) -> LedgerCheckOut:
    """Tally's balances (last snapshot) against SchoolOS's on Tally's as-of date
    (``finance.read`` school-wide; the connector and fees on, else 404). Only ledgers linked to
    students and the receivable ledger are compared."""
    _fees_on(session, ctx)
    if not connector_enabled(session, ctx.tenant_id):
        raise NotFound()
    _school_wide(ctx, READ)
    m = _mapping(vrepo.get_settings(session))
    as_of = repo.party_counts(session).as_of
    mode = "receivable" if m.party_mode == "receivable" else "student"
    if as_of is None:
        return LedgerCheckOut(
            party_mode=mode,
            as_of=None,
            compared=0,
            matching=0,
            differing=0,
            tally_total=ZERO,
            schoolos_total=ZERO,
            not_exported=0,
            rows=[],
        )
    owed = fees.balances_as_of(session, as_of)
    parties = _parties(session, m)
    present = [b for b in repo.linked_balances(session, None) if b.present]
    rows: list[CheckRow] = []
    tally_total = schoolos_total = ZERO

    if m.party_mode == "student":
        by_party: dict[uuid.UUID, tuple[str, Decimal, set[uuid.UUID]]] = {}
        for b in present:
            if parties.get(b.student_id) != b.ledger_name:
                continue  # students with several links go to the receivable ledger
            _, _, ids = by_party.setdefault(b.party_id, (b.ledger_name, b.closing_balance, set()))
            ids.add(b.student_id)
        for name, tally_balance, ids in by_party.values():
            ours = _sum(owed.get(sid, ZERO) for sid in ids)
            rows.append(_row(name, tally_balance, ours, len(ids)))
        rest = [sid for sid in owed if sid not in parties]
    else:
        rest = list(owed)
    ours = _sum(owed[sid] for sid in rest)
    receivable = vrepo.party_by_name(session, m.receivable_ledger)
    if receivable is not None or ours != ZERO:
        rows.append(
            _row(
                m.receivable_ledger,
                receivable.closing_balance if receivable is not None else None,
                ours,
                sum(1 for sid in rest if owed[sid] != ZERO),
            )
        )
    for r in rows:
        tally_total += r.tally_balance or ZERO
        schoolos_total += r.schoolos_balance or ZERO
    feed = fees.tally_feed_for(
        session,
        as_of - dt.timedelta(days=rules().vouchers.max_period_days - 1),
        as_of,
        demand=m.include_demand,
    )
    keys = [f"{c.kind}:{c.id}" for c in feed.cash] + [f"demand:{d.id}" for d in feed.demand]
    not_exported = len(set(keys) - vrepo.exported_keys(session, keys))
    differing = [r for r in rows if r.state != "matches"]
    differing.sort(key=lambda r: (-abs(r.difference), r.ledger_name))
    return LedgerCheckOut(
        party_mode=mode,
        as_of=as_of,
        compared=len(rows),
        matching=len(rows) - len(differing),
        differing=len(differing),
        tally_total=tally_total,
        schoolos_total=schoolos_total,
        not_exported=not_exported,
        rows=differing[:500],
    )


def _row(name: str, tally: Decimal | None, ours: Decimal, students_count: int) -> CheckRow:
    if tally is None:
        state = "missing_in_tally"
        difference = -ours
    else:
        difference = tally - ours
        state = "matches" if difference == ZERO else "differs"
    return CheckRow(
        ledger_name=name,
        state=state,
        tally_balance=tally,
        schoolos_balance=ours,
        difference=difference,
        students=students_count,
    )


__all__ = [
    "REPORT",
    "check",
    "export",
    "get_settings",
    "list_exports",
    "plan",
    "preview",
    "put_settings",
]
