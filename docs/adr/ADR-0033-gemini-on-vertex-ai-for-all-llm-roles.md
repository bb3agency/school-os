# ADR-0033: Google Gemini on Vertex AI for every LLM role

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-30 |
| Deciders | Product owner (decision: "switch everything to Gemini", 2026-09-30); lead engineer (implementation) |
| Amends / supersedes | Amends [ADR-0005](ADR-0005-llm-gateway-and-provider.md) (the provider and the citation mechanism; the gateway decision stands) |

## Context

ADR-0005 put every LLM call behind `app/knowledge/gateway/` and chose Anthropic Claude through the Messages API, with retrieved content passed as `search_result` blocks so answers carry native citations. On 2026-09-30 the product owner decided to move **every** LLM job (answer and tool loop, router, metadata, translation, register extraction, circular reading, notice drafting, offline eval judge) to Google Gemini to cut model cost, mentioning the Flash and Flash-Lite classes.

Facts checked on 2026-09-30 (Google's documentation pages were blocked by the session's egress proxy, so these come from search-result extracts of the official Vertex AI pages and release notes, and from the `google-genai` 2.25.0 / `google-auth` 2.59.0 packages; the live evaluation must re-confirm them):

- Gemini on **Vertex AI** (Google Cloud; now also branded "Gemini Enterprise Agent Platform") offers regional endpoints, including **asia-south1 (Mumbai)**, commercial terms without training on customer data, and a documented **Zero Data Retention** set-up: disable the project's in-memory data caching (`projects.updateCacheConfig`, `disableCache: true`; otherwise prompts are cached in memory up to 24 h), do not enable request-response logging, and request the exception from prompt logging for abuse monitoring where the project is eligible. Explicit context caches are customer-created, customer-deleted storage and may be used for content without personal data.
- The Gemini Developer API (AI Studio API keys) is a different product with different terms; it is not used.
- Models: Gemini **2.5 Flash / Flash-Lite** are served from asia-south1 but Vertex AI **retires them on 2026-10-16**. **Gemini 3.5 Flash** (GA 2026-05-19) lists asia-south1 among its regions (about USD 1.50 / 9.00 per million input / output tokens global, 1.65 / 9.90 regional). **Gemini 3.5 Flash-Lite** is about USD 0.30 / 2.50 (regional availability reported inconsistently). **Gemini 3.8 Flash** (GA 2026-09-02, introductory USD 0.75 / 3.75 until 2026-12-31) and **3.1 Flash-Lite** (USD 0.25 / 1.50) were listed for the global and US/EU multi-region endpoints when checked, not for asia-south1. **Gemini 3.1 Pro** is in preview on the global endpoint (USD 2 / 12).
- Gemini supports streaming (`streamGenerateContent`, SSE), function calling (`functionDeclarations` with JSON Schema), structured output (`responseMimeType` + `responseJsonSchema`), image input, and thinking controls (`thinkingLevel` on Gemini 3, `thinkingBudget` on 2.5). Gemini 3 models return a **thought signature** with each function call that must be sent back on the next request (Vertex rejects the replay otherwise), and cannot switch thinking off entirely. Gemini has no equivalent of Anthropic's `search_result` blocks.
- Explicit context caches need a minimum prefix (4,096 tokens reported for Gemini 3); our static system prompts and tool definitions are usually smaller.

## Decision

