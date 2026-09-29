"""The school's full data export through the service and worker (US-1201 AC1; FR-ADM-001,
BR-08; invariants 3, 4, 5, 7; docs/07 §5.2 step-up, §8 masking; synthetic data only)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.admin import service as admin
from app.admin.config import load_config
from app.admin.schemas import TenantExportCreate
from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, StepUpRequired
from app.documents import service as documents_service
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
AD = sys.modules["sos_test_admin_support"]
SW = AD.SW
D = AD.D

NAME = "Synthetica Pallavi Varanasi"
PRINTED = "Synthetica Printed Varanasi"
NOTE = "Synthetic inhaler note for Pallavi"
PHONE = "9876501234"
ADDRESS = "Synthetic Street 12, Guntur"
AAD = "aadhaar_as_printed"


@pytest.fixture(scope="module")
def records(school: Any, world: Any, admin_engine: Engine) -> dict[str, Any]:
    """One student with C2 and C3 values from several sources, a guardian with phone and
    address, a pending change request, a ready document; and a student of another school."""
    AD.install()
    sid = SW.create(
        school,
        name=NAME,
        section_key="section_9a",
        admission_no="ADM/EXP/1",
        extra=[
            ValueIn(attribute_key="category", source="parent_form", value="obc"),
            ValueIn(attribute_key="health_notes", source="parent_form", value=NOTE),
            ValueIn(attribute_key="aadhaar_last4", source=AAD, value="4821"),
            ValueIn(attribute_key="aadhaar_name_as_printed", source=AAD, value=PRINTED),
        ],
    )
    gid = SW.add_guardian(
        school, sid, full_name="Synthetica Guardian", phone=PHONE, address=ADDRESS
    )
    doc = D.make_document(admin_engine, school.tenant_id, school.people["owner"].user_id)
    other = SW.create(world.b, name="Synthetica Otherschool Student", section_key="section_9a")
    return {"student": sid, "guardian": gid, "document": doc, "other": other}


def _events(admin_engine: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    return [e for e in AD.W.audit_events(admin_engine, tenant_id) if e["action"] == action]


# --- requests -------------------------------------------------------------------------------------


def test_FR_ADM_001_owner_request_is_queued_audited_and_handed_to_the_worker(
    school: Any, admin_engine: Engine
) -> None:
    AD.settle(admin_engine, school)
    out = AD.request(school)
    assert out.status == "queued"
    assert out.own is True
    assert out.can_download is False
    assert out.include_sensitive is False
    row = AD.row(admin_engine, out.id)
    assert row["requested_by"] == school.people["owner"].user_id
    assert row["job_id"] is not None
    events = AD.audit_rows(admin_engine, school.tenant_id, out.id)
    assert [e["action"] for e in events] == ["admin.export.requested"]
    summary = events[0]["summary"]
    assert summary["include_sensitive"] is False
    assert summary["sensitive_columns"] == []
    assert summary["link_valid_hours"] == 24
    outbox = D.outbox_events(admin_engine, school.tenant_id, "admin.tenant_export.requested")
    assert {"tenant_export_id": str(out.id), "job_id": str(row["job_id"])} in outbox
    # One export at a time per school.
    with pytest.raises(Conflict) as exc:
        AD.request(school, role="owner_2", as_ctx=AD.ctx(school, school.people["owner_2"], "owner"))
    assert exc.value.code == "tenant_export_in_progress"
    AD.settle(admin_engine, school)


@pytest.mark.parametrize("role", ["principal", "office_admin", "accountant", "auditor_readonly"])
def test_FR_ADM_001_only_holders_of_tenant_export_all_may_request(school: Any, role: str) -> None:
    with pytest.raises(Forbidden):
        AD.request(school, role=role)


def test_SEC_005_request_needs_a_recent_mfa_sign_in(school: Any, admin_engine: Engine) -> None:
    AD.settle(admin_engine, school)
    owner = school.people["owner"]
    stale = dataclasses.replace(
        AD.ctx(school, owner, "owner"),
        auth_time=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=6),
    )
    with pytest.raises(StepUpRequired):
        AD.request(school, as_ctx=stale)
    no_mfa = dataclasses.replace(AD.ctx(school, owner, "owner"), mfa=False)
    with pytest.raises(StepUpRequired):
        AD.request(school, as_ctx=no_mfa)


def test_FR_ADM_001_include_sensitive_needs_school_wide_read_sensitive(
    school: Any, admin_engine: Engine
) -> None:
    AD.settle(admin_engine, school)
    owner = school.people["owner"]
    base = AD.ctx(school, owner, "owner")
    without = dataclasses.replace(base, permissions=base.permissions - {"student.read_sensitive"})
    with pytest.raises(Forbidden) as exc:
        AD.request(school, include_sensitive=True, as_ctx=without)
    assert exc.value.code == "sensitive_not_allowed"


# --- the archive ----------------------------------------------------------------------------------


EXPECTED_TABLES = {
    "school",
    "academic_years",
    "classes",
    "sections",
    "staff",
    "roles",
    "students",
    "student_values",
    "enrollments",
    "guardians",
    "student_guardians",
    "promotion_runs",
    "promotion_items",
    "change_requests",
    "dq_runs",
    "dq_findings",
    "import_batches",
    "import_mapping_templates",
    "documents",
    "document_versions",
    "document_acl",
    "certificates",
    "certificate_counters",
    "circular_readings",
    "circular_suggestions",
    "tasks",
    "parent_notices",
    # M6 Tally connector (0036_tally, ADR-0032): empty tables are still in the archive.
    "tally_devices",
    "tally_enrolment_codes",
    "tally_groups",
    "tally_syncs",
    "tally_parties",
    "tally_party_links",
    "retention_settings",
}


@pytest.fixture(scope="module")
def masked_export(school: Any, records: dict[str, Any], admin_engine: Engine) -> uuid.UUID:
    """A ready full export of ``records``' school without restricted values."""
    export_id: uuid.UUID = AD.ready_export(admin_engine, school)
    return export_id


