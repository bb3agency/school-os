"""The verification queue follows the ACL of the source register scan (audit 2026-10-04
data-layer hardening note 10; FR-IMP-020, SEC-015, invariant 3). Synthetic data only.

Extracted rows are C2 student data. An ``import.run`` holder who cannot see the scanned page
(its document ACL) does not see its rows in the queue, and reading, confirming or rejecting
such a row answers 404 like a row that does not exist.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.extraction import service

pytestmark = pytest.mark.db
X = sys.modules["sos_test_extraction_support"]
ITEMS = "/api/v1/extraction-items"


def _hidden_and_open_items(admin: Engine, world: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A batch with a page only one membership may see (``target``) and a page with an empty
    ACL (school-wide readers): returns (batch id, hidden item, open item)."""
    a = world.a
    X.SW.configure_keyring()
    owner = a.people["owner"].user_id
    hidden_doc = X.register_scan(
        admin,
        a.tenant_id,
        owner,
        X.page_png([X.register_row(f"Synthetica Hidden {uuid.uuid4().hex[:6]}")]),
        acl=[("membership", str(a.people["target"].membership_id))],
    )
    open_doc = X.register_scan(
        admin,
        a.tenant_id,
        owner,
        X.page_png([X.register_row(f"Synthetica Open {uuid.uuid4().hex[:6]}")]),
    )
    batch_id = X.start_batch(a, [hidden_doc, open_doc])
    service.process_batch(a.tenant_id, batch_id)
    by_doc = {
        X.row_of(admin, "sis.extraction_items", i)["document_id"]: i
        for i in X.item_ids(admin, batch_id)
    }
    return batch_id, by_doc[hidden_doc], by_doc[open_doc]


def test_DL_hardening_10_queue_lists_only_rows_of_visible_scans(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    batch_id, hidden, visible = _hidden_and_open_items(admin_engine, world)
    staff = world.a.people["office_staff"]
    res = api.call(staff, "GET", ITEMS, params={"batch_id": str(batch_id)})
    assert res.status_code == 200, res.text
    assert [i["id"] for i in res.json()["data"]] == [str(visible)]
    # document.manage_acl holders see every document, so every row.
    admin = api.call(
        world.a.people["office_admin"], "GET", ITEMS, params={"batch_id": str(batch_id)}
    )
    assert {i["id"] for i in admin.json()["data"]} == {str(hidden), str(visible)}


def test_DL_hardening_10_paging_skips_invisible_rows_without_losing_visible_ones(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    batch_id, hidden, visible = _hidden_and_open_items(admin_engine, world)
    staff = world.a.people["office_staff"]
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, Any] = {"batch_id": str(batch_id), "limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = api.call(staff, "GET", ITEMS, params=params).json()
        seen += [i["id"] for i in page["data"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [str(visible)]
    assert str(hidden) not in seen


def test_DL_hardening_10_invisible_rows_answer_404_on_read_confirm_and_reject(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    _, hidden, visible = _hidden_and_open_items(admin_engine, world)
    staff = world.a.people["office_staff"]
    random = api.call(staff, "GET", f"{ITEMS}/{uuid.uuid4()}")
    read = api.call(staff, "GET", f"{ITEMS}/{hidden}")
    assert read.status_code == 404
    assert read.json()["detail"] == random.json()["detail"]
    assert api.call(staff, "GET", f"{ITEMS}/{visible}").status_code == 200
    # The exam coordinator holds import.commit but not document.manage_acl: the hidden page's
    # rows cannot be confirmed or rejected by them (404, nothing recorded).
    coordinator = world.a.people["exam_coordinator"]
    reject = api.call(coordinator, "POST", f"{ITEMS}/{hidden}/reject", json={"reason": "duplicate"})
    assert reject.status_code == 404, reject.text
    confirm = api.call(coordinator, "POST", f"{ITEMS}/{hidden}/confirm", json={})
    assert confirm.status_code in (404, 422), confirm.text
    row = X.row_of(admin_engine, "sis.extraction_items", hidden)
    assert row["status"] == "pending_review"
    ok = api.call(coordinator, "POST", f"{ITEMS}/{visible}/reject", json={"reason": "duplicate"})
    assert ok.status_code == 200, ok.text
