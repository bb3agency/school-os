# ADR-0055: "Records & Readiness" entry tier sold beside an existing ERP

| Field | Value |
|---|---|
| Status | **Proposed** (the product owner decides; nothing here is built or priced) |
| Date | 2026-11-02 |
| Deciders | Product owner (commercial); lead engineer (enforcement design) |
| Amends / supersedes | Would amend [ADR-0015](ADR-0015-deployment-and-commercial-model.md) ("both tiers have the same features") and [ADR-0038](ADR-0038-commercial-catalogue-one-time-fee-and-ai-answer-bundles.md) (adds a third published plan). Reopens owner decision **D8** of 2026-10-10 ("Keep ADR-0038; no entry tier for now", docs/18 §8). Builds on [ADR-0041](ADR-0041-boards-import-presets-and-alongside-mode.md) (alongside mode), [ADR-0042](ADR-0042-fee-ledger-receipts-and-payment-links.md) (fees) and [ADR-0043](ADR-0043-whatsapp-parent-messages-via-meta-cloud-api.md) (messaging). |

## Context

### Who it is for

docs/18 §1 found that the checklist ERP (fees, attendance, parent app, transport, payroll) is commoditised and often free, while the sharp, dated pain is government-portal data: UDISE+ feeds APAAR, APAAR is mandatory for CBSE registration from 2026-27, and the AP SSC board took the March 2026 nominal rolls straight from UDISE+ with a three-day correction window (next window likely February 2027). The owner chose that wedge (D1). Many target schools (300-1,500 students, owner or correspondent signs) **already pay for an ERP and will not switch** in the middle of a year. For them SchoolOS is the records-and-readiness layer next to the ERP, which is exactly what alongside mode (ADR-0041, FR-TEN-022) was built for: the ERP keeps fees and office work; SchoolOS reconciles the admission register, Aadhaar-as-printed, UDISE+ and the board, prepares pre-check sheets, issues certificates and answers questions from the school's own records with citations.

Such a school will not pay for a second full office system. docs/18 R4 therefore lists "possible entry tier *Records & Readiness* sold beside an existing ERP", and §7 Q6 asked whether to add it.

### What the research and the catalogue say about price

- Published ERP prices cluster at **₹50-200 per student per year**; only about 20 % of Indian institutions pay for campus software (docs/18 §1.1; estimates, secondary sources).
- Today's Shared plan (ADR-0038; `apps/api/app/platform/billing.yaml` → `public_pricing`) is ₹15,000 one-time + **₹4,999 a month** (₹59,988 a year) before GST. docs/18 R4 notes that is about **₹120 per student per year at 500 students but ₹200 at 300**. Worked out: ₹199.96 at 300, ₹119.98 at 500, ₹59.99 at 1,000 students. With the cheapest AI bundle (Lite, ₹699 a month) it is ₹5,698 a month: ₹227.92, ₹136.75 and ₹68.38 per student per year.
- So a 300-student school sits at the top of the ERP band for a product it uses *beside* its ERP. That is the gap this proposal addresses.
- On 2026-10-10 the owner kept ADR-0038 and declined an entry tier "for now" (D8). This ADR puts the question back with the design worked out, so the owner can decide on facts, not reopen it by default.

### What the code supports today for limiting a school's modules (checked 2026-11-02 on `main` 6d2f433d)

