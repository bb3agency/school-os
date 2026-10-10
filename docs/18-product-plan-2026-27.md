# 18. Product plan 2026-27: from market research to build order

| Field | Value |
|---|---|
| Status | **Accepted by the product owner 2026-10-10** (decisions in §8). Items move into `docs/14-roadmap.md`, the PRD and ADRs as each release starts. |
| Date | 2026-10-10 |
| Evidence | `docs/research/2026-10-market/report.md` (synthesis) and `docs/research/2026-10-market/notes/*.md` (sourced notes). Most figures there are secondary sources; estimates are marked as such. |

## 1. What the research says, in one page

1. **The market is many schools and little money.** About 15,700 AP private schools (37-44 lakh children, about 55% of AP enrolment). Published ERP prices cluster at ₹50-200 per student per year; only about 20% of Indian institutions pay for campus software. Roughly 12,000 AP private schools still run on registers, Excel and WhatsApp (estimate).
2. **The checklist ERP is commoditised.** Fees, attendance, parent apps, transport and payroll are sold by everyone and given away by Teachmint (whose FY25 revenue was all hardware). The Hyderabad incumbent MyClassboard has no AI features found and a parent app rated 2.4/5.
3. **The sharp, dated pain is government-portal data.** UDISE+ feeds APAAR, APAAR is mandatory for CBSE registration from 2026-27, and for March 2026 the AP SSC board took Class 10 nominal rolls straight from UDISE+, accepting corrections only where they matched Aadhaar (3,497 schools, 9,986 students, a three-day window). The next window is likely **February 2027**.
4. **Nobody found offers what SchoolOS already has at its core:** cross-source reconciliation (admission register / Aadhaar-as-printed / UDISE+ / board), maker-checker identity changes with evidence, and cited answers over the school's own records and documents. (Absence in public evidence, not proof.)
5. **DPDP's main duties start about 13-14 May 2027.** The school is the data fiduciary and SchoolOS the processor; the education exemption covers educational activities and child safety only. A school choosing software for 2027-28 is choosing its compliance partner.
6. **Money must never pass through SchoolOS.** RBI's Payment Aggregator Directions (15 Sep 2025) require a licence to hold merchants' funds. The school is the merchant of a licensed aggregator; SchoolOS only makes links and reconciles. Budget schools prefer cash and their own free UPI QR to a 2% gateway fee.
7. **WhatsApp is cheap and parents are on it.** Utility templates cost about ₹0.115 + GST each (about ₹800 a month for a 1,000-student school at 6 messages per child). Indian WhatsApp business accounts must move to INR billing by 31 Dec 2026. SMS needs TRAI DLT registration.
8. **AI must be trustworthy, not generic.** Teachers already use free AI for lesson plans; parents and teachers worry about wrong answers. "Every answer shows the record it came from" is the selling point; generic content generation is not sellable.
9. **Buyers and timing.** Independent schools and small groups (300-1,500 students); the owner or correspondent signs, the principal gatekeeps, the clerk can veto by not using it. Budgets are set in Feb-Mar, decisions in Apr-May and onboarding in Jun-Jul. Large chains (Sri Chaitanya, Narayana) build their own. Management associations (APPUSMA) are the likely trust channel. Local-language support is named as a tier-2 blocker.

**Positioning (proposed):** *"Your students' records pass UDISE+, APAAR and the SSC/CBSE board the first time, and every change is proven."* The rest of the office follows that wedge; SchoolOS can run alongside an existing ERP from day one.

## 2. Where SchoolOS stands today (2026-10-10)

Built and merged to `main` (66c8c58f): tenancy, identity (OIDC, MFA, step-up), authz with scopes, hash-chained audit, students with per-source values, imports with 24-hour revert, register-photo extraction (fake provider only), DQ rules DQ-001..012, maker-checker changes, pre-check exports, documents, "Ask the school" (knowledge), certificates and registers, circulars → tasks → notices (copy/download only), academics (daily attendance, marks), insights (early warning), read-only Tally connector, notifications (in-app), admin export and retention, break-glass, and the control plane (provisioning, plans, GST invoices, fleet, support). Security audit waves 1-6 are closed.