def test_US_1201_AC1_archive_holds_records_and_documents_masked_by_default(
    school: Any, records: dict[str, Any], masked_export: uuid.UUID, admin_engine: Engine
) -> None:
    export_id = masked_export
    row = AD.row(admin_engine, export_id)
    assert row["status"] == "ready"
    assert row["object_key"] == f"t/{school.tenant_id}/tenant-export/{export_id}.zip"
    assert row["expires_at"] - row["finished_at"] == dt.timedelta(hours=24)
    stored = D.memory_store().objects[row["object_key"]]
    assert stored.lifecycle == "tenant-export-2d"
    assert stored.content_type == "application/zip"
    assert len(stored.data) == row["size_bytes"]
    assert hashlib.sha256(stored.data).digest() == bytes(row["sha256"])

    zf = AD.archive(school, export_id)
    names = set(zf.namelist())
    assert {"README.txt", "manifest.json", "audit/audit-log.csv"} <= names
    for table in EXPECTED_TABLES:
        assert f"records/{table}.csv" in names, table
        assert f"records/{table}.json" in names, table
    # Documents as files, byte for byte.
    doc_path = f"documents/{records['document']}/v1.pdf"
    assert doc_path in names
    key = f"t/{school.tenant_id}/docs/{records['document']}/v1/original.pdf"
    assert zf.read(doc_path) == D.memory_store().objects[key].data

    values = [
        v
        for v in AD.records_csv(zf, "student_values")
        if v["student_id"] == str(records["student"])
    ]
    by_key = {v["attribute_key"]: v for v in values}
    assert by_key["full_name"]["value"] == NAME
    assert by_key["full_name"]["value_state"] == "value"
    for key_ in ("category", "health_notes", "aadhaar_last4"):
        assert by_key[key_]["value"] == "••••", key_
        assert by_key[key_]["value_state"] == "masked"
    assert by_key["aadhaar_name_as_printed"]["value"] == ""
    assert by_key["aadhaar_name_as_printed"]["value_state"] == "withheld"
    guardians = {g["id"]: g for g in AD.records_csv(zf, "guardians")}
    guardian = guardians[str(records["guardian"])]
    assert guardian["phone"] == "••••"
    assert guardian["address"] == "••••"
    as_json = AD.records_json(zf, "student_values")
    assert "c3_masked" in as_json["notes"]
    assert "aadhaar_as_printed_withheld" in as_json["notes"]

    everything = b"".join(zf.read(n) for n in names if not n.startswith("documents/"))
    for secret in (NOTE, PRINTED, PHONE, ADDRESS, "4821"):
        assert secret.encode() not in everything, secret
    # Other schools' records never reach the archive (RLS, invariant 1).
    assert b"Otherschool" not in everything
    assert str(records["other"]).encode() not in everything


