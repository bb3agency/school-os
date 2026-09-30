"""Exact-repeat answer cache, documents only (docs/06 "Cost and performance design").

Product owner decision of 2026-09-30: an earlier checked answer is reused only when ALL hold:

1. same school (RLS) and same normalised question: the keyed ``question_hmac`` (NFC, casefold,
   whitespace collapsed; the tenant HMAC key) of the stored question;
2. same access fingerprint: SHA-256 over the asker's document-visibility keys (roles, the
   sections and classes their ``document.read`` scope reaches, school-wide, ACL manager and C3
   flags; ``tools.access.acl_keys``). Two people whose document reach differs never share;
3. a standalone question: no conversation context (earlier turns or a summary), no memory items
   in use, not a regenerate (``regenerate_of`` always bypasses the cache);
4. the earlier answer used documents only (route ``documents``, every source given to the model
   a ``sos://doc`` page or an active verified answer, which quotes document pages), was
   ``answered``, is not itself a reuse, and was not invalidated;
5. every source it was given and cited is STILL current and visible to the caller now
   (:meth:`SourceVisibility.current`: the current, active version of a document they can see;
   an active verified answer whose every cited document they can see), so a person whose own
   ACL entries (membership) differ can never receive content they cannot open;
6. it is younger than ``answer_cache.ttl_hours``;
7. AI answers are on for the school (the kill switch, ``ai_features_enabled`` and the flag): a
   school that switched AI off never gets a stored AI answer either. A used-up budget does not
   stop a reuse (it costs nothing; product owner decision 2026-09-30 to confirm).

Invalidation: a cited or retrieved document's new searchable version, ACL change, archive or
deletion sets ``cache_invalidated_at`` (the FR-KB-030 "sources changed" hook, ``lifecycle``).
A reuse is a new ``kb.queries`` row (FR-KB-009) with zero tokens and ``cached_from``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.knowledge import repository as repo
from app.knowledge import sources
from app.knowledge.config.conversations import AnswerCache
from app.knowledge.conversations import (
    StoredCitation,
    answer_of,
    stored_citations,
    stored_followups,
)
from app.knowledge.domain import AclKeys
from app.knowledge.visibility import SourceVisibility

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FINGERPRINT_VERSION: Final = 1
CANDIDATES: Final = 5


def fingerprint(keys: AclKeys | None) -> bytes | None:
    """The access fingerprint of a caller's document reach (None: cannot read documents)."""
    if keys is None:
        return None
    canonical = {
        "v": FINGERPRINT_VERSION,
        "roles": sorted(keys.roles),
        "sections": sorted(str(s) for s in keys.section_ids),
        "classes": sorted(str(c) for c in keys.class_ids),
        "school_wide": keys.school_wide,
        "sees_all": keys.sees_all,
        "read_sensitive": keys.read_sensitive,
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).digest()


@dataclass(frozen=True, slots=True)
class Hit:
    query_id: uuid.UUID
    text: str
    citations: tuple[StoredCitation, ...]
    retrieved: tuple[str, ...]
    followups: tuple[str, ...]
    language: str | None


DOCUMENT_KINDS: Final = frozenset({"doc", "verified"})
"""Document pages, and verified answers (themselves quotes of document pages, FR-KB-030)."""


def _documents_only(found: list[str]) -> bool:
    for source in found:
        try:
            if sources.parse(source).kind not in DOCUMENT_KINDS:
                return False
        except ValueError:
            return False
    return bool(found)


def lookup(
    session: Session,
    *,
    question_hmac: bytes,
    access: bytes,
    visibility: SourceVisibility,
    cfg: AnswerCache,
    now: dt.datetime | None = None,
) -> Hit | None:
    """The newest reusable answer, or None (see the module docstring for every rule)."""
    if not cfg.enabled:
        return None
    since = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(hours=cfg.ttl_hours)
    for row in repo.cache_candidates(
        session, question_hmac=question_hmac, fingerprint=access, since=since, limit=CANDIDATES
    ):
        retrieved = [str(r.get("source", "")) for r in row.retrieved or []]
        cited = stored_citations(session, row)
        used = retrieved + [c.source for c in cited]
        if not cited or not _documents_only(used):
            continue
        if not all(visibility.current(s) for s in used):
            continue
        text = answer_of(session, row)
        if not text:
            continue
        return Hit(
            query_id=row.id,
            text=text,
            citations=tuple(cited),
            retrieved=tuple(retrieved),
            followups=stored_followups(session, row),
            language=row.language,
        )
    return None


__all__ = ["Hit", "fingerprint", "lookup"]
