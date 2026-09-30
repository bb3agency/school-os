"""Knowledge tables in schema ``kb`` (migrations 0021_kb_tables, 0024_kb_metering; docs/05 §6).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs, indexes) lives in the
migration. Only ``app.knowledge.repository`` and ``app.knowledge.retrieval`` use these mappings
(import-linter ``knowledge-internals``); the document tables they reference belong to
``app.documents`` and are never mapped here (the composite FKs are enforced by the database).

``HalfVector`` maps pgvector's ``halfvec(n)`` without the ``pgvector`` Python package: values
travel as pgvector's text form (``[0.1,0.2,...]``) and are cast explicitly in SQL.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import (
    BindParameter,
    Boolean,
    ColumnElement,
    Computed,
    Date,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    Uuid,
    cast,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.core.model_base import Base

SCHEMA: Final = "kb"
EMBEDDING_DIMENSIONS: Final = 1024
"""``kb.document_chunks.embedding`` / ``kb.embedding_cache.embedding`` (ADR-0006 placeholder;
``knowledge/config/embeddings.yaml`` ``storage.dimensions`` must match, a test checks)."""


def vector_literal(values: Sequence[float], dimensions: int = EMBEDDING_DIMENSIONS) -> str:
    """pgvector text form of ``values``; refuses a wrong length or a non-finite component."""
    if len(values) != dimensions:
        raise ValueError(f"expected {dimensions} dimensions, got {len(values)}")
    parts = []
    for v in values:
        f = float(v)
        if not math.isfinite(f):
            raise ValueError("embedding components must be finite")
        parts.append(repr(f))
    return "[" + ",".join(parts) + "]"


def parse_vector(value: str) -> list[float]:
    inner = value.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        raise ValueError("not a pgvector text value")
    body = inner[1:-1]
    return [float(x) for x in body.split(",")] if body else []


class HalfVector(UserDefinedType[list[float]]):
    """``halfvec(n)``: bound as text and cast by PostgreSQL; read back as ``list[float]``."""

    cache_ok = True

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: Any) -> str:
        return f"halfvec({self.dimensions})"

    def bind_expression(self, bindvalue: BindParameter[list[float]]) -> ColumnElement[list[float]]:
        # Explicit CAST: the operator and column type never depend on parameter type inference.
        return cast(bindvalue, self)

    def bind_processor(self, dialect: Dialect) -> Callable[[Any], str | None]:
        dimensions = self.dimensions

        def process(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, str):
                return value
            return vector_literal(value, dimensions)

        return process

    def result_processor(self, dialect: Dialect, coltype: object) -> Callable[[Any], Any]:
        def process(value: Any) -> list[float] | None:
            if value is None:
                return None
            return parse_vector(str(value))

        return process


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    version_id: Mapped[uuid.UUID]
    chunk_no: Mapped[int] = mapped_column(Integer)
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    context_header: Mapped[str] = mapped_column(Text, server_default=text("''"))
    content: Mapped[str] = mapped_column(Text)
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, context_header || ' ' || content)"),
        nullable=False,
    )
    language: Mapped[str | None] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    is_table: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    embedding: Mapped[list[float]] = mapped_column(HalfVector())
    embedding_model: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    issued_on: Mapped[dt.date | None] = mapped_column(Date)
    academic_year_id: Mapped[uuid.UUID | None]
    sensitivity: Mapped[str] = mapped_column(Text)
    acl_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    acl_sections: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid(as_uuid=True)), server_default=text("'{}'")
    )
    acl_classes: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid(as_uuid=True)), server_default=text("'{}'")
    )
    acl_memberships: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid(as_uuid=True)), server_default=text("'{}'")
    )
    is_latest: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class EmbeddingCacheEntry(Base):
    __tablename__ = "embedding_cache"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    input_type: Mapped[str] = mapped_column(Text, primary_key=True)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(HalfVector())
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class Query(Base):
    __tablename__ = "queries"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    session_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    question_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    question_hmac: Mapped[bytes] = mapped_column(LargeBinary)
    answer_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    tools: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    retrieved: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    citations: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    model_ids: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str | None] = mapped_column(Text)
    feedback_reason: Mapped[str | None] = mapped_column(Text)
    feedback_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    # 0038_ask_conversations (ADR-0034): conversation, supersession, encrypted answer details.
    conversation_id: Mapped[uuid.UUID | None]
    superseded_by: Mapped[uuid.UUID | None]
    revises: Mapped[uuid.UUID | None]
    revision: Mapped[str | None] = mapped_column(Text)
    citations_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    followups_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_fingerprint: Mapped[bytes | None] = mapped_column(LargeBinary)
    cached_from: Mapped[uuid.UUID | None]
    cache_invalidated_at: Mapped[dt.datetime | None]
    summarized: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Conversation(Base):
    """One user's Ask conversation (0038; docs/05 §6.4). Title and summary are ciphertext."""

    __tablename__ = "conversations"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    title_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer)
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    summary_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    summary_oldest_at: Mapped[dt.datetime | None]
    summary_through: Mapped[dt.datetime | None]
    summary_sources: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    deleted_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class UserMemory(Base):
    """One memory item of one user in one school (0038; ADR-0034). Text is ciphertext."""

    __tablename__ = "user_memories"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    text_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    conversation_id: Mapped[uuid.UUID | None]
    query_id: Mapped[uuid.UUID | None]
    expires_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class UserMemorySettings(Base):
    """A user's memory switch in one school (no row = on; ADR-0034)."""

    __tablename__ = "user_memory_settings"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class VerifiedAnswer(Base):
    __tablename__ = "verified_answers"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    question_canonical: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[Any]] = mapped_column(JSONB)
    document_id: Mapped[uuid.UUID | None]
    verified_by: Mapped[uuid.UUID]
    verified_at: Mapped[dt.datetime]
    review_due: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class LlmCall(Base):
    """One metered model call (0024_kb_metering; FR-KB-009, FR-KB-011). Ids and counts only."""

    __tablename__ = "llm_calls"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    occurred_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    feature: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    query_id: Mapped[uuid.UUID | None]
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6))
