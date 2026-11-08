# Feature discovery, 2026-11-09: summary

Six research agents looked for features SchoolOS does not have yet, one area each. They read the docs and the code and checked current Indian rules and portal dates on the web (sources and access dates are in each report). This page ranks the results across areas. Nothing here is decided: each item still needs the owner's yes and, where marked, an ADR and a privacy review (docs/14 §5).

| Report | Prefix | Proposals |
|---|---|---|
| [Office and portals](office-portals.md) | FO | 13 |
| [Money](money.md) | FM | 12 |
| [Parents and communication](parents-communication.md) | FP | 13 |
| [Teaching and academics](teaching-academics.md) | FT | 14 |
| [Compliance and child safety](compliance-safety.md) | FC | 12 |
| [AI and the SaaS business](ai-platform.md) | FA | 13 |

Many portal and regulatory facts come from secondary sources; each report marks what it could not verify. Confirm dates with a pilot school before relying on them.

## 1. Dates that drive the order

| Date | What | Proposals |
|---|---|---|
| Now (mid to late Nov 2026, unverified) | BSEAP SSC March 2027 exam fee window (last year 13-25 Nov, no extension); UDISE+ must be right before the fee is paid | FO-05 |
| 31 Dec 2026 | Each school's WhatsApp Business account must bill in INR; Meta stops delivery from non-INR accounts on 1 Jan 2027 | onboarding checklist, FP-11 |
| 1 Jan 2027 | Gemini Flash introductory prices end (about double) | FA-02 |
| Jan-Feb 2027 (if CBSE repeats 2025-26) | CBSE attendance cut-off and shortage cases; internal-assessment uploads | FT-07, FT-01 |
| Jan-Mar 2027 | Parents ask for tuition fee tax certificates (new Income-tax Act section numbers) | FM-06 |
| Feb 2027 | SSC nominal-roll correction window | FO-01, FO-02 |
| Feb-May 2027 | Schools' buying window | FA-10, FA-11, FA-12 |
| 13-14 May 2027 | DPDP duties for schools start (notice, children's consent, rights, grievances within a published period) | FC-03, FP-04, FP-13, FC-12 |
| June 2027 | First year change for pilot schools with fees on | FM-01, FT-08 |
| Already in effect | CBSE revised mandatory-disclosure format (circular 1/2026); Meta charges utility templates inside the service window since 1 Oct 2026 | FC-02, FP-11 |

## 2. Recommended order (lead's ranking across areas)

### Now: small, time-bound or fixing a promise we already make

| Id | Feature | Effort | Why now |
|---|---|---|---|
| FO-05 | Portal calendar refresh (SSC fee window, CBSE LOC and portals, recognition renewal, RTE lottery) | S | The SSC fee window is likely open now; config only |
| FC-02 | CBSE disclosure page in the revised 2026 format | S | CBSE treats a missing or outdated disclosure as an affiliation breach |
| FP-11 | Align messaging costs with Meta's current pricing; monthly spend view | S | Our config still calls paid messages free |
| FA-02 | AI unit economics: cost gate in evals, cheaper answer models, router, then reprice bundles | M | Bundles are priced below estimated model cost, and model prices rise on 1 Jan 2027 |
| FC-12 | Legal holds | M | docs/08 §7 promises them; the nightly retention run deletes behaviour notes after 365 days |
| FT-12 | Fix the class-teacher guide's Ask example (Ask cannot read attendance) | S | Documentation is wrong today |

### Before May 2027 (DPDP) and June 2027 (first year change)

| Id | Feature | Effort |
|---|---|---|
| FC-03 | DPDP readiness checklist per school | S |
| FP-13 | Privacy notice broadcast with per-guardian delivery evidence | S |
| FP-04 | Parent request page for DPDP rights and grievances (public write: ADR-0046 amendment, ADR, privacy review) | M |
| FM-01 | Last year's dues carried into the new year, leavers with dues, maker-checker write-off | M |
| FT-01 | Exam lock and marks history (audit YA-04) | S-M |
| FT-06 | School calendar: working days and holidays | M |
| FT-08 | Year-end progression pack (held back, UDISE+ progression, TC prefill) | M |
| FT-09 | Real admission dates from imports, then refuse attendance before enrolment (audit YA-06) | S |
| FM-06 | Annual tuition fee certificate for income tax | S |

### High value next, in the order the pilots will feel them

| Id | Feature | Effort |
|---|---|---|
| FO-02, FO-04, FO-01, FO-03 | UDISE+ work: correction requests to the MEO, list import matched by PEN, follow-up on fixes outside the school, movement to-do list | S-M each |
| FM-04 | Accountant and management reports (period collections, demand-collection-balance, registers, ageing, year-end pack) | M |
| FM-03 | Tally voucher export (XML import file; connector stays read-only) | M |
| FP-01 | WhatsApp opt-in coverage and reach follow-up per section | S |
| FP-02 | Urgent closure message (ADR-0043 amendment for the quiet-hours window) | S |
| FC-01, FC-07 | Compliance certificate register with expiry reminders, then inspection packs | M each |
| FA-05 | Counts-only weekly brief for management | S-M |
| FA-07 | Evaluation gaps: faithfulness, correctness, cost per answer, register-photo extraction with an Aadhaar hard gate | M |
| FA-11 | Onboarding tracker and adoption health (ADR-0020 amendment) | M |
| FT-02 | Subject list and subject-teacher marks entry (authz ADR) | M-L |
| FM-02 | Per-student extra charges (transport bands, hostel, activities) | M |

### Larger, or blocked on a decision

- FA-01 OCR for scanned documents and a real register-photo provider: the most valuable single AI item, but L and blocked by D2.
- FA-09 groups of schools run by one trust: a billing account is limited to one school today.
- FA-10 entry tier: ADR-0055 waits for the owner (the report recommends option C with a cap at the Shared price).
- New data classes, each needing an ADR and a privacy review: FO-07 staff register, FO-08 admissions desk, FC-04 staff safety register, FC-06 child-protection concern register, FT-05 self and peer assessment, FM-09 fee follow-up notes.
- Reopen proposals (owner's call): FO-13 RTE register and FM-12 RTE reimbursement claims (D10, D43), FC-08 transport compliance records.

## 3. Decisions for the owner

1. **Parent pages that write.** ADR-0046 says no parent page changes a record. One amendment allowing request-type writes that staff act on would unlock FP-03, FP-04, FP-05, FP-08 and FM-05.
2. **AI pricing.** Reprice the AI answer bundles, or accept the loss for pilots (FA-02). Also: Aadhaar redaction in page images before Gemini (ADR-0033 open question) and whether to accept Vertex embeddings at a small recall cost (FA-03).
3. **Entry tier** (ADR-0055, FA-10) before the Feb-May 2027 buying window.
4. **Exams.** Who may unlock an exam, and whether a correction after the lock needs a second person (FT-01). Whether subject teachers enter marks (FT-02). Whether report cards and the Holistic Progress Card are in scope for 2026-27, and whether ranks are allowed.
5. **CBSE attendance eligibility** for the January 2027 cut-off or for 2027-28 (FT-07).
6. **Child protection.** Whether SchoolOS should hold POCSO concern records at all (FC-06).
7. **New data classes**: staff register (FO-07, FC-04), admissions desk (FO-08).
8. **Reopen** RTE (FO-13, FM-12) and transport records (FC-08)?
9. **Operator visibility** of per-school readiness and adoption counts (FC-03, FA-11; ADR-0020 amendment).
10. **Telugu.** Who reviews the 9 Telugu templates (all `reviewed: false`) and by when; proposed trigger: more Telugu once a quarter of pilot guardians choose it (FP-10).
11. **Money details.** Whether automatic reminders include last year's dues and what to call a management waiver (FM-01); one Tally party per student or one receivable ledger (FM-03).
12. **Urgent messages.** The quiet-hours window for closure messages (FP-02).

## 4. Questions to add to the pilot interviews

The MEO's UDISE+ correction format; which registers inspectors ask for; AP's Class 1 age cut-off; the real UDISE+ download columns; how many sibling families; cheque use; sponsor-paid fees; transport fee bands; how often parents ask for tax certificates; a copy of CBSE circular 1/2026 and its annexure; the AP fire NOC and recognition G.O.

## 5. Not worth building (agreed across reports)

A native parent app or parent login, a chat inbox, a WhatsApp Ask bot for parents, SMS, homework or marketing messages, staff attendance and leave, substitution and timetable, period-wise attendance, AI-written report-card remarks or predicted results, biometric attendance. Each report gives its reasons.
