# Feature discovery: teaching and academics (FT)

| Field | Value |
|---|---|
| Date | 2026-11-09 |
| Area | Attendance (with teachers on phones), exams and marks, report cards and the NEP 2020 Holistic Progress Card, board assessment schemes, exam lock (YA-04), admission dates for attendance checks (YA-06), promotions and year-end, teacher workload, early warning (M5, invariant 14), staff attendance and leave, substitution |
| Modules read | `apps/api/app/academics`, `insights`, `tenancy`, `students` (promotions, enrolments), `certificates/formats/ap-tc-2027.yaml`, `dq/config/profiles/udise-plus.yaml`, `dq/config/portal_calendar.yaml`, `knowledge/tools`, `authz/roles.yaml`; web `apps/web/src/features/insights`, `promotions`, `readiness`; `docs/guides/class-teacher.md`, `exam-coordinator.md` |
| Status | Research only. Nothing here is decided. Web sources are cited with access date 2026-11-09; most are secondary (news, teacher sites); unverified claims are marked |

## 1. What exists today (short inventory)

| Area | Built | Missing or partial |
|---|---|---|
| Daily attendance | One status per student per day (present, absent, late, leave), month register on A4, month sheet import, optimistic versions (YA-01) (`academics/service.py:311`) | No school calendar: "a school day is a day with at least one mark" (`docs/14-roadmap.md:286`); no working-day count; dates checked only against the year, not the enrolment (YA-06, `academics/service.py:228`); no offline tolerance on phones |
| Exams and marks | Exam = name + date in the current year (`academics/models.py`, `service.py:526`); marks per student and free-text subject with a maximum; sheet import; overall % per exam | No exam type, term, weight or components; no subject catalogue (subjects are free text, matched case-insensitively); no grading scale; no edit, lock or history (YA-04); "Grading scales and CCE/FA/SA structure (not modelled)" (`docs/14-roadmap.md:274`) |
| Who enters marks | Class teacher (scoped to their sections) and exam coordinator (school-wide) (`authz/roles.yaml:334-340`, `:314-316`) | The `teacher` role (subject teacher) has no attendance or marks grants at all (`authz/roles.yaml:349-357`; guide: "Sections 1-4 below do not apply to the teacher role", `docs/guides/class-teacher.md:15`) |
| Report cards | None. The access report even says "SchoolOS stores no grade" (`academics/subjects.py:4`) | Term and annual report cards; HPC |
| Promotions | Year-end bulk move with preview, undo, fingerprint and idempotency; held-back students are a hand-typed list (`students/service.py:2797`) | No link to results; no record of the result (passed/promoted/detained) for UDISE+ progression |
| TC | AP TC asks "Whether qualified for promotion", "Number of working days up to the date of leaving" and "Number of working days the pupil attended" as typed inputs (`certificates/formats/ap-tc-2027.yaml:115-122`) | Not prefilled from the register |
| UDISE+ profile | "Status in the previous academic year (class studied, result)" is listed with `maps_to: null` (`dq/config/profiles/udise-plus.yaml:83`) | Result, % marks and days attended of the previous year are not produced |
| Early warning (M5) | Five deterministic rules (`insights/rules.yaml`), flags with owner, actions, notes (encrypted), timeline, principal settings | Open PO questions since 2026-09-29: counsellor/vice-principal role, management counts, exam coordinator access, the AP streak definition with holidays (`docs/14-roadmap.md:288-299`) |
| Ask the school | Tools for students, counts, documents, findings, due dates, fees, history (`knowledge/tools/`) | No attendance or marks tool, although the class-teacher guide gives "Which students in Class VII-B were absent more than five days in October?" as the example question (`docs/guides/class-teacher.md:104`) |
| Portal calendar | UDISE+, APAAR, CBSE registration/LOC, CISCE, BSEAP nominal roll windows (`dq/config/portal_calendar.yaml`) | No CBSE attendance-shortage or internal-assessment upload windows |
| Staff attendance, leave, substitution | Nothing; staff exist only as users with roles | See §4 (not recommended now) |

