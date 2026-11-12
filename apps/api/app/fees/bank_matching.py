"""Bank statement matching for cheques and bank transfers; cheque clearing status (FM-08;
FR-FEE-126..150; ADR-0042 decision 9, P9; owner decision D19). Internal: other modules use
``app.fees.service``.

- **Proposals, never postings (FR-FEE-127).** Next to the UPI rules of :mod:`app.fees.statements`,
  two rules (``bank_matching.yaml``) propose: the cheque number and amount of an open cheque
  receipt (``cheque_exact``), and the NEFT/RTGS/IMPS reference and amount of an open bank transfer
  receipt (``transfer_exact``), each inside its date window and only when exactly one receipt
  fits. A person confirms; the confirmation is a row of ``fin.upi_confirmations`` (the "seen in
  the bank" facts; the receipt is never edited) and nothing touches the ledger. The school is the
  merchant; SchoolOS never holds money.
- **Cheque status (FR-FEE-131..137)** is a list of append-only ``fin.cheque_status_events``:
  ``deposited`` (a person took it to the bank: the deposit slip), ``cleared`` (written in the same
  transaction as the bank confirmation of the cheque receipt), ``returned`` (the bank returned it:
  a ``cheque_dishonoured`` cancellation request is raised through the existing maker-checker in
  the same transaction; the cancellation, not this module, reverses the ledger when approved).
- **Audit** (same transaction; ids and counts only): ``fee.cheque.deposited`` (counts),
  ``fee.cheque.returned`` (receipt id, cancellation id), ``fee.cheque.deposit_slip_viewed``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.fees import _common as c
from app.fees import approvals, cheque_templates, upi
from app.fees import bank_matching_repository as repo
from app.fees.bank_matching_config import BankMatchingConfig, load_bank_matching_config
from app.fees.bank_matching_schemas import (
    ChequeDepositIn,
    ChequeDepositOut,
    ChequeListOut,
    ChequeOut,
    ChequeReturnIn,
    ChequeReturnOut,
    ChequeSkipped,
    ChequeStatus,
)
from app.fees.cheque_models import ChequeStatusEvent
from app.fees.models import Receipt
from app.fees.money import group_indian
from app.fees.schemas import CancellationRequest
from app.fees.templates import format_date
from app.fees.upi_models import BankStatementLine
from app.tenancy import service as tenancy

log = get_logger(__name__)

BANK_MODES: Final = frozenset({"cheque", "bank_transfer"})
_MAX_RANGE_DAYS: Final = 366


def _cfg() -> BankMatchingConfig:
    return load_bank_matching_config()


# --- matching (proposals only; FR-FEE-127) --------------------------------------------------------


def _refs(line: object) -> set[str]:
    refs: set[str] = set()
    transfer = getattr(line, "transfer_ref", None)
    if transfer:
        refs.add(str(transfer).upper())
    other = getattr(line, "utr_or_ref", None) or getattr(line, "reference", None)
    if other:
        refs.add(str(other).upper())
    return refs


def _in_window(receipt: Receipt, value_date: dt.date, before: int, after: int) -> bool:
    return (
        receipt.receipt_date - dt.timedelta(days=before)
        <= value_date
        <= receipt.receipt_date + dt.timedelta(days=after)
    )


def propose(
    session: Session,
    lines: Sequence[tuple[int, object]],
    taken: set[uuid.UUID],
) -> dict[int, tuple[uuid.UUID, str]]:
    """``{index: (receipt id, rule)}`` for lines a cheque or transfer rule matches. ``taken``
    (receipts already proposed in this run) is extended. A receipt wanted by two credits is
    proposed for neither."""
    cfg = _cfg()
    wanted: dict[uuid.UUID, list[tuple[int, str]]] = {}
    if cfg.matching.cheque_exact:
        cheque_lines = [(i, x) for i, x in lines if getattr(x, "cheque_no", None)]
        receipts = repo.open_cheque_receipts(
            session, {str(getattr(x, "cheque_no", "")) for _, x in cheque_lines}
        )
        spec = cfg.cheques
        for index, line in cheque_lines:
            fits = [
                r
                for r in receipts
                if r.id not in taken
                and r.mode_details.get("cheque_number") == getattr(line, "cheque_no", None)
                and r.amount == getattr(line, "amount", None)
                and _in_window(
                    r,
                    getattr(line, "value_date"),  # noqa: B009 - ParsedLine or BankStatementLine
                    spec.credit_days_before_receipt,
                    spec.credit_days_after_receipt,
                )
            ]
            if len(fits) == 1:
                wanted.setdefault(fits[0].id, []).append((index, "cheque_exact"))
    if cfg.matching.transfer_exact:
        transfer_lines = [
            (i, x)
            for i, x in lines
            if not getattr(x, "cheque_no", None) and _refs(x) and not _is_upi_line(x)
        ]
        all_refs = set().union(*(_refs(x) for _, x in transfer_lines)) if transfer_lines else set()
        receipts = repo.open_transfer_receipts(session, all_refs)
        spec_t = cfg.transfers
        for index, line in transfer_lines:
            refs = _refs(line)
            fits = [
                r
                for r in receipts
                if r.id not in taken
                and str(r.mode_details.get("reference", "")).upper() in refs
                and r.amount == getattr(line, "amount", None)
                and _in_window(
                    r,
                    getattr(line, "value_date"),  # noqa: B009 - ParsedLine or BankStatementLine
                    spec_t.credit_days_before_receipt,
                    spec_t.credit_days_after_receipt,
                )
            ]
            if len(fits) == 1:
                wanted.setdefault(fits[0].id, []).append((index, "transfer_exact"))
    out: dict[int, tuple[uuid.UUID, str]] = {}
    claimed: set[int] = set()
    for receipt_id, hits in wanted.items():
        if len(hits) == 1 and hits[0][0] not in claimed:
            out[hits[0][0]] = (receipt_id, hits[0][1])
            claimed.add(hits[0][0])
            taken.add(receipt_id)
    return out


def _is_upi_line(line: object) -> bool:
    """A credit with a UTR and no transfer reference is a UPI credit for the transfer rule
    (an IMPS credit carries its reference in ``transfer_ref``)."""
    return getattr(line, "ref_kind", None) == "utr" and not getattr(line, "transfer_ref", None)


def bank_facts_conflict(session: Session, line: BankStatementLine, receipt: Receipt) -> str | None:
    """Why ``line`` cannot confirm the cheque or transfer ``receipt`` (the database checks the
    cheque number and the return too), else ``None``. Amount and status are checked by the
    caller."""
    if receipt.mode == "cheque":
        if line.cheque_no and line.cheque_no != receipt.mode_details.get("cheque_number"):
            return "cheque_number_differs"
        if repo.returned_receipt_ids(session, [receipt.id]):
            return "cheque_returned"
    return None


def after_confirm(
    session: Session, ctx: UserContext, line: BankStatementLine, receipt: Receipt
) -> None:
    """A confirmed cheque receipt is "cleared (seen in statement)" from the line's value date
    (FR-FEE-133). Already cleared by hand: nothing more."""
    if receipt.mode != "cheque":
        return
    events = repo.events_of(session, [receipt.id]).get(receipt.id, [])
    if any(e.status == "cleared" for e in events):
        return
    with upi.db_errors():
        repo.insert_event(
            session,
            {
                "id": new_id(),
                "receipt_id": receipt.id,
                "status": "cleared",
                "event_date": line.value_date,
                "source": "statement",
                "statement_line_id": line.id,
                "recorded_by": ctx.membership_id,
            },
        )


# --- cheque status (FR-FEE-131..137) --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _State:
    status: ChequeStatus
    deposited_on: dt.date | None
    cleared_on: dt.date | None
    cleared_from_statement: bool
    returned_on: dt.date | None
    cancellation_id: uuid.UUID | None


def _state(events: Sequence[ChequeStatusEvent]) -> _State:
    by = {e.status: e for e in events}
    status: ChequeStatus = "received"
    for key in ("deposited", "cleared", "returned"):
        if key in by:
            status = key
    cleared = by.get("cleared")
    returned = by.get("returned")
    return _State(
        status=status,
        deposited_on=by["deposited"].event_date if "deposited" in by else None,
        cleared_on=cleared.event_date if cleared else None,
        cleared_from_statement=bool(cleared and cleared.source == "statement"),
        returned_on=returned.event_date if returned else None,
        cancellation_id=returned.cancellation_id if returned else None,
    )


def returned_ids(session: Session, receipt_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    """The cheque receipts among ``receipt_ids`` the bank returned."""
    return repo.returned_receipt_ids(session, receipt_ids)


def _cheque_out(
    receipt: Receipt, events: Sequence[ChequeStatusEvent], today: dt.date, due_days: int
) -> ChequeOut:
    state = _state(events)
    details = receipt.mode_details or {}
    student = receipt.content.get("student") or {}
    raw_date = details.get("cheque_date")
    return ChequeOut(
        receipt_id=receipt.id,
        receipt_number=receipt.receipt_number,
        receipt_date=receipt.receipt_date,
        receipt_status=receipt.status,
        amount=receipt.amount,
        cheque_number=str(details.get("cheque_number", "")),
        bank_name=str(details.get("bank_name", "")),
        cheque_date=dt.date.fromisoformat(raw_date) if raw_date else None,
        student_name=str(student.get("name", "")),
        class_section=str(student.get("class_section", "")),
        cheque_status=state.status,
        deposited_on=state.deposited_on,
        cleared_on=state.cleared_on,
        cleared_from_statement=state.cleared_from_statement,
        returned_on=state.returned_on,
        cancellation_id=state.cancellation_id,
        deposit_overdue=(
            state.status == "received"
            and receipt.status == "issued"
            and receipt.receipt_date + dt.timedelta(days=due_days) < today
        ),
    )


def _range(
    session: Session, date_from: dt.date | None, date_to: dt.date | None
) -> tuple[dt.date, dt.date]:
    today = c.today(session)
    last = date_to or today
    first = date_from or (last - dt.timedelta(days=30))
    if first > last:
        raise ValidationFailed([c.error("date_from", "after_date_to")])
    if (last - first).days > _MAX_RANGE_DAYS:
        raise ValidationFailed([c.error("date_from", "range_too_long")])
    return first, last


def list_cheques(
    session: Session,
    ctx: UserContext,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    status: ChequeStatus | None = None,
) -> ChequeListOut:
    """Cheque receipts of a receipt-date range (default the last 30 days) with their cheque
    status (``fee.collect`` or ``fee.reconcile``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.RECONCILE)
    spec = _cfg().cheques_list
    first, last = _range(session, date_from, date_to)
    today = c.today(session)
    rows = repo.cheque_receipts(session, date_from=first, date_to=last, limit=spec.max_rows + 1)
    truncated = len(rows) > spec.max_rows
    rows = rows[: spec.max_rows]
    events = repo.events_of(session, [r.id for r in rows])
    items = [_cheque_out(r, events.get(r.id, []), today, spec.deposit_due_days) for r in rows]
    counts = dict.fromkeys(("received", "deposited", "cleared", "returned"), 0)
    for item in items:
        counts[item.cheque_status] += 1
    if status is not None:
        items = [x for x in items if x.cheque_status == status]
    return ChequeListOut(
        as_of=today,
        date_from=first,
        date_to=last,
        deposit_due_days=spec.deposit_due_days,
        items=items,
        counts=counts,
        truncated=truncated,
    )


