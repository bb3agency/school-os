"""Contextual chunk headers at ingestion (docs/06 §4.11; FR-KB-001; PO approval 2026-09-30).

:class:`ChunkContextualizer` asks the ``contextualize`` model role, through the gateway only
(ADR-0005, CLAUDE.md §11), for a short context situating each chunk in its document, and checks
every answer (:mod:`app.knowledge.contextual.rules`). It runs outside any database transaction,
between chunking and embedding, and only when the composition root gives the pipeline one
(``retrieval.yaml`` ``contextual_chunks: on``).

- **Input** is the document's own text after cleaning and Aadhaar masking (invariant 4; the
  gateway masks the whole request again), the title/issuer the office typed and this call's
  passages. No other document, no record, nothing the document's readers could not read: a
  context is stored on the same document's chunks, under the same ACL.
- **Calls**: ``chunks_per_call`` passages per call; the system prompt (instructions + document)
  is identical for every call of one version, so the provider's prompt cache serves it (the
  gateway marks the system prompt cacheable). Metering: feature and role ``contextualize`` with
  the document id (``kb.llm_calls.document_id``), inside the school's monthly AI budget.
- **Reuse** (idempotent per version): a chunk whose content, model and prompt are unchanged
  keeps its stored context (``ok`` or ``rejected``) without a call.
- **Budget-aware**: when the gateway refuses (budget used up, school AI switched off, rate
  limit, provider down or rejecting) this and every later chunk of the document are
  ``deferred``: indexed without a context now, asked again by the backfill task. An invalid
  structured output rejects that call's passages only.

Nothing here logs text: ids, counts and codes only (invariant 5).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from app.core.logging import get_logger
from app.knowledge.config.contextual import ContextualConfig
from app.knowledge.contextual import rules
from app.knowledge.domain import Chunk, Metering
from app.knowledge.gateway.errors import GatewayError, GatewayMisuse, InvalidModelOutput
from app.knowledge.ingestion.ports import NO_CONTEXT, ChunkContext, StoredContext
from app.knowledge.interfaces import LlmGateway
from app.knowledge.prompts.registry import PromptTemplate

log = get_logger(__name__)

ROLE: Final = "contextualize"
DEFERRED: Final = ChunkContext(status="deferred")
REJECTED: Final = ChunkContext(status="rejected")


def content_sha256(chunk: Chunk) -> bytes:
    return hashlib.sha256(chunk.content.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class DocumentInput:
    """What the model may see about one document version (already masked)."""

    tenant_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    doc_type: str
    issuer: str | None
    text: str
    """:func:`app.knowledge.contextual.rules.document_text` of the cleaned document."""


@dataclass(slots=True)
class ContextOutcome:
    contexts: list[ChunkContext]
    """One per chunk, in order."""
    calls: int = 0
    reused: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    """Rejection or deferral codes and how many chunks each covers."""

    def count(self, status: str) -> int:
        return sum(1 for c in self.contexts if c.status == status)


class ChunkContextualizer:
    def __init__(
        self,
        *,
        gateway: LlmGateway,
        model: str,
        prompt: PromptTemplate,
        config: ContextualConfig,
    ) -> None:
        if prompt.header.model_config_key != ROLE:
            raise ValueError("the contextualize prompt must be written for the contextualize role")
        self._gateway = gateway
        self._model = model
        self._prompt = prompt
        self._label = f"{prompt.header.id}.v{prompt.header.version}"
        self._config = config

    @property
    def model(self) -> str:
        return self._model

    @property
    def prompt_label(self) -> str:
        return self._label

    def reusable(self, chunk: Chunk, stored: StoredContext | None) -> ChunkContext | None:
        """The stored context when nothing it depends on changed (content, model, prompt)."""
        if stored is None or stored.content_sha256 != content_sha256(chunk):
            return None
        ctx = stored.context
        if ctx.status == "ok" and (ctx.model, ctx.prompt) == (self._model, self._label):
            return ctx
        if ctx.status == "rejected" and ctx.prompt == self._label and ctx.model == self._model:
            return ctx
        return None

    def contextualize(
        self,
        document: DocumentInput,
        chunks: Sequence[Chunk],
        stored: Mapping[int, StoredContext] | None = None,
    ) -> ContextOutcome:
        stored = stored or {}
        out = ContextOutcome(contexts=[NO_CONTEXT] * len(chunks))
        pending: list[int] = []
        for i, chunk in enumerate(chunks):
            reused = self.reusable(chunk, stored.get(chunk.chunk_no))
            if reused is None:
                pending.append(i)
            else:
                out.contexts[i] = reused
                out.reused += 1
        if not pending:
            return out
        source = "\n".join(p for p in (document.title, document.issuer or "", document.text) if p)
        checker = rules.ContextChecker(self._config, source)
        system = self._prompt.render(
            title=rules.escape_tags(document.title),
            doc_type=rules.escape_tags(document.doc_type.replace("_", " ")),
            document=rules.escape_tags(
                rules.clip_document(document.text, self._config.max_document_chars)
            ),
        )
        metering = Metering(
            tenant_id=document.tenant_id, feature=ROLE, document_id=document.document_id
        )
        size = self._config.chunks_per_call
        batches = [pending[n : n + size] for n in range(0, len(pending), size)]
        for b, batch in enumerate(batches):
            passages = [
                rules.Passage(number=n, text=chunks[i].content, language=chunks[i].language)
                for n, i in enumerate(batch, start=1)
            ]
            try:
                value = self._gateway.generate_json(
                    metering, ROLE, system, rules.passages_message(passages), rules.SCHEMA
                )
            except InvalidModelOutput as exc:
                out.calls += 1
                self._mark(out, batch, REJECTED, exc.code)
                continue
            except GatewayError as exc:
                if isinstance(exc, GatewayMisuse):
                    log.error(
                        "knowledge.contextual.misuse",
                        tenant_id=document.tenant_id,
                        resource_type="document",
                        resource_id=document.document_id,
                        error_code=exc.code,
                    )
                rest = [i for later in batches[b:] for i in later]
                self._mark(out, rest, DEFERRED, exc.code)
                break
            out.calls += 1
            answers = rules.contexts_from_output(value, (p.number for p in passages))
            for passage, i in zip(passages, batch, strict=True):
                raw = answers.get(passage.number)
                if raw is None:
                    self._mark(out, [i], REJECTED, rules.MISSING)
                    continue
                checked = checker.check(raw, passage.language)
                if checked.context is None:
                    self._mark(out, [i], REJECTED, checked.reason or "rejected")
                    continue
                out.contexts[i] = ChunkContext(
                    text=checked.context, status="ok", model=self._model, prompt=self._label
                )
        rejected = ChunkContext(status="rejected", model=self._model, prompt=self._label)
        out.contexts = [rejected if c is REJECTED else c for c in out.contexts]
        return out

    @staticmethod
    def _mark(
        out: ContextOutcome, indexes: Sequence[int], context: ChunkContext, code: str
    ) -> None:
        for i in indexes:
            out.contexts[i] = context
        if indexes:
            out.reasons[code] = out.reasons.get(code, 0) + len(indexes)


__all__ = [
    "ROLE",
    "ChunkContextualizer",
    "ContextOutcome",
    "DocumentInput",
    "content_sha256",
]
