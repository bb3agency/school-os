"""Record tools over ``students.service`` (docs/06 §7; ADR-0008; FR-KB-003/004, SEC-020).

- ``find_students``: the scoped student search (``students.service.search``: current-year
  enrolments in the caller's sections for scoped holders, US-302 AC2), capped at
  ``max_results``; one ``search_result`` block per student with the ID and display fields only.
- ``get_student_facts``: the fields the model NAMES (an enum, at most ``max_fields``) of ONE
  student the caller can reach (``students.service.get_profile``: 404 outside scope, C3 values
  hidden without ``student.read_sensitive`` and masked even with it). Never the whole record:
  one block per requested field, with its source, verification status and "as of" date
  (CLAUDE.md §11 "send only the field needed").

Sensitive (C3) values never reach the model: they are masked by the students service and the
tool says the value is hidden and how a person may reveal it (the reveal is audited there).
Sources are ``sos://student/{id}/field/{attribute}?src={source}``; fields stored on the student
row itself (admission number, status, class/section) use the source key ``record``. Read-only:
the tools call read functions of ``students.service`` and nothing else.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.errors import DomainError
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec
from app.students import service as students
from app.students.schemas import SearchFilters as StudentFilters
from app.students.schemas import StudentOut

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

FIND: Final = "find_students"
FACTS: Final = "get_student_facts"
RECORD_SOURCE: Final = "record"
"""Source key of values kept on the student row itself (not a per-source attribute value)."""
ROW_FIELDS: Final = frozenset({"admission_no", "status", "current_class_section"})
IST: Final = ZoneInfo("Asia/Kolkata")
MAX_QUERY_CHARS: Final = 100

_SOURCE_LABELS: Final = {
    "admission_register": "admission register",
    "aadhaar_as_printed": "Aadhaar as printed",
    "udise_plus": "UDISE+",
    "board_registration": "board registration",
    "parent_form": "parent form",
    "tc_previous_school": "transfer certificate of the previous school",
    RECORD_SOURCE: "student record",
}


def _today() -> str:
    return dt.datetime.now(IST).strftime("%d/%m/%Y")


def _display_date(value: str) -> str:
    """ISO dates as DD/MM/YYYY (docs/06 §11); anything else unchanged."""
    try:
        return dt.date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


def source_label(key: str | None) -> str:
    if key is None:
        return "no source"
    return _SOURCE_LABELS.get(key, key.replace("_", " "))


def _error(call_id: str) -> ToolOutcome:
    return ToolOutcome(call_id=call_id, blocks=(), is_error=True)


class FindArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)


class FindStudentsTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``find_students``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("find_students needs a description in tools.yaml")
        self._max = config.max_results or 20
        self._spec = ToolSpec(
            name=FIND,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Name (English or Telugu), admission number, parent "
                        "name or a class/section token such as 9b.",
                        "maxLength": MAX_QUERY_CHARS,
                    }
                },
                "required": ["query"],
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
        if not self.allowed(ctx):
            return _error(call_id)
        try:
            args = FindArgs.model_validate(dict(arguments))
            page = students.search(session, ctx, StudentFilters(query=args.query), limit=self._max)
        except (ValidationError, DomainError):
            return _error(call_id)
        today = _today()
        blocks = []
        for s in page.data[: self._max]:
            name = s.display_name or "(no name recorded)"
            section = s.class_section or "no current class"
            text = (
                f"Student ID: {s.id}. Name: {name}. Admission no.: {s.admission_no or 'none'}. "
                f"Class/section: {section}. Status: {s.status}. As of {today}."
            )
            blocks.append(
                SearchResultBlock(
                    source=sources.student_field(
                        s.id, attribute="admission_no", source=RECORD_SOURCE
                    ),
                    title=f"Student record · {name} · {section}",
                    text=text,
                )
            )
        return ToolOutcome(call_id=call_id, blocks=tuple(blocks))


class FactsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    student_id: uuid.UUID
    fields: tuple[str, ...] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def _unique(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(v)) != len(v):
            raise ValueError("fields must be unique")
        return v


class GetStudentFactsTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``get_student_facts``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None or not config.fields:
            raise ValueError("get_student_facts needs a description and fields in tools.yaml")
        self._fields = config.fields
        self._max = config.max_fields or len(config.fields)
        self._spec = ToolSpec(
            name=FACTS,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "student_id": {"type": "string", "description": "ID from find_students"},
                    "fields": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(config.fields)},
                        "maxItems": self._max,
                    },
                },
                "required": ["student_id", "fields"],
                "additionalProperties": False,
            },
            permission=config.permission,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def allowed(self, ctx: UserContext) -> bool:
        return ctx.has(self._spec.permission)

    def _valid(self, arguments: Mapping[str, object]) -> FactsArgs | None:
        try:
            args = FactsArgs.model_validate(dict(arguments))
        except ValidationError:
            return None
        if len(args.fields) > self._max or not set(args.fields) <= set(self._fields):
            return None
        return args

    def run(
        self, session: Session, ctx: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        if not self.allowed(ctx):
            return _error(call_id)
        args = self._valid(arguments)
        if args is None:
            return _error(call_id)
        try:
            profile = students.get_profile(session, ctx, args.student_id)
            labels = {a.key: a.label_en for a in students.attribute_catalog(session)}
        except DomainError:
            # Outside the caller's scope, another school's or no such student: all look alike.
            return _error(call_id)
        name_value = profile.canonical.get("full_name")
        name = (
            name_value.value
            if name_value is not None and name_value.value and not name_value.masked
            else "(no name recorded)"
        )
        section = profile.enrollment.label if profile.enrollment else "no current class"
        today = _today()
        blocks = [
            self._block(profile, field, labels, name=name, section=section, today=today)
            for field in args.fields
        ]
        return ToolOutcome(call_id=call_id, blocks=tuple(blocks))

    @staticmethod
    def _block(
        profile: StudentOut,
        field: str,
        labels: Mapping[str, str],
        *,
        name: str,
        section: str,
        today: str,
    ) -> SearchResultBlock:
        label = labels.get(field) or field.replace("_", " ").capitalize()
        src: str = RECORD_SOURCE
        status = "verified"
        notes: list[str] = []
        if field in ROW_FIELDS:
            value: str
            if field == "admission_no":
                value = profile.admission_no or "not recorded"
            elif field == "status":
                value = profile.status
            elif profile.enrollment is not None:
                roll = profile.enrollment.roll_no
                value = profile.enrollment.label + (f", roll no. {roll}" if roll else "")
            else:
                value = "not enrolled in the current academic year"
        else:
            canonical = profile.canonical.get(field)
            if canonical is None or canonical.value is None:
                value, status = "not recorded", "no value"
            elif canonical.masked:
                value = (
                    "hidden (sensitive field). Open the student's page to reveal it; the reveal "
                    "is recorded"
                )
                src = canonical.source or RECORD_SOURCE
                status = "sensitive"
            else:
                value = _display_date(canonical.value)
                src = canonical.source or RECORD_SOURCE
                status = "verified" if canonical.verified else "not verified"
                if canonical.provisional:
                    notes.append("provisional until verified")
                if canonical.conflicts:
                    others = ", ".join(source_label(c) for c in canonical.conflicts)
                    notes.append(f"other sources record a different value: {others}")
        where = f"{source_label(src)}, {status}"
        text = f"{label}: {value}. Source: {where}."
        if notes:
            text += " Note: " + "; ".join(notes) + "."
        text += f" As of {today}."
        return SearchResultBlock(
            source=sources.student_field(profile.id, attribute=field, source=src),
            title=f"Student record · {name} · {section} · {label} ({where})",
            text=text,
        )


__all__ = [
    "FACTS",
    "FIND",
    "RECORD_SOURCE",
    "FindStudentsTool",
    "GetStudentFactsTool",
    "source_label",
]
