"""``DocumentDetail.allowed_doc_types``: the document types that suit a document's purpose,
from the same rule the PATCH check uses (FR-DOC-005; SEC-015, SEC-001). The school console
offers only these when details are edited. Synthetic data only."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

from app.documents import service

pytestmark = pytest.mark.db
S = sys.modules["sos_test_documents_support"]
DOCS = "/api/v1/documents"


@pytest.mark.parametrize(
    ("purpose", "doc_type"),
    [
        ("circular", "circular"),
        ("policy", "policy"),
        ("other", "other"),
        ("evidence", "evidence"),
        ("register_scan", "register_scan"),
    ],
)
def test_FR_DOC_005_detail_lists_doc_types_allowed_for_its_purpose(
    world: Any, api: Any, admin_engine: Engine, purpose: str, doc_type: str
) -> None:
    owner = world.person("owner")
    doc = S.make_document(
        admin_engine, world.a.tenant_id, owner.user_id, purpose=purpose, doc_type=doc_type
    )
    res = api.call(owner, "GET", f"{DOCS}/{doc}")
    assert res.status_code == 200, res.text
    assert res.json()["allowed_doc_types"] == list(service.purpose_rule(purpose).doc_types)


def test_FR_DOC_005_allowed_doc_types_follow_the_patch_rule(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    doc = S.make_document(
        admin_engine,
        world.a.tenant_id,
        owner.user_id,
        purpose="register_scan",
        doc_type="register_scan",
    )
    detail = api.call(owner, "GET", f"{DOCS}/{doc}")
    assert detail.json()["allowed_doc_types"] == ["register_scan"]
    assert "circular" not in detail.json()["allowed_doc_types"]


def test_SEC_001_other_school_document_detail_is_404(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    doc = S.make_document(admin_engine, world.a.tenant_id, world.person("owner").user_id)
    assert api.call(world.b.people["owner"], "GET", f"{DOCS}/{doc}").status_code == 404
