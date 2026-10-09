"""``sos://`` source URIs (docs/06 §8): the one builder and parser every producer shares.

Retrieval, record tools and verified answers build sources here, and citation validation
(FR-KB-005, docs/06 §9 rule 1) compares exact strings, so there is exactly one spelling. URIs
carry only IDs and keys (lower-case attribute and source keys), never names or values: they
reach logs, audit rows and the browser (docs/07 §11 "No personal data in URLs").

Pure: no I/O.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Set
from dataclasses import dataclass
from typing import Final, Protocol

from app.knowledge.domain import SourceKind


class Reach(Protocol):
    """How far one permission reaches (``app.authz.context.ScopeGrant`` fits; read structurally
    so this pure module imports nothing outside knowledge)."""

    @property
    def school_wide(self) -> bool: ...
    @property
    def class_ids(self) -> Set[uuid.UUID]: ...
    @property
    def section_ids(self) -> Set[uuid.UUID]: ...


_KEY: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_UUID: Final = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_DOC: Final = re.compile(rf"^sos://doc/(?P<id>{_UUID})/v(?P<v>[0-9]{{1,6}})#p(?P<p>[0-9]{{1,6}})$")
_FIELD: Final = re.compile(
    rf"^sos://student/(?P<id>{_UUID})/field/(?P<attr>[a-z][a-z0-9_]{{0,63}})"
    r"\?src=(?P<src>[a-z][a-z0-9_]{0,63})$"
)
_SIMPLE: Final = re.compile(rf"^sos://(?P<kind>finding|change|verified)/(?P<id>{_UUID})$")
_AGGREGATE: Final = re.compile(
    rf"^sos://(?P<kind>count|fee)/(?P<id>{_UUID})(?:#s(?P<scope>[0-9a-f]{{16}}))?$"
)
"""A tool aggregate with the fingerprint of the scope it was computed over (W3-09); keys
stored before the fingerprint have none (visible only school-wide, see ``visibility``)."""
_SCOPE: Final = re.compile(r"^[0-9a-f]{16}$")
_CONVERSATION: Final = re.compile(rf"^sos://conversation/(?P<id>{_UUID})#q(?P<q>{_UUID})$")


@dataclass(frozen=True, slots=True)
class SourceRef:
    kind: SourceKind
    object_id: uuid.UUID
    version_no: int | None = None
    page: int | None = None
    attribute: str | None = None
    source: str | None = None
    query_id: uuid.UUID | None = None
    """``conversation`` sources: the earlier question within the conversation."""
    scope: str | None = None
    """``count`` / ``fee`` sources: :func:`scope_fingerprint` of the scope it was computed over
    (``None`` for keys stored before audit W3-09)."""


def _key(value: str, what: str) -> str:
    if not _KEY.fullmatch(value):
        raise ValueError(f"{what} must be a lower-case key, never a name or value")
    return value


def _positive(value: int, what: str) -> int:
    if value < 1:
        raise ValueError(f"{what} must be positive")
    return value


def document_page(document_id: uuid.UUID, *, version_no: int, page: int) -> str:
    _positive(version_no, "version_no")
    _positive(page, "page")
    return f"sos://doc/{document_id}/v{version_no}#p{page}"


def student_field(student_id: uuid.UUID, *, attribute: str, source: str) -> str:
    """A record field from one source (``admission_register``, ``udise_plus``, ...)."""
    _key(attribute, "attribute")
    _key(source, "source")
    return f"sos://student/{student_id}/field/{attribute}?src={source}"


def finding(finding_id: uuid.UUID) -> str:
    return f"sos://finding/{finding_id}"


def change_request(change_request_id: uuid.UUID) -> str:
    return f"sos://change/{change_request_id}"


def verified_answer(verified_answer_id: uuid.UUID) -> str:
    return f"sos://verified/{verified_answer_id}"


def scope_fingerprint(grant: Reach) -> str:
    """16 hex characters naming the reach of one permission (school-wide, or the sorted class
    and section ids): ids only, hashed, so a source carries no list of sections. Equal grants
    give equal fingerprints; any change of scope gives another one (audit W3-09)."""
    if grant.school_wide:
        canonical = "school"
    else:
        classes = ",".join(sorted(str(c) for c in grant.class_ids))
        sections = ",".join(sorted(str(s) for s in grant.section_ids))
        canonical = f"classes={classes}|sections={sections}"
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def _scope(scope: str) -> str:
    if not _SCOPE.fullmatch(scope):
        raise ValueError("scope must be a scope fingerprint")
    return scope


def student_count(count_id: uuid.UUID, *, scope: str) -> str:
    """A student count the ``count_students`` tool computed (numbers only; docs/06 §8). The id
    is derived from the school, breakdown and day, so it names no student; ``scope`` is the
    :func:`scope_fingerprint` of the caller's reach the count was computed over, compared when
    the source is re-checked (a school-wide total is not shown again after the person's scope
    narrows; audit W3-09)."""
    return f"sos://count/{count_id}#s{_scope(scope)}"


def fee_dues(fee_id: uuid.UUID, *, scope: str) -> str:
    """Fee dues the ``get_fee_dues`` tool read from synced Tally ledgers (M6, ADR-0032). The id
    is derived from the school, the student (or the summary) and the snapshot, so it names no
    person and no ledger; ``scope`` as for :func:`student_count`."""
    return f"sos://fee/{fee_id}#s{_scope(scope)}"


def conversation_question(conversation_id: uuid.UUID, query_id: uuid.UUID) -> str:
    """One of the caller's own earlier questions and its answer (``search_my_conversations``,
    ADR-0034). Ids only; the text is the caller's own and is re-read under their access."""
    return f"sos://conversation/{conversation_id}#q{query_id}"


def parse(uri: str) -> SourceRef:
    """Parse a URI built by this module; anything else raises ``ValueError``."""
    if m := _DOC.fullmatch(uri):
        version_no, page = int(m["v"]), int(m["p"])
        if version_no >= 1 and page >= 1:
            return SourceRef(
                kind="doc", object_id=uuid.UUID(m["id"]), version_no=version_no, page=page
            )
    elif m := _FIELD.fullmatch(uri):
        return SourceRef(
            kind="student",
            object_id=uuid.UUID(m["id"]),
            attribute=m["attr"],
            source=m["src"],
        )
    elif m := _CONVERSATION.fullmatch(uri):
        return SourceRef(
            kind="conversation", object_id=uuid.UUID(m["id"]), query_id=uuid.UUID(m["q"])
        )
    elif m := _SIMPLE.fullmatch(uri):
        kind: SourceKind = m["kind"]  # type: ignore[assignment]
        return SourceRef(kind=kind, object_id=uuid.UUID(m["id"]))
    elif m := _AGGREGATE.fullmatch(uri):
        aggregate: SourceKind = m["kind"]  # type: ignore[assignment]
        return SourceRef(kind=aggregate, object_id=uuid.UUID(m["id"]), scope=m["scope"])
    raise ValueError("not a valid sos:// source URI")


__all__ = [
    "Reach",
    "SourceRef",
    "change_request",
    "conversation_question",
    "document_page",
    "fee_dues",
    "finding",
    "parse",
    "scope_fingerprint",
    "student_count",
    "student_field",
    "verified_answer",
]
