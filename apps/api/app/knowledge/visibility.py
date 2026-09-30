"""Can the caller still see a ``sos://`` source NOW? (invariant 8; docs/06 §5, ADR-0033).

Earlier answers are shown again (conversation history), sent again as context (recent turns, the
rolling summary, chat search) and reused (the answer cache). Each of those re-checks every source
the earlier answer cited, under the caller's CURRENT permissions and scopes, through the owning
modules' services (the same rules as the UI and the record tools):

- ``doc``: the documents service's visibility (ACL and scope) and, for a restricted (C3)
  document, ``student.read_sensitive`` (the retrieval rule, docs/06 §6). :meth:`current`
  additionally needs the cited version to be the document's current, active version.
- ``student``: ``students.get_profile`` under the caller's scope, and the field not masked for
  the caller now (a restricted value they may no longer read is withheld).
- ``finding`` / ``change``: the dq / changes services' scoped reads.
- ``verified``: an answer of this school that is not retired and whose every citation is a
  document the caller can read.
- ``count`` / ``fee``: aggregates of a tool; visible while the caller may still use that tool
  (fail closed when the caller's tools are not known).
- ``conversation``: one of the caller's own conversations that is not deleted.

Anything that does not parse is not visible (fail closed). Results are cached per instance
(one request or job). Nothing here logs source text.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Final

from app.changes import service as changes
from app.core.errors import DomainError
from app.documents import service as documents
from app.dq import service as dq
from app.knowledge import repository as repo
from app.knowledge import sources
from app.knowledge.tools.students import ROW_FIELDS
from app.students import service as students

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

READ_SENSITIVE: Final = "student.read_sensitive"
COUNT_TOOL: Final = "count_students"
FEE_TOOL: Final = "get_fee_dues"


class SourceVisibility:
    """Per-request cache of "may the caller see this source now"."""

    def __init__(
        self,
        session: Session,
        ctx: UserContext,
        *,
        tools_available: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._session = session
        self._ctx = ctx
        self._tools_available = tools_available
        self._tools: frozenset[str] | None = None
        self._cache: dict[str, bool] = {}
        self._docs: dict[uuid.UUID, object | None] = {}

    def visible(self, source: str) -> bool:
        if source not in self._cache:
            self._cache[source] = self._check(source)
        return self._cache[source]

    def all_visible(self, found: Iterable[str]) -> bool:
        return all(self.visible(s) for s in found)

    def current(self, source: str) -> bool:
        """A document page of the caller's visible, active document at its CURRENT version, or
        an ACTIVE verified answer the caller can see (the answer cache reuses only answers
        given current passages)."""
        try:
            ref = sources.parse(source)
        except ValueError:
            return False
        if ref.kind == "verified":
            row = repo.get_verified_answer(self._session, ref.object_id)
            return row is not None and row.status == "active" and self.visible(source)
        if ref.kind != "doc" or not self.visible(source):
            return False
        doc = self._document(ref.object_id)
        current = getattr(doc, "current_version", None)
        return (
            doc is not None
            and getattr(doc, "status", None) == "active"
            and current is not None
            and current.version_no == ref.version_no
        )

    # --- checks ---------------------------------------------------------------------------------

    def _check(self, source: str) -> bool:  # noqa: PLR0911 - one return per source kind
        try:
            ref = sources.parse(source)
        except ValueError:
            return False
        try:
            match ref.kind:
                case "doc":
                    return self._doc_visible(ref.object_id)
                case "student":
                    return self._field_visible(ref.object_id, ref.attribute)
                case "finding":
                    dq.get_finding(self._session, self._ctx, ref.object_id)
                    return True
                case "change":
                    changes.get_request(self._session, self._ctx, ref.object_id)
                    return True
                case "verified":
                    return self._verified_visible(ref.object_id)
                case "count":
                    return COUNT_TOOL in self._tool_names()
                case "fee":
                    return FEE_TOOL in self._tool_names()
                case "conversation":
                    return (
                        repo.get_conversation(self._session, ref.object_id, self._ctx.user_id)
                        is not None
                    )
        except DomainError:
            return False
        return False  # pragma: no cover - every SourceKind is matched above

    def _document(self, document_id: uuid.UUID) -> object | None:
        if document_id not in self._docs:
            try:
                self._docs[document_id] = documents.get_document(
                    self._session, self._ctx, document_id
                )
            except DomainError:
                self._docs[document_id] = None
        return self._docs[document_id]

    def _doc_visible(self, document_id: uuid.UUID) -> bool:
        doc = self._document(document_id)
        if doc is None:
            return False
        return getattr(doc, "sensitivity", "C3") != "C3" or self._ctx.has(READ_SENSITIVE)

    def _field_visible(self, student_id: uuid.UUID, attribute: str | None) -> bool:
        profile = students.get_profile(self._session, self._ctx, student_id)
        if attribute is None:
            return False
        if attribute in ROW_FIELDS:
            return True
        value = profile.canonical.get(attribute)
        return value is None or not value.masked

    def _verified_visible(self, answer_id: uuid.UUID) -> bool:
        row = repo.get_verified_answer(self._session, answer_id)
        if row is None or row.status == "retired":
            return False
        for citation in row.citations:
            try:
                ref = sources.parse(str(citation.get("source", "")))
            except ValueError:
                return False
            if ref.kind != "doc" or not self._doc_visible(ref.object_id):
                return False
        return True

    def _tool_names(self) -> frozenset[str]:
        if self._tools is None:
            self._tools = (
                frozenset(self._tools_available()) if self._tools_available else frozenset()
            )
        return self._tools


__all__ = ["SourceVisibility"]