def mark_deposited(session: Session, ctx: UserContext, data: ChequeDepositIn) -> ChequeDepositOut:
    """Mark cheques as taken to the bank on ``deposited_on`` (``fee.collect`` or
    ``fee.reconcile``; FR-FEE-132). Each receipt is checked under lock; others are listed in
    ``skipped`` (``not_a_cheque``, ``receipt_cancelled``, ``before_receipt``, ``not_found``).
    Marking again is harmless (``already_deposited``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.RECONCILE)
    if len(data.receipt_ids) > _cfg().cheques_list.deposit_batch_max:
        raise ValidationFailed([c.error("receipt_ids", "too_many")])
    today = c.today(session)
    if data.deposited_on > today:
        raise ValidationFailed([c.error("deposited_on", "in_future")])
    wanted = list(dict.fromkeys(data.receipt_ids))
    rows = repo.receipts_for_update(session, wanted)
    found = {r.id: r for r in rows}
    events = repo.events_of(session, list(found))
    skipped = [ChequeSkipped(receipt_id=i, code="not_found") for i in wanted if i not in found]
    done = already = 0
    for receipt in rows:
        have = {e.status for e in events.get(receipt.id, [])}
        code = None
        if receipt.mode != "cheque":
            code = "not_a_cheque"
        elif have:
            already += 1
            continue
        elif receipt.status != "issued":
            code = "receipt_cancelled"
        elif data.deposited_on < receipt.receipt_date:
            code = "before_receipt"
        if code is not None:
            skipped.append(ChequeSkipped(receipt_id=receipt.id, code=code))
            continue
        with upi.db_errors():
            repo.insert_event(
                session,
                {
                    "id": new_id(),
                    "receipt_id": receipt.id,
                    "status": "deposited",
                    "event_date": data.deposited_on,
                    "source": "manual",
                    "recorded_by": ctx.membership_id,
                },
            )
        done += 1
    c.record(
        session,
        action="fee.cheque.deposited",
        resource_type="fee_cheques",
        resource_id=None,
        summary={
            "deposited": done,
            "already": already,
            "skipped": len(skipped),
            "deposited_on": data.deposited_on.isoformat(),
        },
    )
    log.info("fees.cheques_deposited", count=done)
    return ChequeDepositOut(deposited=done, already_deposited=already, skipped=skipped)


def mark_returned(
    session: Session, ctx: UserContext, receipt_id: uuid.UUID, data: ChequeReturnIn
) -> ChequeReturnOut:
    """The bank returned a deposited cheque (``fee.reconcile``; FR-FEE-134): a
    ``cheque_dishonoured`` cancellation request is raised through the existing maker-checker
    (``fee.collect`` or ``fee.manage`` asks; another holder of ``fee.receipt.cancel`` decides)
    and the cheque is marked returned, in one transaction. The ledger is never edited here.
    409 ``cheque_not_deposited``, ``cheque_already_returned``; 404 for a non-cheque receipt."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    receipt = _load_cheque(session, receipt_id)
    today = c.today(session)
    if data.returned_on > today:
        raise ValidationFailed([c.error("returned_on", "in_future")])
    if data.returned_on < receipt.receipt_date:
        raise ValidationFailed([c.error("returned_on", "before_receipt")])
    events = repo.events_of(session, [receipt.id]).get(receipt.id, [])
    have = {e.status for e in events}
    if "returned" in have:
        raise Conflict("This cheque is already marked as returned.", code="cheque_already_returned")
    if not have & {"deposited", "cleared"}:
        raise Conflict(
            "Mark the cheque as deposited first. Only a deposited cheque can be returned by the "
            "bank.",
            code="cheque_not_deposited",
        )
    cancellation = approvals.request_cancellation(
        session,
        ctx,
        receipt.id,
        CancellationRequest(reason_code="cheque_dishonoured", reason=data.reason),
    )
    with upi.db_errors():
        repo.insert_event(
            session,
            {
                "id": new_id(),
                "receipt_id": receipt.id,
                "status": "returned",
                "event_date": data.returned_on,
                "source": "manual",
                "cancellation_id": cancellation.id,
                "recorded_by": ctx.membership_id,
            },
        )
    c.record(
        session,
        action="fee.cheque.returned",
        resource_type="fee_receipt",
        resource_id=receipt.id,
        summary={
            "cancellation_id": str(cancellation.id),
            "returned_on": data.returned_on.isoformat(),
        },
    )
    spec = _cfg().cheques_list
    fresh = repo.events_of(session, [receipt.id]).get(receipt.id, [])
    return ChequeReturnOut(
        cheque=_cheque_out(receipt, fresh, today, spec.deposit_due_days),
        cancellation=cancellation,
    )


