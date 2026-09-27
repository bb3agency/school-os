"""LLM gateway: every call to a model provider goes through here (ADR-0005, docs/06 §5, §12).

Responsibility: implement :class:`app.knowledge.interfaces.LlmGateway` (tool-use turns with
``search_result`` blocks; strict-JSON calls for metadata, query translation and register
extraction) and the network side of :class:`app.knowledge.interfaces.EmbeddingsProvider`.
Before anything leaves the process it applies ``core.redaction.mask_aadhaar()`` to every string
of the request (Verhoeff Aadhaar masking; :mod:`.wire`); it enforces token
caps, per-tenant budgets (search-only at 100%, FR-KB-011), rate limits, timeouts, retries and a
circuit breaker; it meters model, tokens, latency and cost per tenant and feature, never prompt
or completion text (FR-KB-009, NFR-CST-001). Model IDs, prices and limits come from
``knowledge/config/models.yaml``; the API key from ``Settings.anthropic_api_key``
(invariants 10, 13). ``Settings.kb_provider_mode`` = ``fake`` selects an offline deterministic
gateway for local/CI.

Modules (K4): :mod:`.factory` (``build_gateway``: mode guards and wiring, used by the
composition root), :mod:`.gateway` (the controls), :mod:`.wire` (redacted Messages API
requests, parsing), :mod:`.budget` (switches, monthly budget, rate limit, spend ledger),
:mod:`.resilience` (backoff, circuit breaker), :mod:`.metering`, :mod:`.errors`,
:mod:`.transport` (the provider seam), :mod:`.anthropic_transport` (live; the only ``anthropic``
import), :mod:`.fake` (offline), :mod:`.schema_check` (structured-output validation).

Boundary: the only package in the repository that may import a provider SDK (``anthropic``,
``voyageai``, ...; CLAUDE.md §11, semgrep ``sos-llm-sdk-outside-gateway``). It never fetches
records or documents itself: callers hand it exactly the blocks the user may see (import-linter
``knowledge-gateway-isolated`` forbids retrieval, tools, ingestion, embeddings and every tenant
data module).
"""
