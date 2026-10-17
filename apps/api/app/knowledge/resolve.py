"""Where a ``sos://`` source opens (``GET /knowledge/resolve``, FR-KB-042, US-2503).

The web opens most sources itself (``features/ask/answer.ts`` ``sourceHref``, the same paths as
:data:`HREFS`); it asks here for any it does not recognise. The caller must be able to see the
source NOW, under the same rules as answers shown again (:class:`SourceVisibility`: document
ACL and scope, the student scope and masking, the dq / changes scoped reads, verified answers
whose documents are all readable, tool aggregates of the caller's current reach, the caller's
own conversations); anything else, malformed or another school's, is the same 404. The
answer carries the kind, the object id and an app-relative path: never a title or text.

Logs nothing (the source is ids only anyway).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from app.core.errors import NotFound
from app.knowledge import sources
from app.knowledge.schemas import ResolveOut

if TYPE_CHECKING:
    from app.authz.context import UserContext
    from app.knowledge.visibility import SourceVisibility

ASK: Final = "kb.ask"
SEARCH: Final = "document.read"

HREFS: Final = {
    "doc": "/documents/{id}",
    "student": "/students/{id}",
    "finding": "/findings/{id}",
    "change": "/change-requests/{id}",
    "verified": "/ask/verified",
    "fee": "/fees",
    "conversation": "/ask/c/{id}#m-{query_id}",
}
"""App-relative screens per source kind (no locale prefix); ``count`` has none."""


def href_for(ref: sources.SourceRef) -> str | None:
    template = HREFS.get(ref.kind)
    if template is None:
        return None
    return template.format(id=ref.object_id, query_id=ref.query_id)


def resolve(ctx: UserContext, source: str, visibility: SourceVisibility) -> ResolveOut:
    """The screen of ``source`` if the caller can open it now; 404 otherwise.

    A document page needs ``kb.ask`` or ``document.read`` (search results cite pages too);
    every other kind is an Ask source and needs ``kb.ask``. Then the source must be visible."""
    try:
        ref = sources.parse(source)
    except ValueError:
        raise NotFound("Source not found") from None
    allowed = ctx.has(ASK) or (ref.kind == "doc" and ctx.has(SEARCH))
    if not allowed or not visibility.visible(source):
        raise NotFound("Source not found")
    return ResolveOut(kind=ref.kind, id=ref.object_id, href=href_for(ref))


__all__ = ["HREFS", "href_for", "resolve"]
