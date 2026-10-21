"""Database access for public handles (0055_public_handles; ADR-0047).

Callers inside ``app.public`` only. Everything except :func:`resolve` runs in the school's
``core.db.tenant_session``: RLS limits each statement to that school. :func:`resolve` runs in a
``context_free_session`` and calls the one allowlisted definer function,
``core.resolve_public_handle``, which returns a school id and a handle id and nothing else.
The app role may UPDATE only the revocation and view columns and DELETE only handles that
expired more than 30 days ago (database policy), so nothing here can rewrite a handle.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.public.models import PublicHandle

# One statement for every candidate kind (a parent token does not say which kind it is), so a
# miss costs the same indexed lookups whatever the key.
_RESOLVE_ANY = text(
    "SELECT r.tenant_id, r.handle_id, k.kind "
    "FROM unnest(CAST(:kinds AS text[])) AS k(kind) "
    "CROSS JOIN LATERAL core.resolve_public_handle(k.kind, :key_hash) AS r"
)


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("public repository used outside tenant_session")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def resolve_any(
    session: Session, kinds: Collection[str], key_hash: bytes
) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """``(tenant_id, handle_id, kind)`` of the live handles with this key in any of ``kinds``
    (context-free session; at most one row per kind)."""
    rows = session.execute(_RESOLVE_ANY, {"kinds": list(kinds), "key_hash": key_hash}).all()
    return [(uuid.UUID(str(r.tenant_id)), uuid.UUID(str(r.handle_id)), str(r.kind)) for r in rows]


def insert(session: Session, **values: Any) -> PublicHandle:
    row = PublicHandle(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def get(session: Session, handle_id: uuid.UUID, *, lock: bool = False) -> PublicHandle | None:
    stmt = select(PublicHandle).where(PublicHandle.id == handle_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def open_for_guardian(
    session: Session, guardian_id: uuid.UUID, kinds: Collection[str]
) -> list[PublicHandle]:
    """The guardian's handles that are not revoked yet (expired ones included: revoking them is
    harmless and keeps the record clear)."""
    stmt = (
        select(PublicHandle)
        .where(
            PublicHandle.guardian_id == guardian_id,
            PublicHandle.revoked_at.is_(None),
            PublicHandle.kind.in_(list(kinds)),
        )
        .order_by(PublicHandle.created_at)
        .with_for_update()
    )
    return list(session.scalars(stmt))


def open_for_target(
    session: Session, target_type: str, target_id: uuid.UUID, kinds: Collection[str]
) -> list[PublicHandle]:
    stmt = (
        select(PublicHandle)
        .where(
            PublicHandle.target_type == target_type,
            PublicHandle.target_id == target_id,
            PublicHandle.revoked_at.is_(None),
            PublicHandle.kind.in_(list(kinds)),
        )
        .order_by(PublicHandle.created_at)
        .with_for_update()
    )
    return list(session.scalars(stmt))


def mark_revoked(session: Session, handle_ids: Collection[uuid.UUID], reason: str) -> int:
    if not handle_ids:
        return 0
    result = session.execute(
        update(PublicHandle)
        .where(PublicHandle.id.in_(list(handle_ids)), PublicHandle.revoked_at.is_(None))
        .values(revoked_at=func.now(), revoked_reason=reason),
        execution_options={"synchronize_session": False},
    )
    return int(getattr(result, "rowcount", 0) or 0)


def count_view(session: Session, handle_id: uuid.UUID) -> bool:
    """Count one view; ``True`` when it was the first one (``first_viewed_at`` set now)."""
    row = session.execute(
        update(PublicHandle)
        .where(PublicHandle.id == handle_id)
        .values(
            view_count=PublicHandle.view_count + 1,
            first_viewed_at=func.coalesce(PublicHandle.first_viewed_at, func.now()),
            last_viewed_at=func.now(),
        )
        .returning(PublicHandle.view_count),
        execution_options={"synchronize_session": False},
    ).first()
    return row is not None and int(row[0]) == 1


def purge_expired(session: Session, before: dt.datetime, limit: int) -> int:
    """Delete up to ``limit`` handles of the current school that expired before ``before``."""
    ids = select(PublicHandle.id).where(
        PublicHandle.expires_at.is_not(None), PublicHandle.expires_at < before
    )
    result = session.execute(
        delete(PublicHandle).where(PublicHandle.id.in_(ids.limit(limit).scalar_subquery())),
        execution_options={"synchronize_session": False},
    )
    return int(getattr(result, "rowcount", 0) or 0)


# --- data-principal report (R3-1A; FR-DPDP-009) ---------------------------------------------------


def _subject_filter(
    guardian_id: uuid.UUID | None, targets: Mapping[str, Collection[uuid.UUID]]
) -> ColumnElement[bool] | None:
    parts: list[ColumnElement[bool]] = []
    if guardian_id is not None:
        parts.append(PublicHandle.guardian_id == guardian_id)
    for target_type, ids in targets.items():
        if ids:
            parts.append(
                and_(
                    PublicHandle.target_type == target_type,
                    PublicHandle.target_id.in_(list(ids)),
                )
            )
    return or_(*parts) if parts else None


def handles_for_subject(
    session: Session,
    *,
    kinds: Collection[str],
    guardian_id: uuid.UUID | None,
    targets: Mapping[str, Collection[uuid.UUID]],
    limit: int,
) -> tuple[list[PublicHandle], int]:
    """Handles of ``kinds`` sent to ``guardian_id`` or pointing at one of ``targets``
    (target_type -> ids), newest first (at most ``limit``), and how many there are."""
    where = _subject_filter(guardian_id, targets)
    if where is None or not kinds:
        return [], 0
    cond = and_(PublicHandle.kind.in_(list(kinds)), where)
    rows = list(
        session.scalars(
            select(PublicHandle)
            .where(cond)
            .order_by(PublicHandle.created_at.desc(), PublicHandle.id.desc())
            .limit(limit)
        )
    )
    total = session.scalar(select(func.count()).select_from(PublicHandle).where(cond))
    return rows, int(total or 0)


def count_by_state(
    session: Session, kind: str, target_type: str, target_ids: Collection[uuid.UUID]
) -> tuple[int, int]:
    """(live, withdrawn) handles of ``kind`` pointing at ``target_ids``."""
    if not target_ids:
        return 0, 0
    row = session.execute(
        select(
            func.count().filter(PublicHandle.revoked_at.is_(None)),
            func.count().filter(PublicHandle.revoked_at.is_not(None)),
        ).where(
            PublicHandle.kind == kind,
            PublicHandle.target_type == target_type,
            PublicHandle.target_id.in_(list(target_ids)),
        )
    ).one()
    return int(row[0]), int(row[1])


__all__ = [
    "count_by_state",
    "count_view",
    "current_tenant_id",
    "get",
    "handles_for_subject",
    "insert",
    "mark_revoked",
    "now",
    "open_for_guardian",
    "open_for_target",
    "purge_expired",
    "resolve_any",
]
