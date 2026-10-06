"""Stored audit-chain verification with a checkpoint (audit 2026-10-06 R-19; SEC-007, FR-AUD-004).

``GET /audit/verify`` used to re-hash a school's whole chain on every call, without limit (OWASP
API4, CWE-400). Now every verification run stores its result in ``audit.chain_verifications``
(one row per school) and the API serves that row:

- the **daily job** (``audit.verify_all_chains``) always re-hashes the whole chain (``full``);
- an **on-demand run** (``POST /audit/verify``, queued through the outbox, 1 per school per 10
  minutes: ``audit_verify`` in app/core/rate_limits.yaml) verifies only the events after the
  stored checkpoint (``incremental``), or the whole chain when asked (``full``) or when there is
  no checkpoint yet.

The checkpoint is the seq and hash of the last event of a run that found the chain intact. An
incremental run first re-checks the checkpoint event itself (exactly one row at that seq, its
stored hash equals the checkpoint hash, and that hash recomputes from its content), then walks
the later events from the checkpoint hash and compares the head. So a break after the
checkpoint, or a changed checkpoint event, is detected by every run; a change *before* the
checkpoint (which needs database-owner rights: events are append-only for the app roles,
migrations 0002 and 0046) is detected by the next full run, the daily job, and the signed
archive. A run that finds a break stores it and never moves the checkpoint.

The row holds IDs, counts, codes, times and a hash only (no personal data).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.audit import repository, service
from app.audit.hashing import chain_hash, tenant_event_dict
from app.audit.schemas import VerifyResult
from app.core import ratelimit
from app.core.db import tenant_session
from app.ops import service as ops

VERIFY_EVENT = "audit.verify_requested"
VERIFY_TASK = "audit.verify_chain"
COOL_DOWN_POLICY = "audit_verify"

Mode = Literal["full", "incremental"]
Source = Literal["daily", "on_demand"]


class AuditVerificationOut(BaseModel):
    """The school's latest stored verification (``GET``/``POST /audit/verify``)."""

    model_config = ConfigDict(frozen=True)

    ok: bool | None
    """``null`` until the first run has finished."""
    checked: int
    """Events re-hashed by that run (all of them for ``full``, the new ones for ``incremental``)."""
    first_bad_seq: int | None
    reason: str | None
    verified_at: dt.datetime | None
    mode: Mode | None
    source: Source | None
    checkpoint_seq: int
    """The last event verified intact (0: none yet)."""
    checkpoint_at: dt.datetime | None
    last_full_at: dt.datetime | None
    pending: bool
    """An on-demand run is queued or running."""
    requested_at: dt.datetime | None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _cool_down() -> dt.timedelta:
    """A queued run older than the per-school cool-down is queued again (a lost job)."""
    policy = ratelimit.load_config().policies[COOL_DOWN_POLICY]
    return dt.timedelta(seconds=policy.window_s)


def _out(row: Mapping[Any, Any] | None) -> AuditVerificationOut:
    if row is None:
        return AuditVerificationOut(
            ok=None,
            checked=0,
            first_bad_seq=None,
            reason=None,
            verified_at=None,
            mode=None,
            source=None,
            checkpoint_seq=0,
            checkpoint_at=None,
            last_full_at=None,
            pending=False,
            requested_at=None,
        )
    return AuditVerificationOut(
        ok=row["ok"],
        checked=int(row["checked"]),
        first_bad_seq=row["first_bad_seq"],
        reason=row["reason"],
        verified_at=row["verified_at"],
        mode=row["mode"],
        source=row["source"],
        checkpoint_seq=int(row["checkpoint_seq"]),
        checkpoint_at=row["checkpoint_at"],
        last_full_at=row["last_full_at"],
        pending=row["requested_at"] is not None,
        requested_at=row["requested_at"],
    )


def latest(session: Session, tenant_id: uuid.UUID) -> AuditVerificationOut:
    """The stored result (no hashing). Run inside ``tenant_session(tenant_id)``."""
    return _out(repository.get_verification(session, tenant_id))


