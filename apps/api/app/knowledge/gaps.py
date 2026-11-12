"""Knowledge gaps: questions Ask could not answer, for the people who keep the library
(FA-06; FR-KB-081..FR-KB-095; docs/06 §8.1, docs/05 §6.6, docs/09 Knowledge gaps).

Signals (from the encrypted query log ``kb.queries``, never from the model):

- ``not_found``: the answer said "not found in school records";
- ``unchecked``: the answer came back search-only because it could not be checked against its
  sources (``status = 'search_only'`` with no error code; budget, switch-off, rate limits and
  outages are NOT gaps: the library was not the problem);
- ``not_helpful``: the asker marked the answer "not helpful".

:func:`collect` (the ``knowledge.collect_gaps`` job, per school) reads new signals, decrypts the
question, REDACTS it (:func:`gap_question`: ``core.redaction.redact`` plus every remaining digit
run masked, then cut), groups it by a tenant-keyed HMAC of the normalised redacted text and
stores one redacted sample question encrypted under the school's key. Neither the asker nor the
query id is ever shown; the marks table only stops a question being counted twice. A gap is
closed by a verified answer (written from the gap through
:meth:`KnowledgeService.create_verified_answer`, so its citations are checked exactly as any
other verified answer and it is searched under the same SQL ACL filter: invariant 8) or by the
document that was missing (the caller must be able to read it), or dismissed. Gaps are deleted
``retention_days`` after they were last seen or closed (:func:`purge`, the daily
``knowledge.purge_gaps`` job; school setting ``kb_gaps``, legal holds asked first).

Nothing here logs question text: ids, counts and codes only (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Integer,
    and_,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.core import holds, retention
from app.core.crypto import ciphertext_key_version
from app.core.errors import Conflict, Forbidden, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.redaction import redact, redact_log
from app.core.textnorm import nfc
from app.documents import service as documents
from app.identity import service as identity
from app.knowledge import repository as repo
from app.knowledge import sources
from app.knowledge.config.gaps import GapsConfig, load_gaps_config
from app.knowledge.gap_schemas import (
    KnowledgeGapCloseIn,
    KnowledgeGapMonthOut,
    KnowledgeGapOut,
    KnowledgeGapSummaryOut,
)
from app.knowledge.models import KnowledgeGap, KnowledgeGapQuery, Query, VerifiedAnswer
from app.knowledge.schemas import VerifiedAnswerOut
from app.students import crypto

log = get_logger(__name__)

MANAGE: Final = "kb.verified_answer.manage"
READ_SENSITIVE: Final = "student.read_sensitive"
TABLE: Final = "kb.knowledge_gaps"
QUESTION_COLUMN: Final = "question_ciphertext"
HMAC_PURPOSE: Final = "kb_gap"
RETENTION_CATEGORY: Final = "kb_gaps"
PURGE_JOB: Final = "knowledge.purge_gaps"
RECORD_CLASS: Final = "ask_gaps"
IST: Final = ZoneInfo("Asia/Kolkata")
SUMMARY_MONTHS: Final = 3

Signal = Literal["not_found", "unchecked", "not_helpful"]
GapStatus = Literal["open", "closed", "dismissed"]
_COUNTER: Final[dict[Signal, str]] = {
    "not_found": "not_found_count",
    "unchecked": "unchecked_count",
    "not_helpful": "not_helpful_count",
}
_WORD_SEP: Final = re.compile(r"[^\w]+", re.UNICODE)
_DIGIT_MASK: Final = "[number]"


def config() -> GapsConfig:
    return load_gaps_config()


# --- redaction and grouping (pure) ----------------------------------------------------------


def gap_question(text: str, cfg: GapsConfig | None = None) -> str:
    """The text a gap may keep: NFC, ``redact`` (Aadhaar-like numbers, mobiles, emails), the
    log redactions (codes, tokens, long numbers), then every remaining run of
    ``mask_digit_runs_from`` or more digits masked (admission, roll and receipt numbers),
    whitespace collapsed and cut to ``max_question_chars``."""
    cfg = cfg or config()
    out = redact_log(redact(nfc(text)))
    out = re.sub(rf"\d{{{cfg.mask_digit_runs_from},}}", _DIGIT_MASK, out)
    out = " ".join(out.split())
    if len(out) > cfg.max_question_chars:
        out = out[: cfg.max_question_chars - 1].rstrip() + "…"
    return out


def gap_key(redacted: str) -> str:
    """Grouping input: casefolded words of the REDACTED question, punctuation dropped."""
    folded = unicodedata.normalize("NFC", redacted).casefold()
    return " ".join(w for w in _WORD_SEP.split(folded) if w)


# --- collection (knowledge.collect_gaps) ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CollectResult:
    questions: int = 0
    opened: int = 0
    unreadable: int = 0


def retention_days(session: Session, cfg: GapsConfig | None = None) -> int:
    cfg = cfg or config()
    return retention.days(session, RETENTION_CATEGORY, default=cfg.retention_days)


def _signal(row: Query) -> Signal:
    if row.status == "not_found":
        return "not_found"
    if row.status == "search_only" and row.error is None:
        return "unchecked"
    return "not_helpful"


def _candidates(session: Session, cutoff: dt.datetime, limit: int) -> list[Query]:
    q = Query
    asked_recently = q.created_at >= cutoff
    signal = or_(
        and_(q.status == "not_found", asked_recently),
        and_(q.status == "search_only", q.error.is_(None), asked_recently),
        and_(q.feedback == "not_helpful", q.feedback_at.is_not(None), q.feedback_at >= cutoff),
    )
    counted = exists(
        select(KnowledgeGapQuery.query_id).where(
            KnowledgeGapQuery.tenant_id == q.tenant_id, KnowledgeGapQuery.query_id == q.id
        )
    )
    return list(
        session.execute(
            select(q)
            .where(signal, ~counted, or_(q.route.is_(None), q.route != "memory"))
            .order_by(q.created_at, q.id)
            .limit(limit)
        ).scalars()
    )


def _open_gap(session: Session, digest: bytes) -> KnowledgeGap | None:
    return session.execute(
        select(KnowledgeGap)
        .where(KnowledgeGap.question_hmac == digest, KnowledgeGap.status == "open")
        .with_for_update()
    ).scalar_one_or_none()


def collect(
    session: Session, *, now: dt.datetime | None = None, cfg: GapsConfig | None = None
) -> CollectResult:
    """Count this school's new signals into gaps (inside its ``tenant_session``). Idempotent:
    a question is counted once (``kb.knowledge_gap_queries``); one that cannot be decrypted is
    skipped and logged by id only (tried again on the next run while it is in the window)."""
    cfg = cfg or config()
    moment = now or dt.datetime.now(dt.UTC)
    keep_hours = retention_days(session, cfg) * 24
    cutoff = moment - dt.timedelta(hours=min(cfg.lookback_hours, keep_hours))
    tenant_id = repo.current_tenant_id(session)
    questions = opened = unreadable = 0
    for row in _candidates(session, cutoff, cfg.batch_size):
        try:
            plain = crypto.decrypt_value(
                session,
                bytes(row.question_ciphertext),
                table="kb.queries",
                column="question_ciphertext",
                row_id=row.id,
            )
        except crypto.CryptoError:
            unreadable += 1
            log.warning("kb.gap.unreadable", resource_type="kb_query", resource_id=row.id)
            continue
        redacted = gap_question(plain, cfg)
        key = gap_key(redacted)
        if not key:
            continue
        signal = _signal(row)
        seen = (
            row.feedback_at
            if signal == "not_helpful" and row.feedback_at is not None
            else row.created_at
        )
        digest, _ = crypto.blind_index(session, key, purpose=HMAC_PURPOSE)
        gap = _open_gap(session, digest)
        counter = _COUNTER[signal]
        if gap is None:
            gap_id = new_id()
            blob, version = crypto.encrypt_value(
                session, redacted, table=TABLE, column=QUESTION_COLUMN, row_id=gap_id
            )
            session.execute(
                insert(KnowledgeGap).values(
                    id=gap_id,
                    tenant_id=tenant_id,
                    question_ciphertext=blob,
                    question_hmac=digest,
                    key_version=version,
                    language=row.language if row.language in ("en", "te") else None,
                    first_seen_at=seen,
                    last_seen_at=seen,
                    **{counter: 1},
                )
            )
            opened += 1
        else:
            gap_id = gap.id
            column = getattr(KnowledgeGap, counter)
            session.execute(
                update(KnowledgeGap)
                .where(KnowledgeGap.id == gap.id)
                .values(
                    {
                        counter: column + 1,
                        "last_seen_at": func.greatest(KnowledgeGap.last_seen_at, seen),
                        "first_seen_at": func.least(KnowledgeGap.first_seen_at, seen),
                    }
                )
            )
        session.execute(
            insert(KnowledgeGapQuery).values(
                tenant_id=tenant_id, query_id=row.id, gap_id=gap_id, signal=signal
            )
        )
        questions += 1
    return CollectResult(questions=questions, opened=opened, unreadable=unreadable)


# --- retention (knowledge.purge_gaps) ---------------------------------------------------------


def purge(session: Session, *, now: dt.datetime | None = None) -> int:
    """Delete this school's gaps last seen (open) or closed/dismissed more than the retention
    ago (school setting ``kb_gaps``, else ``gaps.yaml``). A legal hold on ``ask_gaps`` keeps
    them all (ADR-0059)."""
    if holds.check(session, job="knowledge.purge_gaps", record_class="ask_gaps").whole_class:
        return 0
    keep = retention_days(session)
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=keep)
    g = KnowledgeGap
    result = session.execute(
        delete(g).where(func.coalesce(g.closed_at, g.last_seen_at) < cutoff).returning(g.id)
    )
    return len(result.all())


# --- reading (kb.verified_answer.manage) ------------------------------------------------------


def _require(ctx: UserContext) -> None:
    if not ctx.has(MANAGE):
        raise Forbidden()


def _occurrences(g: type[KnowledgeGap] = KnowledgeGap) -> ColumnElement[int]:
    total: ColumnElement[int] = g.not_found_count + g.unchecked_count + g.not_helpful_count
    return total


def _question(session: Session, row: KnowledgeGap) -> str | None:
    try:
        return crypto.decrypt_value(
            session, bytes(row.question_ciphertext), table=TABLE, column=QUESTION_COLUMN,
            row_id=row.id,
        )  # fmt: skip
    except crypto.CryptoError:
        log.warning("kb.gap.unreadable", resource_type="kb_knowledge_gap", resource_id=row.id)
        return None


def _out(session: Session, rows: Iterable[KnowledgeGap]) -> list[KnowledgeGapOut]:
    items = list(rows)
    names = identity.member_display_names(
        session, {r.closed_by for r in items if r.closed_by is not None}
    )
    return [
        KnowledgeGapOut(
            id=r.id,
            question=_question(session, r),
            language=r.language,
            occurrences=r.not_found_count + r.unchecked_count + r.not_helpful_count,
            not_found_count=r.not_found_count,
            unchecked_count=r.unchecked_count,
            not_helpful_count=r.not_helpful_count,
            first_seen_at=r.first_seen_at,
            last_seen_at=r.last_seen_at,
            status=r.status,
            resolution=r.resolution,
            verified_answer_id=r.verified_answer_id,
            document_id=r.document_id,
            closed_by=r.closed_by,
            closed_by_name=names.get(r.closed_by) if r.closed_by is not None else None,
            closed_at=r.closed_at,
            version=r.version,
        )
        for r in items
    ]


def list_gaps(
    session: Session, ctx: UserContext, *, status: GapStatus, limit: int, offset: int
) -> tuple[list[KnowledgeGapOut], bool]:
    """Gaps of this school (``kb.verified_answer.manage``): open ones most asked first, closed
    and dismissed ones most recent first. Audited ``kb.gaps.viewed`` (count only): other
    people's (redacted) questions are shown (FR-KB-086)."""
    _require(ctx)
    g = KnowledgeGap
    order = (
        (_occurrences().desc(), g.last_seen_at.desc(), g.id.desc())
        if status == "open"
        else (g.closed_at.desc(), g.id.desc())
    )
    rows = list(
        session.execute(
            select(g).where(g.status == status).order_by(*order).offset(offset).limit(limit + 1)
        ).scalars()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    audit.record(
        session,
        action="kb.gaps.viewed",
        resource_type="kb_knowledge_gap",
        summary={"status": status, "count": len(rows), "offset": offset},
        request_id=ctx.request_id,
    )
    return _out(session, rows), more


def _month_start(moment: dt.datetime, back: int) -> dt.datetime:
    local = moment.astimezone(IST)
    year, month = local.year, local.month - back
    while month < 1:
        month += 12
        year -= 1
    return dt.datetime(year, month, 1, tzinfo=IST)


def summary(
    session: Session, ctx: UserContext, *, now: dt.datetime | None = None
) -> KnowledgeGapSummaryOut:
    """Counts only (FR-KB-090): open gaps and how often they were asked, by signal; gaps
    closed (by kind) and dismissed per IST month for this month and the two before."""
    _require(ctx)
    g = KnowledgeGap
    moment = now or dt.datetime.now(dt.UTC)
    open_row = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(g.not_found_count), 0),
            func.coalesce(func.sum(g.unchecked_count), 0),
            func.coalesce(func.sum(g.not_helpful_count), 0),
        ).where(g.status == "open")
    ).one()
    months: list[KnowledgeGapMonthOut] = []
    for back in range(SUMMARY_MONTHS):
        start = _month_start(moment, back)
        end = _month_start(moment, back - 1) if back > 0 else _next_month(start)
        counts = dict(
            session.execute(
                select(func.coalesce(g.resolution, g.status), func.count().cast(Integer))
                .where(g.status != "open", g.closed_at >= start, g.closed_at < end)
                .group_by(func.coalesce(g.resolution, g.status))
            )
            .tuples()
            .all()
        )
        months.append(
            KnowledgeGapMonthOut(
                month=start.strftime("%Y-%m"),
                closed_with_answer=int(counts.get("verified_answer", 0)),
                closed_with_document=int(counts.get("document", 0)),
                dismissed=int(counts.get("dismissed", 0)),
            )
        )
    return KnowledgeGapSummaryOut(
        open_gaps=int(open_row[0]),
        not_found=int(open_row[1]),
        unchecked=int(open_row[2]),
        not_helpful=int(open_row[3]),
        months=months,
        retention_days=retention_days(session),
    )


