"""``search_documents``: hybrid retrieval as a read-only tool (docs/06 §6-7; FR-KB-001/002).

:class:`DocumentSearch` embeds the query (``input_type="query"``, never cached in the database)
and runs the :class:`~app.knowledge.interfaces.Retriever` with the caller's :class:`AclKeys`
(filtered IN SQL before ranking, invariant 8). The ask loop, the search-only fallback and
``POST /knowledge/search`` all use it, so there is one path from a question to passages.

:class:`SearchDocumentsTool` wraps it for the answer model: validated arguments, permission
``document.read``, results as ``search_result`` blocks whose
``source`` is the chunk's ``sos://doc/{id}/v{n}#p{page}`` and whose text is the chunk content
(already Aadhaar-masked at ingestion). Read-only: no writes, no network of its own (the embedder
is injected by the composition root).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.textnorm import nfc
from app.knowledge.config.tools import ToolConfig
from app.knowledge.domain import (
    RankedChunk,
    RetrievalQuery,
    SearchFilters,
    SearchResultBlock,
    ToolOutcome,
    ToolSpec,
)
from app.knowledge.interfaces import Retriever, TenantEmbedder
from app.knowledge.tools.access import acl_keys

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

NAME: Final = "search_documents"
MAX_QUERY_CHARS: Final = 300
DocType = Literal[
    "circular",
    "policy",
    "minutes",
    "register_scan",
    "certificate",
    "letter",
    "form",
    "report",
    "verified_answer",
    "other",
]
DOC_TYPES: Final[tuple[str, ...]] = get_args(DocType)

_WORD = re.compile(r"\w+", re.UNICODE)


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    doc_types: tuple[DocType, ...] | None = Field(default=None, min_length=1, max_length=10)


def input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for, in the words of the question.",
                "maxLength": MAX_QUERY_CHARS,
            },
            "doc_types": {
                "type": "array",
                "items": {"type": "string", "enum": list(DOC_TYPES)},
                "maxItems": 10,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def _date(value: dt.date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value is not None else None


def block_title(chunk: RankedChunk) -> str:
    """Document type, title, date and page: shown to the model and on source chips."""
    parts = [chunk.doc_type.replace("_", " ").capitalize(), chunk.title]
    issued = _date(chunk.issued_on)
    if issued:
        parts.append(issued)
    title = " · ".join(parts)
    if chunk.page_from is not None:
        pages = (
            f"p.{chunk.page_from}"
            if chunk.page_to in (None, chunk.page_from)
            else f"pp.{chunk.page_from}-{chunk.page_to}"
        )
        title = f"{title} ({pages})"
    return title


def to_block(chunk: RankedChunk) -> SearchResultBlock:
    return SearchResultBlock(source=chunk.source, title=block_title(chunk), text=chunk.content)


class DocumentSearch:
    """Query embedding + ACL-filtered hybrid retrieval for one caller."""

    def __init__(
        self, *, retriever: Retriever, embedder: TenantEmbedder, config: ToolConfig
    ) -> None:
        self._retriever = retriever
        self._embedder = embedder
        self._latest_terms = frozenset(t.casefold() for t in config.latest_terms)

    def prefers_latest(self, query: str) -> bool:
        words = {w.casefold() for w in _WORD.findall(query)}
        return bool(words & self._latest_terms)

    def search(
        self,
        session: Session,
        ctx: UserContext,
        query: str,
        *,
        filters: SearchFilters | None = None,
        k: int = 12,
    ) -> list[RankedChunk]:
        """Passages the caller may read, best first; [] for a caller without document access."""
        acl = acl_keys(session, ctx)
        text = nfc(query).strip()
        if acl is None or not text:
            return []
        vector = self._embedder.embed(session, ctx.tenant_id, [text], "query")[0]
        return self._retriever.search(
            session,
            acl,
            RetrievalQuery(
                texts=(text,),
                vectors=(tuple(vector),),
                k=k,
                filters=filters or SearchFilters(),
                prefer_latest=self.prefers_latest(text),
            ),
        )


class SearchDocumentsTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``search_documents``."""

    def __init__(self, search: DocumentSearch, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("search_documents needs a description in tools.yaml")
        self._search = search
        self._max_results = config.max_results or 6
        self._spec = ToolSpec(
            name=NAME,
            description=config.description,
            input_schema=input_schema(),
            permission=config.permission,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def allowed(self, ctx: UserContext) -> bool:
        return ctx.has(self._spec.permission)

    def run(
        self, session: Session, ctx: UserContext, call_id: str, arguments: Mapping[str, object]
    ) -> ToolOutcome:
        if not self.allowed(ctx):
            return ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        try:
            args = SearchArgs.model_validate(dict(arguments))
        except ValidationError:
            return ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        filters = SearchFilters(
            doc_types=frozenset(args.doc_types) if args.doc_types is not None else None
        )
        found = self._search.search(session, ctx, args.query, filters=filters, k=self._max_results)
        return ToolOutcome(call_id=call_id, blocks=tuple(to_block(c) for c in found))


__all__ = [
    "DOC_TYPES",
    "NAME",
    "DocumentSearch",
    "SearchArgs",
    "SearchDocumentsTool",
    "block_title",
    "input_schema",
    "to_block",
]
