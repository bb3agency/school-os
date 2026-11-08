# Feature research: compliance and child safety (FC)

| Field | Value |
|---|---|
| Date | 2026-11-09 |
| Area | DPDP Act 2023 and DPDP Rules 2025, CBSE affiliation and Appendix IX, AP recognition and renewals, RTE, POCSO and child protection, fire and building safety, transport safety (records only), staff police verification, committees, inspection readiness |
| Modules read | `app/privacy` (requests, consents, breaches), `app/disclosure`, `app/breakglass`, `app/audit`, `app/admin`, `app/documents`, `app/dq` (portal calendar, readiness), `app/insights` (retention only), `app/identity` (staff model), `app/tenancy` (letterhead) |
| Status | Research only. Nothing here is decided. Every regulatory fact is from public sources read on 2026-11-09; claims marked **(unverified)** are from secondary sources only or could not be found in an official copy. **Not legal advice.** |

## 1. Summary

SchoolOS already covers the DPDP *processor pack* well: notices and notice records, one register for data-principal requests and grievances, consents for optional purposes, breach register with clocks, record classes and lawful erasure, and the CBSE Appendix IX page (R3, built 2026-10-21; docs/14 R3). What it does **not** cover is the rest of a school's compliance life, which is where principals and correspondents actually lose sleep and where inspectors look:

- **Certificates that expire** (fire NOC, building safety, recognition, affiliation, sanitation, bus fitness). Documents have `issued_on` but no validity date (`apps/api/app/documents/models.py:31`), so nothing reminds anyone, and the Appendix IX page asks for "valid" fire and building safety certificates without checking validity (`apps/api/app/disclosure/formats/cbse-appendix-ix.yaml` items `building_safety`, `fire_safety`).
- **People who work with children but never sign in** (drivers, attendants, ayahs, guards). SchoolOS knows staff only as signed-in members (`apps/api/app/identity/models.py:62`), so there is no place for police verification or child-protection training records, which CBSE has required for all staff since 2017.
- **Committees and child-protection handling** (School Safety Committee, POCSO/child-protection committee, POSH Internal Committee, SMC, PTA, anti-bullying). Nothing exists; Appendix IX takes the SMC and PTA lists as uploaded PDFs.
- **Inspection packs.** All the evidence is (or could be) in SchoolOS, but there is no "show me everything the DEO / CBSE / fire officer will ask for, and what is missing or expired".
- **Legal holds**, promised in docs/08 §7 ("support legal holds that suspend deletion"), are not built, which matters as soon as a child-protection case or a police inquiry touches records that retention would delete (behaviour notes are deleted after 365 days, `apps/api/app/admin/retention.yaml:56-59`).

The proposals keep the product's shape: registers, evidence documents, reminders through the existing portal-calendar and task engine, read-only Ask over school-level facts, no automatic submission anywhere, no AI on child-protection data.

## 2. Regulatory facts checked (with dates)

