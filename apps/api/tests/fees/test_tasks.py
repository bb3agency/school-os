"""Receipt PDFs on the worker and the nightly late-fee job (FR-FEE-009, FR-FEE-011)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.core.errors import Conflict
from app.fees import service as fees
from app.fees import tasks

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]


def test_FR_FEE_011_render_stores_a_fee_receipt_document(school: Any, admin_engine: Engine) -> None:
    receipt = F.issue(school, F.account(school))
    renderer = F.FakeRenderer()
    with pytest.raises(Conflict) as err:
        F.call(school, "accountant", fees.download_url, receipt.id)
    assert err.value.code == "pdf_not_ready"
    assert (
        fees.render_pdf(
            school.tenant_id, receipt.id, school.people["office_staff"].user_id, renderer=renderer
        )
        == "ready"
    )
    assert len(renderer.pages) == 1
    assert receipt.receipt_number in renderer.pages[0]
    # Idempotent: a stored PDF is left alone.
    assert (
        fees.render_pdf(
            school.tenant_id, receipt.id, school.people["office_staff"].user_id, renderer=renderer
        )
        == "ready"
    )
    assert len(renderer.pages) == 1
    purpose = F.scalar(
        admin_engine,
        "SELECT d.purpose FROM fin.receipts r JOIN kb.documents d ON d.tenant_id = r.tenant_id "
        "AND d.id = r.document_id WHERE r.id = :i",
        i=receipt.id,
    )
    assert purpose == "fee_receipt"
    document_id = F.scalar(
        admin_engine, "SELECT document_id FROM fin.receipts WHERE id = :i", i=receipt.id
    )
    with admin_engine.begin() as c:
        from sqlalchemy import text

        c.execute(
            text("UPDATE kb.document_versions SET status = 'ready' WHERE document_id = :d"),
            {"d": document_id},
        )
    link = F.call(school, "accountant", fees.download_url, receipt.id)
    assert link.filename.startswith("receipt-RCT-2026-27-")


def test_FR_FEE_011_failed_render_is_marked_and_retried(school: Any) -> None:
    receipt = F.issue(school, F.account(school))
    fees.mark_pdf_failed(school.tenant_id, receipt.id, "render_failed")
    got = F.call(school, "accountant", fees.get_receipt, receipt.id)
    assert got.pdf_status == "failed"
    again = F.call(school, "office_staff", fees.retry_render, receipt.id)
    assert again.pdf_status == "queued"


def test_FR_FEE_009_nightly_job_runs_per_school(school: Any) -> None:
    out = tasks.post_all()
    assert out["failed"] == 0
    with tenant_session(school.tenant_id) as s:
        assert fees.post_late_fees(s)["posted"] == 0  # nothing new on a second run


def test_beat_schedule_runs_at_0030_ist() -> None:
    entry = tasks.beat_schedule()["fees-post-late-fees"]
    assert entry["task"] == tasks.LATE_FEES_TASK
    schedule = entry["schedule"]
    assert schedule.hour == {19}
    assert schedule.minute == {0}
