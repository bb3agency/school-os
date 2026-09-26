# 01 · Business Requirements Document (BRD)

| Field | Value |
|---|---|
| Product | SchoolOS (working name) |
| Version | 0.2 · 2026-09-26 |
| Date | 2026-09-26 |
| Changes | 0.2: commercial model decided (managed SaaS, shared and dedicated tiers, recurring subscription; §11), platform operator team added to stakeholders (§6), related risk (§14). 0.1: baseline |
| Owner | Founder (solo student-developer, Andhra Pradesh) |
| Status | Draft for design-partner validation |
| Related | 02-PRD, 03-TRD, 08-Privacy, 16-Platform admin panel, ADR-0015 |

---

## 1. Executive summary

Private school offices in Andhra Pradesh keep the school's institutional memory on paper registers, Excel sheets and in the heads of a few senior staff. They re-type the same student details into many portals and boards (UDISE+, APAAR, state systems, board registration), fix name/date-of-birth mismatches that can take weeks, issue certificates by hand, and answer the same questions for parents and management repeatedly.

SchoolOS is a **board-neutral operations and memory layer** for the admin office. It keeps one checked student record with the value from each source, catches mismatches **before** submissions, produces certificates, registers and portal-ready sheets from that record, and answers questions about the school's history in plain English or Telugu **with sources**.

It is not another ERP. It works alongside the school's existing tools (paper registers, Excel, Tally) and replaces work, not systems.

## 2. Business context and problem

### 2.1 How the office works today

- The office maintains dozens of registers (admission & withdrawal, attendance, TC counterfoils, fee/cash book, stock, inward/outward, log book, audit objections). One state headmasters' association's list runs past 40 registers.
- Work is driven by external deadlines: admissions and TCs (Mar–Jun), scheme verifications (Jul–Aug), UDISE+ updates (Aug–Sep), board registrations (Aug–Nov), exams and results (Feb–May).
- Studies of Indian schools report heavy paperwork caused by duplicated data and records kept in both paper and digital form. In one Delhi study, 93% of teachers said paperwork took a lot of their time (Accountability Initiative). A 2025 report described an Anantapur (AP) teacher staying an hour after school to update UDISE+, attendance and meal apps.

### 2.2 Pain points (validated in research; to be re-validated at the design partner)

| # | Pain | Consequence |
|---|---|---|
| P1 | Same student details typed into many portals | Hours lost per cycle; inconsistent data |
| P2 | Name/DOB mismatches between admission register, Aadhaar and portals | Failed APAAR generation, board registration corrections, errors printed on certificates |
| P3 | Old records are hard to find (paper, scattered files, staff memory) | Slow certificates, lost knowledge when staff leave |
| P4 | Circulars arrive often, at short notice | Missed tasks and deadlines |
| P5 | Parents queue at the counter for certificates, fee and scheme questions | Office interruptions |
| P6 | Management asks for numbers compiled by hand | Delays, errors |
| P7 | Students slipping academically go unnoticed until late | Avoidable failures and dropouts |

## 3. Market and opportunity

- AP has roughly **13,700 private schools** (UDISE+ snapshot, July 2025), teaching about half the state's students.
- School ERP software is crowded and cheap; many vendors now market "AI". Competing as another ERP is not viable.
- The unserved gap: **AP-specific office work** (portals, formats, Telugu, data quality) and an **institutional memory** grounded in the school's own records.
- **Design partner:** the founder's former school: ICSE board, about 2,000 students, average fee about ₹35,000, currently on paper registers + Excel/Google Sheets + Tally.
- ICSE is a small slice of AP (a parliamentary answer listed 54 CISCE-affiliated schools in AP), so the core **MUST be board-neutral**, with board-specific exports as plug-ins (CISCE, BSE AP/SSC, CBSE).

## 4. Vision and positioning

**Vision:** Every school office can find any fact about its history in seconds, and never submits wrong data again.

**Positioning:** "The school's memory that the office actually uses." Enter once, check against every source, use everywhere.

**What SchoolOS is not (now):** an accounting system (Tally stays), a full ERP replacement, a parent app, an LMS, or a replacement for legally required paper registers.

