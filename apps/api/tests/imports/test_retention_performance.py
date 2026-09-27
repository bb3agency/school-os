"""Raw-file retention (FR-IMP-007), validation speed (FR-IMP-006, NFR-PERF-004), log redaction
(SEC-008) and the outbox wiring of the import jobs. Synthetic data only."""

from __future__ import annotations

import datetime as dt
import sys
import time
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.core.errors import Conflict
from app.documents import service as documents
from app.imports import service
from app.ops import service as ops

pytestmark = pytest.mark.db
S = sys.modules["sos_test_imports_support"]
W = S.W


def _delete_document(school: Any, document_id: Any) -> None:
    from app.authz.context import Scopes, UserContext

    owner = school.people["owner"]
    ctx = UserContext(
        user_id=owner.user_id,
        tenant_id=school.tenant_id,
        membership_id=owner.membership_id,
        roles=frozenset({"owner"}),
        permissions=frozenset({"document.read", "document.manage_acl"}),
        scopes=Scopes(school=True),
        mfa=True,
        auth_time=None,
    )
    with tenant_session(school.tenant_id, owner.user_id) as s:
        documents.delete_document(s, ctx, document_id)


def test_FR_IMP_007_raw_file_kept_90_days_after_commit_then_deleted(
    world: Any, admin_engine: Engine
) -> None:
    rows, numbers = S.class_list(1)
    batch_id = S.imported(admin_engine, world.a, S.xlsx_bytes(rows))
    batch = S.batch(admin_engine, batch_id)
    doc = batch["document_id"]
    store = S.D.memory_store()
    with admin_engine.connect() as c:
        from sqlalchemy import text

        key: str = c.execute(
            text("SELECT object_key FROM kb.document_versions WHERE document_id = :d"), {"d": doc}
        ).scalar_one()
    assert key in store.objects
    # Within the retention period the file cannot be deleted by anyone.
    with pytest.raises(Conflict) as err:
        _delete_document(world.a, doc)
    assert err.value.code == "import_file_retained"
    assert service.purge_raw_files(world.a.tenant_id) == 0
    # 91 days later the daily job deletes it; the parsed rows and the students stay.
    S.age_batch(
        admin_engine,
        batch_id,
        committed_at=dt.timedelta(days=91),
        revert_deadline=dt.timedelta(days=91),
        created_at=dt.timedelta(days=91),
    )
    assert service.purge_raw_files(world.a.tenant_id) >= 1
    after = S.batch(admin_engine, batch_id)
    assert after["document_id"] is None
    assert after["raw_file_deleted_at"] is not None
    assert S.count(admin_engine, "SELECT count(*) FROM kb.documents WHERE id = :d", d=doc) == 0
    assert len(S.rows(admin_engine, batch_id)) == 1
    assert S.student_by_adm(admin_engine, world.a.tenant_id, numbers[0]) is not None
    events = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "import.raw_file_deleted")
        if str(e["resource_id"]) == str(batch_id)
    ]
    assert events
    assert events[0]["summary"] == {"document_id": str(doc), "reason": "retention"}
    purge = next(
        p
        for p in S.D.outbox_events(admin_engine, world.a.tenant_id, "document.deleted")
        if p["document_id"] == str(doc)
    )
    assert purge["batch_ids"] == [key.split("/")[3]]
    documents.purge_document_objects(world.a.tenant_id, doc, purge["batch_ids"], store=store)
    assert key not in store.objects


def test_FR_IMP_007_uncommitted_file_can_be_deleted_and_the_batch_then_fails(
    world: Any, admin_engine: Engine
) -> None:
    rows, _ = S.class_list(1)
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    doc = S.batch(admin_engine, batch_id)["document_id"]
    _delete_document(world.a, doc)
    S.request_commit(world.a, batch_id)
    assert S.run_commit(world.a, batch_id) == "failed"
    batch = S.batch(admin_engine, batch_id)
    assert (batch["status"], batch["error_code"]) == ("validated", "file_missing")


