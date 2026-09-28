"""``count_students``: how many students the caller can access, in the current academic year
(docs/06 §7; ADR-0008; FR-KB-004, SEC-020; invariant 14).

Counts come from ``students.service.list_students_in_scope`` per current-year section, so a
scoped caller counts only their own sections (the same scope as the student list). Optional
breakdown by class, section or gender (gender through ``students.service.canonical_values``,
never a C3 attribute). A breakdown by gender is a sensitive breakdown: a cell below
``small_cell_min`` (``tools.yaml``) is not shown, and when only one is, the next smallest is
hidden too so the total cannot reveal it (docs/06 §7 small-cell suppression).
One ``search_result`` block with source ``sos://count/{id}`` (the id is derived from the
school, breakdown and date, docs/06 §8): the numbers only, never names or ids. Read-only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import DomainError
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec
from app.students import service as students
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

NAME: Final = "count_students"
IST: Final = ZoneInfo("Asia/Kolkata")
GroupBy = Literal["total", "class", "section", "gender"]
GROUPS: Final[tuple[GroupBy, ...]] = ("total", "class", "section", "gender")
SENSITIVE_GROUPS: Final = frozenset({"gender"})
_NAMESPACE: Final = uuid.UUID("0192f0c1-7a51-7c4e-9d0e-5c0a6b0e7c01")


class CountArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_by: GroupBy = "total"


class CountStudentsTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``count_students``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("count_students needs a description in tools.yaml")
        self._min = config.small_cell_min or 5
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {"group_by": {"type": "string", "enum": list(GROUPS)}},
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
            args = CountArgs.model_validate(dict(arguments))
            counts, year = self._counts(session, ctx, args.group_by)
        except (ValidationError, DomainError):
            return error
        today = dt.datetime.now(IST).date()
        return ToolOutcome(
            call_id=call_id,
            blocks=(self._block(ctx.tenant_id, args.group_by, counts, year, today),),
        )

    def _counts(
        self, session: Session, ctx: UserContext, group_by: GroupBy
    ) -> tuple[dict[str, int], str | None]:
        year = tenancy.get_current_academic_year(session)
        if year is None:
            return {}, None
        classes = {c.id: c for c in tenancy.list_classes(session)}
        sections = sorted(
            tenancy.list_sections(session, academic_year_id=year.id, include_archived=False),
            key=lambda s: (classes[s.class_id].sort_order if s.class_id in classes else 0, s.name),
        )
        by_section: dict[str, list[uuid.UUID]] = {}
        for s in sections:
            ids = students.list_students_in_scope(session, ctx, section_ids=[s.id])
            if ids:
                klass = classes.get(s.class_id)
                label = f"{klass.display_en if klass else 'Class'} {s.name}"
                by_section[f"{s.class_id}|{label}"] = ids
        counts: Counter[str] = Counter()
        if group_by == "gender":
            everyone = [i for ids in by_section.values() for i in ids]
            values = students.canonical_values(session, everyone, ["gender"])
            for sid in everyone:
                gender = values.get(sid, {}).get("gender")
                counts[(gender.value if gender and gender.value else "not recorded")] += 1
        else:
            for key, ids in by_section.items():
                class_id, label = key.split("|", 1)
                if group_by == "section":
                    counts[label] += len(ids)
                elif group_by == "class":
                    klass = classes.get(uuid.UUID(class_id))
                    counts[klass.display_en if klass else "Class"] += len(ids)
                else:
                    counts["total"] += len(ids)
        return dict(counts), year.label

    def _suppressed(self, counts: Mapping[str, int]) -> set[str]:
        """Cells below ``small_cell_min``; when only one is, the next smallest too, so it cannot
        be worked out from the total (secondary suppression)."""
        small = {label for label, n in counts.items() if n < self._min}
        others = sorted((n, label) for label, n in counts.items() if label not in small)
        if len(small) == 1 and others:
            small.add(others[0][1])
        return small

    def _block(
        self,
        tenant_id: uuid.UUID,
        group_by: GroupBy,
        counts: Mapping[str, int],
        year: str | None,
        today: dt.date,
    ) -> SearchResultBlock:
        as_of = today.strftime("%d/%m/%Y")
        key = uuid.uuid5(_NAMESPACE, f"{tenant_id}|{group_by}|{today.isoformat()}")
        title = f"Student count · current academic year · by {group_by}"
        if year is None:
            text = f"No current academic year is set, so there is nothing to count. As of {as_of}."
            return SearchResultBlock(source=sources.student_count(key), title=title, text=text)
        total = sum(counts.values())
        hidden = self._suppressed(counts) if group_by in SENSITIVE_GROUPS else set()
        parts: list[str] = []
        if group_by != "total":
            for label, n in counts.items():
                shown = f"not shown (a group below {self._min})" if label in hidden else str(n)
                parts.append(f"{label}: {shown}")
        body = "; ".join(parts) + ". " if parts else ""
        text = (
            f"Students enrolled in {year} that you can access: {body}Total: {total}. As of {as_of}."
        )
        return SearchResultBlock(source=sources.student_count(key), title=title, text=text)


__all__ = ["GROUPS", "NAME", "CountStudentsTool"]
