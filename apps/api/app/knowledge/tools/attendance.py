"""``get_attendance_summary``: attendance by section over a date range, counts only (FA-04;
docs/06 §7; ADR-0008 as amended by D86; FR-KB-070..073; invariants 3, 5, 8, 9, 14).

Reads ``academics.service.attendance_rates_for_ask`` under the caller's ``UserContext``
(permission ``attendance.read``; class teachers: their own sections, as on the register screen;
the roll is the caller's reach through both ``student.read_basic`` and ``attendance.read``).
Arguments are IST dates ``from_date`` and ``to_date`` (both optional, inclusive): ``to_date``
defaults to today (a later date is cut to today) and ``from_date`` to the ``default_days`` days
ending on ``to_date``; a range longer than ``max_days`` is cut and the passage says so
(``tools.yaml``). One ``search_result`` block, source ``sos://attendance/{id}#s{scope}`` (the id
is derived from the school, the range and the day; ``scope`` fingerprints the caller's
``attendance.read`` reach, audit W3-09): per section the share of marks that were present or
late, the students on the roll and the days marked; a section with fewer than
``small_cell_min`` students on the roll shows no figure (re-identification), and the total is
over the sections shown only, so a hidden section cannot be worked out from it. Never a child's
name, id or own attendance (no per-child lists), never early-warning flags or indicators
(invariant 14: ``insights`` is not read). Read-only; no network.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.academics import service as academics
from app.core.errors import DomainError
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec
from app.knowledge.tools.due import today_ist

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

NAME: Final = "get_attendance_summary"
_NAMESPACE: Final = uuid.UUID("0192f0c1-7a51-7c4e-9d0e-5c0a6b0e7d31")
_DATE_PATTERN: Final = r"^\d{4}-\d{2}-\d{2}$"


class AttendanceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    from_date: str | None = Field(default=None, pattern=_DATE_PATTERN)
    to_date: str | None = Field(default=None, pattern=_DATE_PATTERN)


def attendance_range(
    args: AttendanceArgs, *, today: dt.date, default_days: int, max_days: int
) -> tuple[dt.date, dt.date, bool]:
    """``(start, end, cut)``: the inclusive IST range asked for, never after today; ``cut``
    when it covered more than ``max_days`` days and was shortened (the latest days are kept).
    Raises ``ValueError`` for a bad date or an end before the start."""
    end = dt.date.fromisoformat(args.to_date) if args.to_date else today
    end = min(end, today)
    start = (
        dt.date.fromisoformat(args.from_date)
        if args.from_date
        else end - dt.timedelta(days=default_days - 1)
    )
    if end < start:
        raise ValueError("to_date before from_date")
    cut = (end - start).days + 1 > max_days
    if cut:
        start = end - dt.timedelta(days=max_days - 1)
    return start, end, cut


def _date(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


def _percent(value: float) -> str:
    return f"{value:.1f}%"


class GetAttendanceSummaryTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``get_attendance_summary``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("get_attendance_summary needs a description in tools.yaml")
        self._min = config.small_cell_min or 5
        self._default_days = config.default_days or 7
        self._max_days = config.max_days or 31
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "from_date": {
                        "type": "string",
                        "pattern": _DATE_PATTERN,
                        "description": (
                            f"First date, YYYY-MM-DD (IST); default {self._default_days} days "
                            f"ending on to_date; at most {self._max_days} days in all"
                        ),
                    },
                    "to_date": {
                        "type": "string",
                        "pattern": _DATE_PATTERN,
                        "description": "Last date, YYYY-MM-DD (IST); default today",
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
        self,
        session: Session,
        ctx: UserContext,
        call_id: str,
        arguments: Mapping[str, object],
        *,
        today: dt.date | None = None,
    ) -> ToolOutcome:
        error = ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        if not self.allowed(ctx):
            return error
        day = today or today_ist()
        try:
            args = AttendanceArgs.model_validate(dict(arguments))
            start, end, cut = attendance_range(
                args, today=day, default_days=self._default_days, max_days=self._max_days
            )
            found = academics.attendance_rates_for_ask(session, ctx, start=start, end=end)
        except (ValidationError, ValueError, DomainError):
            return error
        scope = sources.scope_fingerprint(ctx.scope_for(self._spec.permission))
        key = uuid.uuid5(
            _NAMESPACE, f"{ctx.tenant_id}|{start.isoformat()}|{end.isoformat()}|{day.isoformat()}"
        )
        return ToolOutcome(
            call_id=call_id,
            blocks=(
                SearchResultBlock(
                    source=sources.attendance(key, scope=scope),
                    title=f"Attendance · {_date(start)} to {_date(end)} · by section",
                    text=self._text(found, cut=cut),
                ),
            ),
        )

    def _text(self, found: academics.AttendanceRates, *, cut: bool) -> str:
        span = f"from {_date(found.start)} to {_date(found.end)}"
        notes = f" Only {self._max_days} days at a time are read." if cut else ""
        if found.year_label is None:
            return f"No current academic year is set, so there is no attendance {span}.{notes}"
        if not found.sections:
            return (
                f"No section you can access has students on the roll in {found.year_label}, "
                f"so there is no attendance {span}.{notes}"
            )
        parts: list[str] = []
        shown: list[academics.SectionAttendance] = []
        for s in found.sections:
            if s.students < self._min:
                parts.append(f"{s.label}: not shown (fewer than {self._min} students)")
            elif s.rate is None:
                parts.append(f"{s.label}: no attendance marked ({s.students} students)")
            else:
                shown.append(s)
                parts.append(
                    f"{s.label}: {_percent(s.rate)} present or late "
                    f"({s.students} students, {s.days_marked} days marked)"
                )
        return (
            f"Attendance {span} in the sections you can access ({found.year_label}), as the share "
            f"of marks that were present or late: {'; '.join(parts)}. {self._total(shown)}{notes}"
        )

    @staticmethod
    def _total(shown: Sequence[academics.SectionAttendance]) -> str:
        marks = sum(s.marks for s in shown)
        if not marks:
            return "No total: no section with a figure."
        attended = sum(s.present + s.late for s in shown)
        return (
            f"Total over the {len(shown)} section(s) with a figure: "
            f"{_percent(100.0 * attended / marks)}."
        )


__all__ = ["NAME", "AttendanceArgs", "GetAttendanceSummaryTool", "attendance_range"]