| Mechanism | Where | Who sets it | Can it carry a commercial limit? |
|---|---|---|---|
| **Operating mode** `full` / `alongside` | `apps/api/app/tenancy/operating_modes.yaml` (alongside hides `tally`, `fees`); `_hidden_modules()` and `module_enabled()` in `apps/api/app/tenancy/service.py`; reported as `TenantOut.modules_hidden` | **The school** (`PATCH /tenant`, `tenant.settings.manage`, step-up) | **No.** The school can switch back to `full` at any time, and a hidden module only refuses *new* set-up (409 `module_hidden`); the school's own switch `fees_enabled` turns fees on even in alongside mode (ADR-0042 decision 12) |
| **Per-school feature flags** | `platform.feature_flags`; evaluated by `apps/api/app/core/feature_flags.py` (tenant side, the one platform table `sos_app` may read) and `apps/api/app/platform/flags.py` (control plane); docs/16 §5.11 | **Operators** (`platform.flags.manage`, audited) | **Yes, for the modules that check a flag.** Fees check `fees.enabled` AND the school switch (`apps/api/app/fees/_common.py::fees_on`); Tally checks `tally.connector.enabled` (`apps/api/app/tally/agent_auth.py`); Ask checks `kb.ask.enabled` (`apps/api/app/knowledge/policy.py::read_ai_settings`). Unknown flags are off (fail closed). Messaging, academics, insights and circulars have **no flag** today |
| **Plan `features`** | `platform.plans.features` (jsonb `{flag_key: bool}`, `apps/api/app/platform/models.py`, `PlanIn.features` in `apps/api/app/platform/schemas.py`); docs/16 §5.6 calls them "flag defaults" | Operators, on a draft plan; frozen once published | **Stored but not applied.** Nothing reads `features` when a subscription starts or changes plan |
| **Plan `limits`** | `platform.plans.limits` (students, staff users, storage, documents, AI tokens) | Operators | Alert-only (80 % / 100 % thresholds, `billing.yaml` → `usage_thresholds`); never blocks |
| **Per-student pricing** | `plans.pricing_model = per_student`, `included_students`, `per_student_price_inr`; invoice line `per_student` in `apps/api/app/platform/billing.py` (active students from `platform.usage_daily`, counted in the school's own session by `apps/api/app/platform/usage.py`) | Operators | **Yes, already built**: base price covers the included students, each active student above that is billed per month |
| **Plan change** | `billing.change_plan()` on the same subscription, effective next period (docs/16 §5.6) | Operators | Yes. The one-time fee is charged once per subscription (`subscription_has_line(..., "one_time_fee")`), so a plan change never charges a second one-time fee |
| `plan_tier` / `tier` | `core.tenants.plan_tier`, `platform.plans.tier`: CHECK `('shared','dedicated')` (migrations `0003`, `0005`) | Provisioning | Describes deployment shape, not features; should stay that way |

Ask's fee tool (`apps/api/app/knowledge/tools/fees.py`) is offered only while fees or the Tally connector is on, so turning those modules off also removes fee answers from Ask without new code.

## Decision (proposed)

### 1. What the tier is

A third published plan on the **shared deployment only**, code `records-readiness` (display name "Records & Readiness"), for a school that keeps its ERP. It is the same product and the same tenant shape as Shared: same isolation, security baseline, audit, backups, data-in-India and support, with fewer modules switched on. A school on it runs in **alongside mode** by default (set at provisioning; the school may change the mode, but the plan, not the mode, decides what is switched on, see §3).

### 2. What it includes and excludes

| Capability (docs/02 §3) | In the tier? | Reason |
|---|---|---|
| C1 identity, C2 school setup, C10 audit, C11 in-app notifications, C12 admin console (users, roles, retention, data export, plan & billing) | **Included** | Platform basics; the audit trail and full data export are part of the trust promise (BO-05) and of DPDP duties |
| C3 student record with per-source values; C4 imports (incl. ERP refresh presets, ADR-0041) and register-photo extraction | **Included** | The record SchoolOS reconciles; the ERP refresh path is the tier's main way in |
| C5 data-quality engine, C6 maker-checker changes | **Included** | The wedge: findings and evidenced changes (BO-01, invariant 6) |
| C9 exports and C19 board/portal readiness (APAAR consent, PEN, transfer-in, readiness diff), C31 portal calendar and reminders | **Included** | The reason the school buys (D1, docs/18 §1.3) |
| C15 / C20 certificates and registers, AP TC, public verification | **Included** | BO-04; the TC is where wrong records hurt parents. See open question 6 on serial numbers when the ERP also prints TCs |
| C7 documents & knowledge base, C8 / C25 Ask the school | **Included, AI answers only with a bundle** (§4) | "Every answer shows the record it came from" is a selling point (docs/18 §1.8); documents are needed for evidence in maker-checker anyway |
| C26-C28 DPDP pack, consents, breach register; C29 CBSE Appendix IX page | **Included** | Compliance duties apply to every school SchoolOS processes data for; they must never be a paid upgrade (docs/18 §1.5, ADR-0048..0051) |
| C16 circulars → tasks (copy/download notices only) | **Included** | Government circulars become dated tasks; it pairs with readiness and sends nothing |
| C21 fee ledger, receipts, payment links (ADR-0042) | **Excluded** | The ERP keeps fees; this is what alongside mode already hides |
| C18 Tally read connector | **Excluded** | Fees and accounts stay with the ERP and Tally (alongside mode already hides it) |
| C22 WhatsApp messages, C24 parent link pages | **Excluded** | The ERP's parent app usually covers parent contact; sending from two systems confuses parents. Parent pages only exist to carry messages and receipts |
| C17 timeline and early warning; academics (daily attendance, marks) | **Excluded** | Attendance and marks are entered in the ERP; early warning is only as good as data entered daily in SchoolOS. Offered on the full plan (open question 5) |
| C23 parent language | n/a | Follows messaging and parent pages |

### 3. How it is enforced (existing mechanisms first)

The commercial boundary MUST be set by the control plane, never by a school setting:

1. **Plan features become per-school flags.** A published plan's `features` map (already stored, docs/16 §5.6 "flag defaults") MUST be applied when a subscription starts and when a plan change takes effect: the control plane writes per-school rows in `platform.feature_flags` (its own table; no tenant table, no definer function, no new grant; ADR-0020 boundary unchanged), audited as `flag.plan_applied` with the plan code and version. The `records-readiness` plan carries `{"fees.enabled": false, "tally.connector.enabled": false, "messaging.enabled": false, "academics.enabled": false, "insights.enabled": false}`; the Shared and Dedicated plans carry the same keys as `true` (a new catalogue version of each, prices unchanged).
2. **Modules that have no flag get one** (fail closed, read through `app/core/feature_flags.py`): `messaging.enabled` (connect, send, webhooks keep recording STOP as under D35), `academics.enabled` (attendance and marks routes), `insights.enabled` (flags and interventions). Each module's service refuses with the existing 409 `module_hidden` while its flag is off, on reads as well as writes, so the limit is not only a hidden menu. Existing data is never deleted or hidden from export.
3. **`modules_hidden` reports the plan as well as the mode.** `tenancy.service._hidden_modules()` adds every module whose plan flag is off, and a school switch such as `fees_enabled` can no longer show a module the plan has switched off. The web app already hides by `modules_hidden`; the Plan & billing page names the modules the plan does not include, with "Ask SchoolOS to upgrade".
4. **Operator overrides stay possible but visible.** An operator may still set a per-school flag (pilots, support), but the school's page in the operator console MUST show where a flag differs from its plan, so a forgotten override is not a silent free upgrade.

What exists today: the flags for fees, Tally and Ask; the `features` column; per-student pricing; plan change on the same subscription. What would need building: applying `features` on activation and plan change; three new flags and their checks; `_hidden_modules` reading flags; the drift view; one catalogue migration.

### 4. Ask the school and AI answers

- Ask is included, but AI answers need an **AI answer bundle**, as on Shared (ADR-0038). While a `records-readiness` school has no bundle, `kb.ask.enabled` MUST be off for it, so the school's own `ai_monthly_budget_inr` fallback (ADR-0038 amendment C1, meant for trials) does not become free AI usage. Search over documents without AI answers stays available.
- The tier MAY take any published bundle. The owner may also add a smaller **Starter** bundle (for example 100 answers a month) as a new catalogue row; its derived AI budget would be about ₹864 a month (100 × $0.06 × 1.5 × 95.97), and at the estimated ≈ ₹5.76 model cost per answer any price under about ₹576 a month is below estimated cost (the same caveat ADR-0038 C1 records for the existing bundles). Never "unlimited"; schools see answers, never tokens.

### 5. Upgrade to the full plan (no data migration)

The tier is the same tenant, schemas, RLS and files as Shared. Upgrading is `change_plan` on the existing subscription to `shared` (next period, or at once during a trial): the new plan's `features` are applied, the flags turn on, `modules_hidden` shrinks, and the school then chooses to leave alongside mode and turn on fees itself. Nothing is copied, imported or re-verified. Downgrading from Shared keeps all recorded fee, messaging and academic data (kept, exportable, counted in DPDP access reports) but makes those modules read-refused until the next upgrade; an operator MUST see a warning listing the modules that hold data before confirming. The one-time fee is charged once per subscription, so an upgrade charges no second fee (see open question 3 for a lower entry fee).

### 6. Pricing: options for the owner, not a decision

All prices ex-GST; **GST at 18 % is shown separately** on the pricing page and the invoice, as for every plan. "Per student per year" below = monthly price × 12 ÷ students, AI bundle not included.

| Option | Monthly (ex-GST) | Per year | 300 students | 500 students | 1,000 students |
|---|---|---|---|---|---|
| Today: Shared (for comparison) | ₹4,999 | ₹59,988 | ₹199.96 | ₹119.98 | ₹59.99 |
| **A. Flat ₹2,499** | ₹2,499 | ₹29,988 | ₹99.96 | ₹59.98 | ₹29.99 |
| **B. Flat ₹2,999** | ₹2,999 | ₹35,988 | ₹119.96 | ₹71.98 | ₹35.99 |
| **C. Per student**: ₹1,999 a month including 300 active students, + ₹4 a month for each active student above 300 (the built `per_student` model) | ₹1,999 / ₹2,799 / ₹4,799 | ₹23,988 / ₹33,588 / ₹57,588 | ₹79.96 | ₹67.18 | ₹57.59 |

Notes for the owner:
- With the Lite bundle (₹699 a month, ₹8,388 a year) add ₹27.96, ₹16.78 and ₹8.39 per student per year at 300, 500 and 1,000 students.
- Option C passes the Shared price at about **1,050 active students** (₹1,999 + ₹4 × 750 = ₹4,999), so a large school would pay more for fewer modules. It needs a cap (for example "never more than the Shared monthly price") or a rule that schools above 1,000 students are offered Shared. A cap is not built: the invoice adds a per-student line without a ceiling.
- Option A at 300 students lands in the middle of the ERP band; option B keeps the ₹120 figure docs/18 quotes for Shared at 500 students, but for 300.
- **One-time fee (open question 3).** Import, reconciliation and the first readiness run are the same work for this tier as for Shared, so the "Implementation and data verification" effort does not shrink. Candidates: (i) the same ₹15,000; (ii) a lower ₹7,500 with the remaining ₹7,500 charged by an operator-added line if the school upgrades within a set period; (iii) a paid pilot credited to year one (docs/18 R4), with the fee waived by the existing equal `discount` line. Quotes always state migration, training and GST (docs/18 R4).

## Consequences

- Good: a school that keeps its ERP can buy the wedge at a price inside the published ERP band, which is the buyer docs/18 §1.9 describes; the upgrade is a plan change, so the entry tier feeds Shared instead of competing with it.
- Good: the commercial boundary becomes a control-plane setting enforced on reads and writes, not a school preference; the same mechanism lets future plans differ by module without new code.
- Good: plan `features`, documented since docs/16 v0.2 but never applied, finally does what it says.
- Bad / costs: it adds three flags, flag checks in messaging, academics and insights, plan-to-flag application with an audit event, a drift view and a catalogue migration; every new module must from now on decide which plans include it.
- Bad / costs: ADR-0015 and the BRD (§11) promise "both tiers have the same features"; that promise changes to "the same security, isolation and support; the entry plan has fewer modules". The pricing page (`apps/web/src/features/marketing/catalogue.ts`, `billing.yaml` → `public_pricing`, both pinned by tests) gains a third column.
- Bad / costs: a cheaper plan may pull schools down from Shared (cannibalisation); the module list in §2 is the guard, and its value depends on fees and messaging being things schools want from SchoolOS.
- Risk: certificates issued in SchoolOS beside an ERP that also prints TCs could give two serial series for one school (open question 6).
- Dedicated tier is unaffected: the entry plan is shared-only.

## Alternatives considered

| Option | Why not chosen (as the proposal) |
|---|---|
| Keep one plan and use the **negotiated price** (FR-PLT-013, built) for alongside schools | Works today with no code, but every quote is a negotiation, the public page cannot show it, and the school still gets fees and messaging it does not use; kept as the fallback if the owner rejects this ADR |
| A **per-student price for every plan** instead of a separate tier | Fixes the 300-student problem but cuts Shared's price for small full-office schools too, and does not tell an ERP school what it is buying; option C keeps per-student pricing inside the entry tier only |
| **A published discount on Shared** for schools in alongside mode | Ties price to a setting the school controls (it could take the discount and switch to `full`); enforcement would need the same flags anyway |
| **Module add-ons** (base records plan, then fees, messaging and academics each bought separately) | Most flexible, but multiplies catalogue rows, invoice lines and support questions before any school has paid; may follow later on the same plan-features mechanism |
| Enforce through **alongside mode alone** | The school sets the mode, hidden modules only block new set-up, and `fees_enabled` overrides it; not a commercial boundary |
| A new `tier` value (`entry`) beside `shared`/`dedicated` | `tier` means deployment shape (and drives provisioning, fleet and the dedicated host); mixing features into it would touch CHECKs and provisioning for no gain |

## Open questions for the product owner

1. Do you want to reopen **D8** and add an entry tier at all, or keep one Shared plan and use negotiated prices for ERP schools?
2. Is the module list in §2 right? In particular: should circulars → tasks stay in, and should certificates stay in when the school's ERP also prints certificates?
3. Which one-time fee: ₹15,000 as Shared, a lower fee with the difference due on upgrade, or a paid pilot credited to year one?
4. Which price shape: flat (A or B, or another figure) or per student (C, with a cap at the Shared price or a size limit)?
5. Should attendance and marks (and so early warning) be an add-on to this tier later, or only on the full plan?
6. When the ERP also issues transfer certificates, should SchoolOS be the only TC register for a school on this tier (one serial series), or may the tier switch certificate issuing off and keep only the check?
7. Should AI answers need a bundle (as proposed), or should the tier include a small Starter bundle in its price, knowing a 100-answer bundle costs about ₹576 a month in estimated model cost?
8. Is the tier shared-only (as proposed), or should a dedicated variant exist?
9. Annual billing: offer the tier annually (bundles cannot be added to annual plans yet, ADR-0038)?

## Follow-up work if accepted

1. Status-line edits on ADR-0015 and ADR-0038 ("Amended by ADR-0055"); BRD §11 and docs/18 §8 (D8 superseded by the new decision).
2. Catalogue migration: plan `records-readiness` (shared, monthly, chosen price and one-time fee), new versions of `shared` and `dedicated` with `features` set and prices unchanged; `tests/platform/test_catalogue.py` updated. Optional `ai-starter` bundle.
3. Control plane: apply plan `features` to per-school flags on activation and on a plan change taking effect (audited `flag.plan_applied`); flag drift on the school page; downgrade warning listing modules with data; docs/16 §5.6, §5.11, §16.
4. Tenant side: flags `messaging.enabled`, `academics.enabled`, `insights.enabled` with checks on every route of those modules (409 `module_hidden`); `_hidden_modules()` includes plan-off modules and the school switch cannot override them; Ask off without a bundle on this plan. Tests: route enumeration of each gated module, upgrade/downgrade keeps data, export still includes hidden-module data. FR-TEN-022 and a new FR-PLT requirement in docs/03.
5. If option C: a per-student cap (never above a set monthly amount) in invoice drafting, or a size rule at sale.
6. Web: pricing page third column (`catalogue.ts`, `billing.yaml` → `public_pricing`, both pinned), Plan & billing page lists modules not included with "Ask SchoolOS to upgrade"; English strings.
7. Sales: quote template stating migration, training and GST (docs/18 R4).

## Related requirements

FR-TEN-022, FR-FEE-001, FR-MSG-001, FR-KB-009, FR-KB-011, FR-PLT-010, FR-PLT-013, FR-PLT-015, FR-PLT-022, NFR-CST-001; BO-01, BO-06; docs/01 §7, §11; docs/02 §3; docs/16 §5.6, §5.7, §5.11; docs/18 §1, R4, §7 Q6, §8 D1 and D8; ADR-0015, ADR-0020, ADR-0038, ADR-0041, ADR-0042, ADR-0043.