def _verify_from_checkpoint(
    session: Session, tenant_id: uuid.UUID, cp_seq: int, cp_hash: bytes
) -> VerifyResult:
    rows = repository.events_at(session, tenant_id, cp_seq)
    if len(rows) != 1:
        return VerifyResult(False, 0, cp_seq, "checkpoint_mismatch")
    event = rows[0]
    if (
        bytes(event["hash"]) != cp_hash
        or chain_hash(bytes(event["prev_hash"]), tenant_event_dict(event)) != cp_hash
    ):
        return VerifyResult(False, 0, cp_seq, "checkpoint_mismatch")
    head = repository.get_head(session, tenant_id)
    later = repository.iter_events_after(session, tenant_id, cp_seq)
    return service.verify_events(
        later, head, tenant_event_dict, start_seq=cp_seq + 1, start_prev_hash=cp_hash
    )


def run(
    session: Session, tenant_id: uuid.UUID, *, full: bool, source: Source
) -> AuditVerificationOut:
    """Verify (``full`` or from the checkpoint) and store the result. Run inside
    ``tenant_session(tenant_id)``; the caller's transaction commits it."""
    if repository.current_tenant(session) != tenant_id:
        raise service.AuditContextError("verification.run must run inside tenant_session")
    row = repository.get_verification(session, tenant_id, for_update=True)
    cp_seq = int(row["checkpoint_seq"]) if row is not None else 0
    cp_hash = bytes(row["checkpoint_hash"]) if row is not None and row["checkpoint_hash"] else None
    incremental = not full and cp_seq > 0 and cp_hash is not None
    if not full and cp_seq > 0 and cp_hash is not None:
        result = _verify_from_checkpoint(session, tenant_id, cp_seq, cp_hash)
    else:
        result = service.verify_chain(session, tenant_id)
    at = _now()
    values: dict[str, Any] = {
        "verified_at": at,
        "mode": "incremental" if incremental else "full",
        "source": source,
        "ok": result.ok,
        "checked": result.checked,
        "first_bad_seq": result.first_bad_seq,
        "reason": result.reason,
    }
    if not incremental:
        values["last_full_at"] = at
    if result.ok:
        head = repository.get_head(session, tenant_id)
        if head is not None and head[0] > 0 and head[0] != cp_seq:
            values.update(checkpoint_seq=head[0], checkpoint_hash=head[1], checkpoint_at=at)
    if source == "on_demand":
        values.update(requested_at=None, requested_by=None, requested_full=False)
    if row is None and result.ok and result.checked == 0 and "checkpoint_seq" not in values:
        # An empty chain (a new school, or a deleted school whose chain was purged after its
        # retention): nothing to keep.
        return _out({**_EMPTY_DEFAULTS, **values})
    repository.save_verification(session, tenant_id, values)
    return latest(session, tenant_id)


_EMPTY_DEFAULTS: dict[str, Any] = {
    "checkpoint_seq": 0,
    "checkpoint_at": None,
    "last_full_at": None,
    "requested_at": None,
}


def request(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, *, full: bool
) -> AuditVerificationOut:
    """Queue an on-demand run (outbox -> ``audit.verify_chain``). A run already queued within
    the cool-down is not queued twice; asking for ``full`` upgrades it."""
    row = repository.get_verification(session, tenant_id, for_update=True)
    at = _now()
    queued = (
        row is not None
        and row["requested_at"] is not None
        and at - row["requested_at"] < _cool_down()
    )
    values: dict[str, Any] = {
        "requested_at": row["requested_at"] if queued and row is not None else at,
        "requested_by": user_id,
        "requested_full": bool(full or (queued and row is not None and row["requested_full"])),
    }
    repository.save_verification(session, tenant_id, values)
    if not queued:
        ops.enqueue_event(session, VERIFY_EVENT, {"full": bool(full)})
    return latest(session, tenant_id)


def run_requested(tenant_id: uuid.UUID) -> AuditVerificationOut:
    """The queued on-demand run (the ``audit.verify_chain`` task), in the school's own session."""
    with tenant_session(tenant_id) as session:
        row = repository.get_verification(session, tenant_id)
        full = bool(row is not None and row["requested_full"])
        out = run(session, tenant_id, full=full, source="on_demand")
    return out


ops.register_outbox_route(VERIFY_EVENT, VERIFY_TASK)
