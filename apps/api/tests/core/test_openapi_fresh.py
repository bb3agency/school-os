"""The committed OpenAPI document matches the code (docs/09; the TS client is generated from it)."""

from __future__ import annotations

from pathlib import Path

from app.openapi_export import document

COMMITTED = Path(__file__).resolve().parents[2] / "openapi.json"


def test_openapi_document_is_up_to_date() -> None:
    assert COMMITTED.exists(), "run `make openapi` and commit apps/api/openapi.json"
    assert COMMITTED.read_text(encoding="utf-8") == document(), (
        "apps/api/openapi.json is stale: run `make openapi` and commit the result"
    )
