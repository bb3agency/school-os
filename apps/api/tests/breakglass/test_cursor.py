"""Page cursors must carry a timezone-aware time (docs/09 cursors), as exports already check."""

import base64
import datetime as dt
import json
import uuid
from collections.abc import Callable

import pytest

from app.breakglass import service as breakglass
from app.changes import service as changes
from app.core.errors import ValidationFailed

After = Callable[[str | None], tuple[dt.datetime, uuid.UUID] | None]


def _cursor(t: str) -> str:
    raw = json.dumps({"t": t, "i": str(uuid.uuid4())}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.mark.parametrize("after", [breakglass._after, changes._after])
def test_docs_09_naive_cursor_time_is_rejected(after: After) -> None:
    with pytest.raises(ValidationFailed):
        after(_cursor("2026-09-26T04:30:00"))


@pytest.mark.parametrize("after", [breakglass._after, changes._after])
def test_docs_09_aware_cursor_time_is_accepted(after: After) -> None:
    result = after(_cursor("2026-09-26T04:30:00+00:00"))
    assert result is not None
    assert result[0].tzinfo is not None
