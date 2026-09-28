"""Verified answers as searchable sources (docs/06 §2, §4.8, §6 "verified-answer boost";
FR-KB-030, FR-KB-002, SEC-018).

:func:`search_verified_answers` finds ACTIVE ``kb.verified_answers`` whose question or answer
shares a word with the query (full text, ``simple`` configuration, any term) and that the caller
may see, IN SQL before ranking: every document the answer cites must have a searchable chunk
that passes :func:`.acl.acl_predicate` for the caller's keys (the same filter as passages, so
ACLs, scopes, ``is_latest`` and the C3 rule apply unchanged). An answer citing anything else
(a record, an unparsable source) never matches. ``needs_review`` and ``retired`` answers are
never returned. Tenant isolation is RLS on both tables.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import (
    Integer,
    Text,
    Uuid,
    bindparam,
    case,
    cast,
    column,
    exists,
    func,
    literal,
    not_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, REGCONFIG, TSQUERY
from sqlalchemy.orm import Session

from app.knowledge.domain import AclKeys
from app.knowledge.models import DocumentChunk, VerifiedAnswer
from app.knowledge.retrieval.acl import acl_predicate

DOC_PREFIX: Final = "sos://doc/"
_UUID_RE: Final = r"^sos://doc/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/v"
MAX_RESULTS: Final = 5


@dataclass(frozen=True, slots=True)
class VerifiedHit:
    id: uuid.UUID
    question: str
    answer_text: str
    verified_at: dt.datetime
    score: float


def search_verified_answers(
    session: Session, acl: AclKeys, query: str, *, limit: int = 2
) -> list[VerifiedHit]:
    """Best first, at most ``limit`` (<= 5); [] for an empty query."""
    text_ = query.strip()
    if not text_ or limit < 1:
        return []
    va = VerifiedAnswer
    ts_config = cast(bindparam("va_ts_config", "simple"), REGCONFIG)
    # OR of the query's lexemes (plainto_tsquery ANDs them), built by PostgreSQL.
    tsquery = cast(
        func.replace(
            cast(func.plainto_tsquery(ts_config, bindparam("va_q", text_, type_=Text)), Text),
            " & ",
            " | ",
        ),
        TSQUERY,
    )
    document = func.to_tsvector(ts_config, va.question_canonical + literal(" ") + va.answer_text)
    rank = func.ts_rank_cd(document, tsquery)
    citation = (
        func.jsonb_array_elements(va.citations).table_valued(column("value", JSONB)).alias("c")
    )
    source = citation.c.value["source"].astext
    # NULL unless the source is a document page, so the uuid cast never sees another shape.
    cited_document = case(
        (
            source.op("~")(literal(_UUID_RE)),
            cast(func.substr(source, len(DOC_PREFIX) + 1, 36), Uuid()),
        ),
        else_=None,
    )
    visible_chunk = exists(
        select(literal(1, Integer))
        .select_from(DocumentChunk)
        .where(DocumentChunk.document_id == cited_document, acl_predicate(acl))
    )
    # Hidden: any citation that is not a document page (NULL never matches), or whose
    # document has no chunk the caller may retrieve.
    hidden = exists(select(literal(1, Integer)).select_from(citation).where(not_(visible_chunk)))
    rows = session.execute(
        select(va.id, va.question_canonical, va.answer_text, va.verified_at, rank.label("score"))
        .where(va.status == "active", document.op("@@")(tsquery), not_(hidden))
        .order_by(rank.desc(), va.id)
        .limit(min(limit, MAX_RESULTS))
    ).all()
    return [
        VerifiedHit(
            id=r.id,
            question=r.question_canonical,
            answer_text=r.answer_text,
            verified_at=r.verified_at,
            score=float(r.score),
        )
        for r in rows
    ]


__all__ = ["VerifiedHit", "search_verified_answers"]
