"""Composition root: the one place the knowledge implementations are wired (docs/06 §3).

:func:`runtime` builds, once per process and on first use, everything the API and the worker
need:

- **Embeddings:** ``select_embeddings_provider`` (``SOS_KB_PROVIDER_MODE``: the offline fake in
  local/CI, the ADR-0006 selection built by ``gateway.build_voyage_provider`` in live mode)
  wrapped in :class:`CachingTenantEmbedder` with the ``kb.embedding_cache`` adapter
  (:class:`SqlEmbeddingCache`).
- **Retrieval:** :class:`HybridRetriever` and :class:`DocumentSearch` (query embedding + ACL keys).
- **Gateway:** ``build_gateway`` with :class:`SchoolAiPolicy` (school setting AND the
  ``kb.ask.enabled`` flag; budget from tenancy) and :class:`LedgerMeteringSink`
  (``kb.llm_calls``). Its rate-limit counters follow the process KV store (``authz.kv``).
- **Tools and the answer loop:** the tools described in ``tools.yaml``; :class:`AnswerEngine`
  with the ``answer_system`` prompt (v1).
- **Ingestion:** :func:`configure_ingestion` hands ``ingestion.runtime`` a factory for
  :class:`DocumentIngestionPipeline` over ``documents.service`` (:class:`DocumentsServiceSource`),
  the SQL chunk store and the tenant embedder. Importing this module installs the documents
  hooks (``ingestion.hooks``: version ready / ACL changed -> outbox; ``lifecycle``: archive,
  unarchive, delete) in whichever process imports it (API through ``knowledge.service``,
  worker through ``knowledge.tasks``).

Nothing is built at import time (no provider construction, no database), so importing is cheap
and live-mode misconfiguration fails on first use, loudly (``EmbeddingsNotConfiguredError``,
``ProviderModeError``). Tests replace the runtime with :func:`set_runtime`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from functools import partial
from typing import Final

import app.knowledge.ingestion.hooks
import app.knowledge.lifecycle  # noqa: F401  (installs the archive/unarchive/delete hooks)
from app.authz.kv import KVStore, kv_store
from app.core.config import Settings, get_settings
from app.knowledge.answer import AnswerEngine
from app.knowledge.config.conversations import ConversationsConfig, load_conversations_config
from app.knowledge.config.embeddings import EmbeddingsConfig, load_embeddings_config
from app.knowledge.config.llm import LlmConfig, load_llm_config
from app.knowledge.config.tools import ToolsConfig, load_tools_config
from app.knowledge.embeddings import CachingTenantEmbedder, select_embeddings_provider
from app.knowledge.gateway.embeddings_voyage import build_voyage_provider
from app.knowledge.gateway.factory import build_gateway
from app.knowledge.gateway.metering import MeteringSink
from app.knowledge.gateway.transport import Transport
from app.knowledge.ingestion import runtime as ingestion_runtime
from app.knowledge.ingestion.documents_source import DocumentsServiceSource
from app.knowledge.ingestion.pipeline import INDEXED_HOOKS, DocumentIngestionPipeline
from app.knowledge.interfaces import EmbeddingsProvider, LlmGateway, TenantEmbedder
from app.knowledge.policy import LedgerMeteringSink, SchoolAiPolicy
from app.knowledge.prompts.registry import load_prompt
from app.knowledge.retrieval import HybridRetriever
from app.knowledge.store import SqlChunkStore, SqlEmbeddingCache
from app.knowledge.tools.documents import NAME as SEARCH_TOOL
from app.knowledge.tools.documents import DocumentSearch
from app.knowledge.tools.registry import OfferedTool, build_tools

ANSWER_PROMPT: Final = ("answer_system", 2)


class _ProcessKV:
    """The current process KV store on every call (tests swap it with ``set_kv_store``)."""

    def get(self, key: str) -> bytes | None:
        return kv_store().get(key)

    def set(self, key: str, value: bytes, *, ttl_s: int, nx: bool = False) -> bool:
        return kv_store().set(key, value, ttl_s=ttl_s, nx=nx)

    def delete(self, *keys: str) -> None:
        kv_store().delete(*keys)

    def incr(self, key: str, *, ttl_s: int) -> int:
        return kv_store().incr(key, ttl_s=ttl_s)


@dataclass(frozen=True, slots=True)
class Runtime:
    settings: Settings
    llm_config: LlmConfig
    tools_config: ToolsConfig
    embedder: TenantEmbedder
    search: DocumentSearch
    gateway: LlmGateway
    tools: dict[str, OfferedTool]
    engine: AnswerEngine
    policy: SchoolAiPolicy | None = None
    conversations: ConversationsConfig = field(default_factory=load_conversations_config)
    """Ask conversations, memory and the answer cache (``conversations.yaml``; ADR-0033)."""


def build_runtime(
    settings: Settings | None = None,
    *,
    transport: Transport | None = None,
    gateway: LlmGateway | None = None,
    provider: EmbeddingsProvider | None = None,
    sink: MeteringSink | None = None,
    counters: KVStore | None = None,
    embeddings_config: EmbeddingsConfig | None = None,
    llm_config: LlmConfig | None = None,
    tools_config: ToolsConfig | None = None,
    conversations_config: ConversationsConfig | None = None,
) -> Runtime:
    settings = settings or get_settings()
    emb = embeddings_config or load_embeddings_config()
    llm = llm_config or load_llm_config()
    tools_cfg = tools_config or load_tools_config()
    provider = provider or select_embeddings_provider(
        settings.resolved_kb_provider_mode,
        emb,
        network={"voyage": partial(build_voyage_provider, settings=settings, http=emb.voyage)},
    )
    embedder = CachingTenantEmbedder(provider, emb, SqlEmbeddingCache())
    search = DocumentSearch(
        retriever=HybridRetriever(), embedder=embedder, config=tools_cfg.tools[SEARCH_TOOL]
    )
    policy: SchoolAiPolicy | None = None
    if gateway is None:
        policy = SchoolAiPolicy()
        gateway = build_gateway(
            settings,
            policy=policy,
            sink=sink or LedgerMeteringSink(),
            transport=transport,
            counters=counters or _ProcessKV(),
            config=llm,
            tools_config=tools_cfg,
        )
    tools = build_tools(tools_cfg, search)
    prompt_id, version = ANSWER_PROMPT
    engine = AnswerEngine(
        gateway=gateway,
        tools=tools,
        search=search,
        config=llm,
        prompt=load_prompt(prompt_id, version),
    )
    return Runtime(
        settings=settings,
        llm_config=llm,
        tools_config=tools_cfg,
        embedder=embedder,
        search=search,
        gateway=gateway,
        tools=tools,
        engine=engine,
        policy=policy,
        conversations=conversations_config or load_conversations_config(),
    )


_lock = threading.Lock()
_runtime: Runtime | None = None


def runtime() -> Runtime:
    global _runtime  # noqa: PLW0603 - process-wide composition, built once on first use
    with _lock:
        if _runtime is None:
            _runtime = build_runtime()
        return _runtime


def set_runtime(value: Runtime | None) -> None:
    """Replace (or, with None, reset) the process runtime (tests, the offline eval)."""
    global _runtime  # noqa: PLW0603 - see runtime()
    with _lock:
        _runtime = value


def _pipeline() -> DocumentIngestionPipeline:
    return DocumentIngestionPipeline(
        source=DocumentsServiceSource(),
        store=SqlChunkStore(),
        embedder=runtime().embedder,
        indexed_hooks=INDEXED_HOOKS,
    )


def configure_ingestion() -> None:
    """Give the worker tasks their pipeline (``ingestion.runtime``); idempotent."""
    ingestion_runtime.configure(_pipeline)


__all__ = [
    "ANSWER_PROMPT",
    "Runtime",
    "build_runtime",
    "configure_ingestion",
    "runtime",
    "set_runtime",
]