Not live: nothing is deployed to AWS; CI has never run on GitHub; the §3 pilot-ready gate in `docs/14-roadmap.md` has no item ticked. `docs/14-roadmap.md` still says M3-M6 sit on unmerged branches; that is stale (they are on `main`) and is corrected when this plan is accepted.

Gaps against the research (from the code inventory):

| Topic | Status | Note |
|---|---|---|
| UDISE+ export | Partial | `app/dq/config/profiles/udise-plus.yaml` has placeholder fields |
| APAAR | Partial | Stored, searchable, DQ-checked (ADR-0037); no consent register, no failure list |
| AP SSC nominal roll (BSEAP) | Absent | Only a synthetic board in seed data |
| CBSE registration | Absent | Only a CISCE profile exists |
| Transfer-in by PEN / duplicate guard | Absent | |
| Official AP TC format, public QR verification | Absent | M3 follow-ups |
| Fee ledger / receipts / collection | Absent (Tally read-only) | BRD §7.3 non-goal; ADR-0016 Proposed |
| WhatsApp / SMS sending | Absent | Notices are copied into existing groups; SchoolOS never sends |
| Parent-facing pages | Absent | BRD §7.3 non-goal |
| DPDP consent register, data-principal requests, grievance log | Spec only | PRV-006 / PRV-010 in docs/08 |
| CBSE Appendix IX disclosure page | Absent | |
| RTE 25% register | Absent | |
| Real LLM / embeddings / OCR providers | Not wired live | Fakes in tests; no Vertex evaluation |
| Ask-the-school conversation screens | Not built | API exists |
| Usage meters, AI answer count | Partly 0 | |

## 3. Proposed build order

Each release is a set of user stories that ships whole (tests, authz/BOLA, audit, docs, `make check`). Dates assume the owner wants pilot schools using the readiness check before the February 2027 SSC window.

### R0 · Go-live foundation (Oct-Nov 2026) — prerequisite for any real school

- CI green on GitHub; branch protection; required checks.
- AWS staging then prod (Terraform apply in ap-south-1, backups to ap-south-2), the docs/10 runbook steps, `sync_system_roles --apply`.
- Real providers behind existing interfaces: Vertex AI Gemini with ZDR (LLM), embeddings chosen by `make eval`, an OCR provider for register photos.
- Pilot-ready gate (`docs/14-roadmap.md` §3): restore drill, DPA and DPIA, ZDR confirmation, CA sign-off of the invoice layout, training material.
- Correct the stale roadmap status.

### R1 · Board and portal readiness — the wedge (Oct-Dec 2026)

1. **SSC Class 9/10 readiness check (AP State Board).** A BSEAP profile: character-level diff of admission register vs Aadhaar-as-printed vs UDISE+ for name, surname, DOB, gender (and parent names where the board prints them). Every mismatch says **who must fix it**: the parent (Aadhaar centre), the school via the MEO/MIS coordinator (UDISE+), or the school's own register (a change request with evidence). Printable per-student parent verification slip; class-level readiness dashboard ("142 of 160 ready"). Builds on `dq` rules, findings and `exports` profiles; never auto-corrects (invariant 6).
2. **UDISE+ profile with the real 2026-27 field list** (replacing placeholders), including PEN, and a **transfer-in by PEN** check that warns before creating a second record for a child who already has a PEN.
3. **APAAR consent register.** Per student: consent given / refused / pending, with the dated signed form as an evidence document, using a form that offers refusal (SC order, 20 Jul 2026). An **APAAR failure list** driven by the same diff (why generation will fail and who must fix it). No Aadhaar number is ever stored (invariant 4).
4. **CBSE registration profile** (Pariksha Sangam Class 9/11, LOC 10/12 with APAAR mandatory) for the CBSE minority.
5. **Onboarding concierge.** Import template library (M7 item pulled forward), "run alongside your current ERP" mode (SchoolOS as the records layer only), and a guided first-week checklist for the clerk.

### R2 · Office essentials around the wedge (Jan-Apr 2027)

