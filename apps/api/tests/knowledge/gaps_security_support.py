"""Knowledge gap routes in the cross-module security suites (FA-06, FR-KB-085..090).

Loaded by tests/security/test_bola.py (a gap of school B must answer 404 to school A) and
tests/security/test_authz_matrix.py (one request per route for every system role). Synthetic
text only (invariant 11).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, insert

from app.core.db import tenant_session
from app.knowledge import gaps
from app.knowledge.models import KnowledgeGap
from app.students import crypto

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]

BASE = "/api/v1/knowledge/gaps"
GAP = f"{BASE}/{{gap_id}}"
BOLA_PREFIX = BASE

_QUESTION = "Is there a synthetic canteen menu circular for the winter term?"
_CITATION = {"source": f"sos://doc/{uuid.UUID(int=1)}/v1#p1", "cited_text": "synthetic"}


def _gap(tenant_id: uuid.UUID) -> uuid.UUID:
    """One open gap in the school, written the way ``gaps.collect`` writes it."""
    gap_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    with tenant_session(tenant_id) as s:
        redacted = gaps.gap_question(_QUESTION)
        digest, _ = crypto.blind_index(
            s, gaps.gap_key(redacted) + gap_id.hex, purpose=gaps.HMAC_PURPOSE
        )
        blob, version = crypto.encrypt_value(
            s, redacted, table=gaps.TABLE, column=gaps.QUESTION_COLUMN, row_id=gap_id
        )
        s.execute(
            insert(KnowledgeGap).values(
                id=gap_id,
                tenant_id=tenant_id,
                question_ciphertext=blob,
                question_hmac=digest,
                key_version=version,
                language="en",
                first_seen_at=now,
                last_seen_at=now,
                not_found_count=1,
            )
        )
    return gap_id


def b_gap(w: Any) -> uuid.UUID:
    """School B's gap (created once per world)."""
    if "kb_gap" not in w.b.ids:
        w.b.ids["kb_gap"] = _gap(w.b.tenant_id)
    value: uuid.UUID = w.b.ids["kb_gap"]
    return value


BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("POST", f"{GAP}/close"): {"resolution": "dismissed"},
    ("POST", f"{GAP}/verified-answer"): {
        "question": _QUESTION,
        "language": "en",
        "answer_text": "Synthetic answer.",
        "citations": [_CITATION],
    },
}


def matrix_specs() -> dict[tuple[str, str], Builder]:
    """One builder per gap route (school A). The id routes name a gap that does not exist:
    the guard is what the matrix checks, and an allowed role then gets the service's 404."""
    missing = uuid.uuid4()
    if_match = {"If-Match": '"1"'}
    return {
        ("GET", BASE): lambda w, r, a: (BASE, None, {}),
        ("GET", f"{BASE}/summary"): lambda w, r, a: (f"{BASE}/summary", None, {}),
        ("POST", f"{GAP}/close"): lambda w, r, a: (
            f"{BASE}/{missing}/close",
            BOLA_BODIES[("POST", f"{GAP}/close")],
            if_match,
        ),
        ("POST", f"{GAP}/verified-answer"): lambda w, r, a: (
            f"{BASE}/{missing}/verified-answer",
            BOLA_BODIES[("POST", f"{GAP}/verified-answer")],
            if_match,
        ),
    }


# Guard passed; no such gap in school A.
MATRIX_STATUS: dict[tuple[str, str], int] = {
    ("POST", f"{GAP}/close"): 404,
    ("POST", f"{GAP}/verified-answer"): 404,
}
