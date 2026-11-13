"""``list_compliance_status``: the school's compliance certificate register (FC-11; docs/06 §7;
ADR-0008 as amended by D86; FR-KB-077..079; invariants 8, 9).

Reads ``compliance.service.register`` (permission ``compliance.read``, school-wide only): every
catalogue kind that applies to the school's boards (``missing`` until one is recorded) and every
current certificate, most urgent first (expired, missing, expiring, valid, no expiry). One
``search_result`` block per register line, source ``sos://compliance/{id}#s{scope}`` (the id is
derived from the school, the line and the day; it opens the register screen): the kind's label,
the state, the valid-until date and the days left, and whether a copy is in Documents the
caller can open. These are school-level facts with no personal data; the renewal owner's name,
who recorded it, the reference number and the notes are left out. Staff verification (FC-04),
committees (FC-05) and safeguarding records (FC-06) are not read (not built, or never for AI).
Read-only; no network.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from app.compliance import service as compliance
from app.core.errors import DomainError
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext
    from app.compliance.schemas import RegisterRowOut

NAME: Final = "list_compliance_status"
_NAMESPACE: Final = uuid.UUID("0192f0c1-7a51-7c4e-9d0e-5c0a6b0e7d41")
_STATES: Final = {
    "valid": "valid",
    "expiring": "expiring soon",
    "expired": "expired",
    "no_expiry": "valid, no expiry date",
    "missing": "missing (no certificate recorded)",
}


def _date(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


def describe(row: RegisterRowOut, today: dt.date) -> str:
    """One register line: label, state, valid-until date, days left, copy on file."""
    text = f"{row.kind_label}: {_STATES.get(row.state, row.state)}."
    cert = row.certificate
    if cert is None:
        return text
    if cert.valid_until is not None:
        if cert.valid_until < today:
            text += f" It expired on {_date(cert.valid_until)}."
        else:
            days = (cert.valid_until - today).days
            text += f" Valid until {_date(cert.valid_until)} ({days} days left)."
    if cert.valid_from is not None:
        text += f" Valid from {_date(cert.valid_from)}."
    if cert.document is not None and cert.document.title is not None:
        text += " A copy is in Documents."
    return text


class ListComplianceStatusTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``list_compliance_status``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("list_compliance_status needs a description in tools.yaml")
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            permission=config.permission,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def allowed(self, ctx: UserContext) -> bool:
        """``compliance.read`` is school-wide only (FC-01); a scoped grant fails closed."""
        return ctx.has(self._spec.permission) and ctx.scope_for(self._spec.permission).school_wide

    def run(
        self, session: Session, ctx: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        error = ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        if not self.allowed(ctx) or dict(arguments):
            return error
        try:
            found = compliance.register(session, ctx)
        except DomainError:
            return error
        scope = sources.scope_fingerprint(ctx.scope_for(self._spec.permission))
        as_of = _date(found.today)
        blocks: list[SearchResultBlock] = []
        for row in found.rows:
            line = row.certificate.id if row.certificate is not None else row.kind
            key = uuid.uuid5(_NAMESPACE, f"{ctx.tenant_id}|{line}|{found.today.isoformat()}")
            blocks.append(
                SearchResultBlock(
                    source=sources.compliance(key, scope=scope),
                    title=f"Compliance register · {row.kind_label}",
                    text=f"{describe(row, found.today)} As of {as_of}.",
                )
            )
        return ToolOutcome(call_id=call_id, blocks=tuple(blocks))


__all__ = ["NAME", "ListComplianceStatusTool", "describe"]