1. **Certificates finish-up:** official AP TC format, public certificate verification by QR/serial (no PII on the public page beyond what the certificate itself shows).
2. **Fee ledger and receipts** *(needs owner decision Q4)*: fee heads, instalments, concessions, late fees; receipts from one series for cash, the school's own UPI QR (matched by UTR) and licensed-aggregator payment links (school is the merchant; SchoolOS reconciles from webhooks; ADR-0016 to be decided). Coexists with the Tally connector.
3. **Parent messages over WhatsApp utility templates with SMS fallback** *(needs owner decision Q5)*: from the school's own number, opt-in recorded, each message links to a short-lived mobile web page for the notice or receipt; audit trail; fair-use allowance in the plan.
4. **Ask the school, visible:** conversation screens in the web app, live evaluation on Vertex, the contextual retrieval and reranking switched on if `make eval` says so.

### R3 · Compliance pack and differentiation (Apr-Jun 2027, before DPDP's May 2027 duties)

1. **DPDP processor pack:** DPA template, consent register (shared with APAAR consent), data-principal requests (access, correction via changes, erasure where lawful), grievance log, the 72-hour breach runbook and notification templates, retention per record class (PRV-006, PRV-010).
2. **CBSE Appendix IX public disclosure page** generated from data SchoolOS already holds (no student PII).
3. **RTE 25% register:** lottery reference, documents, reimbursement claim tracking.
4. **Readiness reminders** tied to the portal calendar (UDISE+ freeze, SSC window, CBSE registration).

### R4 · Commercial and growth (alongside R1-R3)

- Plan catalogue revisited against research pricing (Q6): today ₹15,000 one-time + ₹4,999/month (ADR-0038) is about ₹120/student/year at 500 students but ₹200 at 300 students. Possible entry tier "Records & Readiness" sold beside an existing ERP.
- Paid pilot credited to year one; usage meters live; quotes always state migration, training and GST.
- Public pricing page brought in line with ADR-0038.

### Kept out of scope (unchanged unless the owner reopens them)

Replacing Tally/accounting, timetable, transport/GPS, hostel, payroll, LMS/homework, biometric hardware, CCTV, a native parent app, automated submission into government portals (there are no APIs; SchoolOS prepares and checks, the clerk submits).

## 4. Invariants that shape the new work

- **No Aadhaar numbers** (inv. 4): the readiness check compares *as-printed* demographic fields and last-4 only; the clerk keys full numbers into the government portal.
- **Never auto-correct** (inv. 6): every mismatch is a finding with an owner; register changes go through maker-checker with evidence.
- **Purpose limit** (inv. 14): no fee-financing referrals that share student data; messaging is for school communication only.
- **Money never touches SchoolOS** (new rule, from RBI PA Directions): payment links only; the school is the merchant of record.
- **Messaging** needs an ADR before any sending code (provider through a gateway-style interface like the LLM gateway; templates as config; opt-in; no PII in logs).

## 5. Research still needed (cheap, before heavy R2 scope)

10-20 interviews with AP school offices (correspondent, principal, clerk) covering: school size and board, how fees are collected today (cash / UPI QR / gateway / challan), how many SSC corrections they hit in Feb 2026, whether parents read English WhatsApp notices, current software and spend, what they would pay to avoid portal rejections. Plus the official documents in Q3.

## 6. How we would build it

The same pattern as waves 1-6: small waves of agents on `wip/*` branches, each story with tests first, merged `--no-ff` after `make check`-equivalent verification (lint-imports, mypy, ruff, full pytest, web lint/typecheck/vitest/build, eval when knowledge changes), then cleanup of worktrees and branches. ADRs before any decision that reverses a BRD non-goal.

## 7. Questions for the product owner