## 5. Business objectives

| ID | Objective | Measure | Target (design partner, first 6 months of use) |
|---|---|---|---|
| BO-01 | Eliminate avoidable submission errors | Post-submission correction requests caused by data errors | 0 for batches pre-checked by SchoolOS |
| BO-02 | Cut time to find historical information | Median time to answer a records question | < 1 minute (baseline: hours to days) |
| BO-03 | Reduce duplicate data entry | Student fields re-typed per portal cycle | ≥ 70% reduction |
| BO-04 | Faster certificates (M3) | Time from request to printed certificate | < 5 minutes |
| BO-05 | Earn trust with sensitive data | Security incidents; audit completeness | 0 incidents; 100% of identity changes audited |
| BO-06 | Prove willingness to pay | Design partner converts to paid | Paid agreement after first real submission cycle |
| BO-07 | Build a repeatable product | Schools onboarded with < 1 week effort each | 5 schools by end of Stage 0 |
| BO-08 | Support weak students (M5) | Flagged students with an assigned owner and action | ≥ 90% of flags actioned within 7 days |

## 6. Stakeholders and users

| Stakeholder | Role in school | What they need | Relationship |
|---|---|---|---|
| Correspondent / Management | Owner, final buyer | Visibility, risk reduction, value for money | Economic buyer |
| Principal | Academic + admin head, signs certificates | Accurate records, fewer escalations | Champion / approver |
| Office admin (senior clerk) | Runs registers, portals, certificates | Less re-typing, fewer corrections, fast lookup | **Primary user** |
| Office staff | Data entry, counter | Simple screens, clear errors | Primary user |
| Accountant | Fees, Tally | Fee questions answered without re-entry | Secondary user |
| Exam coordinator | Board registration | Clean candidate data before deadlines | Primary user (seasonal) |
| Class teacher | Attendance, marks, student welfare | Quick student context, early warnings (M5) | Secondary user |
| Parents | Receive notices/certificates | Correct documents, clear bilingual notices | Indirect |
| Boards/authorities | Receive submissions | Correct data | Indirect |
| Platform operator team (SchoolOS) | Builds and runs SchoolOS: owner, engineers, support, billing (roles in 16 §2) | Secure, low-ops platform; provisioning, billing and support tools without access to school data (BR-09) | Operator / supplier |

## 7. Scope

### 7.1 In scope: core platform (M0–M2)
- Multi-tenant platform, identity, RBAC with scopes, audit
- Academic structure, student record with per-source values, guardians
- Onboarding: Excel/Sheets import, register-photo extraction with human verification
- Data-quality engine (mismatch rules), findings workflow, maker-checker for identity changes
- Board/portal pre-check exports (first: CISCE registration check; UDISE+ check)
- Document library and knowledge base; "Ask the school" with citations (English/Telugu)

### 7.2 In scope: next modules (M3–M7, see 14-Roadmap)
Certificates & registers · circulars→tasks and bilingual notices · student timeline & early warning · Tally read connector · multi-school readiness.

### 7.3 Out of scope (until an explicit decision)
Replacing Tally or doing accounting · online fee collection from parents · parent mobile app · LMS/homework · timetable · transport/GPS · biometric attendance · automated submission into government portals (no APIs; exports only) · CCTV.

## 8. Business rules

