"""DEK rotation and background re-encryption of C3 values (SEC-012; docs/05 §9, 07 §8).

A school's key moves through three steps, each in the school's own ``tenant_session`` as
``sos_app`` (RLS applies; no definer function, no cross-school query):

1. :func:`rotate` adds the next key version (``tenancy.create_key_version``; KMS-wrapped per
   settings), which becomes current for new writes, and puts ``keys.rotated`` in the outbox so a
   worker starts re-encryption. Older versions stay available for decryption.
2. :func:`run_reencryption` (Celery ``maintenance.reencrypt_tenant``) re-encrypts every stored
   ciphertext whose header names an older key version to the current one, in batches of
   :data:`DEFAULT_BATCH_SIZE` rows. Each batch is one transaction: rows are locked
   (``FOR UPDATE``), decrypted and encrypted again with the same associated data, written, audited
   (``tenant.key.reencrypted``, counts only) and the ``ops.job_runs`` progress updated. A crash
   loses at most the open batch; a rerun picks up whatever still uses an older version, so the job
   is idempotent and resumable. Guardian phone blind indexes are recomputed with the target
   version's HMAC key.
3. :func:`retire_unreferenced` retires older versions that :func:`census` shows no ciphertext
   still uses (``tenancy.retire_key_version``; also waits out the key cache). Wrapped keys are
   never deleted here: destroying key material is an offboarding (crypto-shredding) action.

Which ciphertext is where: :data:`CIPHERTEXT_COLUMNS` lists every column holding tenant-DEK
ciphertext (pinned against the catalog by ``tests/students/test_key_rotation.py``). Re-encryption
for the columns of ``sis`` is built in; other modules add theirs with :func:`register_reencryptor`
(``kb.queries`` belongs to ``knowledge``). The census covers every listed column either way, so a
version is never retired while an unregistered column still uses it.

Logs and audit carry IDs, key versions and counts only: never keys, plaintext or ciphertext.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import (
    ColumnElement,
    Engine,
    Integer,
    LargeBinary,
    and_,
    func,
    or_,
    select,
    update,
)
from sqlalchemy import column as sql_column
from sqlalchemy import table as sql_table
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import ColumnClause, TableClause

from app.audit import service as audit
from app.core.crypto import KeyWrapper, ciphertext_key_version
from app.core.db import tenant_session
from app.core.errors import Conflict
from app.core.logging import get_logger
from app.ops import service as ops
from app.students import crypto
from app.students.service import PHONE_INDEX_PURPOSE
from app.tenancy import service as tenancy

__all__ = [
    "CIPHERTEXT_COLUMNS",
    "DEFAULT_BATCH_SIZE",
    "REENCRYPT_TASK",
    "ROTATED_EVENT",
    "BatchResult",
    "ReencryptionRun",
    "Reencryptor",
    "RotationError",
    "census",
    "references",
    "register_reencryptor",
    "retire_unreferenced",
    "rotate",
    "run_reencryption",
]

log = get_logger(__name__)

ROTATED_EVENT: Final = "keys.rotated"
CONTINUE_EVENT: Final = "keys.reencrypt_requested"
REENCRYPT_TASK: Final = "maintenance.reencrypt_tenant"
DEFAULT_BATCH_SIZE: Final = 200
MAX_BATCH_SIZE: Final = 1000
JOB_KEY_PREFIX: Final = "dek-reencrypt-v"
_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

# (table, column) of every tenant-DEK ciphertext column. The AAD table name is the table itself.
CIPHERTEXT_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("sis.attribute_values", "value_ciphertext"),
    ("sis.guardians", "phone_ciphertext"),
    ("sis.guardians", "address_ciphertext"),
    ("sis.change_requests", "new_value_ciphertext"),
    ("sis.change_requests", "old_value_ciphertext"),
    ("sis.import_cell_edits", "old_value_ciphertext"),
    ("sis.import_cell_edits", "new_value_ciphertext"),
    ("kb.queries", "question_ciphertext"),
    ("kb.queries", "answer_ciphertext"),
    # 0038_ask_conversations (ADR-0034): answer details, conversations and memory items.
    ("kb.queries", "citations_ciphertext"),
    ("kb.queries", "followups_ciphertext"),
    ("kb.conversations", "title_ciphertext"),
    ("kb.conversations", "summary_ciphertext"),
    ("kb.user_memories", "text_ciphertext"),
    ("sis.behaviour_notes", "body_ciphertext"),
    ("sis.flag_actions", "note_ciphertext"),
)

# Ciphertext-looking columns that are NOT under a tenant DEK, with the reason.
NOT_TENANT_DEK: Final[Mapping[tuple[str, str], str]] = {
    ("core.users", "phone_ciphertext"): "global user row (no tenant); nothing writes it yet",
    # M6 (ADR-0032 §2): Tally edge-agent HMAC secrets are wrapped by the key wrapper (KMS,
    # encryption context bound to the school), like the fleet heartbeat keys, not by a tenant
    # DEK; the agent rotates them itself every 90 days and offboarding deletes them.
    ("ops.tally_devices", "key_ciphertext"): "KMS key wrapper bound to the school (ADR-0032)",
    ("ops.tally_devices", "next_key_ciphertext"): "KMS key wrapper bound to the school (ADR-0032)",
}


class RotationError(Exception):
    """Re-encryption met a row it cannot handle (reason code only; no values)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Reencryptor = Callable[[Session, crypto.TenantKeyring, int, int], int]
