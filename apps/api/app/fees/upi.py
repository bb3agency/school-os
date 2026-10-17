"""UPI settings, the static QR, the counter's UTR check, UPI exceptions and the confirmed vs
unconfirmed report (R2-2E; ADR-0042 decision 9; owner decision D19; FR-FEE-014, FR-FEE-015,
FR-FEE-017). Internal: other modules use ``app.fees.service``.

- **Money never touches SchoolOS (ADR-0042 decision 2).** The QR is the school's own static UPI
  QR (its UPI ID and payee name, no amount); the parent pays the school's bank account. A UPI
  receipt is issued at once at the counter with the UTR the parent shows (D19) and is confirmed
  later by a person from the bank statement (``app.fees.statements``).
- **Permissions.** Settings: read with any of ``fee.collect``, ``fee.manage``, ``finance.read``,
  ``fee.reconcile``; write ``fee.manage``. UTR check: ``fee.collect``. Exceptions:
  ``fee.reconcile``. Report: ``fee.reconcile`` or ``finance.read``. School-wide only.
- **Audit** (same transaction; ids, codes and counts only, never the UPI ID, payee name or a UTR):
  ``fee.upi.settings_updated``.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Final

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.fees import _common as c
from app.fees import repository as fees_repo
from app.fees import upi_repository as repo
from app.fees.models import Receipt
from app.fees.money import ZERO
from app.fees.upi_config import UpiConfig, load_upi_config
from app.fees.upi_models import UpiSettings
from app.fees.upi_qr import qr_svg, upi_uri
from app.fees.upi_schemas import (
    UnconfirmedReceipt,
    UpiReportOut,
    UpiReportRow,
    UpiSettingsIn,
    UpiSettingsOut,
    UtrCheckOut,
)

SETTINGS_READERS: Final = (c.COLLECT, c.MANAGE, c.READ, c.RECONCILE)
REPORT_READERS: Final = (c.RECONCILE, c.READ)
MAX_REPORT_DAYS: Final = 366


def config() -> UpiConfig:
    return load_upi_config()


@contextmanager
def db_errors() -> Iterator[None]:
    """UPI and statement constraints first, then the fee ledger's (``fees.repository``)."""
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc) or fees_repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


class UpiNotSet(NotFound):
    code = "upi_not_set"

    def __init__(self) -> None:
        super().__init__(
            "The school's UPI ID is not set yet. The accountant adds it in Settings > UPI."
        )


# --- settings (FR-FEE-015) ----------------------------------------------------------------------


def _out(row: UpiSettings | None) -> UpiSettingsOut:
    if row is None:
        return UpiSettingsOut(
            configured=False,
            payee_name=None,
            vpa=None,
            confirm_within_days=config().upi.confirm_within_days_default,
            upi_uri=None,
            version=0,
        )
    return UpiSettingsOut(
        configured=True,
        payee_name=row.payee_name,
        vpa=row.vpa,
        confirm_within_days=row.confirm_within_days,
        upi_uri=upi_uri(row.vpa, row.payee_name),
        version=row.version,
    )


def get_settings(session: Session, ctx: UserContext) -> UpiSettingsOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, *SETTINGS_READERS)
    return _out(repo.get_settings(session))


def put_settings(
    session: Session, ctx: UserContext, data: UpiSettingsIn, *, expected_version: int | None
) -> UpiSettingsOut:
    """Set the school's payee name, UPI ID and confirmation window (``fee.manage``; ``If-Match``
    once set). The UPI ID is checked for its shape only, never with a bank (FR-FEE-015)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    c.refuse_aadhaar("payee_name", data.payee_name)
    row = repo.get_settings(session, lock=True)
    values = {
        "payee_name": " ".join(data.payee_name.split()),
        "vpa": data.vpa,
        "confirm_within_days": data.confirm_within_days,
        "updated_by": ctx.membership_id,
    }
    with db_errors():
        if row is None:
            if expected_version not in (None, 0):
                raise PreconditionFailed(
                    "This was changed by someone else. Reload it and try again."
                )
            vpa_changed = True
            row = repo.insert_settings(session, {"id": new_id(), **values})
        else:
            c.check_version(row, expected_version)
            vpa_changed = row.vpa != data.vpa or row.payee_name != values["payee_name"]
            row = repo.update_settings(session, row.id, values)
    c.record(
        session,
        action="fee.upi.settings_updated",
        resource_type="upi_settings",
        resource_id=row.id,
        summary={
            "payee_changed": vpa_changed,
            "confirm_within_days": row.confirm_within_days,
            "version": row.version,
        },
    )
    return _out(row)


def qr_image(session: Session, ctx: UserContext) -> str:
    """The school's static UPI QR as SVG (404 ``upi_not_set`` until the settings exist)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, *SETTINGS_READERS)
    row = repo.get_settings(session)
    if row is None:
        raise UpiNotSet()
    return qr_svg(
        upi_uri(row.vpa, row.payee_name),
        label=f"UPI QR code for {row.payee_name}",
        error=config().upi.qr_error,
    )


