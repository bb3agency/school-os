"""DEK rotation and background re-encryption (SEC-012; docs/05 §9, 07 §8; migration 0026).

Every test builds its own synthetic school(s) so rotations never touch the shared world. Each
school gets: one student with C3 values (health notes, Aadhaar last 4), a superseded C3 history
row, a guardian with phone (blind index) and address, and a cancelled (decided) change request
with C3 old/new values. Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import Conflict
from app.students import crypto, rotation
from app.students import service as students
from app.students import tasks as student_tasks
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W

HEALTH = "Synthetic rotation asthma note"
HEALTH_NEW = "Synthetic rotation inhaler note"
LAST4 = "5307"
PHONE = "9876512345"
ADDRESS = "Synthetic rotation lane 7"
CR_NEW = "Synthetic requested note"
PLAINTEXTS = (HEALTH, HEALTH_NEW, LAST4, PHONE, ADDRESS, CR_NEW)
SIS_COLUMNS = [c for c in rotation.CIPHERTEXT_COLUMNS if c[0].startswith("sis.")]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


D = _load(
    "sos_test_documents_support", Path(__file__).resolve().parents[1] / "documents" / "support.py"
)


# --- synthetic schools --------------------------------------------------------------------------


def _cancelled_request(admin: Engine, school: Any, student_id: uuid.UUID) -> uuid.UUID:
    owner = school.people["owner"]
    doc = D.make_document(
        admin,
        school.tenant_id,
        owner.user_id,
        purpose="evidence",
        doc_type="certificate",
        sensitivity="C3",
    )
    with admin.connect() as c:
        old_id: Any = c.execute(
            text(
                "SELECT id FROM sis.attribute_values WHERE student_id = :s "
                "AND attribute_key = 'health_notes' AND superseded_by IS NOT NULL"
            ),
            {"s": student_id},
        ).scalar_one()
    rid = uuid.uuid4()
    table = "sis.change_requests"
    with tenant_session(school.tenant_id) as db:
        new_blob, version = crypto.encrypt_value(
            db, CR_NEW, table=table, column="new_value_ciphertext", row_id=rid
        )
        old_blob, _ = crypto.encrypt_value(
            db, HEALTH, table=table, column="old_value_ciphertext", row_id=rid
        )
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.change_requests (id, tenant_id, student_id, attribute_key, "
                "target_source, old_value_id, old_value_ciphertext, new_value_ciphertext, "
                "key_version, reason, evidence_document_id, status, requested_by, decided_at, "
                "expires_at) VALUES (:i, :t, :s, 'health_notes', 'parent_form', :o, :ob, :nb, :v, "
                "'Synthetic correction reason', :d, 'cancelled', :m, now(), "
                "now() + interval '30 days')"
            ),
            {
                "i": rid,
                "t": school.tenant_id,
                "s": student_id,
                "o": old_id,
                "ob": old_blob,
                "nb": new_blob,
                "v": version,
                "d": doc,
                "m": owner.membership_id,
            },
        )
    return rid


def make_school(admin: Engine) -> Any:
    SW.configure_keyring()
    D.memory_store()
    school = W.School(W.provision_school())
    owner = W.add_member(admin, school.tenant_id, ["owner"])
    school.people["owner"] = owner
    W.build_structure(school, owner)
    sid = SW.create(
        school,
        name="Synthetica Rotation Kumar",
        section_key="section_9a",
        extra=[
            SW.ValueIn(attribute_key="health_notes", source="parent_form", value=HEALTH),
            SW.ValueIn(attribute_key="aadhaar_last4", source="aadhaar_as_printed", value=LAST4),
        ],
    )
    with tenant_session(school.tenant_id, owner.user_id) as db:
        students.record_value(
            db, SW.admin_ctx(school), sid, "health_notes", "parent_form", HEALTH_NEW
        )
    gid = SW.add_guardian(
        school, sid, full_name="Synthetica Rotation Guardian", phone=PHONE, address=ADDRESS
    )
    school.ids.update(student=sid, guardian=gid, request=_cancelled_request(admin, school, sid))
    return school


@pytest.fixture
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    return make_school(admin_engine)


@pytest.fixture
def other(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    return make_school(admin_engine)


# --- helpers ----------------------------------------------------------------------------------


def plaintexts(tenant_id: uuid.UUID) -> dict[tuple[str, str, uuid.UUID], str]:
    """Every C3 cell of the school, decrypted (tenant_session, process keyring)."""
    out: dict[tuple[str, str, uuid.UUID], str] = {}
    with tenant_session(tenant_id) as db:
        for table, column in SIS_COLUMNS:
            rows = db.execute(
                text(f"SELECT id, {column} AS b FROM {table} WHERE {column} IS NOT NULL")
            ).all()
            for row in rows:
                out[(table, column, row.id)] = crypto.decrypt_value(
                    db, bytes(row.b), table=table, column=column, row_id=row.id
                )
    return out


def raw_cells(admin: Engine, tenant_id: uuid.UUID) -> dict[tuple[str, str, uuid.UUID], bytes]:
    out: dict[tuple[str, str, uuid.UUID], bytes] = {}
    with admin.connect() as c:
        for table, column in SIS_COLUMNS:
            for row in c.execute(
                text(
                    f"SELECT id, {column} AS b FROM {table} "
                    f"WHERE tenant_id = :t AND {column} IS NOT NULL"
                ),
                {"t": tenant_id},
            ):
                out[(table, column, row.id)] = bytes(row.b)
    return out


def census(tenant_id: uuid.UUID) -> dict[int, int]:
    with tenant_session(tenant_id) as db:
        return rotation.census(db)


def versions(tenant_id: uuid.UUID) -> list[Any]:
    with tenant_session(tenant_id) as db:
        return tenancy.list_key_versions(db)


def age_keys(admin: Engine, tenant_id: uuid.UUID, minutes: int = 20) -> None:
    """Pretend the rotation happened ``minutes`` ago (the key cache window has passed)."""
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenant_keys SET created_at = created_at - make_interval(mins => :m) "
                "WHERE tenant_id = :t"
            ),
            {"m": minutes, "t": tenant_id},
        )


def rotate(school: Any, *, new_hmac_key: bool = False) -> int:
    return rotation.rotate(school.tenant_id, wrapper=W.wrapper(), new_hmac_key=new_hmac_key)


def guardian_row(admin: Engine, guardian_id: uuid.UUID) -> Any:
    with admin.connect() as c:
        return c.execute(
            text(
                "SELECT phone_ciphertext, phone_blind_index, address_ciphertext, key_version, "
                "version FROM sis.guardians WHERE id = :i"
            ),
            {"i": guardian_id},
        ).one()


def events(admin: Engine, tenant_id: uuid.UUID, prefix: str) -> list[dict[str, Any]]:
    return [e for e in W.audit_events(admin, tenant_id) if e["action"].startswith(prefix)]


# --- rotation -----------------------------------------------------------------------------------


def test_SEC_012_rotation_adds_a_current_version_and_old_data_still_decrypts(
    school: Any, other: Any, admin_engine: Engine
) -> None:
    before = plaintexts(school.tenant_id)
    assert set(before.values()) == set(PLAINTEXTS)
    assert census(school.tenant_id) == {1: 7}
    other_raw = raw_cells(admin_engine, other.tenant_id)

    assert rotate(school) == 2

    keys = versions(school.tenant_id)
    assert [(k.key_version, k.current, k.retired_at) for k in keys] == [
        (1, False, None),
        (2, True, None),
    ]
    assert plaintexts(school.tenant_id) == before, "old versions keep decrypting"
    assert census(school.tenant_id) == {1: 7}, "rotation itself rewrites nothing"
    rotated = events(admin_engine, school.tenant_id, "tenant.key.rotated")
    assert [e["summary"]["key_version"] for e in rotated] == [2]
    assert rotated[0]["summary"]["previous_key_version"] == 1
    assert rotated[0]["summary"]["hmac_key"] == "carried"
    with admin_engine.connect() as c:
        queued: Any = c.execute(
            text("SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e"),
            {"t": school.tenant_id, "e": rotation.ROTATED_EVENT},
        ).scalar_one()
    assert queued == {"key_version": 2}
    # The other school is untouched.
    assert [k.key_version for k in versions(other.tenant_id)] == [1]
    assert raw_cells(admin_engine, other.tenant_id) == other_raw


def test_SEC_012_new_writes_use_the_new_version_and_blind_index_is_carried(
    school: Any, admin_engine: Engine
) -> None:
    before = guardian_row(admin_engine, school.ids["guardian"])
    rotate(school)
    sid = school.ids["student"]
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as db:
        students.record_value(
            db, SW.admin_ctx(school), sid, "health_notes", "parent_form", "Synthetic third note"
        )
    gid = SW.add_guardian(
        school, sid, full_name="Synthetica Second Guardian", phone=PHONE, address="Synthetic 9"
    )
    with admin_engine.connect() as c:
        current = c.execute(
            text(
                "SELECT value_ciphertext, key_version FROM sis.attribute_values "
                "WHERE student_id = :s AND attribute_key = 'health_notes' "
                "AND superseded_by IS NULL"
            ),
            {"s": sid},
        ).one()
    assert current.key_version == 2
    assert int.from_bytes(bytes(current.value_ciphertext)[1:3], "big") == 2
    new_guardian = guardian_row(admin_engine, gid)
    assert new_guardian.key_version == 2
    assert int.from_bytes(bytes(new_guardian.phone_ciphertext)[1:3], "big") == 2
    # HMAC key carried over: the same phone gives the same blind index under both versions.
    assert bytes(new_guardian.phone_blind_index) == bytes(before.phone_blind_index)


def test_SEC_012_reencryption_moves_every_value_and_is_idempotent(
    school: Any, other: Any, admin_engine: Engine
) -> None:
    before = plaintexts(school.tenant_id)
    guardian_before = guardian_row(admin_engine, school.ids["guardian"])
    other_raw = raw_cells(admin_engine, other.tenant_id)
    rotate(school)

    run = rotation.run_reencryption(school.tenant_id, batch_size=2)

    assert run.done
    assert run.key_version == 2
    assert run.remaining == 0
    assert run.rows == {"attribute_values": 3, "guardians": 1, "change_requests": 1}
    assert run.batches == 3  # 5 rows (7 values) in batches of 2
    assert census(school.tenant_id) == {2: 7}
    assert plaintexts(school.tenant_id) == before
    guardian_after = guardian_row(admin_engine, school.ids["guardian"])
    assert guardian_after.key_version == 2
    assert guardian_after.version == guardian_before.version, "re-encryption is not an edit"
    assert bytes(guardian_after.phone_blind_index) == bytes(guardian_before.phone_blind_index)
    # History (superseded) rows and the decided change request were re-encrypted too.
    with admin_engine.connect() as c:
        superseded: Any = c.execute(
            text(
                "SELECT key_version FROM sis.attribute_values WHERE student_id = :s "
                "AND superseded_by IS NOT NULL AND value_ciphertext IS NOT NULL"
            ),
            {"s": school.ids["student"]},
        ).scalars()
        assert set(superseded) == {2}
        request = c.execute(
            text("SELECT key_version, status FROM sis.change_requests WHERE id = :i"),
            {"i": school.ids["request"]},
        ).one()
        assert (request.key_version, request.status) == (2, "cancelled")
        job = c.execute(
            text(
                "SELECT status, progress, idempotency_key FROM ops.job_runs "
                "WHERE tenant_id = :t AND task_name = :n"
            ),
            {"t": school.tenant_id, "n": rotation.REENCRYPT_TASK},
        ).one()
    assert job.status == "succeeded"
    assert job.idempotency_key == "dek-reencrypt-v2"
    assert job.progress["total"] == 5
    assert job.progress["remaining"] == 0
    audited = events(admin_engine, school.tenant_id, "tenant.key.reencrypted")
    assert sum(e["summary"]["total"] for e in audited) == 5
    assert all(set(e["summary"]) == {"key_version", "rows", "total"} for e in audited)

    again = rotation.run_reencryption(school.tenant_id)
    assert again.done
    assert again.total == 0
    assert len(events(admin_engine, school.tenant_id, "tenant.key.reencrypted")) == len(audited)
    # Cross-tenant: the other school's ciphertext and keys are exactly as they were.
    assert raw_cells(admin_engine, other.tenant_id) == other_raw
    assert census(other.tenant_id) == {1: 7}
    assert [k.key_version for k in versions(other.tenant_id)] == [1]


def test_SEC_012_crash_mid_batch_rolls_back_that_batch_and_a_rerun_resumes(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = plaintexts(school.tenant_id)
    rotate(school)
    first = rotation.run_reencryption(school.tenant_id, batch_size=1, max_batches=2)
    assert not first.done
    assert first.total == 2
    assert census(school.tenant_id) == {1: 5, 2: 2}

    real = crypto.reencrypt_value
    calls = {"n": 0}

    def crash_on_second(*args: Any, **kwargs: Any) -> bytes:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("synthetic worker crash")
        return real(*args, **kwargs)

    monkeypatch.setattr(crypto, "reencrypt_value", crash_on_second)
    with pytest.raises(RuntimeError):
        rotation.run_reencryption(school.tenant_id, batch_size=3)
    assert census(school.tenant_id) == {1: 5, 2: 2}, "the crashed batch rolled back"
    with admin_engine.connect() as c:
        failed = c.execute(
            text("SELECT status, error FROM ops.job_runs WHERE tenant_id = :t AND task_name = :n"),
            {"t": school.tenant_id, "n": rotation.REENCRYPT_TASK},
        ).one()
    assert (failed.status, failed.error) == ("failed", "RuntimeError")

    monkeypatch.setattr(crypto, "reencrypt_value", real)
    resumed = rotation.run_reencryption(school.tenant_id, batch_size=3)
    assert resumed.done
    assert resumed.total == 3
    assert census(school.tenant_id) == {2: 7}
    assert plaintexts(school.tenant_id) == before
    with admin_engine.connect() as c:
        job = c.execute(
            text(
                "SELECT status, attempts FROM ops.job_runs WHERE tenant_id = :t AND task_name = :n"
            ),
            {"t": school.tenant_id, "n": rotation.REENCRYPT_TASK},
        ).one()
    assert job.status == "succeeded"
    assert job.attempts == 3, "one job run per key version, resumed (never duplicated)"


def test_SEC_012_incident_rotation_replaces_the_hmac_key_and_recomputes_blind_indexes(
    school: Any, admin_engine: Engine
) -> None:
    before = guardian_row(admin_engine, school.ids["guardian"])
    assert rotate(school, new_hmac_key=True) == 2
    assert (
        events(admin_engine, school.tenant_id, "tenant.key.rotated")[0]["summary"]["hmac_key"]
        == "new"
    )
    rotation.run_reencryption(school.tenant_id)
    after = guardian_row(admin_engine, school.ids["guardian"])
    assert bytes(after.phone_blind_index) != bytes(before.phone_blind_index)
    with tenant_session(school.tenant_id) as db:
        expected, _ = crypto.blind_index(
            db, PHONE, purpose=students.PHONE_INDEX_PURPOSE, key_version=2
        )
    assert bytes(after.phone_blind_index) == expected


# --- retirement ---------------------------------------------------------------------------------


def test_SEC_012_old_versions_retire_only_when_unreferenced_and_after_the_cache_window(
    school: Any, admin_engine: Engine
) -> None:
    rotate(school)
    rotate(school)  # two rotations in a row: 1 and 2 are both older than 3
    early = rotation.retire_unreferenced(school.tenant_id, apply=True)
    assert early.in_use == {1: 7}
    assert early.blocked == {2: "key_cache_window"}
    age_keys(admin_engine, school.tenant_id)
    in_use = rotation.retire_unreferenced(school.tenant_id, apply=True)
    assert in_use.in_use == {1: 7}
    assert in_use.retired == (2,)

    rotation.run_reencryption(school.tenant_id)
    plan = rotation.retire_unreferenced(school.tenant_id, apply=False)
    assert plan.retired == (1,)
    assert versions(school.tenant_id)[0].retired_at is None, "dry run writes nothing"
    done = rotation.retire_unreferenced(school.tenant_id, apply=True)
    assert done.retired == (1,)

    keys = versions(school.tenant_id)
    assert [(k.key_version, k.current, k.retired_at is not None) for k in keys] == [
        (1, False, True),
        (2, False, True),
        (3, True, False),
    ]
    retired = events(admin_engine, school.tenant_id, "tenant.key.retired")
    assert sorted(e["summary"]["key_version"] for e in retired) == [1, 2]
    with tenant_session(school.tenant_id) as db, pytest.raises(Conflict) as current:
        tenancy.retire_key_version(db, 3)
    assert current.value.code == "key_current"
    # Wrapped keys are never deleted: a retired version still decrypts (e.g. restored data).
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM core.tenant_keys WHERE tenant_id = :t"),
                {"t": school.tenant_id},
            ).scalar_one()
            == 3
        )
    crypto.get_keyring().forget(school.tenant_id)
    with tenant_session(school.tenant_id) as db:
        blob, _ = crypto.encrypt_value(
            db, "Synthetic", table="sis.guardians", column="address_ciphertext", row_id=uuid.uuid4()
        )
        assert int.from_bytes(blob[1:3], "big") == 3
        crypto.get_keyring().dek(db, 1)


def test_SEC_012_retirement_refuses_while_values_use_the_version(
    school: Any, admin_engine: Engine
) -> None:
    rotate(school)
    age_keys(admin_engine, school.tenant_id)
    with tenant_session(school.tenant_id) as db, pytest.raises(Conflict) as refused:
        tenancy.retire_key_version(db, 1)
    assert refused.value.code == "key_in_use"
    assert versions(school.tenant_id)[0].retired_at is None


def test_SEC_012_rotation_refuses_a_school_that_is_not_active(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine
) -> None:
    result = tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Provisioning School", wrapper=W.wrapper()
    )
    with pytest.raises(Conflict) as refused:
        rotation.rotate(result.tenant_id, wrapper=W.wrapper())
    assert refused.value.code == "key_state"


# --- migration 0026 triggers --------------------------------------------------------------------


def test_SEC_012_history_and_decided_requests_change_only_by_reencryption(
    school: Any, app_engine: Engine
) -> None:
    rotate(school)
    rotation.run_reencryption(school.tenant_id)
    with tenant_session(school.tenant_id) as db:
        superseded: Any = db.execute(
            text(
                "SELECT id FROM sis.attribute_values WHERE superseded_by IS NOT NULL "
                "AND value_ciphertext IS NOT NULL"
            )
        ).scalar_one()
    statements = (
        # A superseded value's verification cannot change.
        (
            "UPDATE sis.attribute_values SET verification_status = 'verified', "
            "verified_at = now() WHERE id = :i",
            superseded,
            "superseded attribute values are immutable",
        ),
        # Re-encryption never goes back to an older key version.
        (
            "UPDATE sis.attribute_values SET key_version = 1 WHERE id = :i",
            superseded,
            "re-encryption to a newer key",
        ),
        # A decided request's workflow columns cannot change.
        (
            "UPDATE sis.change_requests SET decision_note = 'Synthetic later note' WHERE id = :i",
            school.ids["request"],
            "decided change requests are immutable",
        ),
        (
            "UPDATE sis.change_requests SET key_version = 1 WHERE id = :i",
            school.ids["request"],
            "re-encryption to a newer key",
        ),
    )
    for sql, row_id, message in statements:
        with pytest.raises(Exception, match=message), tenant_session(school.tenant_id) as db:
            db.execute(text(sql), {"i": row_id})


# --- census catalog -----------------------------------------------------------------------------


def test_SEC_012_census_lists_every_ciphertext_column(admin_engine: Engine) -> None:
    """A new ciphertext column must be added to the census (or excluded with a reason)."""
    with admin_engine.connect() as c:
        found = {
            (f"{r.table_schema}.{r.table_name}", r.column_name)
            for r in c.execute(
                text(
                    "SELECT table_schema, table_name, column_name FROM information_schema.columns "
                    "WHERE data_type = 'bytea' AND column_name LIKE '%ciphertext%' "
                    "AND table_schema IN ('core', 'sis', 'kb', 'ops', 'audit')"
                )
            )
        }
    assert found == set(rotation.CIPHERTEXT_COLUMNS) | set(rotation.NOT_TENANT_DEK)


def test_SEC_012_retirement_fails_closed_without_a_census(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    rotate(school)
    rotation.run_reencryption(school.tenant_id)
    age_keys(admin_engine, school.tenant_id)
    monkeypatch.setattr(tenancy, "KEY_REFERENCE_COUNTERS", [])
    with tenant_session(school.tenant_id) as db, pytest.raises(Conflict) as refused:
        tenancy.retire_key_version(db, 1)
    assert refused.value.code == "key_census_missing"


# --- worker task ------------------------------------------------------------------------------


def test_SEC_012_worker_task_reencrypts_in_bounded_runs_and_queues_its_continuation(
    school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    rotate(school)
    monkeypatch.setattr(student_tasks, "MAX_BATCHES", 1)
    original = rotation.run_reencryption

    def small(tenant_id: uuid.UUID, **kw: Any) -> rotation.ReencryptionRun:
        kw["batch_size"] = 2
        return original(tenant_id, **kw)

    monkeypatch.setattr(rotation, "run_reencryption", small)
    result = student_tasks.reencrypt_tenant.run(
        str(school.tenant_id), str(uuid.uuid4()), {"key_version": 2}
    )
    assert result["done"] is False
    assert result["total"] == 2
    with admin_engine.connect() as c:
        continuation: Any = c.execute(
            text("SELECT count(*) FROM ops.outbox WHERE tenant_id = :t AND event_type = :e"),
            {"t": school.tenant_id, "e": rotation.CONTINUE_EVENT},
        ).scalar_one()
    assert continuation == 1
    for _ in range(5):
        result = student_tasks.reencrypt_tenant.run(
            str(school.tenant_id), str(uuid.uuid4()), {"key_version": 2}
        )
        if result["done"]:
            break
    assert result["done"] is True
    assert census(school.tenant_id) == {2: 7}


# --- no personal data in logs -------------------------------------------------------------------


def test_SEC_008_rotation_logs_no_plaintext_or_key_material(
    school: Any,
    admin_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    capsys.readouterr()
    rotate(school)
    rotation.run_reencryption(school.tenant_id, batch_size=2)
    age_keys(admin_engine, school.tenant_id)
    rotation.retire_unreferenced(school.tenant_id, apply=True)
    captured = capsys.readouterr()
    logs = captured.out + captured.err + caplog.text
    for value in PLAINTEXTS:
        assert value not in logs
    with admin_engine.connect() as c:
        wrapped: Any = c.execute(
            text("SELECT wrapped_dek FROM core.tenant_keys WHERE tenant_id = :t"),
            {"t": school.tenant_id},
        ).scalars()
        for blob in wrapped:
            assert bytes(blob).hex() not in logs
    for event in events(admin_engine, school.tenant_id, "tenant.key."):
        for value in PLAINTEXTS:
            assert value not in repr(event["summary"])