"""``fn(session, keyring, target_version, limit) -> rows re-encrypted`` (at most ``limit``).

Runs inside the school's ``tenant_session``; must lock the rows it rewrites, re-encrypt only
ciphertext whose header version differs from ``target_version`` and keep the associated data.
"""


def _table(name: str, *columns: str) -> TableClause:
    """A lightweight table construct (``id``, ``key_version`` and the given bytea columns) for a
    constant ``schema.table`` name: SQLAlchemy Core quotes it, no SQL is built from strings."""
    schema, _, relation = name.partition(".")
    return sql_table(
        relation,
        sql_column("id"),
        sql_column("key_version", Integer),
        *(sql_column(c, LargeBinary) for c in columns),
        schema=schema,
    )


def _key_version(value: ColumnClause[Any]) -> ColumnElement[int]:
    # Header: version(1) | key_version(2, big-endian) | ... (app.core.crypto).
    return func.get_byte(value, 1, type_=Integer) * 256 + func.get_byte(value, 2, type_=Integer)


def _stale(value: ColumnClause[Any], version: int) -> ColumnElement[bool]:
    return and_(value.is_not(None), _key_version(value) != version)


def _attribute_values(
    session: Session, ring: crypto.TenantKeyring, version: int, limit: int
) -> int:
    name, col = "sis.attribute_values", "value_ciphertext"
    t = _table(name, col, "value_blind_index")
    rows = session.execute(
        select(t.c.id, t.c.value_ciphertext, t.c.value_blind_index)
        .where(_stale(t.c.value_ciphertext, version))
        .order_by(t.c.id)
        .limit(limit)
        .with_for_update()
    ).all()
    for row in rows:
        if row.value_blind_index is not None:
            # No attribute has a blind index today; its purpose would be unknown here.
            raise RotationError("attribute_blind_index_unsupported")
        blob = crypto.reencrypt_value(
            session,
            bytes(row.value_ciphertext),
            table=name,
            column=col,
            row_id=row.id,
            key_version=version,
            keyring=ring,
        )
        session.execute(
            update(t).where(t.c.id == row.id).values(value_ciphertext=blob, key_version=version)
        )
    return len(rows)


