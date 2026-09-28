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

from app.core.errors import DomainError
from app.core.textnorm import nfc
from app.documents import service as documents
from app.documents.schemas import DocumentOut, VersionOut
from app.knowledge import sources
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
from app.knowledge.tools.access import SENSITIVE, acl_keys

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


LIST_NAME: Final = "list_documents"
NOT_LISTED_PURPOSES: Final = frozenset({"evidence", "import_file"})
"""Student-record files (identity evidence, raw imports) are never listed to the model."""


class ListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_type: DocType | None = None


class ListDocumentsTool:
    """:class:`~app.knowledge.interfaces.RecordTool` ``list_documents`` (docs/06 §7): metadata
    of active documents the caller may see (``documents.service.list_documents``: the same ACL
    and scope as the documents list), newest first, at most ``max_results``. Never content
    (``search_documents`` reads content); never restricted (C3) documents unless the caller
    holds ``student.read_sensitive``; never identity evidence or import files. Each block's
    source is the current version's first page, so a chip opens the document."""

    def __init__(self, config: ToolConfig) -> None:
        if config.description is None:
            raise ValueError("list_documents needs a description in tools.yaml")
        self._max = config.max_results or 20
        self._spec = ToolSpec(
            name=LIST_NAME,
            description=config.description,
            input_schema={
                "type": "object",
                "properties": {"doc_type": {"type": "string", "enum": list(DOC_TYPES)}},
                "additionalProperties": False,
            },
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
            args = ListArgs.model_validate(dict(arguments))
            docs, _ = documents.list_documents(
                session, ctx, limit=self._max * 2, doc_type=args.doc_type, status="active"
            )
        except (ValidationError, DomainError):
            return ToolOutcome(call_id=call_id, blocks=(), is_error=True)
        sensitive = ctx.has(SENSITIVE)
        blocks = [
            _metadata_block(d, d.current_version)
            for d in docs
            if d.current_version is not None
            and d.purpose not in NOT_LISTED_PURPOSES
            and (d.sensitivity != "C3" or sensitive)
        ]
        return ToolOutcome(call_id=call_id, blocks=tuple(blocks[: self._max]))


def _metadata_block(doc: DocumentOut, version: VersionOut) -> SearchResultBlock:
    kind = doc.doc_type.replace("_", " ").capitalize()
    issued = _date(doc.issued_on)
    parts = [f"Document: {doc.title}.", f"Type: {kind}."]
    if doc.issuer:
        parts.append(f"Issued by: {doc.issuer}.")
    if issued:
        parts.append(f"Issued on: {issued}.")
    parts.append(
        f"Current version: {version.version_no}, uploaded on "
        f"{version.created_at.strftime('%d/%m/%Y')}."
    )
    parts.append("Content is not included here; search the documents to read it.")
    title = " · ".join(p for p in (kind, doc.title, issued) if p)
    return SearchResultBlock(
        source=sources.document_page(doc.id, version_no=version.version_no, page=1),
        title=title,
        text=" ".join(parts),
    )


__all__ = [
    "DOC_TYPES",
    "LIST_NAME",
    "NAME",
    "DocumentSearch",
    "ListDocumentsTool",
    "SearchArgs",
    "SearchDocumentsTool",
    "block_title",
    "input_schema",
    "to_block",
]