def test_US_1201_AC1_archive_holds_the_audit_log_a_manifest_and_a_bilingual_readme(
    school: Any, masked_export: uuid.UUID, admin_engine: Engine
) -> None:
    export_id = masked_export
    row = AD.row(admin_engine, export_id)
    zf = AD.archive(school, export_id)
    audit_csv = zf.read("audit/audit-log.csv").decode("utf-8")
    assert audit_csv.startswith("﻿seq,occurred_at_utc")
    assert "admin.export.requested" in audit_csv
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["export_id"] == str(export_id)
    assert manifest["school"]["id"] == str(school.tenant_id)
    assert manifest["restricted_values"] == "masked"
    assert {t["name"] for t in manifest["tables"]} == EXPECTED_TABLES
    assert manifest["documents"]["files"] >= 1
    readme = zf.read("README.txt").decode("utf-8")
    assert "SchoolOS full data export" in readme
    assert "పూర్తి డేటా ఎగుమతి" in readme

    counts = row["counts"]
    assert counts["tables"]["students"] >= 1
    assert counts["documents"] >= 1
    assert counts["audit_events"] >= 1


def test_FR_ADM_001_archive_holds_tasks_and_parent_notices(
    school: Any, admin_engine: Engine
) -> None:
    """US-1201 "export all our data": the school's tasks and the parent notices it approved
    (M4, 0034_circulars) are school records and are in the archive."""
    owner = school.people["owner"]
    task_id, notice_id = uuid.uuid4(), uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tasks (id, tenant_id, title, owner_membership_id, due_on, "
                "source, created_by, created_by_membership) VALUES (:i, :t, "
                "'Synthetic export task', :m, '2026-10-15', 'manual', :u, :m)"
            ),
            {"i": task_id, "t": school.tenant_id, "m": owner.membership_id, "u": owner.user_id},
        )
        c.execute(
            text(
                "INSERT INTO ops.parent_notices (id, tenant_id, source, title_en, body_en, "
                "created_by) VALUES (:i, :t, 'blank', 'Synthetic sports day', "
                "'Synthetic notice body', :u)"
            ),
            {"i": notice_id, "t": school.tenant_id, "u": owner.user_id},
        )
    export_id = AD.ready_export(admin_engine, school)
    zf = AD.archive(school, export_id)
    tasks = {t["id"]: t for t in AD.records_csv(zf, "tasks")}
    assert tasks[str(task_id)]["title"] == "Synthetic export task"
    assert "tenant_id" not in tasks[str(task_id)]
    notices = {n["id"]: n for n in AD.records_csv(zf, "parent_notices")}
    assert notices[str(notice_id)]["title_en"] == "Synthetic sports day"
    for table in ("circular_readings", "circular_suggestions"):
        assert f"records/{table}.csv" in zf.namelist(), table


