"""Database access for the control plane (schema ``platform`` via ``platform_session``).

Bound parameters only. Generic row helpers plus the few queries that need locking or
aggregation. The only calls outside schema ``platform`` are the allowlisted definer functions
(``core.create_owner_invite``, ``core.tenant_usage_summary`` via tenancy.service,
``core.set_tenant_status`` via tenancy.service).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    RowMapping,
    Table,
    and_,
    delete,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.platform import models as m

# --- error translation ------------------------------------------------------------------------

_CONSTRAINT_MESSAGES: dict[str, tuple[str, str]] = {
    "deployments_offboard_two_person": ("same_operator", "A different operator must approve."),
    "breakglass_two_person": ("same_operator", "A different operator must confirm."),
    "operator_roles_no_self_grant": ("own_roles", "You cannot change your own roles."),
    "one_live_subscription": ("duplicate", "This school already has a live subscription."),
    "one_invoice_per_period": ("duplicate", "An invoice already exists for this period."),
    "deployments_tenant_code_key": ("duplicate", "This school code is already used."),
    "deployments_tenant_id_key": ("duplicate", "This school already has a deployment."),
    "deployments_custom_domain_key": ("duplicate", "This domain is already used."),
    "provisioning_runs_tenant_code_key": ("duplicate", "This school code is already used."),
    "operators_email_key": ("duplicate", "An operator with this email exists."),
    "operators_idp_subject_key": ("duplicate", "An operator with this sign-in exists."),
    "payments_tenant_id_method_reference_key": (
        "duplicate",
        "A payment with this reference was already recorded.",
    ),
    "plans_code_version_key": ("duplicate", "This plan version already exists."),
    "billing_accounts_gstin_state": ("invalid", "GSTIN must start with the state code."),
    "billing_accounts_gstin_pan": ("invalid", "GSTIN must contain the PAN."),
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:  # noqa: PLR0911
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) or ""
    known = _CONSTRAINT_MESSAGES.get(constraint)
    if state in ("23505", "23514") and known is not None:
        code, message = known
        if code == "invalid":
            return ValidationFailed(
                [{"field": "gstin", "code": code, "message_key": "errors.gstin"}]
            )
        return Conflict(message, code=code)
    if state == "23505":
        return Conflict("This already exists.", code="duplicate")
    if state == "23514":
        return ValidationFailed(
            [{"field": constraint or "body", "code": "invalid", "message_key": "errors.invalid"}]
        )
    if state == "23503":
        return ValidationFailed(
            [
                {
                    "field": constraint or "body",
                    "code": "not_found",
                    "message_key": "errors.not_found",
                }
            ]
        )
    if state == "P0002":
        return NotFound()
    if state == "55000":
        return Conflict(
            getattr(diag, "message_primary", None) or None, code=constraint or "invalid_state"
        )
    if state == "42501":
        return Forbidden()
    return None


# --- generic helpers --------------------------------------------------------------------------


def get(
    session: Session, table: Table, row_id: uuid.UUID, *, for_update: bool = False
) -> RowMapping | None:
    stmt = select(table).where(table.c.id == row_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().one_or_none()


def get_by(
    session: Session, table: Table, *conditions: ColumnElement[bool], for_update: bool = False
) -> RowMapping | None:
    stmt = select(table).where(*conditions)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().first()


def insert_row(session: Session, table: Table, values: Mapping[str, Any]) -> RowMapping:
    return session.execute(insert(table).values(**values).returning(table)).mappings().one()


def update_row(
    session: Session,
    table: Table,
    row_id: uuid.UUID,
    values: Mapping[str, Any],
    *,
    bump_version: bool = True,
) -> RowMapping:
    vals = dict(values)
    if bump_version and "version" in table.c:
        vals["version"] = table.c.version + 1
    return (
        session.execute(update(table).where(table.c.id == row_id).values(**vals).returning(table))
        .mappings()
        .one()
    )


def delete_row(session: Session, table: Table, row_id: uuid.UUID) -> None:
    session.execute(delete(table).where(table.c.id == row_id))


def list_rows(
    session: Session,
    table: Table,
    *conditions: ColumnElement[bool],
    limit: int,
    cursor: uuid.UUID | None = None,
    descending: bool = True,
) -> list[RowMapping]:
    """Keyset page on ``id`` (UUIDv7 is time ordered)."""
    stmt = select(table).where(*conditions)
    if cursor is not None:
        stmt = stmt.where(table.c.id < cursor if descending else table.c.id > cursor)
    order = table.c.id.desc() if descending else table.c.id.asc()
    return list(session.execute(stmt.order_by(order).limit(limit + 1)).mappings())


# --- operators --------------------------------------------------------------------------------


def operator_by_subject(session: Session, subject: str) -> RowMapping | None:
    return get_by(session, m.operators, m.operators.c.idp_subject == subject)


def operator_roles(session: Session, operator_id: uuid.UUID) -> set[str]:
    rows: Any = session.execute(
        select(m.operator_roles.c.role_key).where(m.operator_roles.c.operator_id == operator_id)
    ).scalars()
    return set(rows)


def roles_by_operator(
    session: Session, operator_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    result: dict[uuid.UUID, list[str]] = {i: [] for i in operator_ids}
    rows = session.execute(
        select(m.operator_roles.c.operator_id, m.operator_roles.c.role_key)
        .where(m.operator_roles.c.operator_id.in_(list(operator_ids)))
        .order_by(m.operator_roles.c.role_key)
    )
    for oid, role in rows:
        result.setdefault(oid, []).append(role)
    return result


def set_operator_roles(
    session: Session, operator_id: uuid.UUID, roles: Sequence[str], granted_by: uuid.UUID | None
) -> None:
    session.execute(delete(m.operator_roles).where(m.operator_roles.c.operator_id == operator_id))
    for role in roles:
        session.execute(
            insert(m.operator_roles).values(
                operator_id=operator_id, role_key=role, granted_by=granted_by
            )
        )


def active_owner_ids(session: Session, *, lock: bool = False) -> list[uuid.UUID]:
    stmt = (
        select(m.operators.c.id)
        .join(m.operator_roles, m.operator_roles.c.operator_id == m.operators.c.id)
        .where(m.operators.c.status == "active", m.operator_roles.c.role_key == "platform_owner")
    )
    if lock:
        # Serialise owner-set changes so two concurrent demotions cannot remove the last owner.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('platform.owner_set'))"))
    return list(session.execute(stmt).scalars())


# --- plans / subscriptions / billing ----------------------------------------------------------


def next_plan_version(session: Session, code: str) -> int:
    current: Any = session.execute(
        select(func.max(m.plans.c.version)).where(m.plans.c.code == code)
    ).scalar_one()
    return int(current or 0) + 1


def live_subscription(
    session: Session, tenant_id: uuid.UUID, *, for_update: bool = False
) -> RowMapping | None:
    return get_by(
        session,
        m.subscriptions,
        m.subscriptions.c.tenant_id == tenant_id,
        m.subscriptions.c.status != "cancelled",
        for_update=for_update,
    )


def invoice_lines(session: Session, invoice_id: uuid.UUID) -> list[RowMapping]:
    return list(
        session.execute(
            select(m.invoice_lines)
            .where(m.invoice_lines.c.invoice_id == invoice_id)
            .order_by(m.invoice_lines.c.line_no)
        ).mappings()
    )


def replace_invoice_lines(
    session: Session, invoice_id: uuid.UUID, lines: Sequence[Mapping[str, Any]]
) -> None:
    session.execute(delete(m.invoice_lines).where(m.invoice_lines.c.invoice_id == invoice_id))
    for line in lines:
        session.execute(insert(m.invoice_lines).values(invoice_id=invoice_id, **line))


def next_invoice_number(session: Session, financial_year: str, prefix: str) -> int:
    """Gapless per financial year: row lock on the sequence, in the issuing transaction."""
    session.execute(
        pg_insert(m.invoice_sequences)
        .values(financial_year=financial_year, prefix=prefix, last_number=0)
        .on_conflict_do_nothing(index_elements=[m.invoice_sequences.c.financial_year])
    )
    last: Any = session.execute(
        select(m.invoice_sequences.c.last_number)
        .where(m.invoice_sequences.c.financial_year == financial_year)
        .with_for_update()
    ).scalar_one()
    number = int(last) + 1
    session.execute(
        update(m.invoice_sequences)
        .where(m.invoice_sequences.c.financial_year == financial_year)
        .values(last_number=number)
    )
    return number


def invoice_pdf(session: Session, invoice_id: uuid.UUID) -> RowMapping | None:
    return (
        session.execute(select(m.invoice_pdfs).where(m.invoice_pdfs.c.invoice_id == invoice_id))
        .mappings()
        .one_or_none()
    )


def insert_invoice_pdf(session: Session, values: Mapping[str, Any]) -> bool:
    """Record a rendered PDF; False when the invoice already has one (another render won)."""
    row = session.execute(
        pg_insert(m.invoice_pdfs)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[m.invoice_pdfs.c.invoice_id])
        .returning(m.invoice_pdfs.c.invoice_id)
    ).first()
    return row is not None


def invoices_without_pdf(session: Session, limit: int) -> list[uuid.UUID]:
    """Numbered invoices (issued, paid or void) that have no stored PDF yet, oldest first."""
    stmt = (
        select(m.invoices.c.id)
        .outerjoin(m.invoice_pdfs, m.invoice_pdfs.c.invoice_id == m.invoices.c.id)
        .where(m.invoices.c.invoice_number.is_not(None), m.invoice_pdfs.c.invoice_id.is_(None))
        .order_by(m.invoices.c.issued_at, m.invoices.c.id)
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def payments_total(session: Session, invoice_id: uuid.UUID) -> tuple[Decimal, Decimal]:
    row = session.execute(
        select(
            func.coalesce(func.sum(m.payments.c.amount_inr), 0),
            func.coalesce(func.sum(m.payments.c.tds_inr), 0),
        ).where(m.payments.c.invoice_id == invoice_id, m.payments.c.status == "recorded")
    ).one()
    return Decimal(row[0]), Decimal(row[1])


def overdue_invoices(session: Session, subscription_id: uuid.UUID, today: dt.date) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(m.invoices)
            .where(
                m.invoices.c.subscription_id == subscription_id,
                m.invoices.c.status == "issued",
                m.invoices.c.due_date < today,
            )
        ).scalar_one()
    )


def subscriptions_with_overdue(session: Session, today: dt.date) -> list[RowMapping]:
    stmt = (
        select(m.subscriptions)
        .where(
            m.subscriptions.c.status == "active",
            select(m.invoices.c.id)
            .where(
                m.invoices.c.subscription_id == m.subscriptions.c.id,
                m.invoices.c.status == "issued",
                m.invoices.c.due_date < today,
            )
            .exists(),
        )
        .with_for_update(of=m.subscriptions)
    )
    return list(session.execute(stmt).mappings())


def subscriptions_due_for_invoice(
    session: Session, month_start: dt.date, month_end: dt.date
) -> list[RowMapping]:
    return list(
        session.execute(
            select(m.subscriptions).where(
                m.subscriptions.c.status.in_(("active", "past_due")),
                m.subscriptions.c.cancel_at_period_end.is_(False),
                m.subscriptions.c.current_period_end >= month_start,
                m.subscriptions.c.current_period_end < month_end,
            )
        ).mappings()
    )


def subscriptions_to_roll(session: Session, today: dt.date) -> list[RowMapping]:
    return list(
        session.execute(
            select(m.subscriptions)
            .where(
                m.subscriptions.c.status.in_(("active", "past_due", "suspended")),
                m.subscriptions.c.current_period_end <= today,
            )
            .with_for_update()
        ).mappings()
    )


def latest_usage(session: Session, tenant_id: uuid.UUID) -> RowMapping | None:
    return (
        session.execute(
            select(m.usage_daily)
            .where(m.usage_daily.c.tenant_id == tenant_id)
            .order_by(m.usage_daily.c.usage_date.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )


def usage_on_or_before(session: Session, tenant_id: uuid.UUID, day: dt.date) -> RowMapping | None:
    return (
        session.execute(
            select(m.usage_daily)
            .where(m.usage_daily.c.tenant_id == tenant_id, m.usage_daily.c.usage_date <= day)
            .order_by(m.usage_daily.c.usage_date.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )


def ai_answers_between(session: Session, tenant_id: uuid.UUID, start: dt.date, end: dt.date) -> int:
    """Sum of the daily billable AI answer counts in [start, end) (counts only)."""
    total: Any = session.execute(
        select(func.coalesce(func.sum(m.usage_daily.c.ai_answers), 0)).where(
            m.usage_daily.c.tenant_id == tenant_id,
            m.usage_daily.c.usage_date >= start,
            m.usage_daily.c.usage_date < end,
        )
    ).scalar_one()
    return int(total)


def ai_answers_to_date(
    session: Session, tenant_id: uuid.UUID, start: dt.date, end: dt.date
) -> tuple[int, dt.date | None]:
    """Sum of the daily billable AI answer counts in [start, end) and the last day counted."""
    row = session.execute(
        select(
            func.coalesce(func.sum(m.usage_daily.c.ai_answers), 0),
            func.max(m.usage_daily.c.usage_date),
        ).where(
            m.usage_daily.c.tenant_id == tenant_id,
            m.usage_daily.c.usage_date >= start,
            m.usage_daily.c.usage_date < end,
        )
    ).one()
    return int(row[0]), row[1]


def subscription_has_line(
    session: Session,
    subscription_id: uuid.UUID,
    *,
    kind: str,
    usage_month: dt.date | None = None,
) -> bool:
    """Does a live (not void) invoice of the subscription carry a line of ``kind`` (for that
    ``usage_month`` when given)? Callers hold the subscription row lock."""
    conds: list[ColumnElement[bool]] = [
        m.invoices.c.subscription_id == subscription_id,
        m.invoices.c.status != "void",
        m.invoice_lines.c.kind == kind,
    ]
    if usage_month is not None:
        conds.append(m.invoice_lines.c.usage_month == usage_month)
    stmt = select(
        select(m.invoice_lines.c.id)
        .join(m.invoices, m.invoices.c.id == m.invoice_lines.c.invoice_id)
        .where(and_(*conds))
        .exists()
    )
    return bool(session.execute(stmt).scalar_one())


def upsert_usage(session: Session, values: Mapping[str, Any]) -> None:
    stmt = pg_insert(m.usage_daily).values(**values)
    cols: dict[str, Any] = {
        k: stmt.excluded[k] for k in values if k not in ("tenant_id", "usage_date")
    }
    cols["collected_at"] = func.now()
    session.execute(
        stmt.on_conflict_do_update(index_elements=["tenant_id", "usage_date"], set_=cols)
    )


def record_threshold(session: Session, values: Mapping[str, Any]) -> bool:
    """Insert a threshold crossing; False if it was already recorded for this period."""
    result = session.execute(
        pg_insert(m.usage_threshold_events)
        .values(**values)
        .on_conflict_do_nothing()
        .returning(m.usage_threshold_events.c.metric)
    ).first()
    return result is not None


def usage_range(
    session: Session, tenant_id: uuid.UUID | None, start: dt.date, end: dt.date
) -> list[RowMapping]:
    conds: list[ColumnElement[bool]] = [
        m.usage_daily.c.usage_date >= start,
        m.usage_daily.c.usage_date <= end,
    ]
    if tenant_id is not None:
        conds.append(m.usage_daily.c.tenant_id == tenant_id)
    return list(
        session.execute(
            select(m.usage_daily)
            .where(and_(*conds))
            .order_by(m.usage_daily.c.usage_date, m.usage_daily.c.tenant_id)
            .limit(5000)
        ).mappings()
    )


# --- flags ------------------------------------------------------------------------------------


def flag_rows(session: Session, key: str | None = None) -> list[RowMapping]:
    stmt = select(m.feature_flags)
    if key is not None:
        stmt = stmt.where(m.feature_flags.c.key == key)
    return list(session.execute(stmt.order_by(m.feature_flags.c.key)).mappings())


def flag_row(session: Session, key: str, tenant_id: uuid.UUID | None) -> RowMapping | None:
    cond = (
        m.feature_flags.c.tenant_id.is_(None)
        if tenant_id is None
        else m.feature_flags.c.tenant_id == tenant_id
    )
    return get_by(session, m.feature_flags, m.feature_flags.c.key == key, cond, for_update=True)


def flags_for_evaluation(session: Session, key: str, tenant_id: uuid.UUID) -> list[RowMapping]:
    return list(
        session.execute(
            select(
                m.feature_flags.c.tenant_id,
                m.feature_flags.c.enabled,
                m.feature_flags.c.rollout_percent,
            ).where(
                m.feature_flags.c.key == key,
                or_(
                    m.feature_flags.c.tenant_id.is_(None), m.feature_flags.c.tenant_id == tenant_id
                ),
            )
        ).mappings()
    )


# --- core bridges (allowlisted definer functions) ---------------------------------------------


def call_create_owner_invite(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    subject: str,
    display_name: str,
    email: str | None,
    language: str,
) -> RowMapping:
    return (
        session.execute(
            text(
                "SELECT user_id, membership_id, owner_role_assigned "
                "FROM core.create_owner_invite(:t, :s, :n, CAST(:e AS public.citext), :l)"
            ),
            {"t": tenant_id, "s": subject, "n": display_name, "e": email, "l": language},
        )
        .mappings()
        .one()
    )


# --- job runs ---------------------------------------------------------------------------------


def start_job(
    session: Session, *, task_name: str, idempotency_key: str, created_by: uuid.UUID | None
) -> tuple[RowMapping, bool]:
    """Return (job, created). An existing job with the key is returned unchanged."""
    row = (
        session.execute(
            pg_insert(m.job_runs)
            .values(
                id=new_id(),
                task_name=task_name,
                idempotency_key=idempotency_key,
                status="running",
                attempts=1,
                created_by=created_by,
                started_at=func.now(),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(m.job_runs)
        )
        .mappings()
        .first()
    )
    if row is not None:
        return row, True
    existing = get_by(session, m.job_runs, m.job_runs.c.idempotency_key == idempotency_key)
    if existing is None:  # pragma: no cover - the conflicting row exists
        raise RuntimeError("job run vanished")
    return existing, False


# --- school-chain copies of platform actions (ADR-0020) ---------------------------------------


def insert_tenant_audit(session: Session, values: Mapping[str, Any]) -> None:
    session.execute(insert(m.tenant_audit_outbox).values(**values))


def claim_tenant_audit(
    session: Session, *, tenant_id: uuid.UUID | None, skip: Sequence[uuid.UUID]
) -> RowMapping | None:
    """Lock the oldest undelivered row that is its school's head (no older undelivered row).

    ``FOR UPDATE SKIP LOCKED``: a head held by another deliverer is skipped, and that school's
    later rows are not heads, so each school's events are delivered strictly in ``seq`` order
    while different schools proceed in parallel. ``skip`` excludes heads that failed in this run.
    """
    o = m.tenant_audit_outbox
    older = o.alias("older")
    has_older = (
        select(older.c.id)
        .where(
            older.c.tenant_id == o.c.tenant_id,
            older.c.delivered_at.is_(None),
            older.c.seq < o.c.seq,
        )
        .exists()
    )
    stmt = select(o).where(o.c.delivered_at.is_(None), ~has_older)
    if tenant_id is not None:
        stmt = stmt.where(o.c.tenant_id == tenant_id)
    if skip:
        stmt = stmt.where(o.c.id.not_in(list(skip)))
    stmt = stmt.order_by(o.c.seq).limit(1).with_for_update(of=o, skip_locked=True)
    return session.execute(stmt).mappings().first()


def mark_tenant_audit_delivered(session: Session, event_id: uuid.UUID) -> None:
    o = m.tenant_audit_outbox
    session.execute(
        update(o)
        .where(o.c.id == event_id, o.c.delivered_at.is_(None))
        .values(delivered_at=func.now(), attempts=o.c.attempts + 1, last_error=None)
    )


def mark_tenant_audit_failed(session: Session, event_id: uuid.UUID, error_code: str) -> int:
    o = m.tenant_audit_outbox
    attempts: int = session.execute(
        update(o)
        .where(o.c.id == event_id)
        .values(attempts=o.c.attempts + 1, last_error=error_code)
        .returning(o.c.attempts)
    ).scalar_one()
    return attempts


def pending_tenant_audit(session: Session) -> tuple[int, dt.datetime | None]:
    """(undelivered rows, oldest created_at) for monitoring."""
    o = m.tenant_audit_outbox
    row = session.execute(
        select(func.count(), func.min(o.c.created_at)).where(o.c.delivered_at.is_(None))
    ).one()
    return int(row[0]), row[1]


# --- provisioning runs (FR-PLT-002, docs/16 §5.4) ----------------------------------------------


def claim_provisioning_run(
    session: Session, tenant_id: uuid.UUID, lease_id: uuid.UUID, lease: dt.timedelta
) -> RowMapping | None:
    """Take the run's lease if it is unfinished and no live lease is held; else ``None``.

    Counts the attempt. The returned row still shows the state the run was left in.
    """
    r = m.provisioning_runs
    return (
        session.execute(
            update(r)
            .where(
                r.c.tenant_id == tenant_id,
                r.c.state != "completed",
                or_(r.c.lease_expires_at.is_(None), r.c.lease_expires_at < func.now()),
            )
            .values(
                lease_id=lease_id,
                lease_expires_at=func.now() + lease,
                attempts=r.c.attempts + 1,
            )
            .returning(r)
        )
        .mappings()
        .first()
    )


def lock_provisioning_run(
    session: Session, run_id: uuid.UUID, lease_id: uuid.UUID
) -> RowMapping | None:
    """Lock the run for the caller's transaction if ``lease_id`` still holds it (fencing)."""
    r = m.provisioning_runs
    stmt = select(r).where(r.c.id == run_id, r.c.lease_id == lease_id).with_for_update()
    return session.execute(stmt).mappings().first()


def update_provisioning_run(
    session: Session, run_id: uuid.UUID, lease_id: uuid.UUID, values: Mapping[str, Any]
) -> RowMapping | None:
    """Change a run only while ``lease_id`` still holds it (fencing); ``None`` if it does not."""
    r = m.provisioning_runs
    return (
        session.execute(
            update(r)
            .where(r.c.id == run_id, r.c.lease_id == lease_id)
            .values(**values)
            .returning(r)
        )
        .mappings()
        .first()
    )