def test_FR_IMP_007_purge_is_a_system_delete_and_one_refusal_does_not_stop_the_rest(
    world: Any, admin_engine: Engine
) -> None:
    """documents.delete_for_retention (no user): a raw file still linked as evidence is kept
    (409 document_in_use inside a savepoint) and the other due files are still deleted."""
    from sqlalchemy import text

    from app.students import service as students

    kept_rows, kept_numbers = S.class_list(1)
    kept_batch = S.imported(admin_engine, world.a, S.xlsx_bytes(kept_rows))
    gone_rows, _ = S.class_list(1)
    gone_batch = S.imported(admin_engine, world.a, S.xlsx_bytes(gone_rows))
    kept_doc = S.batch(admin_engine, kept_batch)["document_id"]
    gone_doc = S.batch(admin_engine, gone_batch)["document_id"]
    sid = S.student_by_adm(admin_engine, world.a.tenant_id, kept_numbers[0])
    ctx = S.SW.admin_ctx(world.a)
    with tenant_session(world.a.tenant_id, ctx.user_id) as s:
        students.record_value(
            s, ctx, sid, "mother_tongue", "parent_form", "Telugu", evidence_document_id=kept_doc
        )
    for batch_id in (kept_batch, gone_batch):
        S.age_batch(
            admin_engine,
            batch_id,
            committed_at=dt.timedelta(days=91),
            revert_deadline=dt.timedelta(days=91),
            created_at=dt.timedelta(days=91),
        )
    assert service.purge_raw_files(world.a.tenant_id) >= 1
    assert S.batch(admin_engine, kept_batch)["document_id"] == kept_doc
    assert S.batch(admin_engine, kept_batch)["raw_file_deleted_at"] is None
    assert S.batch(admin_engine, gone_batch)["document_id"] is None
    with admin_engine.connect() as c:
        actors = c.execute(
            text(
                "SELECT actor_type, summary FROM audit.events WHERE tenant_id = :t "
                "AND action = 'document.deleted' AND resource_id = :d"
            ),
            {"t": world.a.tenant_id, "d": gone_doc},
        ).all()
    assert [(a.actor_type, a.summary["reason"]) for a in actors] == [("system", "import_raw_file")]


def test_FR_IMP_006_2000_rows_validate_well_within_60_seconds(
    world: Any, admin_engine: Engine
) -> None:
    rows, _ = S.class_list(2000)
    data = S.xlsx_bytes(rows)
    batch_id = S.start(admin_engine, world.a, data, parse=False)
    started = time.perf_counter()
    assert S.run_parse(world.a, batch_id) == "validated"  # parse + map + validate + store
    elapsed = time.perf_counter() - started
    batch = S.batch(admin_engine, batch_id)
    assert batch["row_count"] == 2000
    assert batch["error_count"] == 0
    assert elapsed < 20, f"2,000 rows took {elapsed:.1f}s (budget 60 s, NFR-PERF-004)"


def test_FR_IMP_001_outbox_routes_to_the_import_workers(world: Any, admin_engine: Engine) -> None:
    rows, _ = S.class_list(1)
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows), parse=False)
    events = S.D.outbox_events(admin_engine, world.a.tenant_id, "import.parse_requested")
    payload = next(e for e in events if e["batch_id"] == str(batch_id))
    assert set(payload) == {"batch_id", "job_id", "user_id", "membership_id"}
    assert ops.OUTBOX_ROUTES["import.parse_requested"] == "imports.parse"
    assert ops.OUTBOX_ROUTES["import.validate_requested"] == "imports.validate"
    assert ops.OUTBOX_ROUTES["import.commit_requested"] == "imports.commit"
    from app.imports import tasks

    assert tasks.parse.run(str(world.a.tenant_id), "evt", payload) == "validated"
    assert tasks.parse.run(str(world.a.tenant_id), "evt", payload) == "skipped"  # idempotent


def test_SEC_008_imports_do_not_log_personal_data(
    world: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "Jonnalagadda Synthetica Importlog"
    father = "Jonnalagadda Syntheticus Importlog"
    aadhaar = S.valid_aadhaar("45678901234")
    rows = [
        [*S.HEADER, "Caste", "Remarks"],
        [S.adm("LOG"), name, father, "23/11/2011", "M", "IX", "A", "Synthetic Caste Log", ""],
        [S.adm("LOG"), "Synthetica Other", "", "24/11/2011", "F", "IX", "A", "", aadhaar],
    ]
    capsys.readouterr()
    batch_id = S.start(admin_engine, world.a, S.xlsx_bytes(rows))
    S.commit(world.a, batch_id, skip_error_rows=True)
    with tenant_session(world.a.tenant_id, world.a.people["office_admin"].user_id) as s:
        service.revert(s, S.ctx(world.a), batch_id)
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "imports.commit.done" in logs  # the logs were captured
    for secret in (name, father, "2011-11-23", "23/11/2011", "Synthetic Caste Log", aadhaar):
        assert secret not in logs, secret
    assert aadhaar[-4:] + '"' not in logs