def _load_cheque(session: Session, receipt_id: uuid.UUID) -> Receipt:
    rows = repo.receipts_for_update(session, [receipt_id])
    if not rows or rows[0].mode != "cheque":
        raise NotFound("Cheque receipt not found")
    return rows[0]


def deposit_slip_page(session: Session, ctx: UserContext, day: dt.date | None) -> str:
    """The A4 list of cheques marked deposited on ``day`` (default today) for the bank's pay-in
    slip (``fee.collect`` or ``fee.reconcile``; FR-FEE-136). No student names: the bank needs the
    cheque, its bank and amount. Audit ``fee.cheque.deposit_slip_viewed``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.RECONCILE)
    when = day or c.today(session)
    rows = repo.deposited_on(session, when, _cfg().cheques_list.max_rows)
    total = sum((r.amount for r in rows), start=Decimal("0.00"))
    page = cheque_templates.render_deposit_slip(
        school_name=tenancy.get_tenant(session).name,
        day=format_date(when),
        rows=[
            (
                str(n),
                str(r.mode_details.get("cheque_number", "")),
                str(r.mode_details.get("bank_name", "")),
                format_date(r.mode_details.get("cheque_date")),
                r.receipt_number,
                group_indian(r.amount),
            )
            for n, r in enumerate(rows, start=1)
        ],
        total=group_indian(total),
        count=len(rows),
    )
    c.record(
        session,
        action="fee.cheque.deposit_slip_viewed",
        resource_type="fee_cheques",
        resource_id=None,
        summary={"day": when.isoformat(), "cheques": len(rows)},
    )
    return page


__all__ = [
    "BANK_MODES",
    "after_confirm",
    "bank_facts_conflict",
    "deposit_slip_page",
    "list_cheques",
    "mark_deposited",
    "mark_returned",
    "propose",
    "returned_ids",
]