1. **Provider.** Every LLM role MUST default to **Google Gemini on Vertex AI**, reached only through `app/knowledge/gateway/` (CLAUDE.md §11 unchanged). `models.yaml` names a `provider` per role (`default_provider: gemini`); a role's model MUST belong to that provider; model IDs, prices, limits, thinking settings and capabilities stay in `models.yaml` (invariant 13).
2. **Residency.** Product traffic MUST use the regional endpoint of `SOS_LLM_GCP_LOCATION`, an India region (`asia-south1`, or `asia-south2`); staging/prod refuse any other location, including `global`. Only the offline `eval_judge` role, which only ever sees synthetic evaluation data, MAY name another location in `models.yaml` (the loader refuses it for every other role).
3. **Identity.** The gateway authenticates with a Google **service identity** only: AWS -> Google **workload identity federation** (preferred; no secret) or a service-account key held in Secrets Manager. A person's login (`authorized_user`), consumer accounts and Gemini Developer API keys are refused at start-up in every environment (invariant 10).
4. **Zero Data Retention.** Before AI is switched on in staging/prod, operators MUST disable data caching on the project, keep request-response logging off, request the abuse-monitoring logging exception, and confirm with `SOS_LLM_ZDR_CONFIRMED=true` (docs/10 §11.1). The gateway MUST also check the project's `cacheConfig` itself and send nothing while caching is on (fail closed; answers degrade to search-only). Explicit context caches MAY hold only a role's static system prompt and tool definitions (no question, record, passage or image), with a TTL, and every failure falls back to an uncached request.
5. **Citations become provider-neutral.** For a provider without native search-result citations the gateway sends tool results as **numbered passages** and the model writes **`[n]` markers**; the gateway maps each marker back to a passage given in THIS request, drops markers that name no such passage, and drops a statement's markers when its numbers (dates, amounts, counts, class numbers) are not in the cited passages. The resulting citations go through the unchanged server-side validation (§9: source among this request's blocks, cited text a substring), so an unsupported answer still says "not found" or falls back to search-only (FR-KB-005, FR-KB-007). The Anthropic path keeps native `search_result` citations.
6. **Anthropic stays as a fallback, by configuration only.** The Anthropic transport stays in code; each role records its evaluated Anthropic settings as `fallback` in `models.yaml`. Switching a role back is a reviewed config change plus `make eval`; the gateway MUST NOT fail over between providers automatically (that would send data to a second sub-processor without a decision). Anthropic stays on the sub-processor list while any fallback is configured.
7. **Model choice by evaluation.** Each role gets the cheapest model that passes every hard gate in a live evaluation (`make eval-live`, docs/06 §13.7). Until credentials exist the configuration is **provisional**: `gemini-3.5-flash` for answer, notice and extraction (quality in Telugu, handwriting, tool use), `gemini-3.5-flash-lite` for router, metadata, translation and circular reading, and `gemini-3.1-pro-preview` as the offline judge. 2.5 models are not chosen because of their 2026-10-16 retirement.
8. **Eval judge.** The judge MUST be a stronger model than the answer model. A Gemini Pro judge grading Gemini Flash answers still shares a model family (self-preference bias is reduced, not removed); the calibration set of docs/06 §13.2 (human-labelled items) is the control, and a cross-family judge (the Anthropic fallback, `claude-opus-5-5`) MAY be used for calibration runs because the judge sees synthetic data only.

## Consequences

