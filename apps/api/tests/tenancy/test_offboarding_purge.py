"""Offboarding purge on the school side (FR-PLT-005, US-1303 AC3; ADR-0029).

Every tenant table is populated for school A (``purge_support.populate_school``) and B; A is set
``offboarding`` and purged through ``app.tenancy.service``. Proves: zero rows left for A in every
tenant table of the catalog (new tables are covered automatically), B untouched, files deleted,
idempotent, fails closed (nothing deleted) on a partial failure and resumes, refused for a school
that is not offboarding, keys destroyed and decryption impossible afterwards, the retained audit
chain deleted only after its retention, and the ``sos_purger`` role narrowly scoped.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

import app.main  # noqa: F401  registers every module's purge owner (as the worker does)
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper, generate_tenant_keys
from app.core.db import tenant_session
from app.core.errors import Conflict
from app.documents import storage
from app.students import crypto
from app.tenancy import offboarding
from app.tenancy import service as tenancy

from .purge_support import School, populate_school, row_counts, tables_without_rows

pytestmark = pytest.mark.db

# The chain and its stored verification (R-19) stay until the audit retention ends.
RETAINED = {"audit.events", "audit.chain_heads", "audit.chain_verifications"}
KEYS = "core.tenant_keys"


def _documents_support() -> ModuleType:
    name = "sos_test_documents_support_offboarding"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "documents" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture
def store() -> Iterator[Any]:
    mem = _documents_support().MemoryStore()
    storage.set_object_store(mem)
    yield mem
    storage.set_object_store(None)


@pytest.fixture
def wrapper(monkeypatch: pytest.MonkeyPatch) -> LocalDevKeyWrapper:
    """A process keyring for this test only; the previous one is restored afterwards (other
    suites configure the process keyring once per session)."""
    w = LocalDevKeyWrapper(
        Settings(
            env=Environment.CI,
            key_wrapper=KeyWrapperKind.LOCAL_DEV,
            local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
        )
    )
    monkeypatch.setattr(crypto, "_keyring", crypto.TenantKeyring(w))
    return w


def _status(admin_engine: Engine, tenant_id: uuid.UUID, status: str) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET status = :s WHERE id = :t"), {"s": status, "t": tenant_id}
        )


def _put_files(store: Any, tenant_id: uuid.UUID, n: int = 3) -> None:
    for i in range(n):
        store.put(f"t/{tenant_id}/docs/{uuid.uuid4()}/v1/original.pdf", b"%PDF-" + bytes([i]), "x")


@pytest.fixture
def schools(admin_engine: Engine, app_engine: Engine, store: Any) -> tuple[School, School]:
    b = populate_school(admin_engine, uuid.uuid4())
    # A person who works at both schools: A's purge must leave the shared profile alone.
    a = populate_school(admin_engine, uuid.uuid4(), shared_user_id=b.shared_user_id)
    _put_files(store, a.tenant_id)
    _put_files(store, b.tenant_id)
    _status(admin_engine, a.tenant_id, "offboarding")
    return a, b


def _nonzero(counts: dict[str, int]) -> dict[str, int]:
    return {t: n for t, n in counts.items() if n}


def test_FR_PLT_005_every_tenant_table_is_owned_retained_or_a_key(
    schools: tuple[School, School], admin_engine: Engine
) -> None:
    """The purge registry covers the catalog: a new tenant table fails this test until its
    module registers it (and purge_support populates it)."""
    a, _b = schools
    owned: set[str] = set()
    with tenant_session(a.tenant_id) as s:
        for owner in offboarding.DATA_OWNERS.values():
            owned |= set(owner.count(s))
    catalog = set(row_counts(admin_engine, a.tenant_id))
    assert catalog == owned | RETAINED | {KEYS}
    assert set(offboarding.config().purge_order) == set(offboarding.DATA_OWNERS)
    # The fixture writes a row into every tenant table (the audit chain has only its head).
    assert tables_without_rows(admin_engine, a.tenant_id) == ["audit.events"]


def test_FR_PLT_005_purge_leaves_zero_rows_in_every_tenant_table(
    schools: tuple[School, School], admin_engine: Engine, store: Any
) -> None:
    a, b = schools
    before_b = row_counts(admin_engine, b.tenant_id)
    inventory = tenancy.tenant_data_inventory(a.tenant_id)
    assert inventory.objects == 3
    assert inventory.rows["students"] >= 2
    assert set(inventory.rows) == set(offboarding.config().purge_order)

    result = tenancy.purge_tenant(a.tenant_id)

    assert result.rows == inventory.rows
    assert result.objects_deleted == 3
    assert result.profiles_cleared == 2  # the two people who worked only at A
    left = _nonzero(row_counts(admin_engine, a.tenant_id))
    assert set(left) <= RETAINED | {KEYS}, left
    assert tenancy.verify_tenant_purged(a.tenant_id).rows == {}
    assert tenancy.verify_tenant_purged(a.tenant_id).objects == 0
    # The other school is untouched: every row, every file.
    assert row_counts(admin_engine, b.tenant_id) == before_b
    assert store.count_prefix(f"t/{b.tenant_id}/") == 3
    assert store.count_prefix(f"t/{a.tenant_id}/") == 0
    with admin_engine.connect() as c:
        tenant = c.execute(
            text("SELECT status, settings FROM core.tenants WHERE id = :t"), {"t": a.tenant_id}
        ).one()
        shared = c.execute(
            text("SELECT display_name, email FROM core.users WHERE id = :u"),
            {"u": b.shared_user_id},
        ).one()
        cleared = c.execute(
            text("SELECT display_name, email, phone_ciphertext FROM core.users WHERE id = :u"),
            {"u": a.user_id},
        ).one()
        event: Any = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t "
                "AND action = 'tenant.data_purged'"
            ),
            {"t": a.tenant_id},
        ).scalar_one()
    assert tuple(tenant) == ("offboarding", {})
    assert shared.display_name == "Synthetic Teacher"
    assert shared.email is not None
    assert tuple(cleared) == ("Former staff member", None, None)
    assert event["rows"] == inventory.rows
    assert event["profiles_cleared"] == 2


def test_FR_PLT_005_purge_is_idempotent(schools: tuple[School, School]) -> None:
    a, _b = schools
    tenancy.purge_tenant(a.tenant_id)
    again = tenancy.purge_tenant(a.tenant_id)
    assert sum(again.rows.values()) == 0
    assert again.objects_deleted == 0
    assert again.profiles_cleared == 0


def test_FR_PLT_005_partial_failure_deletes_nothing_and_resumes(
    schools: tuple[School, School], admin_engine: Engine, store: Any, monkeypatch: Any
) -> None:
    a, _b = schools
    before = row_counts(admin_engine, a.tenant_id)
    real = offboarding.DATA_OWNERS["documents"]

    def boom(_session: Any) -> dict[str, int]:
        raise RuntimeError("synthetic failure after earlier owners deleted their rows")

    monkeypatch.setitem(
        offboarding.DATA_OWNERS,
        "documents",
        offboarding.TenantDataOwner(name="documents", count=real.count, purge=boom),
    )
    with pytest.raises(RuntimeError):
        tenancy.purge_tenant(a.tenant_id)
    assert row_counts(admin_engine, a.tenant_id) == before  # one transaction: all or nothing
    assert store.count_prefix(f"t/{a.tenant_id}/") == 3
    with pytest.raises(Conflict) as exc:
        tenancy.destroy_tenant_keys(a.tenant_id)
    assert exc.value.code == "data_remaining"

    monkeypatch.setitem(offboarding.DATA_OWNERS, "documents", real)
    tenancy.purge_tenant(a.tenant_id)
    assert set(_nonzero(row_counts(admin_engine, a.tenant_id))) <= RETAINED | {KEYS}


def test_FR_PLT_005_missing_owner_fails_closed(
    schools: tuple[School, School], admin_engine: Engine, monkeypatch: Any
) -> None:
    a, _b = schools
    before = row_counts(admin_engine, a.tenant_id)
    monkeypatch.delitem(offboarding.DATA_OWNERS, "dq")
    with pytest.raises(Conflict) as exc:
        tenancy.purge_tenant(a.tenant_id)
    assert exc.value.code == "purge_owner_missing"
    assert row_counts(admin_engine, a.tenant_id) == before


def test_FR_PLT_005_school_that_is_not_offboarding_is_refused(
    schools: tuple[School, School], admin_engine: Engine
) -> None:
    _a, b = schools
    before = row_counts(admin_engine, b.tenant_id)
    for call in (tenancy.purge_tenant, tenancy.tenant_data_inventory, tenancy.destroy_tenant_keys):
        with pytest.raises(Conflict):
            call(b.tenant_id)
    assert row_counts(admin_engine, b.tenant_id) == before


def test_ADR_0029_database_refuses_the_purge_role_without_offboarding_and_flag(
    schools: tuple[School, School], admin_engine: Engine
) -> None:
    """Even code that switches to sos_purger deletes nothing unless the school is offboarding
    AND the transaction flag names it; and sos_app itself still cannot delete protected rows."""
    a, b = schools
    for tenant_id, flag in (
        (b.tenant_id, b.tenant_id),
        (a.tenant_id, None),
        (a.tenant_id, b.tenant_id),
    ):
        with tenant_session(tenant_id) as s:
            if flag is not None:
                s.execute(text("SELECT set_config('app.purge_tenant', :f, true)"), {"f": str(flag)})
            s.execute(text("SET LOCAL ROLE sos_purger"))
            deleted = s.execute(text("DELETE FROM sis.change_requests RETURNING id")).all()
            s.execute(text("RESET ROLE"))
        assert deleted == []
    for sql in ("DELETE FROM sis.change_requests", "DELETE FROM core.tenant_keys"):
        # Even with the flag set, sos_app itself has no DELETE on these tables.
        flagged = "SELECT set_config('app.purge_tenant', core.current_tenant()::text, true); "
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            tenant_session(a.tenant_id) as s,
        ):
            s.execute(text(flagged + sql))


def test_ADR_0029_the_purge_flag_lets_only_sos_purger_past_the_row_guards(
    schools: tuple[School, School],
) -> None:
    """Audit 2026-10-04, DL-05: ``core.tenant_purge_allowed()`` checked only the flag and the
    status, and the flag is a plain setting any role may set. sos_app holds DELETE on
    sis.students (import revert), so with the flag set it removed students of an offboarding
    school past the "only by reverting the import" guard without being sos_purger. Only the
    purge role may use the flag."""
    a, _ = schools

    def delete_lone_student_as_app() -> None:
        with tenant_session(a.tenant_id) as s:
            flag = "SELECT set_config('app.purge_tenant', core.current_tenant()::text, true)"
            s.execute(text(flag))
            assert s.execute(text("SELECT current_user")).scalar_one() == "sos_app"
            lone = uuid.uuid4()  # no enrolment or values: only the guard can stop the delete
            s.execute(
                text(
                    "INSERT INTO sis.students (id, tenant_id, status) "
                    "VALUES (:i, core.current_tenant(), 'active')"
                ),
                {"i": lone},
            )
            s.execute(text("DELETE FROM sis.students WHERE id = :i"), {"i": lone})

    with pytest.raises(DBAPIError, match="students_delete_only_by_import_revert"):
        delete_lone_student_as_app()
    with tenant_session(a.tenant_id) as s:
        s.execute(text("SELECT set_config('app.purge_tenant', core.current_tenant()::text, true)"))
        assert s.execute(text("SELECT core.tenant_purge_allowed()")).scalar_one() is False
        s.execute(text("SET LOCAL ROLE sos_purger"))
        assert s.execute(text("SELECT core.tenant_purge_allowed()")).scalar_one() is True
        s.execute(text("RESET ROLE"))


def test_ADR_0029_purge_role_privileges_are_narrow(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        role = c.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = 'sos_purger'"
            )
        ).one()
        assert tuple(role) == (False, False, False, False, False)
        # sos_app may SET ROLE but inherits nothing (INHERIT FALSE).
        assert c.execute(text("SELECT pg_has_role('sos_app', 'sos_purger', 'SET')")).scalar_one()
        assert not c.execute(
            text("SELECT pg_has_role('sos_app', 'sos_purger', 'USAGE')")
        ).scalar_one()
        for other in ("sos_platform", "sos_readonly", "sos_definer"):
            assert not c.execute(
                text("SELECT pg_has_role(:r, 'sos_purger', 'MEMBER')"), {"r": other}
            ).scalar_one()
        grants = c.execute(
            text(
                """
                SELECT n.nspname || '.' || cl.relname AS tbl,
                       has_table_privilege('sos_purger', cl.oid, 'INSERT') AS ins,
                       has_table_privilege('sos_purger', cl.oid, 'UPDATE') AS upd,
                       has_table_privilege('sos_purger', cl.oid, 'TRUNCATE') AS trn,
                       has_table_privilege('sos_purger', cl.oid, 'DELETE') AS can_delete,
                       EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = cl.oid
                               AND p.polname = 'offboarding_purge' AND NOT p.polpermissive
                               AND p.polroles = ARRAY[(SELECT oid FROM pg_roles
                                                       WHERE rolname = 'sos_purger')]) AS guarded
                FROM pg_class cl JOIN pg_namespace n ON n.oid = cl.relnamespace
                WHERE n.nspname IN ('core','sis','kb','audit','ops','platform')
                  AND cl.relkind IN ('r','p') AND NOT cl.relispartition
                """
            )
        ).all()
    for g in grants:
        assert not g.ins, g.tbl
        assert not g.upd, g.tbl
        assert not g.trn, g.tbl
        if g.can_delete:
            assert g.guarded, f"{g.tbl}: DELETE for sos_purger without the restrictive policy"
    deletable = {g.tbl for g in grants if g.can_delete}
    assert not deletable & {"core.tenants", "core.users", "core.permissions"}
    assert not any(t.startswith("platform.") for t in deletable)


def _real_keys(admin_engine: Engine, tenant_id: uuid.UUID, wrapper: LocalDevKeyWrapper) -> None:
    dek, mac = generate_tenant_keys(tenant_id, wrapper)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenant_keys SET wrapped_dek = :d, wrapped_hmac = :h, "
                "kms_key_arn = :k WHERE tenant_id = :t"
            ),
            {"d": dek, "h": mac, "k": wrapper.key_id, "t": tenant_id},
        )


def test_FR_PLT_005_keys_destroyed_and_decryption_impossible(
    schools: tuple[School, School], admin_engine: Engine, wrapper: LocalDevKeyWrapper
) -> None:
    a, _b = schools
    _real_keys(admin_engine, a.tenant_id, wrapper)
    row = uuid.uuid4()
    with tenant_session(a.tenant_id) as s:
        blob, _version = crypto.encrypt_value(
            s, "Synthetic value", table="sis.guardians", column="phone_ciphertext", row_id=row
        )
    with pytest.raises(Conflict) as exc:
        tenancy.destroy_tenant_keys(a.tenant_id)
    assert exc.value.code == "data_remaining"

    tenancy.purge_tenant(a.tenant_id)
    destroyed = tenancy.destroy_tenant_keys(a.tenant_id)

    assert (destroyed.count, destroyed.key_versions) == (1, [1])
    assert row_counts(admin_engine, a.tenant_id)[KEYS] == 0
    with pytest.raises(crypto.KeyMaterialMissing), tenant_session(a.tenant_id) as s:
        crypto.decrypt_value(s, blob, table="sis.guardians", column="phone_ciphertext", row_id=row)
    assert tenancy.destroy_tenant_keys(a.tenant_id).count == 0  # idempotent
    with admin_engine.connect() as c:
        summary: Any = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t "
                "AND action = 'tenant.keys_destroyed'"
            ),
            {"t": a.tenant_id},
        ).scalar_one()
    assert summary == {"key_versions": [1], "key_id": wrapper.key_id}


def _old_event(admin_engine: Engine, tenant_id: uuid.UUID, occurred_at: dt.datetime) -> None:
    with admin_engine.begin() as c:
        c.execute(text("SET LOCAL ROLE sos_owner"))  # partitions belong to the owner
        c.execute(text("SELECT audit.create_month_partition(:m)"), {"m": occurred_at.date()})
        c.execute(text("RESET ROLE"))
        seq: Any = c.execute(
            text("SELECT coalesce(max(seq), 0) + 1 FROM audit.events WHERE tenant_id = :t"),
            {"t": tenant_id},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO audit.events (id, tenant_id, seq, occurred_at, actor_type, action, "
                "resource_type, summary, prev_hash, hash) VALUES (:i, :t, :s, :o, 'system', "
                "'synthetic.event', 'tenant', '{}', :h, :h)"
            ),
            {
                "i": uuid.uuid4(),
                "t": tenant_id,
                "s": seq,
                "o": occurred_at,
                "h": hashlib.sha256(str(seq).encode()).digest(),
            },
        )


def test_FR_PLT_005_audit_chain_deleted_only_after_retention(
    schools: tuple[School, School], admin_engine: Engine
) -> None:
    a, b = schools
    now = dt.datetime.now(dt.UTC)
    _old_event(admin_engine, a.tenant_id, now - dt.timedelta(days=400))
    _old_event(admin_engine, b.tenant_id, now - dt.timedelta(days=400))
    tenancy.purge_tenant(a.tenant_id)  # records tenant.data_purged (a recent event)
    with pytest.raises(Conflict):  # still offboarding: the chain is not purgeable yet
        tenancy.purge_expired_audit_chain(a.tenant_id)
    _status(admin_engine, a.tenant_id, "deleted")
    with pytest.raises(DBAPIError, match="append-only"):  # a recent event is still retained
        tenancy.purge_expired_audit_chain(a.tenant_id)
    assert row_counts(admin_engine, a.tenant_id)["audit.events"] == 2

    with admin_engine.begin() as c:  # simulate the retention passing for the recent event
        c.execute(text("ALTER TABLE audit.events DISABLE TRIGGER events_append_only"))
        c.execute(
            text(
                "UPDATE audit.events SET occurred_at = :o WHERE tenant_id = :t "
                "AND action = 'tenant.data_purged'"
            ),
            {"o": now - dt.timedelta(days=400, seconds=-1), "t": a.tenant_id},
        )
        c.execute(text("ALTER TABLE audit.events ENABLE TRIGGER events_append_only"))
    assert tenancy.purge_expired_audit_chain(a.tenant_id) == 2
    counts = row_counts(admin_engine, a.tenant_id)
    assert counts["audit.events"] == 0
    assert counts["audit.chain_heads"] == 0
    assert counts["audit.chain_verifications"] == 0
    assert row_counts(admin_engine, b.tenant_id)["audit.events"] == 1
    # The owner and sos_app still cannot delete audit events at all.
    with pytest.raises(DBAPIError), admin_engine.begin() as c:
        c.execute(text("DELETE FROM audit.events WHERE tenant_id = :t"), {"t": b.tenant_id})
