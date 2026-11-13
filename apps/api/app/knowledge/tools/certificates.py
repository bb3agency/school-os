"""``get_certificates``: the certificates of ONE student the caller can see (FA-04; docs/06 §7;
ADR-0008 as amended by D86; FR-KB-074..076; invariants 3, 5, 8, 9).

Argument ``student_id`` (required, from ``find_students``). ``students.service.get_profile``
first checks the student is in the caller's ``student.read_basic`` scope (404: error result),
then ``certificates.service.list_certificates`` reads that student's certificates under the
caller's certificate reach (``certificate.read``, ``certificate.issue`` or
``certificate.approve`` together with ``student.read_basic``), newest first, at most
``max_results`` (``tools.yaml``). One ``search_result`` block per certificate, source
``sos://certificate/{id}`` (re-checked through ``certificates.service.get_certificate``): the
type, the duplicate number, the serial number, the request, issue or cancellation date and the
status. Requests that were rejected or withdrawn never became certificates and are left out.
Never the certificate's contents, the inputs typed for it, a reason or note, or who acted (a
person's name). No certificate: no block (the answer says nothing was found). Read-only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from app.certificates import service as certificates
from app.core.errors import DomainError
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec
from app.students import service as students

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext
    from app.certificates.schemas import CertificateOut

NAME: Final = "get_certificates"
IST: Final = ZoneInfo("Asia/Kolkata")
SHOWN: Final = frozenset({"issued", "cancelled", "pending"})
"""Statuses reported; ``rejected`` and ``withdrawn`` requests never became certificates."""
TYPES: Final = {
    "transfer": "Transfer certificate (TC)",
    "bonafide": "Bonafide certificate",
    "study": "Study certificate",
    "conduct": "Conduct certificate",
}


class CertificatesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    student_id: uuid.UUID


def _date(value: dt.datetime | None) -> str:
    return value.astimezone(IST).strftime("%d/%m/%Y") if value is not None else "unknown"


def describe(cert: CertificateOut) -> tuple[str, str]:
    """``(title, text)`` of one certificate: type, serial, dates and status only."""
    kind = TYPES.get(cert.certificate_type, cert.certificate_type)
    if cert.duplicate_no:
        kind = f"{kind}, duplicate copy {cert.duplicate_no}"
    serial = cert.serial or "no serial number yet"
    if cert.status == "pending":
        state = f"Requested on {_date(cert.requested_at)}; waiting for approval, not issued yet."
    elif cert.status == "cancelled":
        state = (
            f"Issued on {_date(cert.issued_at)} and cancelled on {_date(cert.cancelled_at)}; "
            "it is no longer valid."
        )
    else:
        state = f"Issued on {_date(cert.issued_at)}. Status: issued."
    title = f"Certificate · {kind} · {serial}"
    return title, f"{kind}. Serial number: {serial}. {state}"


class GetCertificatesTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``get_certificates``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("get_certificates needs a description in tools.yaml")
        self._max = config.max_results or 20
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "student_id": {"type": "string", "description": "ID from find_students"}
                },
                "required": ["student_id"],
                "additionalProperties": False,
            },
            permission=config.permission,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def allowed(self, ctx: UserContext) -> bool:
        return ctx.has(self._spec.permission)

    def run(
        self, session: Session, ctx: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        error = ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        if not self.allowed(ctx):
            return error
        try:
            args = CertificatesArgs.model_validate(dict(arguments))
            students.get_profile(session, ctx, args.student_id)  # 404 outside scope
            page = certificates.list_certificates(
                session, ctx, student_id=args.student_id, limit=self._max
            )
        except (ValidationError, DomainError):
            return error
        blocks: list[SearchResultBlock] = []
        for cert in page.data:
            if cert.status not in SHOWN:
                continue
            title, text = describe(cert)
            blocks.append(
                SearchResultBlock(source=sources.certificate(cert.id), title=title, text=text)
            )
        return ToolOutcome(call_id=call_id, blocks=tuple(blocks))


__all__ = ["NAME", "SHOWN", "CertificatesArgs", "GetCertificatesTool", "describe"]
