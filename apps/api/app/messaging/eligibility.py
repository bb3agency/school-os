"""Who may be sent what (FR-MSG-011, FR-MSG-004, FR-MSG-007, FR-MSG-019, FR-MSG-021; ADR-0043
decisions 7, 8, 10).

Pure functions (no database or web), so the rules are tested as a matrix. This wave owns the
account, template, opt-in and phone pieces; the sending wave (R2-3G) adds the purpose switches,
quiet hours and the daily cap on top of :func:`skip_reason`. Every refusal is a short reason
code recorded on the message (``skipped``), never a silent drop.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final

_MOBILE: Final = re.compile(r"^[6-9][0-9]{9}$")
_WA_ID: Final = re.compile(r"^91([6-9][0-9]{9})$")


@dataclass(frozen=True, slots=True)
class AccountState:
    status: str  # connecting | active | paused | disconnected
    currency: str | None


@dataclass(frozen=True, slots=True)
class TemplateState:
    status: str  # not_created | pending | approved | rejected | paused | disabled
    category: str | None
    paused_by_sos: bool


@dataclass(frozen=True, slots=True)
class OptinState:
    status: str  # opted_in | opted_out
    for_current_number: bool


def whatsapp_number(phone: str | None) -> str | None:
    """``91`` + the 10-digit Indian mobile number Meta expects, or ``None`` when the stored phone
    is not a mobile number (FR-MSG-019)."""
    if phone is None or not _MOBILE.fullmatch(phone):
        return None
    return "91" + phone


def mobile_from_wa_id(wa_id: str | None) -> str | None:
    """The 10-digit mobile number of an inbound sender's ``wa_id`` (``91`` + 10 digits), or
    ``None`` for anything else (other countries are not matched)."""
    if not isinstance(wa_id, str):
        return None
    match = _WA_ID.fullmatch(wa_id)
    return match.group(1) if match else None


def currency_ok(account: AccountState, today: dt.date, inr_required_from: dt.date) -> bool:
    """FR-MSG-004: from 1 Jan 2027 only an account confirmed as INR may send."""
    return today < inr_required_from or account.currency == "INR"


def template_usable(template: TemplateState | None) -> bool:
    """Approved, still utility and not paused by SchoolOS (FR-MSG-006, FR-MSG-007)."""
    return (
        template is not None
        and template.status == "approved"
        and not template.paused_by_sos
        and (template.category is None or template.category.upper() == "UTILITY")
    )


def skip_reason(  # noqa: PLR0911 - one return per refusal reason
    *,
    account: AccountState | None,
    template: TemplateState | None,
    needs_template: bool,
    optin: OptinState | None,
    needs_optin: bool,
    phone: str | None,
    today: dt.date,
    inr_required_from: dt.date,
) -> str | None:
    """The reason a message may not be sent now, or ``None`` when it may. Order: account,
    template, opt-in, phone (the first that fails is recorded)."""
    if account is None or account.status in ("disconnected", "connecting"):
        return "messaging_not_connected"
    if account.status == "paused":
        return "messaging_paused"
    if not currency_ok(account, today, inr_required_from):
        return "messaging_currency_not_inr"
    if needs_template and not template_usable(template):
        return (
            "template_paused"
            if template is not None and (template.paused_by_sos or template.status == "paused")
            else "template_not_approved"
        )
    if needs_optin:
        if optin is None:
            return "no_optin"
        if optin.status != "opted_in":
            return "opted_out"
        if not optin.for_current_number:
            return "optin_for_other_number"
    if whatsapp_number(phone) is None:
        return "no_mobile_number"
    return None


__all__ = [
    "AccountState",
    "OptinState",
    "TemplateState",
    "currency_ok",
    "mobile_from_wa_id",
    "skip_reason",
    "template_usable",
    "whatsapp_number",
]