def test_FR_ADM_001_archive_holds_the_tally_connector_records(
    school: Any, admin_engine: Engine
) -> None:
    """M6 (0036_tally, ADR-0032): the synced ledgers and their links are school records and are
    in the archive; wrapped device keys and enrolment-code hashes never are."""
    owner = school.people["owner"]
    code_id, device_id, sync_id, party_id = (uuid.uuid4() for _ in range(4))
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tally_enrolment_codes (id, tenant_id, code_hash, device_name, "
                "created_by, expires_at) VALUES (:i, :t, :h, 'Office PC', :u, "
                "now() + interval '30 minutes')"
            ),
            {"i": code_id, "t": school.tenant_id, "h": b"\x07" * 32, "u": owner.user_id},
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_devices (id, tenant_id, name, enrolment_code_id, key_id, "
                "key_ciphertext, enrolled_by) VALUES (:i, :t, 'Office PC', :c, "
                "'tdk-abcdefghijklmnopqrst', :k, :u)"
            ),
            {
                "i": device_id,
                "t": school.tenant_id,
                "c": code_id,
                "k": b"\x09" * 40,
                "u": owner.user_id,
            },
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_syncs (id, tenant_id, device_id, batch_id, company, as_of, "
                "groups, parties, created, updated, missing, total_due) VALUES (:i, :t, :d, :b, "
                "'Synthetic School', '2026-09-28', 1, 1, 1, 0, 0, 1500.00)"
            ),
            {"i": sync_id, "t": school.tenant_id, "d": device_id, "b": uuid.uuid4()},
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_parties (id, tenant_id, company, ledger_name, group_name, "
                "closing_balance, as_of, last_sync_id) VALUES (:i, :t, 'Synthetic School', "
                "'Synthetic Export Ledger', 'Sundry Debtors', 1500.00, '2026-09-28', :s)"
            ),
            {"i": party_id, "t": school.tenant_id, "s": sync_id},
        )
    export_id = AD.ready_export(admin_engine, school)
    zf = AD.archive(school, export_id)
    parties = {p["id"]: p for p in AD.records_csv(zf, "tally_parties")}
    assert parties[str(party_id)]["ledger_name"] == "Synthetic Export Ledger"
    assert parties[str(party_id)]["closing_balance"] == "1500.00"
    devices = AD.records_csv(zf, "tally_devices")
    assert devices
    assert "key_ciphertext" not in devices[0]
    assert "next_key_ciphertext" not in devices[0]
    codes = AD.records_csv(zf, "tally_enrolment_codes")
    assert codes
    assert "code_hash" not in codes[0]
    for table in ("tally_groups", "tally_syncs", "tally_party_links"):
        assert f"records/{table}.csv" in zf.namelist(), table


def test_FR_ADM_001_completion_is_audited_notified_and_the_job_finished(
    school: Any, records: dict[str, Any], admin_engine: Engine
) -> None:
    export_id = AD.ready_export(admin_engine, school)
    actions = [e["action"] for e in AD.audit_rows(admin_engine, school.tenant_id, export_id)]
    assert actions == ["admin.export.requested", "admin.export.completed"]
    completed = AD.audit_rows(admin_engine, school.tenant_id, export_id)[1]["summary"]
    assert completed["tables"]["student_values"] >= 1
    assert completed["archive_kib"] >= 1
    # The audit CSV inside the archive is the audit module's export, itself audited.
    exported = _events(admin_engine, school.tenant_id, "audit.exported")
    assert exported
    notes = AD.notifications(admin_engine, school.tenant_id, export_id)
    assert notes == [
        (
            "admin.tenant_export.ready",
            school.people["owner"].membership_id,
            {"tenant_export_id": str(export_id), "hours": 24},
        )
    ]
    row = AD.row(admin_engine, export_id)
    with admin_engine.connect() as c:
        status: object = c.execute(
            text("SELECT status FROM ops.job_runs WHERE id = :j"), {"j": row["job_id"]}
        ).scalar_one()
    assert status == "succeeded"
    # Running a finished export again changes nothing (idempotent worker).
    assert AD.run(school, export_id) == "ready"
    assert len(AD.audit_rows(admin_engine, school.tenant_id, export_id)) == 2


def test_FR_ADM_001_include_sensitive_shows_c3_but_never_aadhaar_as_printed(
    school: Any, records: dict[str, Any], admin_engine: Engine
) -> None:
    export_id = AD.ready_export(admin_engine, school, include_sensitive=True)
    requested = AD.audit_rows(admin_engine, school.tenant_id, export_id)[0]["summary"]
    assert requested["include_sensitive"] is True
    assert {"category", "health_notes", "aadhaar_last4", "guardian_phone"} <= set(
        requested["sensitive_columns"]
    )
    assert "aadhaar_name_as_printed" not in requested["sensitive_columns"]
    zf = AD.archive(school, export_id)
    values = [
        v
        for v in AD.records_csv(zf, "student_values")
        if v["student_id"] == str(records["student"])
    ]
    by_key = {v["attribute_key"]: v for v in values}
    assert by_key["category"]["value"] == "obc"
    assert by_key["health_notes"]["value"] == NOTE
    assert by_key["aadhaar_last4"]["value"] == "XXXX XXXX 4821"  # PRV-014
    assert by_key["aadhaar_name_as_printed"]["value_state"] == "withheld"
    assert PRINTED.encode() not in b"".join(zf.read(n) for n in zf.namelist())
    guardian = {g["id"]: g for g in AD.records_csv(zf, "guardians")}[str(records["guardian"])]
    assert guardian["phone"] == PHONE
    assert guardian["address"] == ADDRESS
    assert "c3_masked" not in AD.records_json(zf, "student_values")["notes"]