## 2. External facts used (access date 2026-11-09)

| Fact | Source | Confidence |
|---|---|---|
| CBSE requires 75% attendance for Class X and XII board exams (Examination Bye-Laws rules 13-14); up to 25% condonation for medical and other serious reasons with documents; schools must keep daily registers signed by the class teacher, warn parents in writing and keep a record of that; surprise inspections; records cannot change after shortage cases are submitted | [Careers360](https://news.careers360.com/cbse-sticks-to-75-percent-attendance-for-class-10-12-board-exam-2026/amp); official circular [cbse.gov.in PDF](https://www.cbse.gov.in/cbsenew/documents/Strict_Compliance_attendance_Eligibility_05082025.pdf) (returned 403 to our fetch, not read directly) | Rule: high. 2025-26 dates (attendance to 1 Jan 2026, shortage cases by 7 Jan, CBSE decision by 7 Feb) come from search summaries of secondary sites: **unverified**; the 2026-27 circular was not found |
| CBSE Class X: 80 theory + 20 internal assessment per subject; Circular 04/2026 asked schools to upload practical, project and internal assessment marks by 14 Feb 2026; Class X has two board exams from 2026 (second optional) | [NewsX](https://www.newsx.com/education/cbse-issues-deadline-for-practical-internal-assessment-marks-upload-by-february-14-165876/), [Free Press Journal](https://www.freepressjournal.in/education/cbse-confirms-class-10-board-exams-twice-a-year-from-2026) | Medium. The breakdown of the 20 marks (periodic tests, multiple assessment, portfolio, subject enrichment) differs between sources: **unverified** |
| AP state schedule 2026-27: FA1 early Aug, FA2 5-8 Oct, SA1 9-18 Nov 2026, FA3 4-7 Jan, FA4 8-11 Feb, SA2 March 2027; FA reduced to 20 and SA to 80 in the result | [Careers360 SA1](https://school.careers360.com/articles/andhra-pradesh-sa-1-exam-time-table-2026), [findmycollege FA2](https://articles.findmycollege.com/news/ap-fa2-time-table-2026-27-out-exams-for-classes-1-12-from-october-5-8/), [teachernews](https://teachernews.in/?p=8049) | Medium; sources disagree on some dates. Whether **private** AP schools must follow FA/SA is not confirmed |
| Whether AP SSC 2027 keeps 20 internal marks per subject | [Aakash](https://www.aakash.ac.in/boards/ap-ssc-class-10-exam-pattern-2025) (80+20, 2025) vs [PW](https://www.pw.live/bseap/exams/ap-class-10-exam-pattern) (100-mark papers) | **Unverified, conflicting** |
| Andhra Pradesh kept the no-detention policy for Classes I-VIII after the RTE (Amendment) Rules 2024, which only bind central-government schools | [ThePrint, 23 Dec 2024](https://theprint.in/india/education/centre-amends-rte-rules-allows-states-uts-to-end-no-detention-policy-for-classes-5-and-8/2416571) | Medium; no later AP order found, so a change since Dec 2024 is possible |
| PARAKH Holistic Progress Card exists for foundational, preparatory and middle stages (self, peer, parent and teacher inputs); the secondary-stage card is not confirmed; CBSE issued an implementation manual for the foundational stage; AP announced HPCs for **state-run** schools | [PARAKH blog](https://parakh.ncert.gov.in/index.php/blogs?year=All), [webjosh/The Hindu](https://webjosh.com/article/students-will-be-provided-holistic-progress-cards-in-state-run-schools-says-andhra-pradesh-hrd-minister-lokesh-1733586152-5988), [Daily Pioneer (Gujarat pilot 2026-27)](https://dailypioneer.com/news/gujarat-first-to-launch-nep-aligned-digital-progress-card) | Medium. Mandatory HPC for AP private or CBSE schools in 2026-27: **unverified** |
| UDISE+ student module asks, per student, the previous year's result, % marks and days attended; promotion is done in the Progression module | [Scribd sample screen](https://www.scribd.com/document/920201015/UDISE-Student-Module), [EFA India](https://educationforallinindia.com/?p=10433) | Medium (third-party copies of the screen) |

## 3. Proposals

### FT-01 · Exam lock and marks history (YA-04)

- **Status:** built 2026-11-09 with the lead defaults of D84 (principal-only unlock with step-up and a reason, no second person for corrections, history of every change after the first entry); `0084_exam_lock_enrol`, FR-MRK-008..FR-MRK-015. Not built here: the "Lock" prompt when report cards are generated (FT-04).

- **Users:** exam coordinator, principal; protects parents and the school.
- **Problem:** any `marks.record` holder can change an exam's marks at any time, including after results are shared; the audit holds counts only, so an old value cannot be recovered (`docs/security/audit-2026-11-09-academics-jobs.md` YA-04; `academics/service.py:667`). Deferred as a feature by D83 (`docs/14-roadmap.md:575`).
- **Today:** optimistic versions per mark (YA-05) only.
- **Proposal:**
  - `exams.locked_at/locked_by`; `POST /exams/{id}:lock` and `:unlock` (`exam.manage`), audited `exam.locked` / `exam.unlocked`.
  - `record_marks` and sheet import answer 409 `exam_locked`.
  - Unlock by the principal only, with step-up and a reason (recommended over maker-checker for every correction: small schools have few approvers).
  - Append-only `exam_mark_history` (previous value, who, when, reason when locked), C2, purged with the module; shown as "Changed after lock" on the grid.
  - Report cards (FT-04) print only from locked exams.
- **BRD objective:** BO-05 (earn trust with sensitive data).
- **Invariants:** 7 (audit in the same transaction), 12 (expand-only migration). No ADR needed beyond recording the unlock policy as a lead/owner decision.
- **Dependencies:** none. **Effort:** S-M. **Value:** H: cheap, closes an audit item, and is a precondition for credible report cards.
- **Risks:** a forgotten lock before results are shared; mitigate with a "Lock" prompt when report cards are generated.
- **Owner questions:** who may unlock (principal only?); does a correction after the lock need a second person?

### FT-02 · Subject catalogue and subject-teacher marks entry

- **Users:** subject teacher, class teacher, exam coordinator.
- **Problem:** in Indian schools subject teachers enter their own subject's marks across several sections. Today the `teacher` role cannot record or read marks or attendance (`authz/roles.yaml:349-357`), and a class teacher's scope covers every subject of the section. Subjects are free text per mark, so "Maths" and "Mathematics" become two subjects and break the `course_decline` comparison and any report card.
- **Today:** section-scoped `marks.record`; class teacher per section (`tenancy/models.py:139`).
- **Proposal:**
  - Subject catalogue per class and year (code, name, max marks default, optional/elective flag, language group), seeded from versioned defaults per board.
  - Allocation table: teacher membership × section × subject (this is **not** a timetable: no periods, rooms or days).
  - `marks.record` for a subject teacher limited to (section, subject) pairs; marks grid shows only those columns.
  - Sheet import maps column headers to catalogue subjects, refusing unknown ones with the closest match.
  - Existing free-text subjects migrated by a one-off mapping screen.
- **BRD objective:** BO-03 (reduce duplicate entry: teachers stop passing paper mark lists to the class teacher).
- **Invariants:** 2 and 3 (a new scope dimension: needs BOLA and authz-matrix tests); an ADR for the scope change (authz model).
- **Dependencies:** none; FT-03, FT-04 and FT-10 build on it. **Effort:** M-L. **Value:** H: without it report cards and board internal-assessment sheets are not workable.
- **Risks:** widening the authz model; keep it to marks (attendance stays per section).
- **Owner questions:** should subject teachers also see the section's attendance (read-only)?

### FT-03 · Assessment schemes per board (FA/SA, CBSE internal assessment, CISCE)

- **Users:** exam coordinator, principal.
- **Problem:** an exam is only a name and a date. AP schools run FA1-FA4 (20%) and SA1-SA2 (80%) on a state schedule (§2); CBSE Class X is 80 board + 20 internal assessment built from several components (§2). Neither weights nor terms nor grades exist (`docs/14-roadmap.md:274`).
- **Today:** `sis.exams(name, held_on)`.
- **Proposal:**
  - Versioned scheme files per board and stage (e.g. `bseap-cce`, `cbse-secondary`, `cisce`), each with `source` and `verified: false` (D3), defining terms, exam types, components, weights and the grade scale.
  - Exam gets `type`, `term` and `component` from the school's chosen scheme; the school may override weights within the scheme's bounds.
  - Computed term and annual results per student and subject (weighted), grade from the scale; stored as derived values, recomputed on change, never typed.
  - Absent papers keep FR-MRK-005 behaviour (left out), with the scheme able to say otherwise.
- **BRD objective:** BO-03; enables BO-01 for internal-assessment uploads (FT-10).
- **Invariants:** 13 (schemes and thresholds in config). No new data class.
- **Dependencies:** FT-02. **Effort:** M-L. **Value:** H for selling the "Office" tier; low on its own.
- **Risks:** many school-specific variations; mitigate with bounded overrides, not free formulas.
- **Owner questions:** which schemes first (BSEAP state pattern and CBSE secondary are proposed); do AP private schools follow the state FA/SA calendar (to confirm with pilots)?

### FT-04 · Term and annual report cards

- **Users:** class teacher (remarks), principal (approve and sign), parents (receive a printed card).
- **Problem:** report cards are a checklist item every competitor sells (`docs/research/2026-10-market/report.md`, "checklist modules"), and schools that run SchoolOS "alongside their ERP" keep the ERP for this alone. SchoolOS has no grade or report card at all.
- **Today:** per-exam percentage only; print-job PDF pipeline exists (certificates, slips).
- **Proposal:**
  - Report-card templates (A4, HTML/CSS, Chromium print job), one per scheme, school logo and signatures.
  - Content: subjects and components from FT-03, attendance from the register (days present / working days from FT-06), class teacher's remark (typed, length-limited, Aadhaar-checked), principal's approval.
  - Only from locked exams (FT-01); batch per section; frozen PDF kept as a document.
  - Phase 2 (separate decision): a parent link page for the report card (new page kind under ADR-0046, ADR amendment and privacy review).
  - No ranks by default (HPC and child-welfare practice discourage public ranking); a school may print a rank only by explicit setting.
- **BRD objective:** BO-03; BO-06 (willingness to pay: removes a reason to keep the old ERP).
- **Invariants:** 5 (no PII in logs), 11 (synthetic data in templates), 14 (remarks are educational; never derived from behaviour notes or flags automatically).
- **Dependencies:** FT-01, FT-02, FT-03, FT-06. **Effort:** L. **Value:** H (table stakes) but not a differentiator.
- **Risks:** template sprawl; keep to 2-3 formats, configurable fields only.
- **Owner questions:** remarks free text or from a phrase bank; ranks allowed at all; parent page in 2027-28?

### FT-05 · Holistic Progress Card (PARAKH) for Classes I-VIII

- **Users:** class teacher, principal, parents.
- **Problem:** NEP 2020 / PARAKH HPC formats exist for foundational, preparatory and middle stages, with teacher, self, peer and parent inputs (§2). CBSE has a foundational-stage manual; AP announced HPCs for state-run schools. Mandatory use by AP private schools is **unverified**.
- **Today:** nothing.
- **Proposal:**
  - An HPC report-card format (FT-04 template family) per stage, with descriptor-level rubrics instead of marks.
  - Teacher observations entered per competency, per term.
  - Self-, peer- and parent-feedback pages as **paper** forms printed for the class and typed back (no child accounts, no parent logins: BRD §7.3).
  - Stays a print output; no AI-written descriptors.
- **BRD objective:** BO-03; competitive parity for CBSE schools.
- **Invariants:** 14; a **new data class** (children's self-assessment and peer comments) → ADR and privacy review (§5 guardrail); retention per record class.
- **Dependencies:** FT-04. **Effort:** L. **Value:** M now (rises to H if AP or CBSE makes it mandatory for private schools).
- **Risks:** format churn from PARAKH and states; peer feedback about children is sensitive.
- **Owner questions:** is HPC a 2027-28 item, or do pilots ask for it now?

### FT-06 · School calendar: working days and holidays

- **Users:** principal, office admin, class teacher; feeds the clerk's TC and UDISE+ work.
- **Problem:** with no calendar, SchoolOS cannot say "days attended out of working days", which the AP TC (`ap-tc-2027.yaml:118-122`), UDISE+ (days attended in the previous year, §2), report cards and the CBSE 75% rule all need. The AP three-day streak rule cannot tell a holiday from an unmarked day (`docs/14-roadmap.md:286`, PO question 5).
- **Today:** "school day = a date with any mark in the section".
- **Proposal:**
  - Per academic year: working-day pattern (e.g. Mon-Sat, second Saturday off), holidays and vacations (Dasara, Sankranti, summer), with optional per-class exceptions (exam days, half days).
  - Seed suggestions from a versioned, unverified state calendar (D3), confirmed by the school.
  - Attendance screen warns when marking a holiday; register shows working days and "not marked" days.
  - Counts per student: working days, present, late, leave, absent (used by FT-04, FT-07, FT-08 and the TC).
  - Insights rules read working days (streak across a holiday continues; unmarked working days are visible to the principal).
- **BRD objective:** BO-03, BO-08.
- **Invariants:** 13 (calendar defaults in config); no PII.
- **Dependencies:** none. **Effort:** M. **Value:** H as an enabler for four other features.
- **Risks:** changing streak semantics changes flag counts; version the rules (`rules.yaml` v2).
- **Owner questions:** confirm the AP absence follow-up definition against the department circular (still open since M5).

### FT-07 · Board attendance eligibility (CBSE 75%) with shortage pack

- **Users:** exam coordinator, principal, class teacher of Class X/XII.
- **Problem:** CBSE refuses board-exam entry below 75% attendance unless condoned; schools must warn parents in writing, keep the record and send shortage cases with documents by a fixed date, after which records are frozen; surprise inspections check registers (§2). A school finding shortages in January is too late.
- **Today:** attendance rate exists only as an early-warning rule (30 marked days, private to class teacher and principal).
- **Proposal:**
  - A readiness-style view for board classes (CBSE X and XII; CISCE if confirmed): attendance to date vs working days (FT-06), projected % at the cut-off, "at risk" and "short" lists.
  - Printable parent warning letter per student (English; record of issue kept with date and method), never sent automatically; WhatsApp only through an approved utility template and opt-in.
  - Condonation pack: per student, reason and evidence documents (medical certificate as a document), the school's recommendation proforma as a print.
  - Portal calendar entries: attendance cut-off and shortage submission dates (unverified, D3).
  - Kept separate from M5 insights: this is an exam-eligibility record (C2), visible to exam coordinator and principal, not a welfare flag.
- **BRD objective:** BO-01 (avoidable board errors), BO-05.
- **Invariants:** 6 (no automatic decision: the list informs, the school decides); PRV-005; medical documents are C3 → document ACL and retention per record class; privacy review for the evidence documents.
- **Dependencies:** FT-06. **Effort:** M. **Value:** H for CBSE schools, low for BSEAP (no equivalent confirmed).
- **Risks:** wrong cut-off dates; keep them as school-confirmed calendar entries.
- **Owner questions:** build for 2026-27 (the cut-off is about 1 Jan 2027 if the 2025-26 pattern repeats) or 2027-28?

### FT-08 · Year-end progression pack (results → promotion → UDISE+ → TC)

- **Users:** principal, exam coordinator, office clerk.
- **Problem:** promotions take a hand-typed held-back list (`students/service.py:2797`) and record no result; the UDISE+ progression asks each child's result, % marks and days attended (`udise-plus.yaml:83` maps it to nothing); the AP TC asks "qualified for promotion", working days and days attended as typed inputs (`ap-tc-2027.yaml:115-122`). The same facts are typed three times (BO-03).
- **Today:** promotions with preview and undo; TC inputs typed.
- **Proposal:**
  - Annual result per student (passed / promoted / detained / not appeared) proposed from FT-03 results and confirmed by the principal (PRV-005: a person decides).
  - Promotion preview pre-fills held-back students from confirmed results; in classes I-VIII warns that AP follows no-detention (config, unverified, D3).
  - UDISE+ progression check sheet: previous class, result, % marks, days attended per student, with blanks listed as findings.
  - TC preview pre-fills working days, days attended and "qualified for promotion" as "From SchoolOS" suggestions the clerk can change (never silently).
- **BRD objective:** BO-03, BO-01.
- **Invariants:** 6 (suggestions only; the TC still shows each value's source), PRV-005, 12.
- **Dependencies:** FT-06 (days), FT-03 (results; % from per-exam marks is an acceptable first version). **Effort:** M. **Value:** H: ties academics to the portal wedge.
- **Risks:** % definition differs by board; keep the formula in the scheme config.
- **Owner questions:** may the principal confirm results for a whole section in one step?

### FT-09 · Real admission dates for attendance checks (YA-06)

- **Status:** built 2026-11-09 (D84); `0084_exam_lock_enrol` with a one-off backfill in the migration; FR-ATT-010..FR-ATT-015. The onboarding path is the spreadsheet import and register extraction (there is no separate onboarding wizard in the API).

- **Users:** class teacher, office clerk.
- **Problem:** a child admitted on 1 November can be marked for July; those marks feed absence alerts and early-warning flags. The fix was blocked because every enrolment without an explicit date gets `started_on = today` (`students/service.py:1331`), so a November onboarding would refuse June-October backfill (YA-06).
- **Today:** `admission_date` is already a C2 identity attribute with register sources (`students/attributes.yaml:120`) and is mapped by every import template.
- **Proposal:**
  - Imports and the onboarding wizard set `started_on` to the later of the year start and the admission date when it falls inside the year; otherwise the year start; flag `started_on_explicit`.
  - Mid-year section moves keep their real move date.
  - Then refuse attendance before an explicit start (422 `before_enrolment`, per-row issue in sheets); implicit starts are not checked.
  - One-off backfill for existing data using the same rule.
- **BRD objective:** BO-05; BO-08 (no false flags).
- **Invariants:** 6 (no change to the admission register itself), 12.
- **Dependencies:** none. **Effort:** S. **Value:** M.
- **Owner questions:** none beyond confirming the rule.

### FT-10 · Board internal-assessment pre-check sheets

- **Users:** exam coordinator.
- **Problem:** CBSE schools upload practical, project and internal-assessment marks for Classes X and XII in a short window (by 14 Feb 2026 last cycle; about 30% of schools were late, §2), and uploaded marks are final. CISCE has similar internal marks (not checked here). AP SSC internal marks are **unverified** for 2027.
- **Today:** board pre-check profiles exist for registration and LOC, not for marks.
- **Proposal:**
  - A pre-check profile per board marks upload: every candidate (from the LOC list) × subject has a component mark within its maximum; missing and out-of-range listed as findings.
  - A check sheet in the portal's column order to type or upload from (no automated submission).
  - Requires the exam lock (FT-01) before the sheet is final.
  - Portal calendar window for the upload (unverified, D3).
- **BRD objective:** BO-01.
- **Invariants:** 6, 13; profiles `verified: false` until a school confirms.
- **Dependencies:** FT-02, FT-03, FT-01. **Effort:** M. **Value:** M-H for CBSE schools (they are the minority in AP).
- **Owner questions:** worth doing for the Feb 2027 window, or 2028?

### FT-11 · Attendance that survives a bad network on a teacher's phone

- **Users:** class teacher (on a personal phone, patchy mobile data).
- **Problem:** the attendance screen is responsive (`features/insights/AttendanceScreen.tsx`) but a failed save loses the teacher's work and there is no "absentees only" entry; the product brief stresses patchy internet (CLAUDE.md §1). If teachers fall back to paper, the absence alerts and M5 flags starve.
- **Today:** whole-day save, 412 reload on conflicts (YA-01).
- **Proposal:**
  - Keep the unsaved day in memory and `sessionStorage` (student ids and statuses only, no names, cleared on sign-out), retry with backoff, explicit "Not saved yet" state.
  - "Type roll numbers of absentees" entry mode (fast on a phone).
  - Save the day idempotently (Idempotency-Key) so a retried save cannot double-count.
  - No service worker or offline student list (shared and personal devices; DPDP).
- **BRD objective:** BO-08 (attendance on time feeds follow-up), BO-07.
- **Invariants:** 5 (no PII in browser storage beyond ids).
- **Dependencies:** none. **Effort:** S-M. **Value:** M-H (adoption by teachers).
- **Owner questions:** none; needs a check with pilot teachers.

### FT-12 · Read-only attendance and marks tool for "Ask the school"

- **Users:** class teacher, principal, exam coordinator.
- **Problem:** the class-teacher guide promises an attendance question (`docs/guides/class-teacher.md:104`) but no knowledge tool reads attendance or marks (`knowledge/tools/`). Either the guide or the product is wrong.
- **Today:** count, student, findings, fees tools.
- **Proposal:**
  - Fix the guide example now (doc bug).
  - A read-only tool: counts and lists of students in scope by attendance (absent ≥ n days in a period) or by exam result (below x% in an exam), through `academics.service` with the caller's scope, filtered in SQL before anything reaches the model.
  - Never reads insights (flags, notes) (invariant 14, M5 AC4).
  - Minimum fields to the LLM (first name + section + count), small-cell suppression as for counts; eval cases added.
- **BRD objective:** BO-02.
- **Invariants:** 8, 9, 14; docs/06 tool catalogue change and privacy review (attendance and marks newly reach the LLM provider).
- **Dependencies:** none. **Effort:** M. **Value:** M.
- **Owner questions:** is sending per-child attendance counts to Vertex (ZDR) acceptable, or counts only?

### FT-13 · Early-warning follow-ups left open since M5

- **Users:** principal, counsellor, management, exam coordinator.
- **Problem:** PO questions 2, 4, 5 and 11 of M5 are unanswered (`docs/14-roadmap.md:288-299`); no counsellor role; management sees nothing.
- **Proposal:**
  - A `counsellor` system role with insights on the whole school (PRV-004 already names it).
  - Management: counts only (raised, actioned on time, overdue), no names.
  - Exam coordinator: course flags read-only (if the owner agrees).
  - A "child safety concern" note category routed to the principal only, with a printed referral record, for POCSO-type concerns (no automatic reporting; the school decides).
- **BRD objective:** BO-08.
- **Invariants:** 14, PRV-004/005; the safety category is a new sensitive class → ADR and privacy review.
- **Dependencies:** FT-06 for the streak definition. **Effort:** M. **Value:** M.
- **Owner questions:** the four M5 questions above.

### FT-14 · Marks and attendance completeness for the principal (teacher workload)

- **Users:** principal, exam coordinator.
- **Problem:** before report cards or board uploads the principal chases teachers by phone to find which papers are not entered; nothing shows missing marks or sections whose attendance was not taken today.
- **Proposal:**
  - "Today: attendance taken in 14 of 16 sections" on Home.
  - Per exam: grid of section × subject with entered / partly entered / missing (from FT-02 allocation).
  - A reminder notification to the allocated teacher, in-app only.
  - Framed as task completeness per section, never as a teacher performance score or ranking.
- **BRD objective:** BO-03; BO-08.
- **Invariants:** none new (no student PII; staff are named only to the principal).
- **Dependencies:** FT-02 for per-subject owners (section-level works without it). **Effort:** S. **Value:** M-H.
- **Owner questions:** may teachers see each other's completeness, or only the principal?

## 4. Not worth building (now)

| Idea | Why not |
|---|---|
| Staff attendance and leave | Not in docs/18 "Kept out of scope" by name, but it sits next to payroll and biometric attendance, which are; it is commoditised and given away free; it adds a new data class (employee records) needing an ADR and privacy review; pilots should ask first. A later minimal "teacher absent today" list could feed FT-14 |
| Substitution | Needs a timetable (out of scope by owner decision). FT-02's allocation is not a timetable and should not grow into one |
| Period-wise attendance | Needs a timetable; one status per day is what the AP register and CBSE rule use |
| Timetable and LMS/homework (reopen proposal considered) | **No reopen recommended.** Free tools (Teachmint, Google Classroom) cover them, they do not move any BRD objective, and the wedge is portal data |
| AI-written report-card remarks or HPC descriptors | AI write capability on children's records (ADR + privacy review), generic content is not sellable (market report), and a wrong remark on a child's card is a trust risk |
| AI risk scores or predicted board results | Flags must come from deterministic rules a teacher can read (M5); PRV-005 |
| Rank lists and merit comparisons sent to parents | HPC direction and child-welfare practice discourage ranking; a print option only (FT-04) |
| Biometric or face attendance for students or staff | Out of scope (BRD §7.3); hardware and biometric data |

## 5. Ranked shortlist (value ÷ effort)

1. **FT-01 Exam lock and marks history** (S-M, H): closes YA-04 and is a precondition for report cards and board uploads.
2. **FT-06 School calendar** (M, H): one model that unlocks TC days, UDISE+ days attended, CBSE 75% and correct streak flags.
3. **FT-08 Year-end progression pack** (M, H): turns marks and attendance into the UDISE+ progression and TC values the clerk types today; ties academics to the wedge.
4. **FT-02 Subject catalogue and subject-teacher marks** (M-L, H): the missing piece that lets real schools enter marks; needed for FT-03/04/10.
5. **FT-07 CBSE attendance eligibility** (M, H for CBSE): time-sensitive (cut-off about 1 Jan 2027 if the last cycle repeats).

Close behind: FT-09 (S; do it in the same wave as FT-06), FT-14 (S), FT-11 (S-M).

Suggested order: FT-01 + FT-09 → FT-06 → FT-02 → FT-08 + FT-07 → FT-03 → FT-04 → FT-10, FT-05.

## 6. Dates to watch (2026-27)

| When | What | Confidence |
|---|---|---|
| about 1 Jan 2027 / about 7 Jan 2027 | CBSE attendance counted to; shortage cases due (pattern of 2025-26) | Inferred, unverified |
| about 1 Jan to mid-Feb 2027 | CBSE practical and internal-assessment marks upload, Class X/XII | Inferred from Circular 04/2026 |
| 4-7 Jan, 8-11 Feb, March 2027 | AP FA3, FA4, SA2 (state schedule) | Secondary sources |
| March-April 2027 | Year-end results, report cards, promotions | Usual practice |
| July-September 2027 | UDISE+ 2027-28 progression and freeze (`portal_calendar.yaml` `udise_plus_2027_freeze`) | Calendar in product, unverified |

## 7. Questions for the owner

1. Exam unlock: principal only with step-up and a reason, or maker-checker for every post-lock correction? (FT-01)
2. Should subject teachers enter marks for their subjects (FT-02)? This needs an authz ADR.
3. Report cards and HPC: in scope for 2026-27 (they reverse no BRD non-goal, but they are not in docs/18)? Ranks allowed? Parent link page later?
4. CBSE attendance eligibility for the January 2027 cut-off, or 2027-28? (FT-07)
5. May per-child attendance counts reach the LLM for Ask (FT-12), or counts only?
6. The M5 questions still open: counsellor role, management counts, exam coordinator access to course flags, the AP absence-streak definition.
7. Confirm with pilot schools: do AP private schools follow the state FA/SA calendar and HPC; does AP SSC 2027 have internal marks?