def payee(session: Session) -> tuple[str, str, str] | None:
    """``(payee name, UPI ID, upi:// URI)`` of the school, or ``None`` (parent dues page, R2-3H;
    no permission check: the caller decides)."""
    row = repo.get_settings(session)
    if row is None:
        return None
    return row.payee_name, row.vpa, upi_uri(row.vpa, row.payee_name)


# --- UTR at the counter (FR-FEE-014) ------------------------------------------------------------


def check_utr(session: Session, ctx: UserContext, utr: str) -> UtrCheckOut:
    """Before issuing a UPI receipt: is this a 12-digit UTR, is it on another issued receipt,
    and has the bank already reported a credit with it? (``fee.collect``). The amount of a bank
    credit is never shown at the counter."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    cleaned = "".join(utr.split())
    if not re.fullmatch(c.settings().modes.utr_pattern, cleaned):
        return UtrCheckOut(state="invalid")
    holder = fees_repo.issued_receipt_with_utr(session, cleaned)
    seen = repo.lines_with_utr(session, cleaned)
    if holder is not None:
        return UtrCheckOut(
            state="in_use",
            receipt_id=holder.id,
            receipt_number=holder.receipt_number,
            seen_in_bank=seen,
        )
    return UtrCheckOut(state="free", seen_in_bank=seen)


# --- exceptions and report (FR-FEE-017) ---------------------------------------------------------


def confirm_within_days(session: Session) -> int:
    row = repo.get_settings(session)
    return row.confirm_within_days if row else config().upi.confirm_within_days_default


def unconfirmed_out(
    session: Session, rows: list[Receipt], today: dt.date
) -> list[UnconfirmedReceipt]:
    out = []
    for r in rows:
        student = r.content.get("student") or {}
        line = repo.proposal_for_receipt(session, r.id)
        out.append(
            UnconfirmedReceipt(
                id=r.id,
                receipt_number=r.receipt_number,
                receipt_date=r.receipt_date,
                amount=r.amount,
                utr=r.utr,
                student_name=str(student.get("name", "")),
                class_section=str(student.get("class_section", "")),
                days_waiting=(today - r.receipt_date).days,
                proposed_line_id=line.id if line and line.match_status == "proposed" else None,
            )
        )
    return out


def exception_counts(session: Session) -> tuple[int, int]:
    """``(UPI receipts unconfirmed past the window, credits needing a person)`` for the morning
    notification (FR-FEE-026)."""
    today = c.today(session)
    cutoff = today - dt.timedelta(days=confirm_within_days(session))
    _, overdue = repo.unconfirmed_receipts(session, dated_before=cutoff, limit=0)
    _, lines = repo.exception_lines(session, 0)
    return overdue, lines


def report(
    session: Session, ctx: UserContext, *, date_from: dt.date | None, date_to: dt.date | None
) -> UpiReportOut:
    """Issued UPI receipts per receipt date, confirmed from the bank vs not yet (``fee.reconcile``
    or ``finance.read``). Default: the last 30 days; at most a year."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, *REPORT_READERS)
    today = c.today(session)
    end = date_to or today
    start = date_from or end - dt.timedelta(days=30)
    if start > end or (end - start).days > MAX_REPORT_DAYS:
        raise ValidationFailed([c.error("date_from", "invalid_range")])
    per: dict[dt.date, list[tuple[int, Decimal]]] = {}
    for day, confirmed, count, total in repo.upi_report(session, start, end):
        slot = per.setdefault(day, [(0, ZERO), (0, ZERO)])
        slot[0 if confirmed else 1] = (count, total)
    rows = [
        UpiReportRow(
            receipt_date=day,
            confirmed_count=v[0][0],
            confirmed_total=v[0][1],
            unconfirmed_count=v[1][0],
            unconfirmed_total=v[1][1],
        )
        for day, v in sorted(per.items())
    ]
    cutoff = today - dt.timedelta(days=confirm_within_days(session))
    overdue = sum(r.unconfirmed_count for r in rows if r.receipt_date <= cutoff)
    return UpiReportOut(
        date_from=start,
        date_to=end,
        rows=rows,
        confirmed_count=sum(r.confirmed_count for r in rows),
        confirmed_total=sum((r.confirmed_total for r in rows), ZERO),
        unconfirmed_count=sum(r.unconfirmed_count for r in rows),
        unconfirmed_total=sum((r.unconfirmed_total for r in rows), ZERO),
        overdue_count=overdue,
    )


def bank_confirmed_at(session: Session, receipt_id: uuid.UUID) -> dt.datetime | None:
    """When a UPI receipt was confirmed from the bank statement (``None``: not yet)."""
    return repo.confirmation_times(session, [receipt_id]).get(receipt_id)


__all__ = [
    "UpiNotSet",
    "bank_confirmed_at",
    "check_utr",
    "exception_counts",
    "get_settings",
    "payee",
    "put_settings",
    "qr_image",
    "report",
]
