# ADR-0038: Commercial catalogue: one-time fee and AI answer bundles with overage

| Field | Value |
|---|---|
| Status | Accepted · amendment 2026-10-03 (the school AI budget is derived from the bundle) |
| Date | 2026-10-01 |
| Deciders | Product owner (commercial model "SaaS first, with a premium Dedicated tier", 2026-10-01); prices below are the owner's |
| Amends / supersedes | Amends [ADR-0015](ADR-0015-deployment-and-commercial-model.md) (commercial model: adds a one-time fee and a metered add-on to the recurring subscription). Implementation amendment B2 to [ADR-0020](ADR-0020-control-plane-boundaries-and-guaranteed-audit-copies.md) (the AI answer count). |

## Context

ADR-0015 said schools pay a recurring subscription in INR, GST extra, and left prices to be agreed per school. Plans (docs/16 §5.6) held a base price, per-student pricing and alert-only limits; invoices billed the plan in advance. The owner has now set a published catalogue:

- **Shared:** a one-time "Implementation and data verification" fee of ₹15,000, then ₹4,999 a month.
- **Dedicated:** a one-time fee of ₹49,000, then ₹9,900 a month; "a managed, isolated SchoolOS environment with your own domain, a dedicated database and a documented data export" (never "your own server").
- **AI answers** as a monthly add-on bundle with an included quota: Lite 300 answers ₹699, Standard 1,000 answers ₹1,499, High 3,000 answers ₹3,499; each extra answer ₹1.50. Never "unlimited"; schools never see tokens.

All are ex-GST starting prices; GST (18%, CGST + SGST or IGST) goes on the invoice as before. The plan model could not express a one-time fee, an add-on, a quota or overage, and the control plane had no count of AI answers (the AI meters were recorded as 0, docs/16 §11).

## Decision

1. **One-time fee on the plan.** `platform.plans.one_time_fee_inr` (default 0) and a plain `description`. It is charged **once, on the subscription's first invoice**. docs/16 §10 already makes that the invoice drafted at activation (billing in advance), so "first invoice" and "at activation" are the same event. Rule: a new draft gets the fee line (kind `one_time_fee`, "Implementation and data verification (one-time)") when no live (non-void) invoice of the subscription already carries one; so a voided first invoice lets the next new invoice charge it, and nothing charges it twice. To waive it, an operator keeps the line and adds an equal `discount` line on that invoice; deleting the line from a draft only moves the fee to the next new invoice.
2. **AI answer bundles are catalogue data.** `platform.ai_bundles` (code, version, name, included answers a month, monthly price, price per extra answer; `published` or `retired`; prices frozen by trigger like plans). A subscription has at most one bundle (`ai_bundle_id`) and `ai_bundle_from`, the first calendar month whose answers count (the month after it was chosen, or after activation for a trial: trial answers are free). Bundles need a monthly plan (409 `ai_bundle_needs_monthly_plan`).
3. **Billing of a bundle.** The bundle is billed in advance on each invoice with the plan (kind `addon`). Answers above the quota in calendar month M are billed in arrears on the invoice whose period starts in M + 1 (kind `usage_overage`, "N extra answers × ₹1.50", `usage_month` = M). A month is billed at most once per subscription (no live invoice already has an overage line for M). A bundle change applies to the next invoice and to any month not yet billed; nothing already invoiced is prorated.
4. **What an "AI answer" is.** One `kb.queries` row with status `answered` (an AI answer with citations), counted per school and IST day. `not_found`, `refused`, `search_only` and `error` are free. The billable statuses are config (`billing.yaml` → `ai_answers.billable_statuses`). A reused cached answer is an `answered` row and counts (open question for the owner, docs/16 §19 Q15).
5. **Only counts cross the boundary.** The shared-tier usage collector counts the day's questions and billable answers **inside the school's own `tenant_session`** (RLS applies, the same pattern as the distinct-active-users count) and stores the two numbers in `platform.usage_daily` (`ai_queries`, `ai_answers`). A dedicated host computes the same function and sends `ai_answers` in its heartbeat `usage` block (optional; 0 from older hosts). No definer function, `definer_access` policy or grant is added; `sos_platform` still has nothing on tenant tables. The pinned list in `tests/platform/test_boundaries.py` gains (`usage.py`, `kb.queries`) (ADR-0020 amendment B2).
6. **Catalogue as data.** Migration `0041_billing_catalogue` seeds the published Shared and Dedicated plans (`created_by` NULL = seeded by a catalogue migration) and the three bundles; `tests/platform/test_catalogue.py` pins the prices. A later price is a new version (a new migration or a new plan version by an operator), never an edit.

