"""``get_readiness_summary``: board and portal readiness by class, counts only (FA-04; docs/06
§7; ADR-0008 as amended by D86; FR-KB-066..069; invariants 3, 8, 9, 14).

Reads ``dq.service.readiness_for_ask`` under the caller's ``UserContext`` (permission
``dq.readiness.read``; class teachers count only their sections, exactly as on the readiness
page). Argument ``profile_key`` (optional): one readiness profile; without it, every readiness
profile listed for the school (at most ``max_results``, ``tools.yaml``). One ``search_result``
block per profile, source ``sos://readiness/{id}#s{scope}`` (the id is derived from the school,
the profile and the day; ``scope`` fingerprints the caller's ``dq.readiness.read`` reach, so the
figure is withheld after that reach changes, audit W3-09): per class and in total, students,
ready, waiting for the parent, waiting for the school and blocked (who must act, the fix owner).
Never a name, an id or a value: the students behind a figure are for ``list_findings`` and the
readiness page under their own scopes. Read-only; no network.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import DomainError
from app.dq import service as dq
from app.knowledge import sources
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import SearchResultBlock, ToolOutcome, ToolSpec

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

NAME: Final = "get_readiness_summary"
IST: Final = ZoneInfo("Asia/Kolkata")
_NAMESPACE: Final = uuid.UUID("0192f0c1-7a51-7c4e-9d0e-5c0a6b0e7d21")
_PROFILE_PATTERN: Final = r"^[a-z0-9][a-z0-9-]{0,63}$"


class ReadinessArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_key: str | None = Field(default=None, pattern=_PROFILE_PATTERN)


def _line(row: dq.ReadinessClassCounts) -> str:
    return (
        f"{row.label_en}: {row.ready} of {row.students} ready, {row.needs_parent} waiting for "
        f"the parent, {row.needs_school} waiting for the school, {row.blocked} blocked "
        "(needs a decision)"
    )


class GetReadinessSummaryTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``get_readiness_summary``."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("get_readiness_summary needs a description in tools.yaml")
        self._max = config.max_results or 5
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {
                    "profile_key": {
                        "type": "string",
                        "pattern": _PROFILE_PATTERN,
                        "description": (
                            "One readiness profile key (for example bseap-ssc-2027); leave out for "
                            "every profile of the school"
                        ),
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
        try:
            args = ReadinessArgs.model_validate(dict(arguments))
            found = dq.readiness_for_ask(
                session, ctx, profile_key=args.profile_key, limit=self._max
            )
        except (ValidationError, DomainError):
            return error
        day = today or dt.datetime.now(IST).date()
        scope = sources.scope_fingerprint(ctx.scope_for(self._spec.permission))
        return ToolOutcome(
            call_id=call_id,
            blocks=tuple(self._block(ctx.tenant_id, p, day, scope) for p in found),
        )

    @staticmethod
    def _block(
        tenant_id: uuid.UUID, profile: dq.ReadinessForAsk, day: dt.date, scope: str
    ) -> SearchResultBlock:
        key = uuid.uuid5(_NAMESPACE, f"{tenant_id}|{profile.profile_key}|{day.isoformat()}")
        as_of = day.strftime("%d/%m/%Y")
        if profile.totals.students == 0:
            body = "No student you can access is in the classes this check covers."
        else:
            parts = [_line(row) for row in profile.classes]
            body = "; ".join([*parts, _line(profile.totals)]) + "."
        return SearchResultBlock(
            source=sources.readiness(key, scope=scope),
            title=f"Readiness · {profile.label_en} · by class",
            text=(
                f"Readiness for {profile.label_en} of the students you can access, from the "
                f"current records: {body} As of {as_of}."
            ),
        )


__all__ = ["NAME", "GetReadinessSummaryTool", "ReadinessArgs"]