def _guardians(session: Session, ring: crypto.TenantKeyring, version: int, limit: int) -> int:
    table = "sis.guardians"
    t = _table(table, "phone_ciphertext", "address_ciphertext", "phone_blind_index")
    rows = session.execute(
        select(t.c.id, t.c.phone_ciphertext, t.c.address_ciphertext)
        .where(or_(_stale(t.c.phone_ciphertext, version), _stale(t.c.address_ciphertext, version)))
        .order_by(t.c.id)
        .limit(limit)
        .with_for_update()
    ).all()
    for row in rows:
        values: dict[str, Any] = {"key_version": version}
        phone = bytes(row.phone_ciphertext) if row.phone_ciphertext is not None else None
        if phone is not None and ciphertext_key_version(phone) != version:
            plain = crypto.decrypt_value(
                session, phone, table=table, column="phone_ciphertext", row_id=row.id, keyring=ring
            )
            digest, _ = crypto.blind_index(
                session, plain, purpose=PHONE_INDEX_PURPOSE, key_version=version, keyring=ring
            )
            del plain
            values["phone_ciphertext"] = crypto.reencrypt_value(
                session,
                phone,
                table=table,
                column="phone_ciphertext",
                row_id=row.id,
                key_version=version,
                keyring=ring,
            )
            values["phone_blind_index"] = digest
        address = bytes(row.address_ciphertext) if row.address_ciphertext is not None else None
        if address is not None and ciphertext_key_version(address) != version:
            values["address_ciphertext"] = crypto.reencrypt_value(
                session,
                address,
                table=table,
                column="address_ciphertext",
                row_id=row.id,
                key_version=version,
                keyring=ring,
            )
        # Re-encryption is not an edit: updated_at moves (trigger) but the ETag version does not.
        session.execute(update(t).where(t.c.id == row.id).values(values))
    return len(rows)


def _change_requests(session: Session, ring: crypto.TenantKeyring, version: int, limit: int) -> int:
    table = "sis.change_requests"
    t = _table(table, "new_value_ciphertext", "old_value_ciphertext")
    rows = session.execute(
        select(t.c.id, t.c.new_value_ciphertext, t.c.old_value_ciphertext)
        .where(
            or_(
                _stale(t.c.new_value_ciphertext, version),
                _stale(t.c.old_value_ciphertext, version),
            )
        )
        .order_by(t.c.id)
        .limit(limit)
        .with_for_update()
    ).all()
    for row in rows:
        values: dict[str, Any] = {"key_version": version}
        for col in ("new_value_ciphertext", "old_value_ciphertext"):
            raw = getattr(row, col)
            if raw is None or ciphertext_key_version(bytes(raw)) == version:
                continue
            values[col] = crypto.reencrypt_value(
                session,
                bytes(raw),
                table=table,
                column=col,
                row_id=row.id,
                key_version=version,
                keyring=ring,
            )
        session.execute(update(t).where(t.c.id == row.id).values(values))
    return len(rows)


# name (audit/progress key) -> re-encryptor. Order = processing order.
_REENCRYPTORS: dict[str, Reencryptor] = {
    "attribute_values": _attribute_values,
    "guardians": _guardians,
    "change_requests": _change_requests,
}


def register_reencryptor(name: str, fn: Reencryptor) -> None:
    """Add re-encryption for another module's ciphertext (e.g. ``knowledge`` for ``kb.queries``).

    ``name`` is the audit/progress key (``[a-z][a-z0-9_]*``, no personal-data words). Register at
    import time of the owning module's ``service``; the worker imports it via TASK_MODULES.
    """
    if not _NAME_RE.match(name):
        raise ValueError("re-encryptor names are lowercase identifiers")
    _REENCRYPTORS[name] = fn


def reencryptor_names() -> tuple[str, ...]:
    return tuple(_REENCRYPTORS)


# --- census -----------------------------------------------------------------------------------