1. **Wedge.** Do we lead with "board and portal readiness" (SSC/UDISE+/APAAR) and aim pilots at the Feb 2027 SSC window?
2. **Go-live.** May we start R0 now: AWS staging/prod spend, GitHub CI and branch protection, a Vertex AI project with ZDR, an OCR provider?
3. **Pilot schools and official documents.** Do you have AP schools ready to pilot (count, board, size, town)? Can you obtain the UDISE+ 2026-27 data capture format, the BSEAP nominal-roll format, the AP TC format and the current APAAR consent form?
4. **Fees.** Build a fee ledger and receipts (cash + UPI QR + aggregator links), reversing the BRD non-goal? If yes, which aggregator first (Razorpay, Cashfree, Easebuzz)?
5. **Parent messaging.** Should SchoolOS send WhatsApp/SMS itself? Meta Cloud API direct or a BSP (Gupshup, Interakt, AiSensy)? Message cost inside the plan or passed through?
6. **Pricing.** Keep ADR-0038 (₹15,000 + ₹4,999/month), or add a cheaper per-student "Records & Readiness" tier for small schools and schools keeping their ERP?
7. **Telugu.** Keep ADR-0036 as is, or turn Telugu on for parent-facing messages/pages only, with Telugu-speaking support?
8. **CBSE and RTE.** Include the CBSE registration profile and the RTE register in this year's scope, or State Board only?
9. **Legal.** Who drafts the DPA, DPIA and privacy notice (a lawyer you use, or a draft from us for review)?
10. **Build pace.** How many agents per wave, and do you want R0 and R1 in parallel?

## 8. Owner decisions (2026-10-10)

| # | Question | Decision | Effect on the plan |
|---|---|---|---|
| D1 | Wedge | **Lead with board and portal readiness**; aim pilots at the Feb 2027 SSC window | R1 first |
| D2 | Go-live | **Code only, no cloud yet.** No AWS apply, no GCP/Vertex project, no GitHub settings changes until pilots are close | R0 shrinks to code-side work (CI workflow kept green locally, roadmap correction); cloud steps wait for the owner |
| D3 | Pilots and formats | **Build from public sources**, mark each format as unverified, confirm with a school later | Every board/portal profile carries `source` and `verified: false` until confirmed |
| D4 | Fees | **Fee ledger and receipts**: cash, school's own UPI QR (UTR matching), payment links; school is always the merchant | New ADR reverses BRD §7.3 "online fee collection" non-goal; R2 |
| D5 | Aggregator | **Razorpay first**, behind a provider interface (fake provider in tests) | ADR-0016 to be accepted with Razorpay |
| D6 | Messaging | **WhatsApp only** (no SMS, no DLT), **Meta Cloud API direct**, through a provider interface | New ADR; R2 |
| D7 | Message cost | **The school pays Meta directly** (its own WhatsApp Business account, INR billing); SchoolOS charges nothing for messages | ADR-0038 unchanged |
| D8 | Pricing | **Keep ADR-0038** (₹15,000 one-time + ₹4,999/month; AI answer bundles) | No entry tier for now |
| D9 | Telugu | **English staff UI; parent-facing WhatsApp templates and parent pages may be Telugu or English per school**; Telugu-speaking support | ADR-0036 amendment: a parent-language setting separate from `SOS_TELUGU_ENABLED` for the staff UI |
| D10 | Scope of boards | **AP State Board SSC, CBSE registration, CBSE Appendix IX page, and ICSE (CISCE) schools as per their norms and practices.** RTE register not selected this year | R1 covers BSEAP, CBSE and CISCE profiles; R3 drops the RTE register |
| D11 | Pace | **Waves of 2-3 agents**, each merged and verified before the next | §6 |

Still open: who drafts the DPA, DPIA and privacy notice (Q9) — not blocking R1.

### Resulting release scope

- **R0 (code side only):** fix the stale roadmap status; keep CI workflows passing locally; nothing that spends cloud money.
- **R1:** BSEAP SSC Class 9/10 readiness (diff, fix-owner, parent verification slip, class dashboard); UDISE+ 2026-27 profile with PEN and transfer-in-by-PEN guard; APAAR consent register and failure list; CBSE registration profile (Class 9/11, LOC 10/12, APAAR mandatory); CISCE registration profile refreshed to current norms; import template library and "alongside your current ERP" mode.
- **R2:** AP TC format and public certificate verification; fee ledger, receipts, UPI QR/UTR matching, Razorpay payment links; WhatsApp utility templates via Meta Cloud API with opt-in, parent language per school, mobile web landing pages; Ask-the-school conversation screens.
- **R3:** DPDP processor pack; CBSE Appendix IX page; portal-calendar readiness reminders.
