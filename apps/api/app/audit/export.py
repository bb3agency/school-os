"""CSV export of the school's audit log (FR-AUD-005, US-1001; invariants 1, 4, 5, 7).

``GET /api/v1/audit/export`` (``audit.read`` with a recent MFA sign-in, like every export,
docs/07 §5.2) streams the events matching the viewer's filters as a UTF-8 CSV file:

1. :func:`start`, in the request's transaction: counts the matching events up to the newest
   one (``seq`` bound, so the file is a fixed snapshot of an append-only log), refuses more than
   ``max_rows`` (422 ``too_many_events``: narrow the date range) and records ``audit.exported``
   with the filters and the count. That transaction commits before the first byte is sent, so
   every export is audited (invariant 7) and the export never contains its own event.
2. :func:`stream_csv`: reads the events oldest first in pages of ``page_size`` (keyset on
   ``seq``), each page in its own short ``tenant_session`` (RLS: this school only), and yields
   CSV bytes. A large school never holds a long transaction or builds the file in memory.

Only what the viewer shows is exported: IDs, codes, the action, resource type and the event
summary, which ``audit.record`` already validated (no names, phone numbers, emails, Aadhaar-like
numbers or long digit runs; invariant 5). Hashes and IP hashes are never exported. Every cell is
NFC text with Aadhaar-like numbers masked (invariant 4, defence in depth) and formula triggers
neutralised with a leading apostrophe (SEC-017, the same rule as ``app.exports.tables``).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import unicodedata
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import service
from app.audit.models import events
from app.audit.viewer import EVENT_COLUMNS, AuditFilters, filtered
from app.core.db import tenant_session
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.core.redaction import mask_aadhaar

log = get_logger(__name__)

CONFIG_PATH: Final = Path(__file__).with_name("export.yaml")
IST: Final = ZoneInfo("Asia/Kolkata")
CSV_BOM: Final = "﻿"  # Excel opens UTF-8 correctly only with a BOM
MEDIA_TYPE: Final = "text/csv; charset=utf-8"
HEADER: Final = (
    "seq",
    "occurred_at_utc",
    "occurred_at_ist",
    "actor_type",
    "actor_id",
    "action",
    "resource_type",
    "resource_id",
    "request_id",
    "summary",
)
_FORMULA_TRIGGERS: Final = ("=", "+", "-", "@", "\t", "\r")
_CONTROL_RE: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ExportLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    max_rows: int = Field(ge=1, le=1_000_000)
    page_size: int = Field(ge=1, le=10_000)


@lru_cache(maxsize=1)
def limits() -> ExportLimits:
    """``export.yaml`` next to this module (invariant 13)."""
    return ExportLimits.model_validate(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """What one export streams: the filters, the newest ``seq`` included and the row count."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    filters: AuditFilters
    up_to_seq: int
    rows: int
    generated_at: dt.datetime

    @property
    def filename(self) -> str:
        return f"audit-log-{self.generated_at.astimezone(IST):%Y%m%d-%H%M%S}.csv"


def _too_many(maximum: int) -> ValidationFailed:
    return ValidationFailed(
        [
            {
                "field": "from",
                "code": "too_many_events",
                "message_key": "errors.audit.too_many_events",
            }
        ],
        detail=f"More than {maximum} events match. Choose a shorter date range and try again.",
    )


def _iso(value: dt.datetime | None) -> str | None:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z") if value else None


def _summary_filters(filters: AuditFilters) -> dict[str, Any]:
    return {
        "actor_id": filters.actor_id,
        "action": filters.action,
        "resource_type": filters.resource_type,
        "resource_id": filters.resource_id,
        "from": _iso(filters.occurred_from),
        "to": _iso(filters.occurred_to),
    }


def start(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    filters: AuditFilters,
    request_id: str | None = None,
    max_rows: int | None = None,
) -> ExportPlan:
    """Check the size, fix the snapshot and audit ``audit.exported`` in the caller's
    ``tenant_session`` (must commit before :func:`stream_csv` runs).

    ``max_rows`` replaces the configured limit for callers that stream to storage rather than
    to a browser (the school's full data export, FR-ADM-001, whose own limit is in
    ``app/admin/config.yaml``)."""
    cfg = limits()
    maximum = cfg.max_rows if max_rows is None else max_rows
    newest: int | None = session.execute(
        select(func.max(events.c.seq)).where(events.c.tenant_id == tenant_id)
    ).scalar_one()
    up_to = int(newest or 0)
    count_stmt = filtered(select(func.count()).select_from(events), tenant_id, filters).where(
        events.c.seq <= up_to
    )
    rows = int(session.execute(count_stmt).scalar_one())
    if rows > maximum:
        raise _too_many(maximum)
    service.record(
        session,
        action="audit.exported",
        resource_type="audit_log",
        summary={
            "format": "csv",
            "filters": _summary_filters(filters),
            "rows": rows,
            "up_to_seq": up_to,
        },
        actor_id=user_id,
        request_id=request_id,
    )
    log.info("audit.exported", tenant_id=tenant_id, action="audit.exported", count=rows)
    return ExportPlan(
        tenant_id=tenant_id,
        user_id=user_id,
        filters=filters,
        up_to_seq=up_to,
        rows=rows,
        generated_at=dt.datetime.now(dt.UTC),
    )


def safe_cell(value: object) -> str:
    """NFC text, no control characters, Aadhaar-like numbers masked, formulas neutralised."""
    if value is None:
        return ""
    text = mask_aadhaar(_CONTROL_RE.sub("", unicodedata.normalize("NFC", str(value))))
    return "'" + text if text.startswith(_FORMULA_TRIGGERS) else text


def _row(event: Mapping[Any, Any]) -> list[str]:
    occurred: dt.datetime = event["occurred_at"]
    summary = json.dumps(
        event["summary"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    values: tuple[object, ...] = (
        event["seq"],
        _iso(occurred),
        f"{occurred.astimezone(IST):%Y-%m-%d %H:%M:%S}",
        event["actor_type"],
        event["actor_id"],
        event["action"],
        event["resource_type"],
        event["resource_id"],
        event["request_id"],
        summary,
    )
    return [safe_cell(v) for v in values]


def _encode(rows: list[list[str]], *, bom: bool = False) -> bytes:
    out = io.StringIO()
    if bom:
        out.write(CSV_BOM)
    writer = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def stream_csv(plan: ExportPlan) -> Iterator[bytes]:
    """The CSV file (BOM, header, then events oldest first), one page per transaction."""
    page_size = limits().page_size
    yield _encode([list(HEADER)], bom=True)
    after = 0
    while True:
        with tenant_session(plan.tenant_id, plan.user_id) as session:
            stmt = (
                filtered(select(*EVENT_COLUMNS), plan.tenant_id, plan.filters)
                .where(events.c.seq > after, events.c.seq <= plan.up_to_seq)
                .order_by(events.c.seq)
                .limit(page_size)
            )
            page = session.execute(stmt).mappings().all()
        if not page:
            return
        yield _encode([_row(e) for e in page])
        if len(page) < page_size:
            return
        after = int(page[-1]["seq"])


__all__ = [
    "HEADER",
    "MEDIA_TYPE",
    "ExportLimits",
    "ExportPlan",
    "limits",
    "safe_cell",
    "start",
    "stream_csv",
]