def census(session: Session) -> dict[int, int]:
    """``{key_version: stored ciphertexts using it}`` for the current school (all listed columns).

    Reads only the 3-byte header of each value (never decrypts). RLS limits it to the school.
    """
    counts: dict[int, int] = {}
    for name, col in CIPHERTEXT_COLUMNS:
        value = _table(name, col).c[col]
        version = _key_version(value).label("v")
        rows = session.execute(
            select(version, func.count().label("n")).where(value.is_not(None)).group_by(version)
        ).all()
        for row in rows:
            counts[int(row.v)] = counts.get(int(row.v), 0) + int(row.n)
    return counts


def references(session: Session, key_version: int) -> int:
    """Stored ciphertexts still using ``key_version`` (registered with tenancy for retiring)."""
    return census(session).get(key_version, 0)


def _stale_count(session: Session, current: int) -> int:
    return sum(n for v, n in census(session).items() if v != current)


tenancy.KEY_REFERENCE_COUNTERS.append(references)


# --- rotation ---------------------------------------------------------------------------------


def rotate(
    tenant_id: uuid.UUID,
    *,
    wrapper: KeyWrapper,
    new_hmac_key: bool = False,
    engine: Engine | None = None,
    keyring: crypto.TenantKeyring | None = None,
) -> int:
    """Add the school's next key version and queue its re-encryption; return the new version.

    One transaction: the key row, audit ``tenant.key.rotated`` and the outbox event
    ``keys.rotated`` (payload: key_version). This process's key cache is refreshed at once;
    other processes pick the new version up within the cache TTL.
    """
    with tenant_session(tenant_id, engine=engine) as session:
        created = tenancy.create_key_version(
            session, tenant_id, wrapper=wrapper, new_hmac_key=new_hmac_key
        )
        ops.enqueue_event(session, ROTATED_EVENT, {"key_version": created.key_version})
    (keyring or crypto.get_keyring()).forget(tenant_id)
    log.info("keys.rotated", tenant_id=str(tenant_id), key_version=created.key_version)
    return created.key_version


@dataclass(frozen=True, slots=True)
class BatchResult:
    key_version: int
    rows: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.rows.values())


def reencrypt_batch(
    session: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    keyring: crypto.TenantKeyring | None = None,
) -> BatchResult:
    """Re-encrypt up to ``batch_size`` rows of the current school to its current key version.

    Runs in the caller's ``tenant_session`` and audits ``tenant.key.reencrypted`` (key_version and
    rows per table) in the same transaction when anything changed.
    """
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    ring = keyring or crypto.get_keyring()
    version = tenancy.current_key_version(session)
    done: dict[str, int] = {}
    left = batch_size
    for name, fn in _REENCRYPTORS.items():
        if left <= 0:
            break
        count = fn(session, ring, version, left)
        if count:
            done[name] = count
            left -= count
    result = BatchResult(version, done)
    if result.total:
        audit.record(
            session,
            action="tenant.key.reencrypted",
            resource_type="tenant",
            resource_id=tenancy.get_tenant(session).id,
            summary={"key_version": version, "rows": result.rows, "total": result.total},
            actor_type="system",
        )
    return result


@dataclass(slots=True)
class ReencryptionRun:
    tenant_id: uuid.UUID
    key_version: int
    job_id: uuid.UUID | None = None
    batches: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    remaining: int = 0
    done: bool = False

    @property
    def total(self) -> int:
        return sum(self.rows.values())

    def progress(self) -> dict[str, Any]:
        return {
            "key_version": self.key_version,
            "batches": self.batches,
            "rows": dict(self.rows),
            "total": self.total,
            "remaining": self.remaining,
        }


