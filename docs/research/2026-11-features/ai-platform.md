# Feature research: AI and the SaaS platform (prefix FA)

| Field | Value |
|---|---|
| Date | 2026-11-09 |
| Area | (1) AI: Ask the school, document understanding and OCR, AI drafting, management summaries, data-quality help, evaluation gaps; (2) the SaaS business: onboarding speed, pricing and the entry tier (ADR-0055), dedicated tier, operator console, usage analytics without student data, sales and demo tooling, multi-school groups |
| Modules read | `app/knowledge` (config, tools, prompts, ingestion, gateway), `app/extraction`, `app/documents`, `app/insights`, `app/notifications`, `app/platform` (models, billing, usage, payments, dashboard), `apps/web/src/features/{marketing,platform,school}`, `evals/`, `deploy/dedicated/compose.yaml` |
| Status | Research only. Nothing here is decided or built. Each proposal names its BRD objective and user (docs/14 §5) |

Facts from the web were checked on 2026-11-09. Most AI prices come from Google's own pricing page for the Gemini API. The Vertex AI pricing page could not be read in full (the fetch was cut off), so any Vertex-specific figure is marked **unverified**.

## 1. What exists today (short inventory)

**AI**
- Ask the school runs a tool loop with server-side citation checks. The tools are a fixed whitelist in `apps/api/app/knowledge/config/tools.yaml`: `find_students`, `get_student_facts`, `get_value_history`, `count_students`, `list_findings`, `list_documents`, `search_documents`, `get_fee_dues`, `search_my_conversations` and `list_due_tasks`. Around the loop there are conversations, memory, verified answers, answer feedback (`POST /knowledge/queries/{id}/feedback`), a usage meter, and a budget that degrades to search-only.
- Model roles are set in `apps/api/app/knowledge/config/models.yaml`. Answer, notice and extraction use `gemini-3.5-flash`; the small roles use `gemini-3.5-flash-lite`. The file marks these choices as **provisional** until a live evaluation runs, which owner decision D2 (no cloud yet) blocks.
- Circular reading and parent-notice drafting are built (`app/circulars`, prompts `circular_reading.v2`, `parent_notice.v2`). The notice role is given circular passages or staff text only, never student records.
- **Scanned documents are not indexed.** `knowledge/ingestion/pdf.py:11-15` fails a scanned or mojibake page with `needs_ocr`, and `knowledge/ingestion/__init__.py:28` says "Not yet: OCR".
- **Register-photo extraction has no real provider.** `extraction/providers.py` contains only `FakeExtractionProvider` and `NotConfiguredProvider`. ADR-0033 lines 42 leaves open a product-owner question: how Aadhaar numbers are redacted in page images *before* they are sent to Gemini.
- **Embeddings and reranking have not been chosen.** `knowledge/config/embeddings.yaml` has `selected: null`, and its candidates are Voyage (a US provider) or a self-hosted model. Vertex embeddings are not among them. `gateway/rerank_voyage.py` is the only reranker.
- Early warning (`app/insights`) is rule-based (`insights/rules.yaml`), with no AI.
- The home page has four role views (office, accountant, teacher, principal). The owner gets the principal's view (`apps/web/src/features/school/HomeView.tsx:57-62`). There is no summary that reaches management outside the app.

**Evaluation (`evals/`)**
- There are 13 synthetic datasets. Hard and soft gates live in `evals/gates.toml`, covering leakage, injection, citations, refusals, circulars, fees, due tasks, contextual retrieval, conversations, English-first and authorised recall.
- The header of `gates.toml` says these are **not measured**: faithfulness, answer correctness and cost per answer. The latest report (`evals/reports/report.md`) ran the `app-fake` adapter, so no real model has ever been evaluated.
- **No register-extraction accuracy eval** exists, and notice drafting is checked only for English-first.

**Platform and business**
- The operator console has provisioning, plans with versioned catalogues, GST invoices, negotiated prices, AI bundles, flags, the fleet with signed heartbeats, support, incidents, break-glass, announcements, offboarding and a usage page.
- `platform.usage_daily` (`platform/models.py:283-301`) records active users, staff, active students, storage, documents, AI queries, tokens, cost and answers. It records **no feature-adoption counts**.
- **One billing account per school.** `platform.billing_accounts.tenant_id` is `NOT NULL UNIQUE` (`migrations/versions/0005_platform.py:101`).
- **One school per dedicated host** (`deploy/dedicated/compose.yaml:3`). The dedicated plan is ₹9,900 a month plus a ₹49,000 one-time fee (`platform/billing.yaml:70`).
- **SchoolOS's own invoices are paid manually only** (`platform/payments.py:1-5`), although ADR-0016 was accepted with Razorpay.
- Staff in several schools already have "Switch school" (`apps/web/src/app/[locale]/choose-school/page.tsx`), but there is no group or trust entity.
- The marketing site has home, features, pricing, security and about pages. Pricing promises "a demonstration on sample data" (`apps/web/messages/en.json:6539`), but `make seed-synthetic` refuses to run outside `local`/`ci`, so there is no demo environment to show.
- The 2026-11-08/09 audit rounds show no open findings in knowledge or platform. Every round-2 item was fixed (for example YK-01 and CP-02/03).

