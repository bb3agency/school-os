"""Control-plane free text refuses control characters (audit 2026-10-06 R-12; API8, input
validation). Ticket messages (also typed by school staff), announcements (shown on every school's
banner), invoice and payment notes and flag descriptions accepted NUL, ESC and other control
characters: a NUL made PostgreSQL refuse the insert (500), the others were stored and shown.
Tabs and line breaks stay allowed in multi-line text."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.platform.schemas import (
    AnnouncementIn,
    FlagIn,
    InvoicePatch,
    PaymentIn,
    SchoolTicketMessageIn,
    TicketCreateSchool,
    TicketMessageIn,
)

NOW = dt.datetime.now(dt.UTC)
CASES: list[tuple[type[BaseModel], dict[str, Any], str]] = [
    (SchoolTicketMessageIn, {}, "body"),
    (TicketMessageIn, {}, "body"),
    (TicketCreateSchool, {"category": "other", "subject": "Synthetic"}, "body"),
    (
        AnnouncementIn,
        {"title_en": "Maintenance", "starts_at": NOW, "ends_at": NOW + dt.timedelta(hours=1)},
        "body_en",
    ),
    (
        AnnouncementIn,
        {"body_en": "Body", "starts_at": NOW, "ends_at": NOW + dt.timedelta(hours=1)},
        "title_en",
    ),
    (InvoicePatch, {}, "notes"),
    (
        PaymentIn,
        {
            "amount_inr": "10.00",
            "method": "upi",
            "reference": "UTR-1",
            "received_on": NOW.date().isoformat(),
        },
        "notes",
    ),
    (FlagIn, {"enabled": True}, "description"),
]


@pytest.mark.parametrize(("model", "base", "field"), CASES)
@pytest.mark.parametrize("bad", ["a\x00b", "a\x1bb", "a\x7fb"])
def test_R_12_control_characters_are_refused(
    model: type[BaseModel], base: dict[str, Any], field: str, bad: str
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**base, field: bad})


@pytest.mark.parametrize(("model", "base", "field"), [c for c in CASES if c[2] != "title_en"])
def test_R_12_ordinary_text_still_passes(
    model: type[BaseModel], base: dict[str, Any], field: str
) -> None:
    value = "Line one" if field == "description" else "Line one\nline two\twith a tab"
    errors: list[Any] = []
    try:
        model.model_validate({**base, field: value})
    except ValidationError as exc:
        errors = exc.errors()
    assert [e for e in errors if e["loc"][0] == field] == []