def _next_month(start: dt.datetime) -> dt.datetime:
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return dt.datetime(year, month, 1, tzinfo=IST)


# --- closing ------------------------------------------------------------------------------


def _locked(
    session: Session, ctx: UserContext, gap_id: uuid.UUID, expected_version: int
) -> KnowledgeGap:
    _require(ctx)
    row = session.execute(
        select(KnowledgeGap).where(KnowledgeGap.id == gap_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Knowledge gap not found")
    if row.version != expected_version:
        raise PreconditionFailed()
    if row.status != "open":
        raise Conflict("This gap is already closed.", code="knowledge_gap_closed")
    return row


def _error(field: str, code: str) -> ValidationFailed:
    return ValidationFailed([{"field": field, "code": code, "message_key": f"errors.{code}"}])


def _document_readable(session: Session, ctx: UserContext, document_id: uuid.UUID) -> bool:
    """Active, within the caller's ACL/scope, and C3 only with ``student.read_sensitive``
    (the rule retrieval applies)."""
    try:
        doc = documents.get_document(session, ctx, document_id)
    except NotFound:
        return False
    return doc.status == "active" and (doc.sensitivity != "C3" or ctx.has(READ_SENSITIVE))


def _answer_usable(session: Session, ctx: UserContext, answer: VerifiedAnswer | None) -> bool:
    if answer is None or answer.status != "active":
        return False
    for citation in answer.citations:
        try:
            ref = sources.parse(str(citation.get("source", "")))
        except ValueError:
            return False
        if ref.kind != "doc" or not _document_readable(session, ctx, ref.object_id):
            return False
    return True


def _close(
    session: Session,
    ctx: UserContext,
    row: KnowledgeGap,
    *,
    status: Literal["closed", "dismissed"],
    resolution: Literal["verified_answer", "document"] | None,
    verified_answer_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> KnowledgeGapOut:
    now = dt.datetime.now(dt.UTC)
    updated = session.execute(
        update(KnowledgeGap)
        .where(KnowledgeGap.id == row.id, KnowledgeGap.version == row.version)
        .values(
            status=status,
            resolution=resolution,
            verified_answer_id=verified_answer_id,
            document_id=document_id,
            closed_by=ctx.membership_id,
            closed_at=now,
            version=KnowledgeGap.version + 1,
        )
        .returning(KnowledgeGap)
    ).scalar_one_or_none()
    if updated is None:  # pragma: no cover - the row is locked
        raise PreconditionFailed()
    audit.record(
        session,
        action="kb.gap.dismissed" if status == "dismissed" else "kb.gap.closed",
        resource_type="kb_knowledge_gap",
        resource_id=row.id,
        summary={
            "resolution": resolution or "dismissed",
            "verified_answer_id": str(verified_answer_id) if verified_answer_id else None,
            "document_id": str(document_id) if document_id else None,
            "occurrences": row.not_found_count + row.unchecked_count + row.not_helpful_count,
        },
        request_id=ctx.request_id,
    )
    return _out(session, [updated])[0]


def close_gap(
    session: Session,
    ctx: UserContext,
    gap_id: uuid.UUID,
    data: KnowledgeGapCloseIn,
    *,
    expected_version: int,
) -> KnowledgeGapOut:
    """Close a gap with an existing ACTIVE verified answer whose every cited document the
    caller can read, or with the document that was missing (one the caller can read; 422
    ``gap_answer_not_usable`` / ``gap_document_not_found`` otherwise), or dismiss it. 404
    unknown (or another school's), 412 stale ``If-Match``, 409 ``knowledge_gap_closed``."""
    row = _locked(session, ctx, gap_id, expected_version)
    if data.resolution == "dismissed":
        return _close(session, ctx, row, status="dismissed", resolution=None)
    if data.resolution == "verified_answer":
        answer = (
            repo.get_verified_answer(session, data.verified_answer_id)
            if data.verified_answer_id is not None
            else None
        )
        if not _answer_usable(session, ctx, answer):
            raise _error("verified_answer_id", "gap_answer_not_usable")
        return _close(
            session,
            ctx,
            row,
            status="closed",
            resolution="verified_answer",
            verified_answer_id=data.verified_answer_id,
        )
    if data.document_id is None or not _document_readable(session, ctx, data.document_id):
        raise _error("document_id", "gap_document_not_found")
    return _close(
        session, ctx, row, status="closed", resolution="document", document_id=data.document_id
    )


def answer_gap(
    session: Session,
    ctx: UserContext,
    gap_id: uuid.UUID,
    create: Callable[[], VerifiedAnswerOut],
    *,
    expected_version: int,
) -> KnowledgeGapOut:
    """Write a verified answer for a gap and close it, in one transaction (FR-KB-087).
    ``create`` is :meth:`KnowledgeService.create_verified_answer` for the request's body
    (the route binds it), unchanged: its citations must quote current versions of documents
    the caller can read, the free text is screened for personal numbers, and it is audited
    ``kb.verified_answer.created``. The gap is locked and checked first."""
    row = _locked(session, ctx, gap_id, expected_version)
    created = create()
    return _close(
        session,
        ctx,
        row,
        status="closed",
        resolution="verified_answer",
        verified_answer_id=created.id,
    )


# --- DEK rotation (SEC-012) ------------------------------------------------------------------


def _header_version(column: Any) -> ColumnElement[int]:
    # Header: version(1) | key_version(2, big-endian) | ... (app.core.crypto).
    high = func.get_byte(column, 1, type_=Integer)
    low = func.get_byte(column, 2, type_=Integer)
    version: ColumnElement[int] = high * 256 + low
    return version


def reencrypt_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """Re-encrypt gap questions whose ciphertext names another key version (at most
    ``limit``; same associated data). The grouping HMAC is left as it is: a rotation that
    replaces the HMAC key only starts new groups for questions asked afterwards."""
    del target_version
    active = keyring.active_version(session)
    rows = session.execute(
        select(KnowledgeGap.id, KnowledgeGap.question_ciphertext)
        .where(_header_version(KnowledgeGap.question_ciphertext) != active)
        .order_by(KnowledgeGap.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for row_id, blob in rows:
        if ciphertext_key_version(bytes(blob)) == active:  # pragma: no cover - filtered above
            continue
        plain = crypto.decrypt_value(
            session, bytes(blob), table=TABLE, column=QUESTION_COLUMN, row_id=row_id,
            keyring=keyring,
        )  # fmt: skip
        again, _ = crypto.encrypt_value(
            session, plain, table=TABLE, column=QUESTION_COLUMN, row_id=row_id, keyring=keyring
        )
        # Re-encryption is not a change: the ETag version stays as it is.
        session.execute(
            update(KnowledgeGap)
            .where(KnowledgeGap.id == row_id)
            .values(question_ciphertext=again, key_version=active)
        )
    return len(rows)


__all__ = [
    "MANAGE",
    "PURGE_JOB",
    "RETENTION_CATEGORY",
    "CollectResult",
    "answer_gap",
    "close_gap",
    "collect",
    "gap_key",
    "gap_question",
    "list_gaps",
    "purge",
    "reencrypt_batch",
    "retention_days",
    "summary",
]