| Topic | Fact | Source (accessed 2026-11-09) | Confidence |
|---|---|---|---|
| DPDP Rules commencement | Rules notified 13/14 Nov 2025 (G.S.R. 846(E)). Rules 1, 2, 17-21 at once; Rule 4 (consent managers) after 12 months, i.e. **13 or 14 Nov 2026**; Rules 3, 5-16, 22, 23 (notice, security, breach, retention, children incl. Rule 10 verifiable parental consent, Rule 12 exemptions) after 18 months, i.e. **13 or 14 May 2027** | [Bar & Bench](https://www.barandbench.com/amp/story/view-point/meity-notifies-final-digital-personal-data-protection-rules-2025), [YourStory](https://yourstory.com/2025/11/digital-personal-data-protection-dpdp-rules-2025-rollout-over-18-months), [S.S. Rana](https://ssrana.in/articles/meity-notifies-final-digital-personal-data-protection-rules-2025/) | Secondary, consistent. Exact day still (to confirm), as docs/08 §16.4 item 1 |
| DPDP timeline compression | MeitY floated (Jan 2026) cutting some periods from 18 to 12 months, mainly Rule 13 (significant data fiduciaries) and Rule 8(3); **no amending notification found**. Schools are not SDFs, so their date would stay May 2027 even if notified | [Mondaq / S.S. Rana](https://ssrana.in/articles/meity-plans-to-cut-short-dpdp-compliance-timeline-and-notify-cross-border-restrictions-for-sdfs/), [Takshashila](https://takshashila.org.in/content/blogs/20260126-meity-considers-reducing-dpdpa-compliance-timelines.html) | **(unverified)**: watch the Gazette |
| Children's data | Rule 10: verifiable parental consent before processing a child's data; Rule 12 + Fourth Schedule exempt educational institutions (and child transport) from s.9(1) and s.9(3) only for educational activities and children's safety | [dpdpa.com Rule 10](https://dpdpa.com/dpdparules/rule10.html), [Schedule 4](https://dpdpa.com/schedule/schedule4.html), [DSCI FAQ](https://www.dsci.in/files/content/documents/2025/FAQs-on-Children-and-PwD-personal-data.pdf) | Secondary; matches docs/08 §4 |
| CBSE mandatory disclosure | Circular 1/2026 (12 Jan 2026) with a **revised Appendix IX**; deadline 15 Feb 2026 "treated as a violation of affiliation" if missed. Asks for strength and qualifications of principal, VP, head teachers, PGT/TGT/PRT, special educators, counsellors; number of registered students; CWSN toilets and library; self-attested certificates | [Careers360](https://news.careers360.com/cbse-schools-class-10-12-results-fees-student-teacher-details-online-by-february-15-2026-affiliation-rules-public-disclosures), copy of the circular hosted by a school (scanned, not machine-readable): [PSBB](https://psbbmillenniumschool.org/wp-content/uploads/2026/01/circular-1.pdf) | Secondary for the field list; circular itself not read **(unverified)** |
| CBSE SARAS | SARAS 7.0 opened 26 Feb 2026 for 2027-28 affiliation; deadline extended to 30 Sep 2026 | [India TV](https://www.indiatvnews.com/education/news/cbse-releases-saras-7-0-portal-for-2027-28-school-affiliations-with-new-built-up-area-rules-details-2026-02-26-1031840), [Free Press Journal](https://www.freepressjournal.in/amp/education/cbse-extends-saras-70-affiliation-application-deadline-for-2027-28-session-to-september-30) | Secondary |
| CBSE staff safety | Circular 19/2017 "Safety of Children in Schools": police verification and psychometric evaluation of **all** staff incl. drivers, conductors, peons; safety audit from the local police station; staff training on abuse prevention; lapses may lead to disaffiliation | [CBSE circular PDF](https://www.cbse.gov.in/cbsenew/Examination_Circular/2017/16_CIRCULAR.pdf), [The Logical Indian](https://thelogicalindian.com/?p=65563) | Official PDF exists; not re-read in full |
| CBSE CCTV | Bye-law clause 4.7.10 (21 Jul 2025): CCTV with audio-visual recording, footage kept at least 15 days | [SCC Online](https://www.scconline.com/blog/post/2025/07/22/cctv-cameras-in-schools-cbse-guidelines-2025-surveillance-student-safety/) | Secondary. CCTV stays out of scope; only a periodic-check record is proposed (FC-09) |
| CBSE transport | 2012 bye-law amendment and Feb 2017 guidelines: speed governor (40 km/h), GPS and CCTV working, a qualified (lady) attendant, disaffiliation for lapses | [CBSE 2012 circular](https://www.cbse.gov.in/circulars/cir_school_trans_2012.pdf), [Business Standard](https://business-standard.com/article/pti-stories/school-buses-must-have-cctvs-gps-speed-governors-cbse-117022400895_1.html) | Official 2012 PDF; 2017 secondary |
| MoE school safety | Guidelines on School Safety and Security (2021, in line with POCSO; advisory; fix accountability of private school managements; safety committee, training of school personnel); MoE asked states in Aug 2024 to report notification status | [DoSEL guidelines PDF](https://dsel.education.gov.in/sites/default/files/update/guidelines_sss.pdf), [Vikaspedia](https://vikaspedia.in/education/childrens-corner/child-rights/child-protection-1/guidelines-on-school-safety-and-security-2021) | Official PDF not read in full **(unverified details)** |
| POCSO | Mandatory reporting to the SJPU or local police (s.19); failure to report is an offence (s.21) | POCSO Act 2012 (India Code) | Statute, well known; specific school procedure from the MoE guidelines **(unverified)** |
| POSH | Every workplace with 10 or more workers must have an Internal Committee; IC annual report to the employer and District Officer (s.21, Rule 14); filing date often given as 31 Jan **(unverified)** | [india-briefing](https://india-briefing.com/news/annual-compliance-for-employers-in-india-under-prevention-of-sexual-harassment-posh-legislation-29410.html), [courtbook s.21](https://courtbook.in/posh/section-21) | Statute; dates secondary |
| AP fire NOC and recognition | Order announced Dec 2025: schools and colleges in buildings up to 30 m need a fire NOC **once every five years** instead of yearly; private school recognition renewal **every ten years** | [Telugu360, 17 Dec 2025](https://www.telugu360.com/nara-lokesh-fulfils-promise-brings-major-relief-to-ap-educational-institutions); recognition 10 years also in `docs/research/2026-10-market/notes/ai_adoption_gtm.md:150` | **G.O. number not found (unverified)**. Long intervals make forgetting more likely, not less |
| AP rules on recognition | AP Registered Schools Rules 1987 under the AP Education Act 1982; recognition needs fire equipment per NBC, structural soundness and occupancy certificates; renewal before expiry | `docs/research/2026-10-market/notes/regulation_portals.md:124`, [Deccan Chronicle 2015](https://www.deccanchronicle.com/150710/nation-current-affairs/article/fire-rules-too-hot-schools) | Secondary, dated |
| AP school buses | Fitness certificate yearly before reopening (AP MV Rules 1989 r.185(G)); RTA special drives each May-June and after accidents | [Hans India](https://assets.thehansindia.com/andhra-pradesh/1150-school-buses-clear-fitness-test-in-east-godavari-536702), [Yo Vizag](https://yovizag.com/rto-vizag-says-that-school-buses-without-fitness-certificate-will-be-seized), [Deccan Chronicle](https://www.deccanchronicle.com/southern-states/andhra-pradesh/ap-rta-launches-week-long-safety-drive-on-private-school-buses-1920349) | Secondary; AP driver/attendant rules not found **(unverified)** |
| APSERMC | The AP regulatory commission inspects private schools (surprise inspections of 130 schools found violations) | `docs/research/2026-10-market/notes/ai_adoption_gtm.md:98` | Secondary, partly dated |

**Time-sensitive dates:** DPDP Rule 4 (consent managers) from about 13-14 Nov 2026 (not a school duty, but it will prompt questions); DPDP day-to-day duties for schools from about **13-14 May 2027**; the next CBSE Appendix IX refresh is likely before the 2027 board season (the 2026 deadline was 15 Feb; next date **unverified**); AP bus fitness before the June 2027 reopening; POSH IC annual report around January.

## 3. Proposals

Effort is for one engineer: S < 1 week, M 1-3 weeks, L > 3 weeks. Value H/M/L.

### FC-01 · Compliance certificate register with expiry reminders

| | |
|---|---|
| **Users** | Principal, correspondent/owner (accountable to DEO, CBSE, fire department), office admin (keeps the files) |
| **Problem** | Fire NOC, building/structural safety, occupancy, recognition, CBSE affiliation and its extension, RTE recognition, trust/society registration, water and sanitation certificates all expire on different cycles (fire NOC now 5 years, recognition 10 years in AP, per the Dec 2025 order, unverified). A lapse is found by an inspector, or by CBSE at the Appendix IX check. Today a document has `issuer` and `issued_on` but no validity (`apps/api/app/documents/models.py:26-31`; `DocType` has no certificate kinds, `apps/api/app/documents/schemas.py:39-52`). The Appendix IX format asks for "valid" fire and building safety certificates as plain document picks (`cbse-appendix-ix.yaml`, section B), so a school can publish an expired NOC without warning. |
| **Exists today** | Documents with versions, ACLs and virus scanning (`app/documents`); the school's own events and the reminder engine of the portal calendar (`app/dq/calendar_service.py:520`, FR-RDY-005..008: tasks through `circulars.service`, in-app notifications, idempotent per lead day) |
| **Proposal** | (1) A versioned catalogue `compliance_certificates.yaml` (kind, label, issuing authority, typical validity, boards/states it applies to, `verified: false`, sources), first for AP State Board and CBSE schools. (2) A school-level register entry per kind: current document (from `documents`, purpose `evidence`), `valid_from`, `valid_until` (or "no expiry"), reference number, renewal owner. (3) Reminders at 90/60/30/7 days before expiry as tasks and in-app notices through the existing calendar/task path; an "expired" state shown on the school dashboard. (4) The Appendix IX page picks the current certificate from the register and refuses to publish (or warns, owner choice) when it is expired. (5) A renewal history: each renewal is a new entry, the old one kept. |
| **BRD objective** | BO-05 (earn trust with sensitive data and compliance; extends the R3 compliance pack), BO-02 (find "when does our NOC expire?" in under a minute) |
| **Invariants / review** | No personal data (school-level documents), so no new data class; tenant RLS as usual; authz new permissions `compliance.read` / `compliance.manage`. No ADR needed beyond a short one for the catalogue if the lead wishes; record class added to `record_classes.yaml` |
| **Dependencies** | `documents`, `dq` calendar engine (or a shared reminder helper extracted from it), `disclosure` for item 4 |
| **Effort** | M |
| **Value** | **H**: cheap to build, every school has the problem, it is the first thing an inspector asks, and it makes the Appendix IX page trustworthy |
| **Risks** | Validity rules differ by district, building height and year; the catalogue must say "typical" and let the school type the real date. AP G.O. details unverified |
| **Owner questions** | Refuse or only warn when publishing Appendix IX with an expired certificate? Include Telangana from the start? |

### FC-02 · Appendix IX format version 2 (CBSE circular 1/2026)

| | |
|---|---|
| **Users** | Principal and office admin of CBSE schools (D10 includes CBSE Appendix IX) |
| **Problem** | CBSE issued a revised Appendix IX with circular 1/2026 (12 Jan 2026) and treats a missed deadline as an affiliation violation (Careers360). The built format is version 1 from secondary summaries (`cbse-appendix-ix.yaml:26-36`, `verified: false`) and lacks items the circular names: number of registered students, VP and head-teacher strength and qualifications, CWSN-friendly toilets, library, and the DEO certificate or **self-certification**. Teacher counts come from SchoolOS members with teacher roles (`app/disclosure/derived.py:61`), which undercounts teachers who never sign in. |
| **Exists today** | The whole disclosure pipeline: draft, frozen snapshot, public link, HTML/PDF files, small-count suppression, link rotation (`app/disclosure`, ADR-0051) |
| **Proposal** | (1) Read the official circular 1/2026 and its annexure (a school copy exists; the PDF is a scan) and write format `version: 2`; keep version 1 snapshots unchanged. (2) Add derived aggregates: registered students per class (counts, suppression below 10 as today), and typed qualification tables per category (counts by qualification, not named teachers unless the circular requires names; to confirm). (3) Add CWSN toilets, library, self-certification document. (4) Add a calendar event "CBSE mandatory disclosure update" (portal `cbse`, unverified date) so the refresh is reminded each year. (5) Item validity from FC-01 when built. |
| **BRD objective** | BO-05; BO-03 (numbers derived, not retyped) |
| **Invariants / review** | Public page: no student data (NFR-PRV-006, PRV-037); if the revised format needs named teachers with qualifications, that is staff personal data published, so it needs the school's notice to staff and a privacy note (ADR-0051 amendment) |
| **Dependencies** | None hard; FC-04 improves teacher counts later |
| **Effort** | S |
| **Value** | **H**: disaffiliation risk, low effort, already a shipped feature that would otherwise be out of date |
| **Risks** | Field list from secondary sources until the circular is read; format churn yearly |
| **Owner questions** | Can a pilot CBSE school share its circular 1/2026 annexure (D3)? |

### FC-03 · DPDP go-live checklist for each school (before May 2027)

| | |
|---|---|
| **Users** | Owner/correspondent and principal (fiduciary), office admin; SchoolOS operator (sees only "ready / not ready" counts, no data) |
| **Problem** | docs/08 §16.3 says that "before mid-May 2027 each school has recorded the notice version it issued, set its privacy contact, and run one test request end to end", and docs/08 §12 lists DPA, DPIA and notice before first real data. Nothing in the product shows a school how far it is, and the R3 tools are spread over several screens. New admissions after the notice was issued are not covered: the notice record is per audience (`parents`/`staff`), not per family (FR-DPDP-003, `docs/03-TRD.md:622`). |
| **Exists today** | Notice templates, notice records, `privacy_contact`, requests register, consent register, breach register, retention settings (all `app/privacy`, `app/admin`) |
| **Proposal** | (1) A checklist page (and dashboard tile) computed from existing records: privacy contact set; notice to parents and staff recorded with the current template version; DPA signed (document of kind "DPA" uploaded); DPIA done; one test request closed; breach contact and phone tree recorded; retention settings reviewed this year; optional-purpose consent forms in use if the school publishes photos. (2) A calendar event "DPDP duties begin" (unverified date) with reminders. (3) "Notice given at admission": a tick (with date and channel) on new admissions so families admitted after the school-wide notice are covered; counts of students without it. (4) Operator view: per school "n of 8 items done", no content (ADR-0020 boundary: needs a usage-style count only). |
| **BRD objective** | BO-05 |
| **Invariants / review** | No new data class (item 3 adds a date and channel per student; small, within the privacy module's record class). Item 4 widens what the control plane reads from schools (counts only), so it needs an ADR-0020 amendment like ADR-0057 |
| **Dependencies** | `privacy`, `admin`, `dq` calendar; `platform` for item 4 |
| **Effort** | S (items 1-3); +S for item 4 |
| **Value** | **H** and time-sensitive: turns the built R3 pack into a visible "you are ready" state, which is what the owner is buying for 2027-28 (docs/18 §1 item 5) |
| **Risks** | Looks like legal assurance; every screen must say "not legal advice" (D30) |
| **Owner questions** | Should the operator see per-school readiness counts (needs an ADR)? |

### FC-04 · Staff safety register (verification and training), including staff who never sign in

| | |
|---|---|
| **Users** | Principal (accountable), office admin (keeps files); management; inspectors (via FC-07) |
| **Problem** | CBSE circular 19/2017 requires police verification and psychometric evaluation of all staff, explicitly drivers, conductors, peons and support staff, plus training on preventing abuse; the MoE 2021 guidelines repeat training and accountability. SchoolOS knows staff only as members who sign in (`apps/api/app/identity/models.py:62-86`: no designation, no qualification, no joining date), so drivers, attendants, ayahs and guards do not exist in it, and teacher counts on Appendix IX are only signed-in teachers. |
| **Exists today** | Memberships, roles; documents as evidence; disclosure counts from memberships |
| **Proposal** | (1) A **staff register** (people who work at the school, with or without a SchoolOS login): name, designation, category (teaching / non-teaching / transport / contract), joined and left dates, qualification (for Appendix IX counts). Not payroll, not attendance (payroll is out of scope). (2) **Verification records** per person: kind (police/character verification, psychometric evaluation, previous-employer reference), date requested, date received, issuing station or agency, result `clear` / `pending` / `adverse` (never the content of the report), evidence document (C3), re-verification due date (school policy, e.g. 3 years, unverified). (3) **Training records**: child-protection, POCSO awareness, first aid, fire safety; date, provider, certificate. (4) Reminders for pending and due re-verification; a dashboard "3 staff working with children without a verification on file". (5) Feeds Appendix IX counts (FC-02) and inspection packs (FC-07). |
| **BRD objective** | BO-05; BO-03 |
| **Invariants / review** | **New data class** (staff verification status and adverse flags are sensitive personal data of adults): ADR and privacy review needed; C3 with a narrow permission (`staff.safety.read`), encrypted notes, no logs of names, not in Ask unless counts; record class and erasure rule (kept while employed + period, to confirm); staff notice template update (`docs/legal/privacy-notice-staff-template.md`). Never store Aadhaar of staff (invariant 4 already masks). |
| **Dependencies** | `identity` (link a register entry to a membership when the person signs in), `documents`, calendar reminders |
| **Effort** | L |
| **Value** | **H**: the CBSE disaffiliation risk and every child-safety inquiry starts with "was this person verified?"; nobody in the competitor set was found to offer it (absence in public evidence) |
| **Risks** | Scope creep into HR/payroll; storing adverse results creates legal exposure, so keep status only |
| **Owner questions** | Accept a staff register (people without logins) in the product? Re-verification period default? |

### FC-05 · Committees register (safety, child protection, POSH, SMC, PTA, anti-bullying, grievance)

| | |
|---|---|
| **Users** | Principal, management, committee conveners |
| **Problem** | Schools must constitute and evidence several committees: School Safety Committee and child-protection arrangements (MoE 2021 guidelines), POSH Internal Committee for 10+ workers with an annual report to the District Officer, SMC and PTA (Appendix IX asks for their lists), anti-bullying and grievance committees (CBSE circulars; **unverified** current numbers). Today the SMC and PTA lists are uploaded PDFs in disclosure (`cbse-appendix-ix.yaml` section C `smc_list`, `pta_list`); nothing records terms, meetings or annual reports. |
| **Exists today** | Documents (minutes as `minutes` doc type, `apps/api/app/documents/schemas.py:42`), tasks and reminders, disclosure |
| **Proposal** | (1) Catalogue of committee kinds (`committees.yaml`, `verified: false`, sources) with required composition hints (e.g. POSH: presiding officer a senior woman, external member) and minimum meetings per year. (2) Per committee: members (staff from the register or memberships; external members typed: name and role only), term start and end, meetings with minutes documents. (3) Reminders: term ending, meeting not held this term, POSH annual report due (date unverified). (4) Appendix IX SMC/PTA items filled from the register (adults named are typed by the school, as PRV-037 already says). (5) Counts only in dashboards; minutes follow document ACLs. |
| **BRD objective** | BO-05; BO-02 |
| **Invariants / review** | Adults' names and roles only; no student data except where a student representative sits on a committee (store name only, or role only; to confirm); no new sub-processor. A short ADR for the catalogue |
| **Dependencies** | `documents`, reminders; FC-04 optional |
| **Effort** | M |
| **Value** | **M-H**: always asked in inspections; light to build on existing pieces |
| **Risks** | Committee rules differ by board and state and change; keep everything as unverified hints |
| **Owner questions** | Which committees for the first release (suggest: safety, child protection, POSH IC, SMC, PTA)? |

### FC-06 · Child-protection concern register (POCSO-aware), restricted

| | |
|---|---|
| **Users** | Designated child-protection lead (often the principal or a counsellor), principal; no one else by default |
| **Problem** | POCSO s.19 obliges anyone who learns of an offence to report it to the SJPU or local police, and s.21 makes failure to report an offence; school guidelines ask for a defined procedure and records. Schools today keep this on paper or not at all. SchoolOS has behaviour notes with a `concern` category (`apps/api/app/insights/rules.yaml:47`), but they are learning-support notes for the class teacher, deleted after 365 days (`apps/api/app/admin/retention.yaml:56-59`), not a protection record, and must not become one. |
| **Exists today** | Nothing dedicated. Insights (wrong place), break-glass audit patterns, encrypted texts with the school key (privacy module) |
| **Proposal** | (1) A separate register: concern raised (date/time, who raised it, category, encrypted description), immediate actions, decision on reporting, **reported to police/SJPU at** (date, station, reference) with a "reporting decision pending" clock shown as overdue after a short period (default to confirm), referral to CWC/DCPU, committee review, closure with outcome. (2) Access: new permission held by the designated lead and principal only; no class teacher, no owner by default; 404 otherwise; every read audited (as insights). (3) Never in Ask, exports (except the school's full export with restricted data), notifications with content, parent pages or messages; no AI ever reads it (import-linter contract, like insights). (4) Retention long and fixed (to confirm with counsel) plus legal hold (FC-12). (5) Training material and a printable procedure from a template in `docs/legal/`. |
| **BRD objective** | BO-05; BR-10 (child safety purpose) |
| **Invariants / review** | **New data class (C4-like: allegations of abuse about children and adults)**: ADR, DPIA addition and privacy review are mandatory; invariant 14 (child safety purpose only); invariant 5 (no PII in logs); invariant 9 (no AI writes; here no AI at all). The Fourth Schedule exemption covers child safety, but the record still needs the school's notice |
| **Dependencies** | `privacy` encryption pattern, audit, FC-12 |
| **Effort** | L |
| **Value** | **H for trust and differentiation, uncertain demand**: a careful register is exactly what a school needs after an incident; many schools may not want such records to exist at all |
| **Risks** | Liability if mis-handled; schools may fear discovery; must never become a surveillance tool. Wrong design here is worse than none |
| **Owner questions** | Is SchoolOS willing to hold child-protection records at all? If yes, a counsel review is a precondition |

### FC-07 · Inspection readiness packs

| | |
|---|---|
| **Users** | Principal, correspondent, office admin before a DEO/MEO, CBSE (affiliation extension, SARAS inspection), APSERMC, fire department or SCPCR visit |
| **Problem** | Inspections ask for the same set of registers and certificates; the market research names APSERMC surprise inspections and the value of "audit-ready documents" (`docs/research/2026-10-market/notes/ai_adoption_gtm.md:98-102`). SchoolOS holds much of the evidence (registers, certificate register, disclosure snapshot, notice records, fee structure) but there is no way to assemble it or to see gaps. |
| **Exists today** | Register print views (certificates module), exports with step-up and audit (ADR-0021), disclosure snapshot files, documents, privacy notice records |
| **Proposal** | (1) Pack definitions in YAML per authority (`verified: false`): list of items, each mapped to a source (certificate kind from FC-01, committee from FC-05, staff verification counts from FC-04, a register print, the Appendix IX snapshot, the privacy notice record, a document tag). (2) A readiness view: present / expired / missing per item, with the fix owner. (3) "Make the pack": one export (index PDF + the current files) through the export pipeline with step-up, 7-day file life, audit; counts only for staff and children (no child lists unless the item is an official register the inspector is entitled to see, chosen explicitly). (4) A school can add its own items. |
| **BRD objective** | BO-02, BO-05; BO-06 (a visible win for the buyer) |
| **Invariants / review** | Exports of registers already follow ADR-0021; no new data class; pack items with personal data need `include_sensitive` rules as today |
| **Dependencies** | FC-01 (core), FC-05 and FC-04 (richer packs) |
| **Effort** | M |
| **Value** | **H**: concentrates all compliance work into one buyer-visible outcome |
| **Risks** | Authorities' lists unverified and differ by district; the pack must say what it is based on |
| **Owner questions** | Which authority first (suggest DEO recognition renewal and CBSE)? |

### FC-08 · Transport compliance records (reopen proposal, records only)

| | |
|---|---|
| **Users** | Transport in-charge, principal, management |
| **Problem** | "Transport/GPS" is in docs/18 §3 "Kept out of scope" and BRD §7.3. But the compliance side has nothing to do with GPS: AP buses need a fitness certificate every year before reopening, plus permit, insurance, PUC and tax; CBSE requires speed governors, a qualified (lady) attendant, and police verification of drivers; RTA drives seize buses without papers. Lapses are a disaffiliation and accident-liability risk. **Reopen proposal**: reopen only "transport compliance records", keep routes, GPS, student allocation and fees for transport out. |
| **Exists today** | Fee head kind `transport` only (FR-FEE-002) |
| **Proposal** | (1) Vehicles: registration number, owned or contracted, seats, operator name. (2) Per vehicle documents with expiry (FC-01 mechanism): fitness, permit, insurance, PUC, road tax; equipment checks with date (speed governor, first aid, fire extinguisher, CCTV/GPS working as a yes/no record only). (3) Crew: driver and attendant linked to the staff register (FC-04): licence expiry, verification status, attendant present. (4) Pre-reopening checklist (May-June) as calendar event and task. (5) No student lists, no routes, no live location. |
| **BRD objective** | BO-05 |
| **Invariants / review** | Reopens an out-of-scope item: owner decision needed; adult staff data via FC-04's review; vehicle data is not personal data except owner-driver contracts (minimal) |
| **Dependencies** | FC-01, FC-04 |
| **Effort** | M |
| **Value** | **M**: real risk, but only for schools that run or contract buses |
| **Risks** | Pulls the product toward a transport module; guard the boundary in the ADR |
| **Owner questions** | Reopen "transport compliance records only"? |

### FC-09 · Safety drills and periodic checks log

| | |
|---|---|
| **Users** | Principal, safety committee convener, office admin |
| **Problem** | Fire and evacuation drills, fire-extinguisher refills, electrical and structural checks, water testing and CCTV working checks (CBSE clause 4.7.10 requires a working system with 15-day footage) are recurring duties inspectors ask about. There is no log. |
| **Exists today** | Tasks and reminders (`circulars.service`), documents |
| **Proposal** | (1) Check kinds in YAML with a default frequency (e.g. drill per term, extinguisher refill yearly; `verified: false`). (2) A log entry: date, kind, done by, participants count, time taken (drills), observations, photo/report document. (3) Overdue reminders as tasks. (4) Items in the inspection pack (FC-07). No CCTV integration, no footage. |
| **BRD objective** | BO-05 |
| **Invariants / review** | No personal data beyond staff names; no new data class; photos of drills may show children: tag them C2 and advise not uploading identifiable children |
| **Dependencies** | FC-01 reminder helper |
| **Effort** | S |
| **Value** | **M**: easy, demonstrable, complements FC-01 |
| **Risks** | Low |
| **Owner questions** | None |

### FC-10 · Policy library with annual review and staff acknowledgement

| | |
|---|---|
| **Users** | Principal (owns policies), all staff (acknowledge), management |
| **Problem** | Child-protection, anti-bullying, safety, privacy and code-of-conduct policies must exist, be reviewed and be known to staff; inspectors ask "how do you know staff have read it?". Documents of purpose `policy` exist (`apps/api/app/documents/schemas.py:26`), and Ask can answer from them, but there is no review date and no acknowledgement. |
| **Exists today** | Policy documents, versions, ACLs; Ask with citations |
| **Proposal** | (1) `review_due` on policy documents with reminders. (2) "Ask staff to acknowledge": each targeted member sees the policy in the app and confirms "I have read this version"; append-only acknowledgements per version; counts and the list of who has not. (3) Starter templates in `docs/legal/` (child-protection policy, anti-bullying), marked not legal advice. (4) Training sessions can be recorded against a policy (feeds FC-04 training records). |
| **BRD objective** | BO-05; BO-02 |
| **Invariants / review** | Acknowledgements are staff personal data (who read what, when): add a record class; no new sub-processor |
| **Dependencies** | `documents`, `notifications` |
| **Effort** | S-M |
| **Value** | **M** |
| **Risks** | Staff without logins cannot acknowledge in-app: allow a paper acknowledgement entry with a scanned sheet |
| **Owner questions** | Ship starter policy templates (more legal drafting under D30)? |

### FC-11 · Ask the school: compliance status questions (read-only tool)

| | |
|---|---|
| **Users** | Principal, owner, office admin |
| **Problem** | "When does our fire NOC expire?", "Which committees have not met this term?", "How many drivers have no verification on file?" are typical inspection-week questions; Ask answers from documents and records but has no tool over compliance registers. |
| **Exists today** | Read-only Ask tools pattern, e.g. `knowledge/tools/due.py` `list_due_tasks` (D74) with eval gates |
| **Proposal** | (1) A read-only tool listing certificates with expiry and state (FC-01), committees and last meeting (FC-05), and **counts only** for staff verification (FC-04). (2) Citations point to the register entry or the certificate document. (3) Eval cases and leakage gates like D74. (4) Never touches FC-06. |
| **BRD objective** | BO-02 |
| **Invariants / review** | Invariants 8 and 9 (grounded, read-only); permission-filtered in SQL; eval gates must pass |
| **Dependencies** | FC-01 (FC-04, FC-05 optional) |
| **Effort** | S |
| **Value** | **M**: shows the differentiator on the new data |
| **Risks** | Low |
| **Owner questions** | None |

### FC-12 · Legal holds (promised in docs/08 §7, not built)

| | |
|---|---|
| **Users** | Principal, owner (instruct the hold), office admin; SchoolOS support (never sees content) |
| **Problem** | docs/08 §7 promises "legal holds that suspend deletion for specific records when the school instructs". No code or record class mentions a hold (searched `app/` and `record_classes.yaml`). Behaviour notes and closed flags are deleted after 365 days by a fixed rule (`apps/api/app/admin/retention.yaml:56-63`), exports after 7 days, imports quickly; if a police inquiry, a court case, a POCSO matter or a parent dispute touches a child, the school has no way to stop the nightly retention run deleting relevant records. |
| **Exists today** | Retention jobs per record class, erasure per module, the record-class catalogue |
| **Proposal** | (1) A hold record: scope (a student, a staff member, or a record class), reason code, reference, placed by, review date; maker-checker to place and to lift (step-up). (2) Every retention and erasure path checks holds through one `privacy.service` function before deleting (a test enumerates the purge jobs). (3) An erasure request on a held subject is answered "held for legal reasons" with the reason in the letter. (4) Holds listed in the access report as "held: yes", never the reason text. (5) Review reminders. |
| **BRD objective** | BO-05; BR-14 |
| **Invariants / review** | Touches every module's retention path; ADR needed (changes retention behaviour); DPDP Rule 8 allows keeping data where law requires; privacy review |
| **Dependencies** | `privacy`, every module with a purge job |
| **Effort** | M |
| **Value** | **M-H**: a documented promise that is not met; prerequisite for FC-06 |
| **Risks** | Holds left forever; mitigated by review dates |
| **Owner questions** | Who may place a hold (owner and principal)? |

## 4. Ranked shortlist (value for effort)

| Rank | Id | Why |
|---|---|---|
| 1 | **FC-02** Appendix IX v2 | S effort, disaffiliation risk, the built page may already be out of date against CBSE circular 1/2026 |
| 2 | **FC-03** DPDP go-live checklist | S effort, time-sensitive (May 2027), turns the built R3 pack into the "compliance partner" story the owner is selling for 2027-28 |
| 3 | **FC-01** Certificate register with expiries | M effort, every school, every inspection; makes Appendix IX trustworthy; base for FC-07, FC-08, FC-09 |
| 4 | **FC-07** Inspection readiness packs | M effort after FC-01; the buyer-visible outcome of all compliance work |
| 5 | **FC-05** Committees register | M effort, always inspected, fills Appendix IX SMC/PTA, needs no new data class |

Next after these: FC-12 legal holds (fixes a documented promise), FC-04 staff safety register (high value but L and a new data class), FC-09 and FC-11 (cheap add-ons). FC-06 only after an owner decision and counsel review. FC-08 only if the owner reopens transport records.

## 5. Not worth building (now)

| Idea | Why not |
|---|---|
| GPS bus tracking, live location for parents | Out of scope (docs/18 §3), hardware and a new data class (children's location), commodity in competitors |
| CCTV integration or footage storage | Out of scope; video of children is a new heavy data class; CBSE only needs the school's own system working (FC-09 records the check) |
| Automatic filing to SARAS, UDISE+, SHe-Box or police portals | No APIs; product rule "SchoolOS prepares and checks, the clerk submits" |
| Psychometric testing of staff inside SchoolOS | Clinical assessment, not records; record only that it was done (FC-04) |
| Police verification by API (CCTNS / citizen portals) | No school-facing API; would add a sub-processor and identity data; record status and evidence only |
| Anonymous student or parent complaint app | Two-way chat and parent logins are out of scope; DPDP grievances already have a register; anonymous allegations need a safeguarding process first (FC-06) |
| A DPDP consent-manager integration (Rule 4, Nov 2026) | Consent managers are optional for fiduciaries; schools rely mostly on the educational exemption; no registered consent managers to integrate with yet (unverified) |
| RTE 25% register | Designed (ADR-0052) but excluded by owner decision D10/D43; nothing new found that changes that |
| AI summarising child-protection or verification records | Against invariants 8, 9 and 14; the value is in the record, not a summary |

## 6. Owner decisions needed

1. **FC-06:** does SchoolOS hold child-protection (POCSO) records at all? If yes, counsel review first.
2. **FC-04:** accept a staff register for people without logins (and the new data class: verification status), without drifting into HR/payroll?
3. **FC-08:** reopen "transport compliance records only" from the out-of-scope list?
4. **FC-03 item 4:** may the control plane read per-school DPDP readiness counts (ADR-0020 amendment)?
5. **FC-02:** a CBSE pilot school's copy of circular 1/2026 and its annexure, to verify the revised Appendix IX (D3).
6. Confirm the AP G.O. on five-yearly fire NOCs and ten-year recognition (number and scope) before the catalogue in FC-01 claims typical validity.

## 7. Claims not verified

- The exact DPDP commencement day (13 or 14 May 2027) and whether MeitY's proposed compression was notified (none found).
- The revised Appendix IX field list (secondary summaries only; the circular copy found is a scanned image).
- The AP order on fire NOC (five years, buildings up to 30 m) and ten-year recognition: G.O. number not found.
- Current CBSE transport and anti-bullying requirements beyond the 2012 circular and 2017 press coverage.
- POSH annual-report filing date (31 Jan in secondary sources) and AP-specific format.
- MoE 2021 safety guideline details on training records (official PDF not read in full).