## Consequences

- Good: the invoice now shows the platform fee, the bundle, the overage and the one-time fee as separate lines with GST as before; the invoice PDF layout is unchanged (the lines fit its table).
- Good: overage is computed from counts the control plane already stores; no tenant data leaves the school's session, and a school sees answers, never tokens.
- Bad: overage lags a month (billed on the next invoice) and depends on the day's usage row being collected (the collector runs at 01:30 IST; the monthly draft run at 02:00 IST on the 1st). A dedicated host that cannot send heartbeats undercounts until it reconnects (in the school's favour).
- Bad: annual plans cannot take a bundle until a rule for them is decided.
- Control plane first: a dedicated host upgraded before the control plane sends `ai_answers`, which an older control plane rejects (strict heartbeat schema). The control plane is upgraded first, as for every release.
- Follow-ups: the 80%/100% alert on a bundle's quota (usage thresholds cover plan limits only); the school-side "Plan & billing" page shows the bundle (built: `ai_bundle` on `GET /api/v1/tenant/billing`; docs/16 §5.18); the public pricing page (`/pricing`) still says prices are agreed per school and calls Dedicated "your own server" (marketing copy, not changed here).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Bundles as extra plans | A school would need two subscriptions; plans carry tier, trial and limits that a bundle does not have |
| Bundle prices only in `billing.yaml` | A price change would silently change what existing schools pay; catalogue rows are versioned and frozen like plans |
| Count answers by extending `core.tenant_usage_summary` (definer) | Needs a `definer_access` policy on `kb.queries` and new `sos_definer` grants (a wider cross-tenant path, ADR-0013); the count in the school's own session needs neither |
| Count from `kb.llm_calls` | Counts model calls, not answers (a question can make several calls; a cached reuse makes none) |
| Overage per billing period instead of calendar month | Periods start on the activation day; the owner's quota is per month, and the previous period is not over when the next draft is made |
| Charge the fee at provisioning | A trial would be invoiced before the school decides; docs/16 bills from activation |

## Related requirements

FR-PLT-010, FR-PLT-013, FR-PLT-015, FR-PLT-016, FR-PLT-017, FR-PLT-020, FR-PLT-021, FR-KB-009, NFR-CST-001; docs/16 §5.6, §5.7, §7, §8.1, §10.2, §11, §19; ADR-0013, ADR-0015, ADR-0020.

## Amendments (2026-10-03)

**C1 · The school's monthly AI budget is derived from its bundle (product owner, 2026-10-03).**
Until now the AI budget (FR-KB-011) was the school's own setting `ai_monthly_budget_inr`
(default ₹5,000), unrelated to what the school buys. Decision and design:

- **Rule.** With a bundle, the budget is `included answers × cost_per_answer_usd × (1 +
  overage_headroom_fraction) × usd_inr_rate`, rounded to a rupee (`knowledge/config/models.yaml`
  → `budget.bundle`: an estimated **$0.06** per answered question at list price, **50 %**
  headroom for billed overage answers and for questions and ingestion calls that are not billable
  answers; at the 2026-10-03 rate 95.97 that is about ₹2,591 for Lite, ₹8,637 for Standard and
  ₹25,912 for High). It stays a **cost cap in rupees**, metered in USD per model call exactly as
  before (reservations, 80 % and 100 % alerts, search-only above it); an "answers per month"
  cap was not chosen because the gateway meters and reserves per model call, not per answer, and
  ingestion calls share the budget. Billing is unchanged: quota and overage come from answer
  counts (§3, §4), never from the budget.
- **Without a bundle** (trials before a bundle is chosen, annual plans, dedicated hosts for now)
  the school's own `ai_monthly_budget_inr` setting still applies.
- **How the number reaches the school.** Only the bundle's included answers cross the boundary,
  through the lifecycle-style call `tenancy.service.set_ai_answer_allowance` in the school's own
  `tenant_session` (ADR-0020 amendment B3); `knowledge.policy` reads it with
  `tenancy.service.ai_answer_allowance`. The allowance applies from the moment the bundle is set
  (it is a cost cap; the billing month `ai_bundle_from` is unchanged). Reconciled daily by the
  usage collector.
- **Commercial note for the owner.** At the estimated $0.06 (≈ ₹5.76) per answer, the ₹1.50
  overage price and the bundle prices are below our estimated model cost per answer; the estimate
  must be checked against `kb.llm_calls` once real usage exists (docs/16 §19 Q20).

Related: FR-KB-011, NFR-CST-001, FR-PLT-013; docs/06 §12, docs/16 §5.7, §11, §19 Q18-Q20.
