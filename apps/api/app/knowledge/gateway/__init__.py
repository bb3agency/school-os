"""LLM gateway: every call to a model provider goes through here (ADR-0005, ADR-0033; docs/06 §5,
§12).

Responsibility: implement :class:`app.knowledge.interfaces.LlmGateway` (tool-use turns over the
passages the caller may see; strict-JSON calls for metadata, query translation, circular reading,
notices and register extraction, with page images for the extraction role) and the network side
of :class:`app.knowledge.interfaces.EmbeddingsProvider`. Before anything leaves the process it
applies ``core.redaction.mask_aadhaar()`` to every string of the request (Verhoeff Aadhaar
masking); it enforces token caps, per-tenant budgets (search-only at 100%, FR-KB-011), rate
limits, timeouts, retries and a circuit breaker per provider; it meters model, tokens (cached and
thinking tokens included), latency and cost per tenant and feature, never prompt or completion
text (FR-KB-009, NFR-CST-001). Providers, model IDs, prices and limits come from
``knowledge/config/models.yaml`` (every role names its provider: ``gemini`` by default since
ADR-0033, ``anthropic`` as a config-selectable fallback); credentials from ``Settings``
(``SOS_LLM_GCP_*`` service identity for Vertex AI, ``SOS_ANTHROPIC_API_KEY`` for a fallback role;
invariants 10, 13). ``Settings.kb_provider_mode`` = ``fake`` selects offline deterministic
providers for local/CI.

Modules (K4): :mod:`.factory` (``build_gateway``: mode guards and wiring, used by the
composition root), :mod:`.gateway` (the controls, provider routing), :mod:`.codec` (the wire
format seam), :mod:`.wire` (Messages API requests and parsing), :mod:`.gemini_wire` (Vertex AI
``generateContent`` requests and parsing), :mod:`.citations` (provider-neutral numbered passages
and ``[n]`` markers), :mod:`.budget` (switches, monthly budget, rate limit, spend ledger),
:mod:`.resilience` (backoff, circuit breaker), :mod:`.metering`, :mod:`.errors`,
:mod:`.transport` (the provider seam), :mod:`.gemini_transport` (live Vertex AI over httpx, the
Zero Data Retention check), :mod:`.gemini_auth` (service-identity tokens; the only ``google.auth``
import), :mod:`.gemini_cache` (explicit context caches of static prefixes),
:mod:`.anthropic_transport` (live fallback; the only ``anthropic`` import), :mod:`.fake`,
:mod:`.fake_gemini` (offline), :mod:`.schema_check` (structured-output validation).

Boundary: the only package in the repository that may import a provider SDK or its credential
library (``anthropic``, ``voyageai``, ``google.auth``, ``google.oauth2``, ...; CLAUDE.md §11,
semgrep ``sos-llm-sdk-outside-gateway``). It never fetches records or documents itself: callers
hand it exactly the blocks the user may see (import-linter ``knowledge-gateway-isolated`` forbids
retrieval, tools, ingestion, embeddings and every tenant data module).
"""
