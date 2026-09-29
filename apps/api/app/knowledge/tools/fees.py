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

Sources are ``sos://fee/{id}`` (id derived from the school, the student or "summary", and the
as-of date and sync time): no person, ledger or amount in the URI. Amounts are written in Indian
grouping with two decimals, exactly as stored (``Decimal``), so the accountant can compare them
with Tally. Read-only; no network.
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
        """Offered only with the permission AND the school's connector flag (ADR-0032 §8)."""
        return self.allowed(ctx) and tally.fee_tool_available(session, ctx)

    def run(
        self, session: Session, ctx: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        error = ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        if not self.allowed(ctx):
            return error
        try:
            args = FeeArgs.model_validate(dict(arguments))
            if args.student_id is None:
                block = self._summary(ctx.tenant_id, tally.fee_summary(session, ctx))
            else:
                block = self._student(
                    ctx.tenant_id, tally.student_fee_dues(session, ctx, args.student_id)
                )
        except (ValidationError, DomainError):
            return error
        return ToolOutcome(call_id=call_id, blocks=(block,))

    @staticmethod
    def _source(tenant_id: uuid.UUID, subject: str, as_of: object, synced: object) -> str:
        key = uuid.uuid5(_NAMESPACE, f"{tenant_id}|{subject}|{as_of}|{synced}")
        return sources.fee_dues(key)

    def _student(self, tenant_id: uuid.UUID, dues: tally.StudentFeeDues) -> SearchResultBlock:
        name = dues.display_name or "(no name recorded)"
        # "admission number", not "no.": answers are split into sentences at ". ".
        who = f"{name} (admission number {dues.admission_no or 'none'}"
        who += f", class {dues.class_section})" if dues.class_section else ")"
        source = self._source(tenant_id, str(dues.student_id), dues.as_of, dues.synced_at)
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

    def _summary(self, tenant_id: uuid.UUID, summary: tally.FeeSummary) -> SearchResultBlock:
        source = self._source(tenant_id, "summary", summary.as_of, summary.synced_at)
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
