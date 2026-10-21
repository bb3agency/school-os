"""``get_fee_dues``: fee dues from synced Tally ledgers (M6; ADR-0032 Proposed; ADR-0008 as it
would be amended; docs/06 §7; FR-TALLY-008; invariants 8, 9).

Offered only when the caller holds ``finance.read`` school-wide AND the school's
``tally.connector.enabled`` flag is on (:func:`app.tally.service.fee_tool_available`); the tool
re-checks both when it runs. Two shapes:

- with ``student_id`` (from ``find_students``): ``tally.service.student_fee_dues`` checks the
  student is in the caller's scope (404 otherwise) and reads, in SQL, only the ledgers LINKED to
  that student by a person. One block: the total and each linked ledger's closing balance with
  its Tally group, the Tally as-of date and when it was synced. Ledger names are NOT sent (the
  amount and group suffice; CLAUDE.md §11 "send only the field needed"). No link: the block says
  SchoolOS has no fee figure for the student; it never guesses a ledger by name.
- without a student: ``tally.service.fee_summary``: numbers only (students with dues, total,
  unlinked ledgers), no names.

Sources are ``sos://fee/{id}#s{scope}`` (id derived from the school, the student or "summary",
and the as-of date and sync time; ``scope`` fingerprints the caller's ``finance.read`` reach,
re-checked when the answer is shown again, audit W3-09): no person, ledger or amount in the URI.
Amounts are written in Indian grouping with two decimals, exactly as stored (``Decimal``), so
the accountant can compare them with Tally. Read-only; no network.

**With fees on (FR-KB-044, R2-4).** While the school keeps its fees in SchoolOS (FR-FEE-001,
``fees.service.enabled``), the tool reads the fee ledger through ``fees.service`` instead and
Tally is not consulted: offered with ``finance.read`` school-wide whatever the connector flag;
with a student, the current year's balance, the overdue amount, what is due per instalment and
the last payment date (``fees.service.ask_student_fees``; a student without a fee account gets
"no fee account", never a figure); without one, the school and per-class totals, numbers only
(``fees.service.ask_school_fees``). Sources stay ``sos://fee/{id}#s{scope}`` with an id derived
from the school, the subject, "ledger" and the as-of date.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import DomainError
from app.fees import service as fees
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec
from app.tally import service as tally

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

NAME: Final = "get_fee_dues"
IST: Final = ZoneInfo("Asia/Kolkata")
_NAMESPACE: Final = uuid.UUID("0192f0c1-7a51-7c4e-9d0e-5c0a6b0e7f11")


class FeeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    student_id: uuid.UUID | None = None


def inr(amount: Decimal) -> str:
    """``Decimal("150000.5")`` -> ``"₹1,50,000.50"`` (Indian grouping, two decimals)."""
    sign = "-" if amount < 0 else ""
    whole, _, fraction = f"{abs(amount):.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    return f"{sign}₹{grouped}.{fraction}"


def _date(value: dt.date | None) -> str:
    return value.strftime("%d/%m/%Y") if value is not None else "unknown"


def _synced(value: dt.datetime | None) -> str:
    return value.astimezone(IST).strftime("%d/%m/%Y %H:%M IST") if value is not None else "never"


class GetFeeDuesTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``get_fee_dues``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("get_fee_dues needs a description in tools.yaml")
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "student_id": {
                        "type": "string",
                        "description": "ID from find_students; leave out for the school totals",
                    }
                },
                "additionalProperties": False,
            },
            permission=config.permission,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def allowed(self, ctx: UserContext) -> bool:
        return ctx.has(self._spec.permission) and tally.can_read_fees(ctx)

    def available(self, session: Session, ctx: UserContext) -> bool:
        """Offered with the permission AND either fees on (the ledger, FR-KB-044) or the
        school's Tally connector flag (ADR-0032 §8)."""
        if not self.allowed(ctx):
            return False
        return fees.enabled(session, ctx.tenant_id) or tally.fee_tool_available(session, ctx)

    def run(
        self, session: Session, ctx: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        error = ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        if not self.allowed(ctx):
            return error
        scope = sources.scope_fingerprint(ctx.scope_for(self._spec.permission))
        try:
            args = FeeArgs.model_validate(dict(arguments))
            if fees.enabled(session, ctx.tenant_id):
                block = self._ledger(session, ctx, args.student_id, scope)
            elif args.student_id is None:
                block = self._summary(ctx.tenant_id, tally.fee_summary(session, ctx), scope)
            else:
                block = self._student(
                    ctx.tenant_id, tally.student_fee_dues(session, ctx, args.student_id), scope
                )
        except (ValidationError, DomainError):
            return error
        return ToolOutcome(call_id=call_id, blocks=(block,))

    @staticmethod
    def _source(
        tenant_id: uuid.UUID, subject: str, as_of: object, synced: object, scope: str
    ) -> str:
        key = uuid.uuid5(_NAMESPACE, f"{tenant_id}|{subject}|{as_of}|{synced}")
        return sources.fee_dues(key, scope=scope)

    # --- the SchoolOS fee ledger (fees on; FR-KB-044) -------------------------------------------

    def _ledger(
        self, session: Session, ctx: UserContext, student_id: uuid.UUID | None, scope: str
    ) -> SearchResultBlock:
        if student_id is None:
            return self._ledger_summary(ctx.tenant_id, fees.ask_school_fees(session, ctx), scope)
        return self._ledger_student(
            ctx.tenant_id, fees.ask_student_fees(session, ctx, student_id), scope
        )

    def _ledger_student(
        self, tenant_id: uuid.UUID, found: fees.AskStudentFees, scope: str
    ) -> SearchResultBlock:
        name = found.display_name or "(no name recorded)"
        who = f"{name} (admission number {found.admission_no or 'none'}"
        who += f", class {found.class_section})" if found.class_section else ")"
        year = f" for {found.year_label}" if found.year_label else ""
        source = self._source(
            tenant_id, f"ledger|{found.student_id}", found.as_of, found.has_account, scope
        )
        title = f"Fee account · {name}"
        if not found.has_account:
            text = (
                f"{who} has no fee account{year} in SchoolOS, so there is no fee figure for this "
                "student. The accountant applies the class fee plan on the fee screens."
            )
            return SearchResultBlock(source=source, title=title, text=text)
        parts = [
            f"{i.label}{f' due {_date(i.due_on)}' if i.due_on else ''}: {inr(i.due)}"
            f"{' (overdue)' if i.overdue else ''}"
            for i in found.instalments
        ]
        text = f"Fee balance for {who}{year}: {inr(found.balance)}"
        if found.overdue > 0:
            text += f", of which {inr(found.overdue)} is overdue"
        if found.oldest_due_on is not None:
            text += f" (oldest due date {_date(found.oldest_due_on)})"
        text += "."
        if parts:
            text += f" Due by instalment: {'; '.join(parts)}."
        if found.last_payment_on is not None:
            text += f" Last payment on {_date(found.last_payment_on)}."
        text += f" From the SchoolOS fee ledger as of {_date(found.as_of)}."
        return SearchResultBlock(source=source, title=title, text=text)

    def _ledger_summary(
        self, tenant_id: uuid.UUID, found: fees.AskSchoolFees, scope: str
    ) -> SearchResultBlock:
        source = self._source(tenant_id, "ledger|summary", found.as_of, found.year_label, scope)
        year = f" for {found.year_label}" if found.year_label else ""
        n = found.students_owing
        text = (
            f"Fee dues{year} from the SchoolOS fee ledger: {n} student"
            f"{'s' if n != 1 else ''} with a balance, {inr(found.balance)} in total, "
            f"{inr(found.overdue)} of it overdue."
        )
        if found.classes:
            rows = [
                f"{k.class_name}: {k.students_owing} student{'s' if k.students_owing != 1 else ''}"
                f", {inr(k.balance)} ({inr(k.overdue)} overdue)"
                for k in found.classes
            ]
            text += f" By class: {'; '.join(rows)}."
        text += f" As of {_date(found.as_of)}."
        return SearchResultBlock(
            source=source, title="Fee dues from the fee ledger · school totals", text=text
        )

    # --- Tally (fees off; M6) ------------------------------------------------------------------

    def _student(
        self, tenant_id: uuid.UUID, dues: tally.StudentFeeDues, scope: str
    ) -> SearchResultBlock:
        name = dues.display_name or "(no name recorded)"
        # "admission number", not "no.": answers are split into sentences at ". ".
        who = f"{name} (admission number {dues.admission_no or 'none'}"
        who += f", class {dues.class_section})" if dues.class_section else ")"
        source = self._source(tenant_id, str(dues.student_id), dues.as_of, dues.synced_at, scope)
        title = f"Fee dues from Tally · {name}"
        if not dues.ledgers:
            text = (
                f"No Tally ledger is linked to {who}, so SchoolOS has no fee figure for this "
                "student. The accountant links Tally ledgers to students on the Tally connector "
                "screen."
            )
            return SearchResultBlock(source=source, title=title, text=text)
        parts = [
            f"ledger {i} in group {ledger.group_name}: {inr(ledger.closing_balance)}"
            for i, ledger in enumerate(dues.ledgers, start=1)
        ]
        count = len(dues.ledgers)
        text = (
            f"Fee due for {who}: {inr(dues.total_due)} in total, from {count} linked Tally "
            f"ledger{'s' if count != 1 else ''} ({'; '.join(parts)}). A negative amount is an "
            f"advance paid. Tally figures as of {_date(dues.as_of)}, synced "
            f"{_synced(dues.synced_at)}."
        )
        return SearchResultBlock(source=source, title=title, text=text)

    def _summary(
        self, tenant_id: uuid.UUID, summary: tally.FeeSummary, scope: str
    ) -> SearchResultBlock:
        source = self._source(tenant_id, "summary", summary.as_of, summary.synced_at, scope)
        text = (
            f"Fee dues from Tally, linked ledgers only: {summary.students_with_dues} "
            f"student{'s' if summary.students_with_dues != 1 else ''} with dues, "
            f"{inr(summary.total_due)} in total. {summary.unlinked_parties} Tally ledger"
            f"{'s are' if summary.unlinked_parties != 1 else ' is'} not linked to a student and "
            f"not counted. Tally figures as of {_date(summary.as_of)}, synced "
            f"{_synced(summary.synced_at)}."
        )
        return SearchResultBlock(
            source=source, title="Fee dues from Tally · school totals", text=text
        )


__all__ = ["NAME", "FeeArgs", "GetFeeDuesTool", "inr"]