| ID | Rule |
|---|---|
| BR-01 | The **admission register** is the legal anchor for identity fields (name, DOB, parents' names, admission number/date). SchoolOS records values from every source but never overwrites the register value automatically. |
| BR-02 | **Aadhaar numbers are never stored.** Only the last 4 digits and the as-printed name, DOB and gender may be kept. |
| BR-03 | SchoolOS **flags** mismatches; it never auto-corrects official data. Each finding states which record likely needs correction and through which route. |
| BR-04 | Changes to identity fields require an evidence document and approval by a second authorized person (maker-checker). Self-approval is impossible. |
| BR-05 | Every AI answer cites its sources or says the information was not found. AI never invents official facts. |
| BR-06 | Users see only what their role and scope allow (e.g., class teachers see their own sections). This applies equally to search and AI answers. |
| BR-07 | Parent-facing outputs are available in English and Telugu. |
| BR-08 | The school owns its data. It can export everything in open formats at any time; on exit, data is deleted and encryption keys destroyed. |
| BR-09 | Platform staff have no standing access to school data. Support access is time-bound, approved by the school (owner/principal), and audited. |
| BR-10 | Student insights are used only for educational activities and child safety; never for marketing or profiling beyond that purpose. |
| BR-11 | Paper registers remain the legal record where law requires; SchoolOS prints in the school's familiar formats. |
| BR-12 | Certificates (M3) carry a unique serial number and a register entry; reprints are marked "Duplicate". |
| BR-13 | SchoolOS works alongside existing tools (Tally, Excel, paper). It imports and reads; it does not require the school to abandon them. |
| BR-14 | Deletion of personal data follows the school's retention settings and law; audit/security logs are retained per 08-Privacy. |
| BR-15 | No real student data is used for development, testing, demos or AI training. |

## 9. Business process overview (as-is → to-be)

| Process | As-is | To-be with SchoolOS |
|---|---|---|
| Board registration (e.g., CISCE Class 9/11) | Exam coordinator types details from register/Excel into CAREERS portal; errors found later | Batch loaded once; pre-check report lists every mismatch and missing field; corrections via maker-checker before typing/upload |
| Answering "when did X join / leave?" | Search paper registers, ask senior clerk | Ask in English/Telugu; answer cites register page / record |
| Onboarding a school's history | Not possible practically | Import Excel; photograph register pages on demand; clerk confirms extracted rows |
| Handling a DEO/board circular (M4) | Read, remember, act | Summarized bilingually, deadlines become tasks |
| Issuing a TC or bonafide (M3) | Handwritten/typed, register updated by hand | Generated from the checked record, serial numbered, register entry automatic |

## 10. Success metrics and KPIs

- **Adoption:** weekly active office users; % of submissions pre-checked in SchoolOS
- **Quality:** mismatches found per batch before submission; corrections after submission (target 0)
- **Speed:** median time-to-answer for records questions; time-to-certificate (M3)
- **Trust:** % AI answers with valid citations (target ≥ 95%); "helpful" rating (target ≥ 80%)
- **Data readiness:** % students with verified identity fields
- **Business:** conversion to paid, renewal, referrals from principals

**Baseline capture (first visit):** time the office spends on one portal cycle and three recent certificates; count last cycle's correction requests; list 20 real questions staff asked in the past month.

## 11. Commercial model

Decided in September 2026 (ADR-0015). Prices are still hypotheses to validate.

**Delivery model: managed SaaS.** SchoolOS runs, patches, backs up and supports the product. Schools use it in a browser. Nothing is installed on the school's premises (except the optional Tally edge agent in M6).

| Tier | What the school gets | Who it suits |
|---|---|---|
| **Shared** (default plan) | The school is an isolated tenant on the pooled SchoolOS platform in AWS Mumbai (ap-south-1), backups in Hyderabad (ap-south-2) | Most schools: lowest price, no IT work |
| **Dedicated** (premium plan) | Its own isolated server in AWS Mumbai running the same software, its own storage and encryption key, nightly encrypted backups to Hyderabad, and an optional **custom domain** (e.g., `office.<school>.edu.in`) | Larger schools, groups, or schools that ask for "our own server" |

Both tiers have the same features, security baseline and data-in-India commitment. The school's data is never used by the platform team without the school's time-bound approval (BR-09).

**Pricing and billing**
- **Recurring subscription** in INR, monthly or annual, plus GST. Plans are versioned; a school's price does not change mid-period.
- Candidate pricing units: per student per year (familiar to schools) or a flat price per enrolment band; the dedicated tier adds a fixed monthly amount for the server. Anchor to value (errors avoided, hours saved), not to cheap ERP prices.
- Pilot is free until the school uses SchoolOS for a real submission cycle (a trial subscription); the price and conversion trigger are agreed **before** the pilot starts.
- Payment by bank transfer or UPI against a GST invoice at first; online payment collection is a later decision (ADR-0016, Proposed).
- Non-payment never switches a school off automatically: there is a 15-day grace period, suspension is a human decision, and never during board exam windows without the owner's approval (16 §9).
- AI usage is metered internally with per-tenant budgets to protect margins; plan limits cover students, staff users, storage, documents and AI usage.
- Price experiments with the next 3–5 schools after the design partner.

## 12. Assumptions and dependencies

- A1: Offices can export or photograph their registers and lists; the design partner grants written permission.
- A2: No government/board portal offers APIs for vendors; SchoolOS produces check reports and formatted sheets, and staff submit.
- A3: At least one office PC with a modern browser and internet access exists per school.
- A4: Anthropic API (commercial terms) and an embeddings provider are available; ZDR can be requested.
- A5: The founder can provide on-site onboarding for early schools in AP.

## 13. Constraints

- Solo founder, student schedule, limited budget → managed services, modular monolith, strong automation
- DPDP Rules' substantive obligations apply from mid-May 2027; the product must be compliant-by-design before scaling
- Portal formats change yearly → exports are versioned configurations, not code forks
- Low digital comfort in offices → print-first, forgiving imports, bilingual UI

## 14. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Built for one school's quirks | High | High | Collect formats from 3–4 other schools early; board-neutral core; configurable templates |
| Data breach of children's data | Low | Severe | 07-Security controls, RLS, encryption, no Aadhaar storage, pen test before paid go-live |
| AI gives a wrong official answer | Medium | High | Grounding + citation validation + "not found" behaviour + evals as CI gates |
| Schools don't pay for admin tools | Medium | High | Agree price before pilot; tie to a deadline-driven, visible outcome |
| Government changes portals/rules | High | Medium | Versioned export profiles; monitor circulars; adapt quickly |
| Founder bandwidth | High | High | Tight scope per milestone; AI-assisted development with strong tests |
| Vendor lock-in (IdP, cloud) | Medium | Medium | Interfaces around identity, LLM, embeddings, storage; ADRs record exit paths |
| Dedicated-tier hosts add operations work | Medium | Medium | Same images and pipeline as shared tier; heartbeat monitoring, automated backups and upgrade waves (10 §15); premium price covers the cost |
| Unpaid invoices from schools | Medium | Medium | Clear terms before pilot; reminders; grace period; manual review before any suspension |
| Competitors copy "AI assistant" | High | Medium | Moat = AP portal/format knowledge + clean per-source data + Telugu + office workflows |

## 15. Compliance summary (details in 08)

DPDP Act 2023 and DPDP Rules 2025 (school = Data Fiduciary, SchoolOS = Data Processor); children's data rules with educational-institution exemptions limited to educational activities and safety; Aadhaar handling constraints; CERT-In directions (6-hour incident reporting, 180-day logs in India, NTP sync); contractual DPA with each school.

## 16. Approval criteria (design partner)

The baseline is accepted when the design partner confirms: (a) the top office tasks and their order, (b) the first module to pilot, (c) data-handling permission and DPA, (d) price and conversion trigger.

## 17. References

- Accountability Initiative, time-use and paperwork findings: https://accountabilityindia.in/blog/why-non-teaching-work-should-be-clearly-defined-in-the-new-education-policy/
- Careers360 (Dec 2025), teachers' data-entry burden incl. Anantapur example: https://news.careers360.com/school-teacher-data-entry-burden-udiseplus-midday-meal-diksha-attendance-nishtha-ullas-portal-apps-login-monitoring-education
- Example list of required school registers (KPPHA): https://kppha.in/uploads/downloads/001a71b7-925b-48e0-8f0a-ff718f39157c16460094042137310408.pdf
- CISCE-affiliated schools by state (parliamentary answer): https://eparlib.nic.in/bitstream/123456789/973581/1/AU4618.pdf
- CISCE 2027/2028 registration windows: https://news.careers360.com/isc-icse-exams-2027-entry-confirmation-begins-at-careers-cisce-org-registration-starts-for-2028-students-schools-board-candidates
