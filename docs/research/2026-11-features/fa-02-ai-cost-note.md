# FA-02 cost note: cost per answer at the January 2027 price, and a bundle repricing proposal

| Field | Value |
|---|---|
| Date | 2026-11-09 |
| Feature | FA-02 AI unit economics (`docs/research/2026-11-features/ai-platform.md`) |
| Requirements | NFR-CST-003..010, FR-KB-046..050 (docs/03 §3.31) |
| Status | **Estimate, not measured.** No live provider call is possible (owner decision D2). Every figure below is list price × the token volumes `models.yaml` assumes; the first `make eval-live` replaces them with measured counts. **Owner decision needed** on repricing; nothing in `platform/billing.yaml` or the catalogue was changed. |

## 1. What was built (and how to re-run these numbers)

- `models.yaml` prices take an **effective-date schedule** (NFR-CST-004). The gateway meters and reserves each call at the price in force on its UTC call date, so an introductory price ending on 31 December 2026 changes budgets on 1 January 2027 without a deploy.
- Candidate answer models are priced as `candidate: true, verified: false` entries (FR-KB-047): `gemini-3.6-flash` and `gemini-3.8-flash` (introductory $0.75 / $3.75 per million input / output tokens until 2026-12-31, then $1.50 / $7.50) and `gemini-3.1-flash-lite` ($0.25 / $1.50). Source: Google's Gemini API pricing page, accessed 2026-11-09, **plus 10 %** for the regional endpoint as `models.yaml` already does for prices Google does not publish per region. None of them is selected: the loader refuses a role that names a candidate. Selection needs the live gates (docs/06 §13.7).
- The eval harness measures **cost per answer** (NFR-CST-003, `sos_evals.cost`): every model call each question made (the `kb.llm_calls` rows on app runs, the gateway's metering events on `make eval-live`) priced at `models.yaml`. A **soft gate** `cost_per_answer_usd <= 0.06` (the estimate the bundle budgets are derived from) blocks release; `make eval-live` fails on it. `python -m sos_evals run --price-date 2027-01-01` prices the same recorded counts at the January 2027 prices (NFR-CST-005).
- **Light-model routing** of single-tool questions (counts, "what's due") is configured and decided by `app/knowledge/routing.py`, but **off** (`routing.enabled: false`) and not wired into the ask path (FR-KB-048..050).

## 2. Assumptions

| Item | Value | Where |
|---|---|---|
| Answer-role calls per answered question | 2 (tool choice, then the cited answer) | `models.yaml` budget note, docs/06 §12 |
| Tokens per answer-role call | ~10,000 input (≤ 12k tool results, prompt, history), ~800 output incl. thinking | same |
| Small conversation roles (rewrite, follow-ups, summary, memory screen) | ~$0.004 per answer on `gemini-3.5-flash-lite` | same |
| USD/INR | 95.97 (FBIL 28 Sep 2026) | `models.yaml` `budget.usd_inr_rate` = `billing.yaml` |
| Bundle prices (before GST) | Lite ₹699 / 300, Standard ₹1,499 / 1,000, High ₹3,499 / 3,000, extra answer ₹1.50 | `platform/billing.yaml` `public_pricing` |

Cost per answer = 2 × (10,000 × input price + 800 × output price) / 1,000,000 + $0.004.

## 3. Cost per answer

| Answer model (regional price per 1M tokens) | USD per answer | ₹ per answer |
|---|---|---|
| A. `gemini-3.5-flash`, selected today ($1.65 / $9.90) | 0.0528 | 5.07 |
| B. `gemini-3.8-flash` or 3.6, introductory price to 2026-12-31 ($0.825 / $4.125) | 0.0271 | 2.60 |
| **C. `gemini-3.8-flash` or 3.6, from 2027-01-01 ($1.65 / $8.25)** | **0.0502** | **4.82** |
| C′. as C with tool results cut to ~6k input tokens (FA-02 item 4, if recall holds) | 0.0370 | 3.55 |
| D. `gemini-3.1-flash-lite` for every answer ($0.275 / $1.65) | 0.0121 | 1.17 |
| E. Routing: 40 % single-tool questions on D, 60 % on C (share **assumed**) | 0.0350 | 3.36 |

Budgets must use the January 2027 price (row C or A), never row B: the introductory price ends before any school's first full year. Rows B-E are **provisional** (unverified prices, candidates not yet evaluated); row E also needs the router share from real questions.

The bundle budget estimate `budget.bundle.cost_per_answer_usd: 0.06` still covers rows A and C, so the derived monthly AI budgets (the cost caps) need no change. The gate keeps it honest: a measured run above $0.06 fails release.

## 4. Against the bundle prices

Revenue per included answer before GST: Lite ₹2.33, Standard ₹1.50, High ₹1.17, extra answer ₹1.50.

| Bundle | Revenue a month | Model cost at full use, row C | Margin at full use | Row E | Row D |
|---|---|---|---|---|---|
| Lite (300) | ₹699 | ₹1,445 | **-₹746** | ₹1,007 (-₹308) | ₹350 (+₹349) |
| Standard (1,000) | ₹1,499 | ₹4,818 | **-₹3,319** | ₹3,357 (-₹1,858) | ₹1,165 (+₹334) |
| High (3,000) | ₹3,499 | ₹14,454 | **-₹10,955** | ₹10,071 (-₹6,572) | ₹3,495 (+₹4) |
| Each extra answer | ₹1.50 | ₹4.82 | **-₹3.32** | -₹1.86 | +₹0.33 |

"Full use" is the worst case; schools that use fewer answers cost less, but the bundle price is what a school that does use them pays. The loss per school is bounded: above the derived budget (included × $0.06 × 1.5 × 95.97: ₹2,591 Lite, ₹8,637 Standard, ₹25,912 High) Ask answers search-only. Only row D (every answer on Flash-Lite) is at or above cost, and only if Flash-Lite passes every hard citation and leakage gate, which is unknown until the live run.

## 5. Repricing proposal for the owner

The owner question from FA-02 stands: reprice the bundles, or accept a loss on AI as a sales cost for the first pilots? Published prices are frozen catalogue rows (ADR-0038), so any change is a new catalogue version (migration, `public_pricing`, the pricing page and its pinning tests), which this work did not do.

**Recommended (proposal):**

1. **Before the first paid AI bundle, run `make eval-live` (when D2 lifts)** with the candidates and routing switched on in the eval only, and read the measured `cost_per_answer_usd` at `--price-date 2027-01-01`. Decide prices on that number, not on this note.
2. **Price for a 40 % gross margin on measured model cost** (price ≥ cost ÷ 0.6). On today's estimates that gives:

   | Bundle | Today | If row C holds (₹8.03 / answer) | If routing works, row E (₹5.60 / answer) |
   |---|---|---|---|
   | Lite (300) | ₹699 | ₹2,499 | ₹1,699 |
   | Standard (1,000) | ₹1,499 | ₹8,099 | ₹5,699 |
   | High (3,000) | ₹3,499 | ₹24,499 (or withdraw) | ₹16,999 (or withdraw) |
   | Extra answer | ₹1.50 | ₹8.50 | ₹6.00 |

   Row C prices make High dearer than the Shared plan itself (₹4,999 a month), which will not sell; the realistic path is routing plus a cheaper answer model, with High withdrawn or capped until measured cost supports it.
3. **Interim, if the owner wants to sell AI to pilots before the live run:** keep the published bundles for the pilot schools as a known sales cost (bounded by the derived budget cap above), do not sell High, and raise the extra-answer price in the next catalogue version (₹6 is the row E break-even plus margin).
4. **Starter bundle (ADR-0055 question 7):** at row E, 100 answers cost about ₹336 a month in model cost; a Starter price of at least ₹599 keeps the 40 % margin. At row C, at least ₹849.
5. **Batch pricing** (half price) only for offline jobs (contextual chunk headers, the eval judge), and only if batch staging fits the Zero Data Retention set-up, which is **unverified** (NFR-CST-010).

## 6. What remains open

- Measured token counts per answer (first `make eval-live`; then `kb.llm_calls` in pilots, NFR-CST-009).
- Vertex AI regional prices and asia-south1 serving for the candidates (all `verified: false`).
- Whether Flash-Lite passes the hard gates on single-tool questions (routing stays off until it does), and the share of such questions in real use.
- Owner decision on repricing (this section) and on promising "simple questions answered by a fast model".
