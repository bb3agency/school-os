"""``list_findings``: data-quality findings the caller can see (docs/06 §7; ADR-0008; FR-KB-004,
FR-DQ-*, SEC-020).

Reads ``dq.service.list_findings`` under the caller's ``UserContext`` (permission
``dq.findings.read``; the dq service limits findings to students in the caller's scope), most
severe first, at most ``max_results``, optionally by severity and status (default: unresolved).
One ``search_result`` block per finding with source ``sos://finding/{id}``: the rule, severity,
status, the student's display name and admission number, and the dq service's own explanation.
Compared values are never included (the dq service masks them; C3 values are revealed only on
the student record). Read-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import DomainError
from app.dq import service as dq
from app.dq.schemas import FindingFilters, FindingOut, FindingStatus, SeverityName
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

NAME: Final = "list_findings"
SEVERITIES: Final = ("blocker", "high", "medium", "low", "info")
STATUSES: Final = ("open", "reopened", "resolved", "waived")


class FindingsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: tuple[SeverityName, ...] | None = Field(default=None, min_length=1, max_length=5)
    status: tuple[FindingStatus, ...] | None = Field(default=None, min_length=1, max_length=4)


class ListFindingsTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``list_findings``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("list_findings needs a description in tools.yaml")
        self._max = config.max_results or 20
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(SEVERITIES)},
                        "maxItems": 5,
                    },
                    "status": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(STATUSES)},
                        "maxItems": 4,
                    },
                },
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
            args = FindingsArgs.model_validate(dict(arguments))
            page = dq.list_findings(
                session,
                ctx,
                FindingFilters(
                    severity=list(args.severity) if args.severity else None,
                    status=list(args.status) if args.status else None,
                ),
                limit=self._max,
            )
        except (ValidationError, DomainError):
            return error
        return ToolOutcome(
            call_id=call_id, blocks=tuple(self._block(f) for f in page.data[: self._max])
        )

    @staticmethod
    def _block(finding: FindingOut) -> SearchResultBlock:
        student = finding.student
        name = student.display_name or "(no name recorded)"
        admission = student.admission_no or "none"
        field = finding.attribute_key.replace("_", " ") if finding.attribute_key else "record"
        text = (
            f"Finding {finding.rule_id} ({finding.severity}"
            f"{', blocks submissions' if finding.blocker else ''}), status {finding.status}. "
            f"Student: {name}, admission no. {admission}. Field: {field}. "
            f"{finding.explanation.en}"
        )
        return SearchResultBlock(
            source=sources.finding(finding.id),
            title=f"Data-quality finding · {finding.rule_id} · {finding.severity} · {name}",
            text=text,
        )


__all__ = ["NAME", "ListFindingsTool"]