# --- downloads ------------------------------------------------------------------------------------


def test_FR_ADM_001_download_link_is_short_lived_audited_and_for_holders_only(
    school: Any, records: dict[str, Any], admin_engine: Engine
) -> None:
    export_id = AD.ready_export(admin_engine, school)
    store = D.memory_store()
    for role, person in (("owner", school.people["owner"]), ("owner", school.people["owner_2"])):
        with tenant_session(school.tenant_id, person.user_id) as db:
            link = admin.download_url(db, AD.ctx(school, person, role), export_id)
        assert link.filename.startswith("schoolos-export-")
        assert link.filename.endswith(".zip")
        assert link.content_type == "application/zip"
        assert store.gets[-1]["ttl"] <= 300
        assert store.gets[-1]["key"] == f"t/{school.tenant_id}/tenant-export/{export_id}.zip"
    downloads = [
        e["summary"]
        for e in AD.audit_rows(admin_engine, school.tenant_id, export_id)
        if e["action"] == "admin.export.downloaded"
    ]
    assert [d["own_export"] for d in downloads] == [True, False]
    principal = school.people["principal"]
    with (
        tenant_session(school.tenant_id, principal.user_id) as db,
        pytest.raises(Forbidden),
    ):
        admin.download_url(db, AD.ctx(school, principal, "principal"), export_id)
    owner = school.people["owner"]
    stale = dataclasses.replace(AD.ctx(school, owner, "owner"), auth_time=None)
    with tenant_session(school.tenant_id, owner.user_id) as db, pytest.raises(StepUpRequired):
        admin.download_url(db, stale, export_id)


def test_FR_ADM_001_download_states_not_ready_failed_and_expired(
    school: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    queued = AD.queued_export(admin_engine, school)
    with tenant_session(school.tenant_id, owner.user_id) as db, pytest.raises(Conflict) as exc:
        admin.download_url(db, AD.ctx(school, owner, "owner"), queued)
    assert exc.value.code == "export_not_ready"
    assert admin.abandon(school.tenant_id, queued) is True
    with tenant_session(school.tenant_id, owner.user_id) as db, pytest.raises(Conflict) as exc:
        admin.download_url(db, AD.ctx(school, owner, "owner"), queued)
    assert exc.value.code == "export_failed"
    ready = AD.ready_export(admin_engine, school)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.tenant_exports SET expires_at = now() - interval '1 minute' "
                "WHERE id = :i"
            ),
            {"i": ready},
        )
    with tenant_session(school.tenant_id, owner.user_id) as db, pytest.raises(Conflict) as exc:
        admin.download_url(db, AD.ctx(school, owner, "owner"), ready)
    assert exc.value.code == "export_expired"


def test_FR_ADM_001_archive_is_purged_24_hours_after_it_was_ready(
    school: Any, admin_engine: Engine
) -> None:
    export_id = AD.ready_export(admin_engine, school)
    key = f"t/{school.tenant_id}/tenant-export/{export_id}.zip"
    store = D.memory_store()
    assert key in store.objects
    admin.purge_expired(school.tenant_id, now=dt.datetime.now(dt.UTC) + dt.timedelta(hours=23))
    assert key in store.objects, "kept while the link is valid"
    admin.purge_expired(school.tenant_id, now=dt.datetime.now(dt.UTC) + dt.timedelta(hours=25))
    assert key not in store.objects
    row = AD.row(admin_engine, export_id)
    assert row["status"] == "expired"
    assert row["files_deleted_at"] is not None
    expired = [
        e
        for e in AD.audit_rows(admin_engine, school.tenant_id, export_id)
        if e["action"] == "admin.export.expired"
    ]
    assert expired
    assert expired[0]["actor_type"] == "system"
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as db:
        out = admin.get_export(db, AD.ctx(school, owner, "owner"), export_id)
    assert out.status == "expired"
    assert out.can_download is False
    assert out.size_bytes is None


# --- failures -------------------------------------------------------------------------------------