## 2. Proposals

### FA-01 · OCR for scanned documents and a real register-photo provider, with Aadhaar redaction before any image leaves

| | |
|---|---|
| Users | Office clerk (uploads circulars and evidence), principal and management (ask about circulars), exam coordinator |
| Problem and evidence | Government circulars from the DEO/MEO and board letters often reach schools as scans or phone photos. Today every such page fails with `needs_ocr` (`knowledge/ingestion/pdf.py:11-15, 92, 123`), so Ask cannot cite them, and circular reading and deadline capture get nothing. Register-photo onboarding (FR-IMP-021/024), a major time-to-value lever for the roughly 12,000 AP schools still on paper (docs/18 §1.1), has no live provider (`extraction/providers.py`: fake and not-configured only). PRV-016 (docs/08 line 89) promises to detect and black out Aadhaar numbers in uploaded images through OCR, but it runs only inside the register-extraction pipeline. A scanned evidence document uploaded to `documents` is not OCR-checked. ADR-0033 (line 42) leaves open how images are redacted before they go to Gemini. |
| Today | `extraction/imaging.py` redacts with OCR boxes once a provider returns spans. `core/redaction.find_aadhaar` exists. The gateway accepts images (`accepts_images: true` on the extraction role). |
| Proposal | (1) **Two-stage design**: an OCR step that returns word boxes runs first and stays inside our boundary or region. It feeds `find_aadhaar`, then black-box redaction, and only the redacted image or text goes to Gemini for structuring. This answers the open ADR-0033 question. The two candidates for the OCR step should be evaluated: Tesseract in the worker image (Telugu and English packs, no new sub-processor, weaker on handwriting) and Google Document AI Enterprise OCR ($1.50 per 1,000 pages, first 1,000 free, global price; [Google](https://cloud.google.com/document-ai/pricing), accessed 2026-11-09; whether asia-south1 serves it is **unverified**). (2) Scanned PDF and image documents go through the same OCR and redaction and are then indexed, with page images kept for citation previews (docs/06 §3 line 87). (3) A vision-structuring adapter for register photos (`extraction` role through the gateway); every row is still verified by a person (unchanged). (4) PRV-016 applies to every image or scanned upload in `documents`, not only register photos. (5) A per-page cost cap, with OCR pages counted in `usage_daily`. |
| BRD objective | BO-02 (time to find information), BO-07 (onboarding < 1 week), BO-05 (trust) |
| Invariants and compliance | Invariant 4 (redaction before any provider), invariant 5, invariant 13 (provider in config). **ADR needed** to choose the OCR provider and close the ADR-0033 follow-up. **Privacy review and DPIA update** are needed if Document AI is used (a new Google service and processing location). Tesseract adds no sub-processor. |
| Dependencies | D2 (cloud) for live Gemini; FA-07 (an extraction eval) to choose the provider |
| Effort | L (OCR step, redaction wiring for documents, adapter, evals) |
| Value | **H**. It unlocks real circulars for Ask and deadlines, and paper-register onboarding, which is the main way into budget schools. It also closes a privacy gap. |
| Risks | Handwritten Telugu OCR quality; cost on large registers; a missed Aadhaar number in an image (mitigation: discard the page when it cannot be redacted, as `imaging.py` already does) |
| Owner questions | Is a local Tesseract step acceptable for redaction even if it is less accurate than Document AI? May an image ever go to Gemini when no number was detected but the page is low-confidence? |

### FA-02 · AI unit economics: measure cost per answer, route to cheaper models, and price bundles above cost

| | |
|---|---|
| Users | SchoolOS owner (margin), billing admin, principal (sees answers, not tokens) |
| Problem and evidence | The bundles sell answers below the estimated model cost. `models.yaml` estimates `cost_per_answer_usd: 0.06`, which is about ₹5.76 at 95.97. The published bundles are Lite at ₹699 for 300 answers (₹2.33 an answer), Standard at ₹1,499 for 1,000 (₹1.50) and High at ₹3,499 for 3,000 (₹1.17), and an extra answer costs ₹1.50 (`platform/billing.yaml:72-74`). ADR-0055 §4 itself says any 100-answer bundle under about ₹576 is below cost. So the more a school uses Ask, the more SchoolOS loses. **Prices have changed since `prices_checked_on: 2026-09-30`.** Google's Gemini API page lists Gemini 3.6 and 3.8 Flash at **$0.75 input / $3.75 output per 1M tokens until 31 Dec 2026, then $1.50 / $7.50**. Gemini 3.1 Flash-Lite is $0.25 / $1.50 and 3.5 Flash-Lite $0.30 / $2.50. Batch is half price ([Gemini API pricing](https://aistudio.google.com/docs/pricing), accessed 2026-11-09). Third-party trackers list 3.5 Flash on Vertex at $1.50 / $9.00 (regional $1.65 / $9.90, the figure in `models.yaml`); I could not confirm this on Google's own Vertex page. |
| Today | Gateway metering, budgets, `usage_daily.ai_cost_usd`, the `router` role (configured, "enable only if evals show no loss"), and explicit caching of static prompts |
| Proposal | (1) A **cost-per-answer gate** in `evals/gates.toml` (a soft gate first, then a release gate) using real token counts from `kb.llm_calls` on live runs. (2) **Evaluate cheaper answer models** in the live run: 3.6/3.8 Flash (its output costs less than 3.5 Flash's) and 3.1 Flash-Lite for simple lookups. **Rough estimate** at the `models.yaml` volumes (2 calls of about 10k input and 800 output tokens each): 3.5 Flash is about $0.049, 3.8 Flash at the post-January price about $0.042, and 3.1 Flash-Lite about $0.007, plus about $0.004 for the small roles. Only Flash-Lite brings an answer below the ₹1.17-2.33 that bundles charge. (3) **Turn on the router** for questions that a single tool call answers (counts, one student's field, due tasks), so they go to Flash-Lite. (4) Cut the input: reduce `tool_result_context_tokens` (12k) if recall holds. (5) Re-run ADR-0038's cost basis and give the owner a bundle repricing (or a higher extra-answer price) once real cost is known. (6) Use batch pricing only for offline jobs (contextual chunks, the eval judge), and only if batch staging fits ZDR (**unverified**). |
| BRD objective | BO-06 (willingness to pay, margins), NFR-CST-001 |
| Invariants and compliance | Invariant 13 (models and prices in config; a change needs `make eval`), invariant 8 (a cheaper model must still pass every hard citation and leakage gate). No new sub-processor. |
| Dependencies | D2 (live credentials); FA-07 |
| Effort | M |
| Value | **H**. The current catalogue loses money per answer. The introductory price ends on **31 Dec 2026**, so budgets must use the January 2027 price. |
| Risks | A cheaper model could cite worse, which the gates must catch. Repricing bundles that are already published needs a new catalogue version, because prices are frozen. |
| Owner questions | Reprice the bundles, or accept a loss on AI as a sales cost for the first pilots? Is "answers from a fast model for simple questions" acceptable to promise? |

### FA-03 · Embeddings and reranking on Vertex AI as an ADR-0006 candidate (one AI sub-processor)

| | |
|---|---|
| Users | Everyone who uses Ask (search quality); the SchoolOS owner (sub-processor list, DPA) |
| Problem and evidence | Live Ask cannot start: `embeddings.yaml` says `selected: null` and refuses live mode. The only network candidate is Voyage, a US API and a second AI sub-processor for every school's chunk text. The other candidate, self-hosted bge-m3 or e5, has no provider implementation. ADR-0006 was written when Anthropic was the LLM, which has no embeddings model (ADR-0006 line 12). Since ADR-0033 moved everything to Gemini on Vertex, Google's `gemini-embedding-001` ($0.15 per 1M tokens) and Gemini Embedding 2 ($0.20 per 1M text tokens) are obvious candidates ([Gemini API pricing](https://aistudio.google.com/docs/pricing), accessed 2026-11-09). Google's locations page lists both in its Asia Pacific table, but the availability cell for Mumbai did not render, so **asia-south1 availability is unverified**. The page also says endpoints "don't guarantee data residency or in-region ML processing", which needs checking against docs/08 ([Vertex locations](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/locations), accessed 2026-11-09). |
| Today | The `TenantEmbedder` interface, caching and batching, Voyage embeddings and rerank adapters, and the contextual retrieval eval |
| Proposal | (1) Add a `vertex` embeddings candidate to `embeddings.yaml` and a gateway adapter using the same service identity as the LLM. (2) Run the ADR-0006 Recall@10 evaluation (English, Telugu and code-mixed) on Vertex, Voyage and bge-m3, including the 512-dimension rule. (3) Also evaluate the Vertex ranking API or an LLM rerank as a replacement for Voyage rerank (**unverified** availability). (4) Amend ADR-0006 with the residency finding. |
| BRD objective | BO-02, BO-05 |
| Invariants and compliance | Invariants 8 and 13. **ADR-0006 amendment.** Choosing Vertex removes a planned sub-processor rather than adding one; the privacy review is simpler. |
| Dependencies | D2; FA-07 |
| Effort | M |
| Value | M-H. It is a go-live blocker for Ask, and it shortens the sub-processor list that DPDP processor terms must name (ADR-0048). |
| Risks | Telugu recall may favour Voyage, in which case the evaluation decides; a re-embedding migration if the dimensions change |
| Owner questions | Prefer one AI vendor (Google) even at a small recall loss? Where is the threshold? |

### FA-04 · Ask tools for readiness, attendance and certificates (counts and one-student facts only)

| | |
|---|---|
| Users | Principal, management/correspondent, office clerk, exam coordinator |
| Problem and evidence | BRD P6 says "management asks for numbers compiled by hand" (docs/01 line 42), and BO-02's target is under a minute per answer. The questions principals actually ask, such as "How many Class 10 students are SSC-ready?", "Which sections had attendance below 75% this week?" and "Was a TC issued to this student, and when?", have no tool. The whitelist (`tools.yaml`) covers student fields, counts by class and gender, findings, documents, fee dues, conversations and due tasks only. Readiness (`GET /dq/readiness`), attendance (`academics`) and certificates exist in their modules. |
| Today | `count_students` with small-cell suppression; `list_findings`; the readiness overview endpoint; the certificate register |
| Proposal | (1) `get_readiness_summary`: per profile and class, ready versus not ready, with blocker counts by fix owner (counts only; names through `list_findings` under the existing scopes). (2) `get_attendance_summary`: per class and section rates for a date range, under the caller's section scope, with no per-child lists in aggregate mode. (3) `get_certificates`: certificates issued to one student the caller can see (type, serial, date, status). (4) Eval datasets and hard leakage gates for each tool, in the same style as `fees.jsonl` and `due.jsonl`. (5) Keep early-warning flags **out** of Ask (invariant 14; insights stay in their purpose-limited screens). |
| BRD objective | BO-02, BO-01 (readiness visibility) |
| Invariants and compliance | Invariants 3, 8, 9 (read-only) and 14. **ADR-0008 amendment** for each new tool (as D74 did). Answers stay counts plus citations. |
| Dependencies | FA-07 for the new eval sets |
| Effort | M (three tools plus evals) |
| Value | M-H. It turns Ask from a document search into the "numbers for management" tool that the buyer feels. |
| Risks | Attendance counts by small group could re-identify a child; reuse the `small_cell_min` rule |
| Owner questions | Should attendance figures be available in Ask to class teachers for their own sections only, or to principals only? |

### FA-05 · Management weekly brief for the owner or correspondent (counts first, AI narrative optional)

| | |
|---|---|
| Users | Owner/correspondent (the economic buyer, docs/01 line 78), principal |
| Problem and evidence | The person who renews the subscription seldom logs in. The owner sees the principal's home view (`HomeView.tsx:62`). There is no digest, so the value the clerk creates (findings fixed, corrections approved, certificates issued, readiness progress, fee collection) is invisible to the buyer. BO-06 (willingness to pay) depends on the buyer seeing that value. E-mail already exists (SES, `notifications/email.py`, used for invitations and D70 alerts). |
| Today | Home KPIs, the readiness overview, fee daily reports, portal-calendar tasks, in-app notifications and 100% quota e-mails |
| Proposal | (1) A weekly in-app brief and optional e-mail to the owner and principal, built only from **counts** that existing services return: readiness by board ("142 of 160 ready, up 18"), open findings by fix owner, change requests waiting, certificates issued, deadlines in the next 14 days, fee collected and due (when fees are on), and Ask usage against the bundle. (2) **No names, no child-level data, no early-warning detail** in the e-mail; it carries counts and links that need sign-in. (3) A printable A4 monthly version for management meetings. (4) Later and optional: a two-sentence AI summary of the same counts, with each figure cited to its count (invariant 8). Ship without AI first. (5) Per-user opt-in and opt-out; the template lives in `notifications/email_templates.yaml`. |
| BRD objective | BO-06, BO-01; user: management |
| Invariants and compliance | Invariant 5 (no PII in e-mail bodies or logs), invariant 14 (aggregates only; insights counts excluded or shown only as "flags open: n" in-app). No new data class. |
| Dependencies | None for the counts version; FA-04 helps |
| Effort | S-M |
| Value | **H** for retention and renewals, at low cost |
| Risks | E-mail fatigue; numbers that disagree with screens (reuse the same service calls) |
| Owner questions | E-mail weekly by default, or opt-in? Should the brief also go on WhatsApp to the owner (it is staff messaging, not parent messaging, so outside ADR-0043's template scope)? |

### FA-06 · Knowledge gaps: turn "not found" and "not helpful" answers into verified answers and missing documents

| | |
|---|---|
| Users | Principal or office admin (holders of `kb.verified_answer.manage`) |
| Problem and evidence | Ask records feedback (`knowledge/api.py:213`) and outcomes (`kb.queries` statuses), and verified answers exist (`/knowledge/verified-answers`). Nothing shows an admin which questions keep failing, so the library never improves after setup. This is a common reason internal Q&A tools are abandoned (**hypothesis, not measured**). |
| Today | Feedback with reason codes, verified-answer review and retire, the 180-day encrypted query log |
| Proposal | (1) A "Questions we could not answer" report: counts of `not_found`, search-only and "not helpful" outcomes, grouped by topic or document type (topic from the metadata role over the **question only**), with no asker identity. (2) For each group: "Write a verified answer" or "Upload the missing document". (3) Question texts shown only after redacting names and numbers (`redact()`), or only as topic labels if the privacy review prefers. (4) A monthly count of gaps closed. |
| BRD objective | BO-02 |
| Invariants and compliance | Showing other users' questions to an admin is a new use of the query log, so a **privacy review is needed** (docs/08 §7 purpose of `kb.queries`). Invariant 5. |
| Dependencies | None |
| Effort | M |
| Value | M |
| Risks | Questions that hold personal data (mitigation: topic-only mode) |
| Owner questions | Topic labels only, or redacted question text? |

### FA-07 · Close the evaluation gaps before any live AI

| | |
|---|---|
| Users | SchoolOS engineering (release gate); indirectly every Ask user |
| Problem and evidence | `evals/gates.toml` lines 6-7 list faithfulness ≥ 0.95, answer correctness ≥ 0.85 and cost per answer as "not measured yet". Every report so far is the `app-fake` adapter (`evals/reports/report.md`). There is no extraction accuracy eval, although extraction is the "hardest reading task" (`models.yaml`, extraction role comment). Notice drafting is checked only for English-first, with no faithfulness-to-circular or "no student data" check. ADR-0033 decision 7 says each role gets the cheapest model that passes every hard gate, which is impossible without these gates. |
| Today | A strong synthetic harness with oracle-based leakage, citation and refusal gates, and the `eval_judge` role configured (`gemini-3.1-pro-preview`, global, offline only) |
| Proposal | (1) Calibrate the LLM judge on a hand-labelled synthetic set (agreement threshold), then turn on faithfulness and correctness as soft gates. (2) A **register-extraction eval**: synthetic register page images (printed and handwritten, English and Telugu, with planted Verhoeff-valid numbers) scored by field accuracy, row recall and **Aadhaar redaction recall (hard gate = 1.0)**. (3) A **notice-draft eval**: every date, amount and place in a draft must appear in the source circular (hard), and no student name ever (hard). (4) A **cost-per-answer gate** (FA-02). (5) A scanned-circular OCR set for FA-01. (6) A short runbook for the first `make eval-live` once D2 lifts. |
| BRD objective | BO-05, BO-02 |
| Invariants and compliance | Invariants 8, 4, 11 (synthetic only) and 13. No ADR; the gates follow docs/06 §13. |
| Dependencies | None for building; D2 for live runs |
| Effort | M |
| Value | **H**. It is the precondition for FA-01, FA-02 and FA-03 and for go-live. |
| Risks | Judge bias within the same model family (ADR-0033 notes it); synthetic handwriting may not resemble real registers |
| Owner questions | None blocking |

### FA-08 · Fix-owner letters from findings (templates, not AI)

| | |
|---|---|
| Users | Office clerk, principal, parent |
| Problem and evidence | Each readiness mismatch names who must fix it: the parent (Aadhaar centre), the school via the MEO/MIS coordinator (UDISE+), or the register (change request) (docs/18 R1.1). The parent slip exists, but the clerk still writes the covering letter to the MEO or MIS coordinator listing UDISE+ corrections by hand, under a three-day window (docs/18 §1.3). The brief asks about "AI-drafted letters". For an official letter whose content is a list of records, a **template filled from findings** is more accurate and auditable than an LLM. |
| Today | Findings with fix owner, the parent verification slip PDF, pre-check sheets, PDF rendering in workers |
| Proposal | (1) A "Letter to MEO/MIS coordinator" PDF: school header, a table of students (admission number, PEN, field, register value, UDISE+ value) for findings owned by "school via UDISE+". (2) A bulk "letters to parents" print for fix owner "parent". (3) Templates versioned like export profiles, `verified: false` until a school confirms (D3). (4) Generation audited as an export (invariant 7). **No AI.** |
| BRD objective | BO-01 |
| Invariants and compliance | Invariant 6 (nothing auto-corrected), invariant 7 (export audit), C3 masking rules |
| Dependencies | None |
| Effort | S |
| Value | M (saves clerk time inside the SSC window) |
| Risks | The format differs by mandal or district; keep it plain |
| Owner questions | Is there an expected MEO format in the pilot district? |

### FA-09 · School groups (trusts and societies running several schools)

| | |
|---|---|
| Users | Correspondent or secretary of a trust, group accountant, SchoolOS billing admin |
| Problem and evidence | The target market includes "small groups of 2-10 campuses" (docs/research/2026-10-market/report.md line 85), and BRD §11 names "groups" as dedicated-tier buyers. The model supports none of it. A billing account belongs to exactly one school (`0005_platform.py:101`, `tenant_id ... UNIQUE`), so a trust gets one invoice, one one-time fee and one subscription per school, with no group price. A dedicated host serves one school (`deploy/dedicated/compose.yaml:3`), so five schools mean five hosts at ₹9,900 plus ₹49,000 each. The only group feature is "Switch school" for people with memberships in several schools. There is no group view of totals. |
| Today | Per-school tenants, multi-school memberships with a school picker, negotiated price per subscription |
| Proposal | (1) **A group billing account** in `platform`: one legal entity (GSTIN or PAN) that pays for several schools, with a consolidated invoice showing one line per school, an optional group discount as a catalogue rule, and the one-time fee per school or a reduced fee for later schools. (2) **A group overview for people who are members of each school**: a web page that calls each school's existing summary endpoints **under that user's own membership in each school** and shows counts side by side (readiness, findings, fee totals). No cross-tenant SQL, no definer function and no platform read of tenant data. (3) **Group circulars**: upload once and copy into each selected school's document library (each copy then belongs to that school). (4) Later: a **group dedicated host** running several tenants of one trust (RLS unchanged), priced per host plus per school. (5) **Not proposed**: one student record across schools. A transfer between group schools stays a TC plus transfer-in by PEN. |
| BRD objective | BO-06, BO-07; user: management of a group |
| Invariants and compliance | Invariant 1 (aggregation in the BFF from per-school calls, never a cross-tenant query), invariant 14 (counts only, each school's own data, no cross-school analytics on identifiable data). ADR-0020 boundary unchanged for (1). **ADR needed** for the group entity and the group dedicated host (which amends ADR-0015 "one-tenant install"). Privacy review: under DPDP the trust may be the single data fiduciary for all its schools, which affects the DPA template (ADR-0048). |
| Dependencies | FA-10 if group pricing is per tier |
| Effort | L (billing entity and invoices M; overview M; group host L) |
| Value | **H**. Groups are the larger deals and the natural reference customers. |
| Risks | Scope creep towards "group ERP"; a correspondent who is not a member of each school (they must be invited to each) |
| Owner questions | Who signs in a trust: is the trust or each school the data fiduciary? Group discount: percentage per extra school or a flat band? Is a group dedicated host wanted before any single-school dedicated customer exists? |

### FA-10 · Decide the entry tier (ADR-0055) with costed AI

| | |
|---|---|
| Users | Owner of a school that keeps its ERP; SchoolOS owner and billing admin |
| Problem and evidence | ADR-0055 is Proposed and reopens D8. Shared costs ₹199.96 per student per year at 300 students (ADR-0055 table), at the top of the ₹50-200 ERP band, for a product such a school uses *beside* its ERP. The design is complete: plan `features` become per-school flags, three new flags, a drift view and a catalogue migration. Its question 7 (a Starter AI bundle) depends on real AI cost (FA-02). |
| Today | Per-student pricing (`plans.pricing_model = per_student`), negotiated price, alongside mode, the flags for fees, Tally and Ask |
| Proposal | My recommendation for the owner to weigh: (1) **accept ADR-0055 with option C** (₹1,999 a month including 300 students plus ₹4 for each student above), **with a cap at the Shared price**; the cap needs building, because the invoice line has no ceiling today. (2) A one-time fee of ₹7,500, with the balance due on upgrade within 12 months (ADR-0055 option ii). (3) AI only with a bundle, and a Starter bundle priced at or above measured cost (FA-02), not ₹576 or less. (4) Build the ADR's follow-up list as written (plan features to flags, flags in messaging, academics and insights, the drift view). |
| BRD objective | BO-06 |
| Invariants and compliance | ADR-0055 (amends ADR-0015 "same features" and ADR-0038); the pricing page is pinned by tests |
| Dependencies | **Owner decision**; FA-02 for the bundle price |
| Effort | M (as listed in ADR-0055) |
| Value | **H**. It opens the "keeps its ERP" segment that docs/18 §1.9 calls the buyer, before the Feb-May 2027 buying window. |
| Risks | Cannibalisation of Shared (ADR-0055 consequences) |
| Owner questions | ADR-0055 questions 1-9; above all 1, 4 and 7 |

### FA-11 · Time to first value: an onboarding tracker and adoption health, without student data

| | |
|---|---|
| Users | SchoolOS operator (onboarding and support), office clerk, principal |
| Problem and evidence | BO-07 targets fewer than 5 schools at under one week of effort each, and M7's "self-serve onboarding wizard" is not built (docs/14 M7). The school side shows "getting started" only until the first import (`HomeView.tsx:618-621`). The operator side records usage volumes (`usage_daily`) but not milestones, so an operator cannot see that a school imported students but never ran readiness, or never issued a certificate. That is exactly how a pilot stalls and does not convert (BO-06). |
| Today | Import templates and presets (R1), alongside mode, staff guides (D76), the usage table, plan-limit tiles |
| Proposal | (1) **School milestones** computed in the school's own session as booleans or counts only: owner accepted, staff invited, first import, students over N, first readiness run, first finding resolved, first change approved, first certificate issued, first Ask answer, documents over N. They are collected by the existing daily usage collector into a new `platform.usage_milestones` table (counts and dates, no student data). (2) **An operator onboarding board**: schools by stage with days in stage, and a "stuck" flag after N days. (3) **A guided first-week checklist** in the school app for the clerk and principal, driven by the same milestones and kept until the first readiness report exists, not just the first import. (4) **Adoption health**: weekly active staff, actions per week and milestones, shown as a renewal-risk list for the operator. (5) Target time: from the signed DPA to the first readiness report in two working days. |
| BRD objective | BO-07, BO-06 |
| Invariants and compliance | ADR-0020 (the control plane receives counts only; the boundary test `tests/platform/test_boundaries.py` must list the new collector reads), invariant 5. **ADR-0020 amendment** for the extra counted relations. No student data. |
| Dependencies | None |
| Effort | M |
| Value | **H**. It directly drives pilot conversion. |
| Risks | Milestones that look like surveillance of staff (keep them school-level only, never per user) |
| Owner questions | Which single milestone is the "first value" promise in sales: the first readiness report? |

### FA-12 · Demo school and sales kit for the February 2027 window

| | |
|---|---|
| Users | SchoolOS owner (selling), association partners, the prospect's owner and principal |
| Problem and evidence | The pricing page offers "a demonstration on sample data" (`messages/en.json:6539`), but `make seed-synthetic` refuses outside `local`/`ci` (CLAUDE.md §5), and with D2 there is no staging. A solo founder demos from a laptop. The buying calendar is fixed: budgets in Feb-Mar, decisions in Apr-May (docs/18 §1.9), with the next SSC window likely February 2027 (docs/18 §1.3). |
| Today | Marketing pages (home, features, pricing, security, about), plan cards, a WhatsApp contact link; synthetic generator; e2e fixtures |
| Proposal | (1) **A demo deployment** (`SOS_ENV=demo`, its own host or the dedicated compose) with a synthetic AP school (SSC, UDISE+ and APAAR mismatches planted), reset nightly, read-mostly demo roles, AI on the fake provider or a capped real budget. It must be refused anywhere real data lives. (2) **A 10-minute demo script** following the wedge: import, readiness, fix owner, parent slip, change with evidence, cited Ask answer. (3) **A "Board readiness" landing page** for SSC 2027, APAAR and UDISE+, plus a DPDP page (what the school must do by May 2027, what SchoolOS gives), both linking to the security page. (4) **A price calculator** on /pricing (students to per-student-per-year cost, GST shown), reading `catalogue.ts`. (5) Association "compliance clinic" handouts (docs/18 §1.9 hypothesis). |
| BRD objective | BO-06 |
| Invariants and compliance | Invariant 11 (synthetic only; the demo env must start refusing any import whose source is not the generator, or carry a large "demo data" banner and a no-upload rule). **An ADR is needed** for a new `SOS_ENV` value and its guards. |
| Dependencies | Hosting money (D2 constrains this; a single small host) |
| Effort | M |
| Value | **H** per rupee spent, if the owner sells in Feb-May 2027 |
| Risks | Someone uploads real data to the demo (mitigation: uploads off, imports from the generator only) |
| Owner questions | Is a small paid host for the demo acceptable before the D2 cloud decision? |

### FA-13 · Online and recurring payment of SchoolOS's own invoices

| | |
|---|---|
| Users | School owner or accountant (pays SchoolOS), SchoolOS billing admin |
| Problem and evidence | ADR-0016 was accepted with Razorpay (docs/14 M7), but `platform/payments.py:1-5` has only the `manual` provider: a billing admin records every bank transfer by hand, and dunning (`past_due_since`, `grace_ends_on`) depends on it. Shared costs ₹4,999 plus 18% GST = ₹5,898.82 a month, under the ₹15,000 per-transaction limit up to which RBI's e-mandate rules allow recurring debits without extra authentication ([RBI circular RBI/2022-23/73](https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=12341), accessed 2026-11-09). A news report of April 2026 mentions consolidated e-mandate directions, which I could not open (**unverified**). |
| Today | GST invoices and PDFs, manual payments with reversal, grace and suspension rules (never in exam windows) |
| Proposal | (1) A Razorpay payment link on each issued invoice (SchoolOS is the merchant for its own invoices). (2) An optional UPI Autopay or e-mandate for the monthly plan. (3) A signed webhook that marks the invoice paid, with re-fetch before marking, as in ADR-0042. (4) Reminder e-mails before the due date. |
| BRD objective | BO-06 |
| Invariants and compliance | No student data. Razorpay becomes a processor of school billing contacts, so the **privacy review** is light (operator data only). ADR-0016 already accepted. |
| Dependencies | A Razorpay account for SchoolOS (D2-like spend and KYC) |
| Effort | M |
| Value | M (less billing-admin time, fewer accidental past-dues) |
| Risks | Failed mandates; refunds |
| Owner questions | Offer Autopay at launch, or links only? |

## 3. Ranked shortlist (value per effort)

| Rank | Id | Why first |
|---|---|---|
| 1 | **FA-02** AI unit economics | Every AI answer sold today is priced below estimated model cost; the cheaper introductory Flash prices end 31 Dec 2026; M effort |
| 2 | **FA-05** Management weekly brief | S-M effort, shows the buyer the clerk's work, no new data class |
| 3 | **FA-11** Onboarding tracker and adoption health | Drives pilot conversion (BO-06, BO-07); counts only |
| 4 | **FA-07** Evaluation gaps | Precondition for any live AI (FA-01..03); buildable now without cloud |
| 5 | **FA-10** Entry tier decision | The design is done; it needs the owner's decision before the Feb-May 2027 buying window |

Next after these: FA-12 (demo kit), FA-01 (OCR, the highest single value but L and gated on D2), FA-09 (groups, L), FA-04, FA-03, FA-08, FA-06, FA-13.

## 4. Not worth building (now)

| Idea | Why not |
|---|---|
| AI-written report-card remarks, lesson plans or generic content | docs/18 §1.8: teachers already use free AI; not sellable. It also moves away from the "every answer shows its record" promise |
| AI that fixes data-quality findings or proposes corrected values to apply | Invariant 6 and BR-03. The deterministic diff with a fix owner (R1) is already more trustworthy. At most, show the diff, which it already does |
| AI name-matching "probably the same person" suggestions | The rules-based name matching in `app/dq` is explainable and auditable; an LLM adds cost and an unexplainable score to an official record for little gain |
| Free-form AI letters to authorities | Official letters are lists of records; templates (FA-08) are exact and auditable. AI drafting stays limited to parent notices from circulars, as built |
| A parent-facing AI chatbot | Parent logins and two-way chat are out of scope (BRD §7.3); a chatbot answering parents would need parent identity and a new purpose |
| AI risk prediction for children beyond the published rules | Invariant 14 and DPDP children's-data limits; the M5 rules are explainable and capped by the principal |
| Cross-school benchmarking ("your school vs similar schools") | Cross-tenant analytics; invariant 14 and ADR-0020. Even anonymised, it needs a new purpose and consent basis |
| Self-serve signup with card payment | The market is relationship-sold (docs/18 §1.9); every school needs a signed DPA and data-handling permission (D81) before real data. A self-serve trial adds risk without much reach |
| Fine-tuning or training a model on school data | ZDR posture, DPDP purpose limits, and no evidence that it beats retrieval |
| A separate vector database | CLAUDE.md §11; pgvector suffices at this scale |
| Labelling AI text under the 2026 IT Rules amendment | The amendment (in force 20 Feb 2026) covers synthetic audio and visual content; text is outside it according to legal analyses ([Business Today](https://www.businesstoday.in/technology/story/govt-tightens-rules-on-ai-generated-content-mandates-labels-and-faster-takedowns-515505-2026-02-10), accessed 2026-11-09). The existing "AI draft, staff confirms" labelling is enough; no new work |

## 5. Time-sensitive dates found

- **31 Dec 2026**: the introductory Gemini 3.6 and 3.8 Flash prices ($0.75 / $3.75) double to $1.50 / $7.50 on 1 Jan 2027 ([Gemini API pricing](https://aistudio.google.com/docs/pricing)). Budget with the 2027 price.
- **1 Dec 2026**: on the Gemini API, requests to Gemini 2.5 Flash are routed to 3.5 Flash-Lite (same page). SchoolOS already avoids 2.5; Vertex retired 2.5 Flash on 2026-10-16 according to `models.yaml`.
- **About 13-14 Nov 2026**: the DPDP consent-manager phase starts; **about 13 May 2027**: the main DPDP duties apply. Sources differ by a day ([Taxmann](https://www.taxmann.com/post/blog/analysis-indias-dpdp-act-and-rules/), [Mondaq](https://www.mondaq.com/india/data-protection/1708164/digital-personal-data-protection-rules-2025-notified)); check the gazette G.S.R. 843(E). The consent-manager phase does not directly bind SchoolOS.
- **February 2027**: the likely AP SSC correction window. **Feb-May 2027**: the school buying window (docs/18 §1.3, §1.9). These matter for FA-10 and FA-12.

## 6. Claims I could not verify

- Gemini 3.5 Flash Vertex prices ($1.50 / $9.00 global, $1.65 / $9.90 regional) come from third-party trackers and `models.yaml`, not Google's Vertex page.
- Whether Gemini 3.6/3.8 Flash, the Gemini embedding models, the Vertex ranking API or Document AI are served from asia-south1.
- Whether Vertex batch prediction is compatible with the ZDR setup.
- The April 2026 consolidated RBI e-mandate directions.
- That schools abandon Q&A tools whose gaps are never filled (FA-06): a hypothesis only.

## Sources (accessed 2026-11-09)

- Gemini API pricing: https://aistudio.google.com/docs/pricing
- Vertex AI locations: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/locations
- Third-party Vertex price listings for 3.5 Flash: https://www.llmreference.com/model/gemini-3.5-flash/gcp-vertex-ai , https://www.getmaxim.ai/bifrost/llm-cost-calculator/provider/vertex_ai/model/gemini-3.5-flash
- Document AI pricing: https://cloud.google.com/document-ai/pricing
- RBI e-mandate limit (2022): https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=12341 ; April 2026 report (unverified): https://ianslive.in/rbi-tightens-e-mandate-rules-makes-extra-authentication-mandatory-for-recurring-payments-above-rs-15000--20260421192243
- DPDP Rules timeline: https://www.taxmann.com/post/blog/analysis-indias-dpdp-act-and-rules/ ; https://www.mondaq.com/india/data-protection/1708164/digital-personal-data-protection-rules-2025-notified
- IT Rules amendment 2026: https://www.businesstoday.in/technology/story/govt-tightens-rules-on-ai-generated-content-mandates-labels-and-faster-takedowns-515505-2026-02-10
