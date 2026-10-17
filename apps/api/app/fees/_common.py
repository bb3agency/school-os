"""Shared plumbing of the fees module (internal; other modules use ``app.fees.service``).

Gate (FR-FEE-001), school-wide permission checks (FR-FEE-024), audit in the caller's transaction
with ids, codes, numbers and counts only (FR-FEE-025), reason checks, clock and errors.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Final

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core import feature_flags
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    StepUpRequired,
    ValidationFailed,
)
from app.core.logging import get_context
from app.core.redaction import contains_full_aadhaar
from app.fees import repository as repo
from app.fees.config import FeesConfig, load_config
from app.fees.money import IST, financial_year
from app.identity.principal import STEP_UP_MAX_AGE
from app.tenancy import service as tenancy

MODULE: Final = "fees"
READ: Final = "finance.read"
COLLECT: Final = "fee.collect"
MANAGE: Final = "fee.manage"
CONCESSION_REQUEST: Final = "fee.concession.request"
CONCESSION_APPROVE: Final = "fee.concession.approve"
RECEIPT_CANCEL: Final = "fee.receipt.cancel"
REFUND_REQUEST: Final = "fee.refund.request"
REFUND_APPROVE: Final = "fee.refund.approve"
REPORT: Final = "fee.report"
RECONCILE: Final = "fee.reconcile"  # R2-2E: bank statements and UPI confirmations
ALL_PERMISSIONS: Final = (
    READ,
    COLLECT,
    MANAGE,
    CONCESSION_REQUEST,
    CONCESSION_APPROVE,
    RECEIPT_CANCEL,
    REFUND_REQUEST,
    REFUND_APPROVE,
    REPORT,
    RECONCILE,
)
AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
AADHAAR_DETAIL: Final = "Don't enter Aadhaar numbers. Enter only the last 4 digits."


def settings() -> FeesConfig:
    return load_config()


# --- errors --------------------------------------------------------------------------------------


class ModuleHidden(NotFound):
    code = "module_hidden"

    def __init__(self) -> None:
        super().__init__(
            "Fees are not switched on for this school. The owner or principal turns them on in "
            "school settings."
        )


class SelfApprovalForbidden(Forbidden):
    code = "self_approval_forbidden"

    def __init__(self) -> None:
        super().__init__("You made this request, so someone else must decide it.")


class NotRequester(Forbidden):
    code = "not_requester"

    def __init__(self) -> None:
        super().__init__("Only the person who made this request can withdraw it.")


class RequestNotPending(Conflict):
    code = "request_not_pending"

    def __init__(self) -> None:
        super().__init__("This request was already decided or withdrawn. Reload to see it.")


class DayClosed(Conflict):
    code = "day_closed"

    def __init__(self) -> None:
        super().__init__(
            "Today's collection is closed, so nothing more can be dated today. Ask the "
            "accountant; receipts can be issued again tomorrow."
        )


def error(field: str, code: str, message_key: str | None = None) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": message_key or f"errors.{code}"}


@contextmanager
def db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def check_version(row: Any, expected: int | None) -> None:
    if expected is not None and row.version != expected:
        raise PreconditionFailed("This was changed by someone else. Reload it and try again.")


# --- gate and permissions ------------------------------------------------------------------------


def fees_on(session: Session, tenant_id: uuid.UUID) -> bool:
    """FR-FEE-001: the per-school flag AND the school's own switch (``fees_enabled``, which the
    alongside mode leaves off: ``tenancy.module_enabled``)."""
    return feature_flags.is_enabled(
        settings().flag, tenant_id, session=session
    ) and tenancy.module_enabled(session, MODULE)


def require_on(session: Session, ctx: UserContext) -> None:
    if not fees_on(session, ctx.tenant_id):
        raise ModuleHidden()


def require_school_wide(ctx: UserContext, *permissions: str) -> str:
    """Fee permissions are never section-scoped (FR-FEE-024): the caller holds one of
    ``permissions`` school-wide, else 403. Returns the first such permission."""
    for permission in permissions:
        if ctx.has(permission) and ctx.scope_for(permission).school_wide:
            return permission
    raise Forbidden()


def holds(ctx: UserContext, permission: str) -> bool:
    return ctx.has(permission) and ctx.scope_for(permission).school_wide


def require_recent_mfa(ctx: UserContext) -> None:
    """Defence in depth for step-up routes: MFA sign-in within 5 minutes, else 428."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


# --- clock ---------------------------------------------------------------------------------------


def now(session: Session) -> dt.datetime:
    """Transaction time (tests move the clock by patching ``app.fees._common.now``)."""
    return repo.now(session)


def today(session: Session) -> dt.date:
    return now(session).astimezone(IST).date()


def fy(day: dt.date) -> str:
    return financial_year(day, settings().numbering.financial_year_start_month)


def ensure_day_open(session: Session, day: dt.date) -> None:
    """Shared day lock, then 409 ``day_closed`` when the day is closed (FR-FEE-019)."""
    repo.lock_day(session, day, exclusive=False)
    if repo.get_day_close(session, day) is not None:
        raise DayClosed()


# --- text ----------------------------------------------------------------------------------------


def clean_reason(field: str, value: str | None, *, required: bool = True) -> str | None:
    """Reasons and notes: trimmed, 10..1000 characters, never a full Aadhaar number."""
    if value is None or not value.strip():
        if required:
            raise ValidationFailed([error(field, "missing")])
        return None
    text = value.strip()
    refuse_aadhaar(field, text)
    limits = settings().limits
    if len(text) < limits.reason_min_length:
        raise ValidationFailed([error(field, "too_short")])
    if len(text) > limits.reason_max_length:
        raise ValidationFailed([error(field, "too_long")])
    return text


def refuse_aadhaar(field: str, text: str) -> None:
    if contains_full_aadhaar(text):
        raise ValidationFailed(
            [error(field, AADHAAR_CODE, "errors.aadhaar_last4_only")], detail=AADHAAR_DETAIL
        )


# --- audit ---------------------------------------------------------------------------------------


def record(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
    system: bool = False,
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=request_id if isinstance(request_id, str) else None,
    )


def money_text(value: Any) -> str:
    """Amounts go into audit summaries as strings (floats are refused there)."""
    return f"{value:.2f}"


def unique_ids(values: Iterable[uuid.UUID | None]) -> set[uuid.UUID]:
    return {v for v in values if v is not None}


__all__ = [
    "ALL_PERMISSIONS",
    "COLLECT",
    "CONCESSION_APPROVE",
    "CONCESSION_REQUEST",
    "MANAGE",
    "READ",
    "RECEIPT_CANCEL",
    "RECONCILE",
    "REFUND_APPROVE",
    "REFUND_REQUEST",
    "REPORT",
    "DayClosed",
    "ModuleHidden",
    "NotRequester",
    "RequestNotPending",
    "SelfApprovalForbidden",
]
