"""``get_value_history``: who recorded or changed one field of ONE student, and when (docs/06 §7;
ADR-0008; FR-KB-004, FR-STU-005, SEC-020).

Reads ``students.service.value_history`` under the caller's ``UserContext`` (404 outside scope,
sensitive values masked or left out by the students service) for ONE field the model names from
the ``tools.yaml`` enum (no C3 attribute is in it, so C3 values never reach the model). One
``search_result`` block per recorded value, newest first, at most ``max_results``: the value,
its source, verification status, when it was recorded, whether it is still current, and whether
an evidence document or change request backs it. Who recorded or verified it is named only for
callers holding ``actor_names_permission`` (``audit.read``); everyone else reads "a staff
member". Read-only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import DomainError
from app.identity import service as identity
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec
from app.knowledge.tools.students import source_label
from app.students import service as students
from app.students.schemas import ValueOut

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

NAME: Final = "get_value_history"
IST: Final = ZoneInfo("Asia/Kolkata")
STAFF: Final = "a staff member"


def _when(value: dt.datetime | None) -> str:
    return value.astimezone(IST).strftime("%d/%m/%Y") if value is not None else "unknown date"


def _display(value: str | None) -> str:
    if value is None:
        return "no value"
    try:
        return dt.date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


class HistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    student_id: uuid.UUID
    field: str


class GetValueHistoryTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``get_value_history``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None or not config.fields:
            raise ValueError("get_value_history needs a description and fields in tools.yaml")
        self._fields = config.fields
        self._max = config.max_results or 10
        self._actor_permission = config.actor_names_permission
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "student_id": {"type": "string", "description": "ID from find_students"},
                    "field": {"type": "string", "enum": list(config.fields)},
                },
                "required": ["student_id", "field"],
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
            args = HistoryArgs.model_validate(dict(arguments))
        except ValidationError:
            return error
        if args.field not in self._fields:
            return error
        try:
            values = students.value_history(session, ctx, args.student_id, args.field)
            labels = {a.key: a.label_en for a in students.attribute_catalog(session)}
        except DomainError:
            return error  # outside the caller's scope, another school's or no such student
        values = values[: self._max]
        names = self._actor_names(session, ctx, values)
        label = labels.get(args.field) or args.field.replace("_", " ").capitalize()
        blocks = tuple(self._block(args.student_id, args.field, label, v, names) for v in values)
        return ToolOutcome(call_id=call_id, blocks=blocks)

    def _actor_names(
        self, session: Session, ctx: UserContext, values: list[ValueOut]
    ) -> Mapping[uuid.UUID, str]:
        if self._actor_permission is None or not ctx.has(self._actor_permission):
            return {}
        users = {v.recorded_by for v in values} | {v.verified_by for v in values if v.verified_by}
        return {u: name for u, (_, name) in identity.members_for_users(session, users).items()}

    @staticmethod
    def _block(
        student_id: uuid.UUID,
        field: str,
        label: str,
        value: ValueOut,
        names: Mapping[uuid.UUID, str],
    ) -> SearchResultBlock:
        shown = "hidden (sensitive field)" if value.masked else _display(value.value)
        state = "current value" if value.current else "replaced by a later value"
        text = (
            f"{label}: {shown}. Source: {source_label(value.source)}. "
            f"Status: {value.verification_status.replace('_', ' ')}; {state}. "
            f"Recorded on {_when(value.recorded_at)} by "
            f"{names.get(value.recorded_by, STAFF)}."
        )
        if value.verified_at is not None:
            verifier = names.get(value.verified_by, STAFF) if value.verified_by else STAFF
            text += f" Verified on {_when(value.verified_at)} by {verifier}."
        if value.evidence_document_id is not None:
            text += " An evidence document is attached."
        if value.change_request_id is not None:
            text += " It came from an approved change request."
        return SearchResultBlock(
            source=sources.student_field(student_id, attribute=field, source=value.source),
            title=f"Student record history · {label} ({source_label(value.source)}, "
            f"recorded {_when(value.recorded_at)})",
            text=text,
        )


__all__ = ["NAME", "GetValueHistoryTool"]