- Good: a large cost reduction per answer (Flash / Flash-Lite prices), and processing in India (asia-south1) instead of a provider outside India, which simplifies the DPDP and school-notice story (docs/08 §1).
- Good: citations no longer depend on one provider's content-block feature; the server-side marker checks are stricter than a bare marker (unknown passages and unsupported numbers are dropped).
- Good: one gateway, one set of controls (redaction, budgets, breaker, metering) for both providers; switching a role is configuration plus evaluation.
- Bad: a second cloud (Google Cloud) to operate: project, IAM, workload identity federation, billing, and a ZDR set-up with a manual abuse-monitoring exception step whose outcome is not in our control.
- Bad: Gemini 3 thinks at least a little on every call (billed as output), and thought signatures must be carried through the tool loop (`ToolCall.signature`).
- Bad: `[n]` markers make citation quality depend on the model following instructions; the checks catch wrong or unsupported markers but can drop a correct citation (for example a figure the model computed), which lowers coverage rather than precision.
- Bad: model regional availability changes quickly (new Gemini models often launch on the global endpoint first); the India-only rule may force an older or pricier model for a while.
- Follow-up: live evaluation per role with credentials (docs/14 M2); Terraform for the Google side and the new secrets; wiring a vision extraction provider (FR-IMP-024) needs a decision on redacting Aadhaar numbers in page images before they are sent (PO question); remove the Anthropic fallback and its sub-processor entry once Gemini has run a full term without a switch-back.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Stay on Anthropic Claude (ADR-0005) | The product owner decided on Gemini for cost |
| Gemini Developer API (AI Studio keys) | API keys, different data terms, no regional endpoint or project-level ZDR controls; conflicts with invariant 10 |
| Official `google-genai` SDK | Pulls `requests`, `websockets`, `tenacity` and its own retries and logging, which would bypass the gateway's retry, breaker and no-text-in-logs controls; plain REST over httpx (CLAUDE.md §3) plus `google-auth` for tokens is smaller |
| Gemini 2.5 Flash / Flash-Lite (the product owner's first guess) | Cheapest and in asia-south1, but retired by Vertex AI on 2026-10-16 |
| Gemini on the `global` endpoint (newest, cheapest models) | No guarantee that prompts are processed in India; allowed only for the synthetic-data judge |
| Automatic failover to Anthropic on a Vertex outage | Moves school data to a second sub-processor without a decision; the product already degrades to search-only |
| Gemini grounding / Vertex RAG Engine for citations | Would move retrieval and permission filtering out of our SQL (invariant 8) |

## Related requirements

FR-KB-003..012, FR-CIR-002/003/008, FR-TALLY-008, FR-IMP-024, NFR-PRV-001, NFR-PRV-003, NFR-AVL-004, NFR-CST-001, SEC-009, SEC-019, SEC-020; CLAUDE.md §3, §6 (invariants 8, 10, 13), §11; docs/06 §5, §7, §9, §12, §13; docs/07 TB5, T13, LLM02; docs/08 §1, §8; docs/10 §11, §11.1; docs/14 M2.

## Amendments

- **2026-09-30 (implementation fact, merge of ADR-0034).** The Ask conversation roles added by ADR-0034 follow decision 7 like every other role: `query_rewrite` (200), `followups` (400), `summary` (600) and `memory_screen` (150) run on `gemini-3.5-flash-lite` with thinking level `low`, provisional until the live evaluation, each with its evaluated Anthropic fallback `claude-haiku-4-5-20251001` (the model the backend was built and tested with). On the Gemini wire the conversation context uses the same layout as the Messages API (rolling summary, recent turns, the question as written, then the question), and the user's memory items are a second system part after the static prompt; a request carrying memory is never put in an explicit context cache (decision 4: caches hold only content without personal data). `search_my_conversations` results are passages like any other, cited with `[n]` markers mapped to their `sos://conversation/...` sources. The offline conversation evaluation (docs/06 §13.5) passes through the Gemini-wire fake. docs/06 §12 lists every role's model.
- **2026-09-30 (implementation fact, merge of ADR-0035).** The `contextualize` role (contextual chunk headers, off by default) runs on `gemini-3.5-flash-lite`, thinking level `low`, output cap 2000, fallback `claude-haiku-4-5-20251001`. Its system instruction carries the whole (Aadhaar-masked) document, which is tenant content, so under decision 4 structured-output requests are never put in an explicit Vertex context cache: only a tool-use turn's static system prompt and tool definitions may be cached, and not when the turn carries the user's memory. Each contextualize call therefore pays the document's input tokens on Gemini (the Anthropic fallback's prompt cache would serve repeat calls of one document); `chunks_per_call` in `contextual.yaml` is the cost lever. Reranking stays provider-off; the Vertex AI ranking API in asia-south1 is the in-region candidate (ADR-0035). The contextual evaluation is docs/06 §13.6; this ADR's live evaluation moved to docs/06 §13.7.