def test_FR_ADM_001_export_fails_when_the_requester_lost_the_permission(
    school: Any, admin_engine: Engine
) -> None:
    AD.settle(admin_engine, school)
    person = AD.W.add_member(admin_engine, school.tenant_id, ["owner"])
    school.people["temp_owner"] = person
    c = AD.ctx(school, person, "owner")
    with tenant_session(school.tenant_id, person.user_id) as db:
        out = admin.request_export(db, c, TenantExportCreate())
    with admin_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM core.membership_roles WHERE membership_id = :m"),
            {"m": person.membership_id},
        )
    # The worker re-reads the requester's roles from the database (no cache involved).
    assert AD.run(school, out.id) == "failed"
    row = AD.row(admin_engine, out.id)
    assert row["error_code"] == "permission_revoked"
    actions = [e["action"] for e in AD.audit_rows(admin_engine, school.tenant_id, out.id)]
    assert actions == ["admin.export.requested", "admin.export.failed"]
    notes = AD.notifications(admin_engine, school.tenant_id, out.id)
    assert [n[0] for n in notes] == ["admin.tenant_export.failed"]
    assert f"t/{school.tenant_id}/tenant-export/{out.id}.zip" not in D.memory_store().objects


def test_FR_ADM_001_too_large_fails_without_storing_anything(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    AD.settle(admin_engine, school)
    D.make_document(admin_engine, school.tenant_id, school.people["owner"].user_id)
    cfg = load_config()
    small = cfg.model_copy(
        update={"tenant_export": cfg.tenant_export.model_copy(update={"max_document_bytes": 1})}
    )
    monkeypatch.setattr(admin, "load_config", lambda: small)
    out = AD.request(school)
    assert AD.run(school, out.id) == "failed"
    assert AD.row(admin_engine, out.id)["error_code"] == "too_large"
    writer = D.memory_store().writers[-1]
    assert writer.aborted
    assert f"t/{school.tenant_id}/tenant-export/{out.id}.zip" not in D.memory_store().objects


def test_FR_ADM_001_a_changed_document_is_never_shipped(school: Any, admin_engine: Engine) -> None:
    AD.settle(admin_engine, school)
    doc = D.make_document(admin_engine, school.tenant_id, school.people["owner"].user_id)
    key = f"t/{school.tenant_id}/docs/{doc}/v1/original.pdf"
    store = D.memory_store()
    original = store.objects[key]
    store.objects[key] = dataclasses.replace(original, data=original.data + b"tampered")
    try:
        out = AD.request(school)
        assert AD.run(school, out.id) == "failed"
        assert AD.row(admin_engine, out.id)["error_code"] == "integrity_mismatch"
        assert store.writers[-1].aborted
    finally:
        store.objects[key] = original


def test_FR_ADM_001_transient_errors_abort_the_upload_and_retry(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    AD.settle(admin_engine, school)
    out = AD.request(school)

    def boom(*_: Any, **__: Any) -> list[Any]:
        raise RuntimeError("synthetic storage outage")

    monkeypatch.setattr(documents_service, "export_files", boom)
    with pytest.raises(RuntimeError):
        AD.run(school, out.id)
    assert D.memory_store().writers[-1].aborted
    assert AD.row(admin_engine, out.id)["status"] == "running"
    monkeypatch.undo()
    assert AD.run(school, out.id) == "ready"  # the retry starts again from nothing


def test_FR_ADM_001_staff_table_leaves_out_break_glass_support_memberships(
    school: Any, admin_engine: Engine
) -> None:
    """07 §6.4: a temporary platform_support membership is SchoolOS support staff, not the
    school's staff; its operator's name and email never go into the school's archive."""
    bg = AD._load("sos_test_breakglass_objects", AD._HERE.parents[1] / "breakglass" / "objects.py")
    bg.active_grant(school.tenant_id, school.people["owner"])
    with admin_engine.connect() as c:
        support: set[object] = set(
            c.execute(
                text(
                    "SELECT mr.membership_id FROM core.membership_roles mr "
                    "JOIN core.roles r ON r.id = mr.role_id "
                    "WHERE mr.tenant_id = :t AND r.key = 'platform_support'"
                ),
                {"t": school.tenant_id},
            ).scalars()
        )
    assert support
    export_id = AD.ready_export(admin_engine, school)
    staff = AD.records_csv(AD.archive(school, export_id), "staff")
    assert {r["membership_id"] for r in staff}.isdisjoint({str(m) for m in support})
    assert str(school.people["owner"].membership_id) in {r["membership_id"] for r in staff}
