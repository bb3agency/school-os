# ADR-0005: LLM gateway and provider (Anthropic Claude)

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) · Amended by ADR-0033 |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

"Ask the school", register-row extraction, metadata extraction and translation need a capable language model that handles English, Telugu and code-mixed text, supports tool use, and can cite the content it was given. The model provider is a sub-processor outside India (08 §1), so data sent to it must be minimal and contractual terms must forbid training on it. Provider APIs, model names and prices change often. Scattered SDK calls would make it impossible to enforce budgets, redaction and logging rules in one place.

## Decision

- Use **Anthropic Claude** through the commercial Messages API with **organization API keys** only. Personal or consumer AI subscriptions are never used for product traffic (CLAUDE.md §6 invariant 10).
- All LLM calls go through **one module: `app/knowledge/gateway/`**. No other module imports a provider SDK (CLAUDE.md §11). The gateway:
  - reads model IDs, prompt versions and limits from versioned config (`kb.answer_model`, `kb.metadata_model`, `kb.router_model`; CLAUDE.md §6 invariant 13);
  - applies `redact()` (Verhoeff Aadhaar masking) to every outgoing prompt;
  - enforces per-tenant and per-user rate limits, token caps and monthly budgets, with search-only fallback at 100% (FR-KB-011);
  - records model, tokens, latency and cost per tenant and feature, never prompt or completion text in plain form (FR-KB-009);
  - has timeouts, retries with jitter and a circuit breaker; on outage the product degrades to search-only answers (NFR-AVL-004).
- Retrieved content is passed as `search_result` content blocks so answers carry citations that the server validates (06 §7, §9).
- Request **Zero Data Retention** for the production API organization. Data sent is minimized to the fields the question needs; Aadhaar data is never sent (NFR-PRV-003).
- Other providers MAY be added behind the same gateway interface after an evaluation run (`make eval`) and a privacy review (new sub-processor → ADR).

## Consequences

- Good: one place to enforce redaction, budgets, logging, and provider changes; model upgrades are config changes gated by evals.
- Good: citations via search result blocks make grounding checkable.
- Bad: dependence on a foreign sub-processor; mitigated by minimization, ZDR request, disclosure in the DPA and graceful degradation.
- Bad: provider features (e.g., which models support search result blocks) must be re-verified at build time.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Direct SDK calls from each module | Budgets, redaction and logging rules would drift; hard to audit |
| Self-hosted open model in India | Weaker quality for Telugu/code-mixed tool use at acceptable cost today; kept possible behind the gateway |
| Multiple providers from day one | More sub-processors and evaluation effort without a measured need |

## Related requirements

FR-KB-003..012, NFR-PRV-001, NFR-PRV-003, NFR-AVL-004, NFR-CST-001, SEC-020; 06 §5, §7, §9, §12; 07 §12; 08 §1, §8.
