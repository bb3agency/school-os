"""Disclosure objects for the security suites (R3-1C; ADR-0051): the authorization matrix and
BOLA. Loaded by tests/security/test_authz_matrix.py and test_bola.py. Synthetic data only.

The disclosure answers 404 ``module_hidden`` outside CBSE schools, so the suites turn CBSE on
for the shared world's school only while a request is sent (:func:`cbse_on`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, text

PREFIX = "/api/v1/disclosure"

# Matrix answers when the guard passes (anything else is the default 200/201).
MATRIX_STATUS: dict[tuple[str, str], int] = {
    # The shared world's draft has no required items: 422 incomplete.
    ("POST", "/api/v1/disclosure/publish"): 422,
    # An older snapshot: only the live one can be withdrawn (409 snapshot_not_live).
    ("POST", "/api/v1/disclosure/snapshots/{snapshot_id}/withdraw"): 409,
    # Files are still queued (409 files_not_ready).
    ("GET", "/api/v1/disclosure/snapshots/{snapshot_id}/download-url"): 409,
    # Never published in the shared world: 409 not_published.
    ("POST", "/api/v1/disclosure/link/rotate"): 409,
}

BOLA_BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("POST", "/api/v1/disclosure/snapshots/{snapshot_id}/withdraw"): {
        "reason": "Synthetic withdrawal reason"
    },
}
BOLA_QUERY: dict[tuple[str, str], dict[str, str]] = {
    ("GET", "/api/v1/disclosure/snapshots/{snapshot_id}/download-url"): {"format": "pdf"},
}


def is_disclosure_path(path: str) -> bool:
    return path.startswith(PREFIX)


@contextlib.contextmanager
def cbse_on(admin: Engine, tenant_id: uuid.UUID) -> Iterator[None]:
    with admin.connect() as c:
        boards: list[str] = list(
            c.execute(
                text("SELECT boards FROM core.tenants WHERE id = :t"), {"t": tenant_id}
            ).scalar_one()
        )
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET boards = :b WHERE id = :t"),
            {"b": sorted({*boards, "CBSE"}), "t": tenant_id},
        )
    try:
        yield
    finally:
        with admin.begin() as c:
            c.execute(
                text("UPDATE core.tenants SET boards = :b WHERE id = :t"),
                {"b": boards, "t": tenant_id},
            )


def insert_snapshot(
    admin: Engine, tenant_id: uuid.UUID, user_id: uuid.UUID, *, days_ago: int = 0
) -> uuid.UUID:
    content = {"format_key": "cbse-appendix-ix", "format_version": 1, "sections": []}
    raw = json.dumps(content, sort_keys=True).encode()
    snapshot = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.disclosure_snapshots (id, tenant_id, format_key, format_version, "
                "content, content_sha256, published_by, published_at) VALUES (:i, :t, "
                "'cbse-appendix-ix', 1, CAST(:c AS jsonb), :h, :u, "
                "now() - make_interval(days => :d))"
            ),
            {
                "i": snapshot,
                "t": tenant_id,
                "c": json.dumps(content),
                "h": hashlib.sha256(raw).digest(),
                "u": user_id,
                "d": days_ago,
            },
        )
    return snapshot


def draft_version(admin: Engine, tenant_id: uuid.UUID) -> int:
    with admin.connect() as c:
        value = c.execute(
            text("SELECT version FROM ops.disclosure_drafts WHERE tenant_id = :t"),
            {"t": tenant_id},
        ).scalar()
    return int(value or 0)


__all__ = [
    "BOLA_BODIES",
    "BOLA_QUERY",
    "MATRIX_STATUS",
    "cbse_on",
    "draft_version",
    "insert_snapshot",
    "is_disclosure_path",
]