def run_reencryption(
    tenant_id: uuid.UUID,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
    engine: Engine | None = None,
    keyring: crypto.TenantKeyring | None = None,
    continue_later: bool = False,
) -> ReencryptionRun:
    """Re-encrypt the school's older-version ciphertext in batches (idempotent, resumable).

    Progress is kept in ``ops.job_runs`` (task ``maintenance.reencrypt_tenant``, idempotency key
    ``dek-reencrypt-v<version>``): each batch commits its rows, audit event and progress together.
    Stops when nothing older is left (job ``succeeded``) or after ``max_batches``; with
    ``continue_later`` the last batch also queues ``keys.reencrypt_requested`` so a worker goes on.
    A failure marks the job ``failed`` (error type only) and re-raises; completed batches stay.
    """
    ring = keyring or crypto.get_keyring()
    ring.forget(tenant_id)  # read the current version from the database, not a stale cache
    with tenant_session(tenant_id, engine=engine) as session:
        status = tenancy.get_tenant(session).status
        if status not in tenancy.ROTATABLE_STATUSES:
            raise Conflict("Only an active or suspended school is re-encrypted.", code="key_state")
        version = tenancy.current_key_version(session)
        job = ops.start_job(
            session, task_name=REENCRYPT_TASK, idempotency_key=f"{JOB_KEY_PREFIX}{version}"
        )
        run = ReencryptionRun(tenant_id, version, job.id, remaining=_stale_count(session, version))
        ops.record_job_progress(session, job.id, run.progress())
    try:
        while not run.done:
            if max_batches is not None and run.batches >= max_batches:
                break
            with tenant_session(tenant_id, engine=engine) as session:
                result = reencrypt_batch(session, batch_size=batch_size, keyring=ring)
                if result.key_version != version:
                    # Rotated again meanwhile: this job ends; the new rotation's job continues.
                    run.done = True
                    ops.finish_job(session, job.id, run.progress())
                    break
                run.batches += 1
                for name, count in result.rows.items():
                    run.rows[name] = run.rows.get(name, 0) + count
                run.remaining = _stale_count(session, version)
                run.done = result.total == 0 or run.remaining == 0
                if run.done:
                    ops.finish_job(session, job.id, run.progress())
                else:
                    ops.record_job_progress(session, job.id, run.progress())
                    if continue_later and max_batches is not None and run.batches >= max_batches:
                        ops.enqueue_event(session, CONTINUE_EVENT, {"key_version": version})
            log.info(
                "keys.reencrypt.batch",
                tenant_id=str(tenant_id),
                key_version=version,
                count=result.total,
                remaining=run.remaining,
            )
    except Exception as exc:
        with tenant_session(tenant_id, engine=engine) as session:
            ops.fail_job(session, job.id, type(exc).__name__)
        log.warning(
            "keys.reencrypt.failed",
            tenant_id=str(tenant_id),
            key_version=version,
            error_type=type(exc).__name__,
        )
        raise
    log.info(
        "keys.reencrypt.done" if run.done else "keys.reencrypt.paused",
        tenant_id=str(tenant_id),
        key_version=version,
        count=run.total,
        remaining=run.remaining,
    )
    return run


# --- retirement -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetireResult:
    retired: tuple[int, ...]
    in_use: dict[int, int]
    blocked: dict[int, str]


def retire_unreferenced(
    tenant_id: uuid.UUID, *, apply: bool, engine: Engine | None = None
) -> RetireResult:
    """Retire every older, unretired key version that no stored ciphertext uses.

    Dry run (``apply=False``) only reports. Each retirement goes through
    ``tenancy.retire_key_version`` (census re-checked under the key lock, cache window, audit).
    """
    retired: list[int] = []
    in_use: dict[int, int] = {}
    blocked: dict[int, str] = {}
    with tenant_session(tenant_id, engine=engine) as session:
        counts = census(session)
        versions = tenancy.list_key_versions(session)
        for v in versions:
            if v.current or v.retired_at is not None:
                continue
            if counts.get(v.key_version, 0):
                in_use[v.key_version] = counts[v.key_version]
                continue
            if not apply:
                retired.append(v.key_version)
                continue
            try:
                with session.begin_nested():
                    tenancy.retire_key_version(session, v.key_version)
            except Conflict as exc:
                blocked[v.key_version] = exc.code
                continue
            retired.append(v.key_version)
    return RetireResult(tuple(retired), in_use, blocked)
