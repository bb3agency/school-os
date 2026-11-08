# Feature deep dive: school office and government/board portals (FO)

| Field | Value |
|---|---|
| Date | 2026-11-09 (web sources accessed 2026-11-09) |
| Area | Admissions, admission register (BR-01), student records and data quality, certificates, registers, UDISE+ 2026-27, APAAR/PEN, BSEAP SSC, CBSE (registration, LOC, OASIS, SARAS), CISCE, RTE 12(1)(c), staff records for portals, portal calendar |
| Modules read | `students`, `imports`, `extraction`, `dq` (incl. `calendar.py`, `readiness*.py`, `config/`), `changes`, `certificates` (incl. `formats/ap-tc-2027.yaml`), `exports`, `apaar`, `tenancy`, `admin`, `disclosure/derived.py`; web `features/students`, `readiness`, `portal-calendar`, `promotions` |
| Status | Research only. Every proposal still needs owner prioritisation. No code was changed |

Source quality: most portal facts below come from secondary sources (news, teacher blogs and guides), as in the October research. A claim marked **(unverified)** was not confirmed on an official `.gov.in` or board page. Owner decision D3 still applies: every new format or date ships with `verified: false`.

## 1. What the office already has (baseline)

SchoolOS already covers the core of this area. The proposals below build on it and do not replace it.

- **Readiness engine.** Per-source values with a character-level diff and a fix owner per difference: parent via Aadhaar, the school via UDISE+, or the school register through a change request (`apps/api/app/dq/config/readiness.yaml:21-34`). It also has parent verification slips (`dq/slip.py`), class dashboards and profiles for BSEAP SSC 2027, UDISE+ 2026-27, APAAR, CBSE registration/LOC 2027 and CISCE 2026/2027 (`dq/config/profiles/`). Every profile is `verified: false`.
- **Identity checks and changes.** The APAAR consent register with refusal (`apaar/service.py`), the PEN/APAAR duplicate guard at admission (FR-STU-018, `students/service.py:766`), maker-checker change requests with evidence and a correction memo (`changes/memo.py`).
- **Certificates and registers.** Certificates (TC, bonafide, study, conduct) plus the AP TC format `ap-tc-2027`. Register print views: the TC counterfoil, the certificate register, and the admission and withdrawal register (`certificates/api.py:309-357`).
- **Imports and exports.** Imports with a 24-hour revert, a template library including a UDISE+ student list, and register-photo extraction (fake OCR provider only, D2). Pre-check exports per profile.
- **Portal calendar.** It covers UDISE+ freeze, the APAAR drive, CBSE registration and LOC, CISCE registration and BSEAP nominal-roll corrections for 2026-27 and 2027-28 (`dq/config/portal_calendar.yaml:40-216`), with reminders at 30/14/7/1 days.

The round-2 audit of this area (`docs/security/audit-2026-11-09-student-records.md`, YS-01..04) found only correctness and concurrency bugs, all fixed. It found no functional gaps. The gaps below come from comparing the code with real office workflows and portal rules.

## 2. Proposals

### FO-01 · Follow-up on fixes outside the school (parent Aadhaar updates and UDISE+ requests)

- **Users:** office clerk (primary), principal, exam coordinator.
- **Problem and evidence:** The readiness engine says *who* must fix a difference. It does not track whether the fix is happening.
  - A `needs_parent` student stays the same until someone records a new Aadhaar-as-printed value. Nothing records "slip handed on 12 Dec", "parent says Aadhaar centre visit on 20 Dec" or "no response after 14 days".
  - `sis.dq_findings` has open/resolved/waived states and no follow-up fields (`apps/api/app/dq/models.py:30-66`).
  - For March 2026, BSEAP accepted corrections **only where they matched Aadhaar**. It used a three-day window (26-28 Feb), and 3,497 schools asked for corrections for 9,986 students ([tlm4all press note](https://www.tlm4all.com/2026/02/ap-ssc-pe--2026-nominal-rolls-correction.html), secondary).
  - An Aadhaar update takes the family days to weeks, so the school has to chase it from December, not in February.
- **Exists today:** readiness status per student (`blocked`, `needs_school`, `needs_parent`, `ready`; `readiness.yaml:38-43`), slips, findings, and calendar reminders with readiness counts (ADR-0053).
- **Proposal:**
  - Add a follow-up log per readiness student and fix owner: `slip_given`, `parent_contacted` (channel only, as D56), `promised_by` date, `update_reported`, `rechecked`. The log is append-only and has no free-text names.
  - Add an "Overdue follow-ups" list per class: needs-parent students with no contact in N days, or a passed promise date.
  - Add a dashboard line: "Waiting on parents: 18 (7 overdue)". The calendar reminder carries the overdue count for the window.
  - Optionally offer a WhatsApp utility template "please update Aadhaar before <date>" through the existing `messaging` module, with opt-in only, the school's own number and no new data.
  - When a new Aadhaar-as-printed value matches, the follow-up closes on the next readiness run.
- **BRD objective:** BO-01 (zero avoidable submission errors). Users: clerk, exam coordinator.
- **Invariants / compliance:**
  - No Aadhaar numbers (inv. 4); only dates and codes are stored.
  - Audit with ids only (inv. 5, 7).
  - The WhatsApp nudge uses a new utility template. ADR-0043 covers templates and opt-in, so no ADR is needed. The template category must stay utility (BR-18).
  - No new data class.
- **Dependencies:** `dq` readiness, `messaging` (optional), portal calendar.
- **Effort:** M. **Value:** H. This is the core of the Feb 2027 wedge: the check is only worth money if the fixes land before the window.
- **Risks:** Clerks may not record contacts. Mitigation: one-click "slip given" when slips are printed in bulk.
- **Owner questions:** Should the nudge message ship in R1.x, or should follow-up stay staff-side only?

### FO-02 · UDISE+ correction request pack for the MEO / MIS coordinator

- **Users:** office clerk, principal (signs).
- **Problem and evidence:**
  - Schools generally cannot edit name or DOB in UDISE+ themselves. Requests go to Block or District MIS coordinators ([EFA India](https://educationforallinindia.com/udise-2026-27-answers-to-the-questions-parents-teachers-and-school-heads-are-actually-asking/), secondary).
  - SchoolOS's route for this owner is a single sentence, "Update UDISE+ after correcting the school record" (`dq/config/explanations.yaml:45-46`).
  - The fix owner `school_udise` (`readiness.yaml:25`) produces no artefact the MEO office can act on.
- **Exists today:** findings with `ROUTE-UDISE`, the correction memo for the paper register (`changes/memo.py`), and pre-check sheets.
- **Proposal:**
  - Add a printable A4 "UDISE+ correction request" from the readiness list. It shows the school UDISE code and, per student: PEN, field, the value in UDISE+, the correct value and its source (admission register, with the change request number when the register itself was corrected), and the evidence document type held (birth certificate, Aadhaar-as-printed copy seen).
  - Group the requests by field type (name, DOB, gender), as MIS offices batch them.
  - Track each request batch: `prepared`, `sent` (date, to whom by role), `done` (rechecked by the next UDISE+ import), `rejected`.
  - Never print an Aadhaar number (inv. 4). For the "Aadhaar-as-printed name", print "matches Aadhaar: yes/no" only.
  - Audit (`export`-style, counts only).
- **BRD objective:** BO-01, BO-03. Users: clerk, principal.
- **Invariants / compliance:** inv. 4, 6 (a request, never a change), 7. No new data class: it is an export of existing C1/C2 fields, so it needs `export.portal`.
- **Dependencies:** `dq` readiness, `exports` print jobs; FO-04 helps the recheck.
- **Effort:** S-M. **Value:** H. It turns a finding into the exact paper the MEO office needs, at almost no cost.
- **Risks:** Each district may ask for its own format. Keep it versioned and `verified: false`, and confirm with a pilot school.
- **Owner questions:** Can a pilot school show the format its MEO accepts today (letter, Excel or the MIS coordinator's own sheet)?

### FO-03 · UDISE+ movement checklist (transfer-out, leavers, new admissions, progression)

- **Users:** office clerk.
- **Problem and evidence:**
  - Creating a fresh record instead of importing by PEN can leave a child "active" in two schools ([EFA India 2025-26 analysis](https://educationforallinindia.com/?p=25804), secondary).
  - The receiving school sees a child in its Transfer-In list only after the previous school has marked the TC in UDISE+ ([Schoolites guide](https://schoolites.com/udise/student-transfer), unverified).
  - Progression must be finished before the new year's entries. A Delhi circular dated 24 Jul 2026 warns that incomplete progression "may adversely affect students' enrolment and migration" ([edudel.nic.in](https://www.edudel.nic.in/upload/upload_2025_26/SamagraShiksha_CircularCompletionofPendingStudentProgressionActivityintheUDISEPortalfortheAcademicYear2025_26_dt_24072026_1202_1215.pdf), official, Delhi).
  - SchoolOS ends enrolment when a TC is issued (FR-CERT-005, `students/service.py:1553`) and runs promotions (`features/promotions`). It never tells the clerk to repeat the same step in UDISE+.
- **Exists today:** TC withdrawal, promotions, the transfer-in by PEN check, and the UDISE+ import as source `udise_plus` (DQ-011).
- **Proposal:**
  - Add a "To do in UDISE+" list generated from SchoolOS events: TC issued (mark transferred in UDISE+), left without TC (dropbox), new admission without PEN (create or import in UDISE+), transfer-in with PEN (import, do not create), class promotion done (progression).
  - The clerk ticks each item as done in UDISE+. Items older than N days show on the home screen and in the UDISE+ freeze reminder.
  - Add a new DQ rule. When a UDISE+ list is imported, a student who is `left` in SchoolOS but still active in the UDISE+ list, or active in SchoolOS but missing from UDISE+, becomes a finding with route ROUTE-UDISE.
  - No portal automation (out of scope): SchoolOS lists the work, the clerk does it.
- **BRD objective:** BO-01, BO-03. User: clerk.
- **Invariants / compliance:** inv. 6 (findings only). No new data class.
- **Dependencies:** `students`, `certificates`, promotions, `imports` (FO-04 makes the matching reliable).
- **Effort:** M. **Value:** H. Duplicate "active" records block other schools' transfer-ins and make APAAR fail. The checklist is cheap and visible every day.
- **Risks:** The UDISE+ download format is unverified (`imports/templates/udise-plus-student-list.yaml`, `verified: false`).
- **Owner questions:** none blocking.

### FO-04 · UDISE+ list import matched by PEN, with a confirmed fallback

- **Users:** office clerk; also the SchoolOS onboarding concierge.
- **Problem and evidence:**
  - The UDISE+ template tells the clerk to "add a column with the school's admission number first" because rows match only by admission number (`apps/api/app/imports/templates/udise-plus-student-list.yaml:13-15, 24-25`).
  - The UDISE+ download has no admission number (same file, unverified). Hand-adding it for 500-1,500 rows is the re-typing BO-03 wants to remove, and it invites mismatched rows.
- **Exists today:** the template library, mapping, validation, the revert, and the PEN uniqueness guard.
- **Proposal:**
  - Match rows in this order: verified PEN in SchoolOS, then any PEN, then an exact name + DOB + class match. A unique match is proposed. Anything else goes to an "unmatched" review queue where a person picks the student or skips the row.
  - Show the match basis per row in the validation sheet.
  - Never create students from a UDISE+ import by default, because the register is the anchor (BR-01).
  - A recorded PEN arrives as `udise_plus` source values, as today. Nothing is overwritten (inv. 6).
- **BRD objective:** BO-03, BO-07. User: clerk.
- **Invariants / compliance:** inv. 3 (scope on the review queue), 6. No new data.
- **Dependencies:** `imports`, `students.search`.
- **Effort:** S-M. **Value:** H. Every readiness check depends on fresh UDISE+ values, so making the refresh painless is what makes the wedge repeatable.
- **Risks:** Fuzzy matching across twins or siblings. Keep the fallback exact and always confirmed by a person.
- **Owner questions:** none.

### FO-05 · Portal calendar refresh for 2026-27 and new office deadlines

- **Users:** clerk, exam coordinator, principal, correspondent.
- **Problem and evidence:**
  - The calendar has only the nominal-roll correction window for BSEAP (`portal_calendar.yaml:122-139`). It is missing several dated events:
    - **BSEAP SSC exam fee window.** Fees are paid through the school login on bse.ap.gov in mid/late November. The 2026 cycle ran with no late fee from 13-25 Nov 2025, then ₹50/₹100/₹200 late fees. "Due dates will not be extended" ([CollegeDekho](https://www.collegedekho.com/news/ap-class-10-exams-2026-fee-payment-dates-released-check-schedule-fee-details-73878), secondary). UDISE+ must be verified before the fee is paid ([teachernews](https://teachernews.in/submission-nominal-rolls-for-ssc-public/)). **The March 2027 notice was not found on 2026-11-09. By the past pattern it is due now (unverified).**
    - **CBSE Class 10 second board exam LOC.** In 2026 it ran 16-22 April, by a circular of 15 Apr 2026 ([Free Press Journal](https://www.freepressjournal.in/amp/education/cbse-releases-loc-schedule-for-class-10-second-board-exam-2026-submission-from-april-16-to-22), secondary). An April 2027 window is likely.
    - **CBSE SARAS affiliation and renewal.** SARAS 7.0 for 2027-28 opened 26 Feb 2026, extended to 30 Sep 2026. Applicants must first update OASIS, the public disclosures and teacher details ([Careers360](https://news.careers360.com/cbse-extends-saras-7-0-application-deadline-for-2027-28-affiliation-session-till-september-30-inspections-compliance-submissions/amp), secondary).
    - **CBSE OASIS teacher data.** A Feb 2026 circular made Class 11-12 teacher data mandatory for on-screen marking ([Brainfeed](https://brainfeedmagazine.com/cbse-directs-schools-to-update-class-11-12-teachers-data-for-on-screen-marking-2026), secondary).
    - **AP private school recognition renewal** on the PSIS portal ([cse.ap.gov.in/PSIS](https://cse.ap.gov.in/PSIS/), official; no 2026 date found). AP cancelled the recognition of 259 schools whose recognition had lapsed ([Deccan Chronicle](https://www.deccanchronicle.com/nation/in-other-news/200321/recognition-of-259-private-schools-cancelled-in-ap.html), date unclear).
    - **AP RTE 12(1)(c) lottery rounds.** The 2026-27 second round allotted 10,453 seats ([Deccan Chronicle](https://deccanchronicle.com/southern-states/andhra-pradesh/rte-second-phase-lottery-allots-10453-ews-seats-in-private-schools-1954931)). These are school-side admission dates, not data.
    - **UDISE+ progression and dropbox closing** (FO-03).
  - The 2027-28 board events name no profile yet (`portal_calendar.yaml:139-140`).
- **Exists today:** the calendar engine, `school:` own events, confirmation by the school, reminders and tasks (ADR-0053).
- **Proposal:**
  - Add the events above to `portal_calendar.yaml`, each `verified: false` with sources.
  - Add a portal kind `school_admin` for non-student deadlines (recognition renewal, SARAS, OASIS) and `rte` for lottery and admission dates. The `Portal` literal in `dq/calendar.py:36` gains them.
  - Link the BSEAP fee event to the `bseap-ssc-2027` readiness counts: "do not pay the fee until 142/160 ready".
  - Add a yearly refresh checklist in docs (who reads which circular in which month).
- **BRD objective:** BO-01 (P4, missed deadlines). Users: clerk, principal.
- **Invariants / compliance:** none. No student data; config only. No ADR, because the change extends ADR-0053 within its design. A new portal kind is a small doc amendment.
- **Dependencies:** none.
- **Effort:** S. **Value:** H. It is time-sensitive (BSEAP fee window now, CBSE second-exam LOC in April), cheap, and shows the "we know your deadlines" promise.
- **Risks:** Wrong dates. Mitigated by the existing "Expected around…" wording and school confirmation.
- **Owner questions:** none. This is a lead-level config change.

### FO-06 · Enrolment statistics sheet (class × gender × category × age)

- **Users:** clerk (UDISE+ school-level tables), principal and correspondent (P6, numbers compiled by hand), inspectors.
- **Problem and evidence:**
  - The UDISE+ DCF asks schools for aggregate enrolment tables: by class and gender, social category, minority, age, CWSN and repeaters (DCF sources listed in `dq/config/profiles/udise-plus.yaml:16-22`).
  - SchoolOS has no aggregate or strength report. A search for "strength" or enrolment summaries in `apps/api/app` found nothing relevant.
  - BRD P6 says "Management asks for numbers compiled by hand" (`docs/01-BRD.md:43`).
- **Exists today:** the student list export and the pre-check sheets per student. Promotions and enrolments hold class and section per year.
- **Proposal:**
  - A print/XLSX sheet per academic year (as of a date) in UDISE+ table shapes: class × gender, class × social category × gender, class × age (as of the UDISE+ reference date), CWSN by type, minority, repeaters (from enrolment history), new admissions and leavers in the year.
  - Counts only, no names. Category, religion and disability are C3, so the sheet needs `read_sensitive`-level permission or shows those tables only to holders.
  - Flag gaps: "12 students have no category recorded".
  - Use the same sheet for management ("strength register") and inspections (FO-12).
- **BRD objective:** BO-03, P6. Users: clerk, principal, correspondent.
- **Invariants / compliance:** inv. 14. Aggregates for the school itself only, never cross-tenant. C3 values are aggregated without being revealed; small cells could still identify a child, so the export is permission-gated and audited. No ADR.
- **Dependencies:** `students`, `tenancy`.
- **Effort:** S-M. **Value:** M-H. A visible management win, and it removes a UDISE+ hand count.
- **Risks:** The UDISE+ age reference date and the category codes are unverified (D3).
- **Owner questions:** none.

### FO-07 · Staff register for portals (UDISE+ teacher module, CBSE OASIS/SARAS, Appendix IX)

- **Users:** clerk, principal, correspondent (affiliation), CBSE schools in particular.
- **Problem and evidence:**
  - Portals ask for teacher records every year:
    - UDISE+ teacher module: name, DOB, gender, academic and professional qualification, appointment type, joining date, subjects and training. An Aadhaar-free format exists ([EFA India guide](https://educationforallinindia.com/udise-2025-26-profile-facility-and-teacher-module-data-entry-guide/), [FAQ](https://educationforallinindia.com/comprehensive-faqs-for-udise-2025-26-data-entry/), secondary).
    - CBSE OASIS teacher data, mandatory for Class 11-12 on-screen marking: name, subject, experience, DOB, mobile, email (Brainfeed, above).
    - SARAS applications require teacher details.
    - The CBSE Appendix IX disclosure lists teacher qualifications.
  - SchoolOS's disclosure module derives only **counts by role** from user memberships (`apps/api/app/disclosure/derived.py:25-67`). There is no staff profile anywhere (no qualification, joining date or subject in any `models.py`).
- **Exists today:** identity users and roles; disclosure counts.
- **Proposal:**
  - Add a minimal staff record linked to an identity user, or standing alone for staff without a login: DOB, gender, academic and professional qualification (code lists from the UDISE+ DCF), appointment type, joining and leaving dates, classes and subjects taught, trainings, and contact held on the user.
  - **No Aadhaar number** (same rule as students); at most the last 4 digits if a portal shows it.
  - Add DQ rules for staff (missing qualification, DOB format) and a "ready to enter" sheet per portal (UDISE+ teacher module, OASIS) like the student profiles.
  - Feed the disclosure module's qualification table (ADR-0051) instead of manual entry.
  - Explicitly **not payroll** (out of scope): no salary, bank or PF data.
- **BRD objective:** BO-03 (re-typing across portals). Users: clerk, principal.
- **Invariants / compliance:**
  - Staff personal data is a **new data class**: needs an ADR and a privacy review (docs/14 §5), a record class and retention (ADR-0048).
  - The DPDP data-principal rights extend to staff.
  - Never visible to the control plane.
- **Dependencies:** `identity`, `disclosure`, `dq` profiles, `exports`.
- **Effort:** L. **Value:** M (State Board) / H (CBSE: OASIS and SARAS are compulsory and recurring).
- **Risks:** Scope creep toward HR and payroll. Hold the line on "fields a portal asks for".
- **Owner questions:** Is a staff register in this year's scope? Does it apply to CBSE schools only, or to all schools for the UDISE+ teacher module?

### FO-08 · Admission desk: application to admission (lean, not a CRM)

- **Users:** clerk, principal (approves), correspondent (seat counts).
- **Problem and evidence:**
  - BRD §2.1 names "admissions and TCs (Mar–Jun)" as the first office peak (`docs/01-BRD.md:30`). Budgets are set in Feb-Mar and onboarding happens in Jun-Jul (docs/18 §1.9), so admissions is when a new school first touches SchoolOS.
  - There is no admissions module and no stage before "student". `POST /students` creates the record directly (`students/api.py`), and "enquiry" appears nowhere in `docs/`.
  - Every incumbent sells admissions (docs/research/2026-10-market/report.md, vendor table).
  - Some states set a minimum age of 6 for Class 1 from 2026-27 (Delhi circular; [Tribune](https://www.tribuneindia.com/news/delhi/govt-fixes-minimum-age-for-class-1-admission-at-6-yrs/amp)). The **AP rule was not found (unverified)**.
- **Exists today:**
  - Create-student with `admission_kind` (new or transfer-in, PEN required for transfer-in, `features/students/CreateStudent.tsx:85-87`).
  - The PEN/APAAR duplicate guard, DQ-007 age band, documents and evidence.
- **Proposal:**
  - An **application** record (applicant child, parents, class sought, documents checklist: birth certificate, previous TC with PEN, Aadhaar-as-printed seen yes/no, photo) with states `received`, `documents_pending`, `approved`, `admitted`, `declined` or `withdrawn`.
  - Checks before admitting: age-for-class against a versioned state rule (`verified: false`), PEN transfer-in (existing), duplicate sibling or child, and seats left per class and section.
  - "Admit" creates the student, enrolment and admission number in one transaction, and moves the checked documents as `admission_register` and `birth_certificate` sources, so readiness starts clean from day one.
  - Applications not admitted are deleted after a short retention (for example, 90 days after the decision).
  - No enquiry marketing, lead scoring or promotional WhatsApp (BR-18, docs/18 §4 purpose limit).
- **BRD objective:** BO-01 (clean data at the source), BO-07 (onboarding). Users: clerk, principal.
- **Invariants / compliance:**
  - Applicant data about children who are not yet students is a **new data class** with its own purpose and retention: needs an ADR and a privacy review.
  - inv. 4 (no Aadhaar number), inv. 14 (no marketing).
- **Dependencies:** `students`, `documents`, `tenancy` (seats), optionally `fees` (admission fee receipt).
- **Effort:** L. **Value:** H for the sale (buyer-visible, seasonal), M for the wedge.
- **Risks:** Becoming a CRM, a crowded commodity.
- **Owner questions:**
  - Build before the Mar-Jun 2027 admissions season?
  - Applications only, or also walk-in enquiries (counts only)?

### FO-09 · Transfer certificate filled from the record, with a TC request log

- **Users:** clerk, principal (approves), parents (indirectly).
- **Problem and evidence:**
  - The AP TC asks the clerk to type working days, days attended, languages studied, medium, identification marks and the application date for every TC (`apps/api/app/certificates/formats/ap-tc-2027.yaml:36-49`).
  - Attendance already exists in `academics` (`academics/service.py:283`, `percent_of` at `:576`). Medium is not held anywhere; identification marks are not recorded at admission (`certificates/config.yaml` `official_format_todo`).
  - A TC cannot delay admission elsewhere (RTE Act s.5, cited in an AP proceedings order reported by [amaravathiteacher](https://amaravathiteacher.com/streamlining-the-process-of-transferring-the-students-to-next-higher-classes-without-insisting-on-any-documents-mapping-of-students-to-the-opted-schools-orders), secondary). MEOs intervene when private schools withhold TCs ([Deccan Chronicle](https://www.deccanchronicle.com/southern-states/andhra-pradesh/private-schools-across-ap-capture-over-35000-students-1961992), secondary).
  - BO-04 targets less than 5 minutes per certificate.
- **Exists today:** the AP TC format, maker-checker, verification QR, the no-dues rule (D16).
- **Proposal:**
  - Pre-fill working days and days attended from the attendance register for the year to the leaving date. The clerk can override, and the source is shown on the preview.
  - Add a section-level `medium` and `languages` setting in `tenancy` (also needed by FO-10 and the BSEAP roll), and pre-fill them.
  - Add an optional `identification_marks` student attribute captured at admission (C2).
  - Add a **TC request log**: the date the request was received becomes `application_date`, and the TC register shows request-to-issue days. A request open more than N days is flagged to the principal (BO-04 evidence, and a defence when an MEO asks).
- **BRD objective:** BO-04. Users: clerk, principal.
- **Invariants / compliance:** inv. 6, 7. Identification marks are physical descriptions of a child: a new attribute (C2), noted in the privacy record. A full ADR is not needed if docs/05 classifies it.
- **Dependencies:** `academics`, `tenancy`, `certificates`.
- **Effort:** S-M. **Value:** M-H.
- **Risks:** Attendance may be incomplete for schools that started mid-year. Show "from SchoolOS attendance since <date>" and allow an override.
- **Owner questions:** Should identification marks be recorded at admission (they are already typed on every AP TC)?

### FO-10 · Board candidate data beyond identity (medium, languages, subjects, photo readiness)

- **Users:** exam coordinator, clerk.
- **Problem and evidence:**
  - BSEAP and CBSE profiles say "SchoolOS does not hold" the medium and languages, the photograph and signature (`dq/config/profiles/bseap-ssc-2027.yaml:59-65`) and the CBSE subject codes (`cbse-registration-2027.yaml:63-64`, `cbse-loc-2027.yaml:55`).
  - CBSE LOC 2026 allowed **no corrections** after submission and made APAAR mandatory ([Careers360](https://news.careers360.com/cbse-class-10-12-exam-2026-loc-submission-rules-no-correction-apaar-id-mandatory-allowed-board-tightens/amp), secondary). Wrong subject codes are therefore unfixable.
  - For CBSE Class 10, the second-exam LOC needs subject choices again (FO-05).
- **Exists today:** identity readiness only.
- **Proposal:**
  - Add a medium and language scheme per section (shared with FO-09) and per-student exceptions.
  - Add the CBSE subject combination per student for Classes IX-XII, from a versioned subject-code list (`verified: false`), with DQ rules for invalid combinations, a missing subject or a missing language.
  - Add photo-and-signature readiness as a checkbox ("collected / uploaded on portal"). SchoolOS does **not** store the photo (that would be a new data class).
  - Add these to the pre-check sheets and the parent verification slip (CBSE requires schools to give parents a verification slip, docs/research report).
- **BRD objective:** BO-01. User: exam coordinator.
- **Invariants / compliance:** No new sensitive class (subjects and medium are C1/C2). A photo store would need an ADR, which is explicitly avoided here.
- **Dependencies:** `tenancy`, `dq` profiles, `exports`.
- **Effort:** M. **Value:** H for CBSE schools, M for BSEAP.
- **Risks:** Subject code lists change yearly; keep them as versioned config.
- **Owner questions:** Is the subject combination in scope for the CBSE minority this year?

### FO-11 · Government scheme verification pack (attendance lists, bulk study certificates)

- **Users:** clerk, principal, class teacher.
- **Problem and evidence:**
  - BRD §2.1 lists "scheme verifications (Jul–Aug)" as an office peak (`docs/01-BRD.md:30`).
  - AP's Thalliki Vandanam covers students in recognised private schools and needs at least 75% attendance. Sources conflict on whether that is the current or previous year ([Deccan Chronicle](https://deccanchronicle.com/southern-states/andhra-pradesh/thalliki-vandanam-scheme-eligibility-criteria-announced-for-202526-1884797), secondary; eligibility details **unverified**).
  - Today a clerk issues study and bonafide certificates one at a time (`POST /students/{id}/certificates`).
- **Exists today:** attendance, certificates with purpose `scholarship` (`certificates/config.yaml`), registers.
- **Proposal:**
  - A class-wise attendance-percentage list for a chosen period, A4/XLSX, signed by the head. It shows names, admission numbers and the percentage only.
  - **Bulk issue** of study or bonafide certificates for selected students with one purpose. Each gets its own serial and register line, approved per the type's rule.
  - An "attendance certificate" type (percentage for a period) in versioned config.
- **BRD objective:** BO-04, P5 (counter queues). Users: clerk, principal.
- **Invariants / compliance:**
  - Purpose limit (inv. 14): a child-welfare scheme run by the state is arguably within "educational activities", but this needs a **privacy review note**.
  - Lists are handed over by the school; SchoolOS sends nothing to government (no portal automation).
- **Dependencies:** `academics`, `certificates`.
- **Effort:** M. **Value:** M (seasonal, but every AP school has it).
- **Risks:** Scheme rules change yearly. Keep the pack generic (attendance percentage plus certificates), not scheme-specific.
- **Owner questions:** Confirm that scheme lists are an acceptable purpose under the school's privacy notice.

### FO-12 · Inspection pack (one-click register bundle)

- **Users:** principal, correspondent, clerk.
- **Problem and evidence:**
  - BRD §2.1 says the office keeps "dozens of registers… past 40", and BR-11 says SchoolOS prints in familiar formats.
  - APSERMC inspects private schools and found norm violations in 130 schools ([The News Minute](https://www.thenewsminute.com/amp/story/andhra-pradesh/pvt-schools-ap-flouting-govt-norms-charging-excessive-fees-regulatory-commission-118395), secondary). Recognition renewal (PSIS) and MEO/DEO visits ask for registers.
  - SchoolOS has separate register views (`certificates/api.py:309-357`), attendance and fee reports, but no bundle and no record of what was shown to whom.
- **Exists today:** the individual registers, fee day reports, audit.
- **Proposal:**
  - Add a "Prepare for inspection" screen. Pick an academic year and the registers (admission and withdrawal, TC counterfoil, certificate register, attendance register summary, enrolment statistics FO-06, fee heads and receipt summary when fees are on, staff register FO-07 when built). Print all as one PDF, with a cover page and a contents list.
  - Keep an inspection log: date, authority (by role or office, no personal names required) and registers shown. It is audited.
  - Bundles follow the 7-day export retention.
- **BRD objective:** BO-02, P6. Users: principal, correspondent.
- **Invariants / compliance:** A bulk personal-data print, so `register.read` with step-up (FR-REG-004) and audit (inv. 7). No new data class.
- **Dependencies:** `certificates`, `academics`, `fees`, `exports`.
- **Effort:** S-M. **Value:** M. Strong in demos for correspondents; it uses what exists.
- **Risks:** Inspectors may want the paper register itself. BR-11 stands; this is a print aid.
- **Owner questions:** Which registers do AP inspectors actually ask for? This is a pilot interview question (docs/18 §5).

### FO-13 · RTE 12(1)(c) register and reimbursement claims (**reopen proposal** for D10/D43)

- **Users:** principal, accountant, correspondent.
- **Problem and evidence:**
  - AP ran its 2026-27 RTE lottery in two rounds; round 2 allotted 10,453 EWS seats in private schools ([Deccan Chronicle](https://deccanchronicle.com/southern-states/andhra-pradesh/rte-second-phase-lottery-allots-10453-ews-seats-in-private-schools-1954931)). Round 1 selected 30,075 of 54,016 (docs/research report).
  - Schools must keep allotment, eligibility documents and per-year reimbursement claims to Class VIII.
  - AP reimbursement amounts and process were **not found** (unverified).
  - UDISE+ also asks for "admitted under RTE 12(1)(c)" (`udise-plus.yaml` portal field with `maps_to: null`).
- **Exists today:** **Designed** in ADR-0052 (state file, admissions register, claims, `rte` concession request, C3 restricted). **Not scheduled** (D10, D43). Fee concession type `rte` exists.
- **Proposal:** Build ADR-0052 as designed, plus the UDISE+ RTE flag fed from it as a count only (FO-06) and the RTE events in the calendar (FO-05).
- **BRD objective:** BO-01, BO-02. Users: principal, accountant.
- **Invariants / compliance:** C3 RTE status (ADR-0052 decision 5); privacy review already part of ADR-0052. Never in Ask, prints or messages.
- **Dependencies:** `fees`, `documents`, `academics` (attendance for claims).
- **Effort:** M-L. **Value:** M. Most AP unaided schools with an entry class have RTE children, and claims are money the school is owed.
- **Risks:** AP claim rules unknown; the state file stays `verified: false`.
- **Owner questions:** **Reopen D10 for the RTE register in 2026-27?** (Already listed for the owner in docs/14.)

## 3. Ranked shortlist (value / effort)

| Rank | Id | Why first |
|---|---|---|
| 1 | FO-05 Calendar refresh | S effort, H value, **time-sensitive**: the BSEAP SSC fee window is expected in mid/late Nov 2026 and needs UDISE+ verified first; the CBSE Class 10 second-exam LOC is expected in Apr 2027 |
| 2 | FO-02 MEO correction pack | S-M; turns the `school_udise` fix owner into the paper the MEO office acts on |
| 3 | FO-04 UDISE+ import by PEN | S-M; removes the hand-added admission-number column that every readiness refresh depends on |
| 4 | FO-01 Fix follow-up tracker | M; makes the Feb 2027 SSC wedge deliver (parent Aadhaar fixes take weeks) |
| 5 | FO-03 UDISE+ movement checklist | M; stops duplicate "active" children and failed transfer-ins and APAAR |

Next: FO-09 (TC from the record), FO-10 (CBSE subjects; H for CBSE schools), FO-06 (enrolment statistics). Larger bets needing ADRs: FO-08 (admissions), FO-07 (staff register). Owner decision: FO-13 (reopen D10).

## 4. Not worth building (with reasons)

| Idea | Why not |
|---|---|
| Automatic submission or scraping of UDISE+, BSEAP, CBSE or APAAR portals with the clerk's login | No APIs; storing portal credentials is a security liability; explicitly out of scope (docs/18 "Kept out of scope") |
| OCR of Aadhaar card photos to capture the as-printed fields | The image contains the number; storing it breaks invariant 4 even with masking. Clerk entry or a parent slip of as-printed fields is the safe path |
| Auto-applying UDISE+ values to the register when they "look right" | Breaks BR-01 and invariant 6; findings and change requests are the product |
| Admission enquiry CRM with lead scoring and promotional WhatsApp campaigns | Marketing messages are excluded (BR-18; utility-only templates, ADR-0043); commodity; children's data purpose limit |
| Generating APAAR IDs or PENs inside SchoolOS | Only UDISE+ creates them; SchoolOS records and checks them (ADR-0037/0039) |
| Storing student photos and signatures for board uploads | New biometric-adjacent data class for little gain; boards take uploads on their portals. A "collected" checkbox (FO-10) is enough |
| Payroll/HR beyond portal fields in the staff register | Out of scope (payroll), commodity |
| A state-wide "PEN lookup" across schools | Cross-tenant identity lookup breaks tenant isolation (inv. 1) and the purpose limit |

## 5. Time-sensitive regulatory dates found (all secondary or unverified unless noted)

- **BSEAP SSC March 2027 exam fee:** the notice was not found on 2026-11-09. The previous cycle had no late fee for 13-25 Nov 2025, and dates "will not be extended". It is **likely open now or within days**. UDISE+ must be verified before paying.
- **BSEAP nominal-roll correction window:** expected around February 2027 (already in the calendar, `verified: false`).
- **CBSE Class 10 second board exam LOC:** expected around April 2027 (16-22 Apr in 2026).
- **APAAR:** the Supreme Court (20 Jul 2026, *Abhishek Baxi v. Union of India*, WP(C) 832/2026) directed a pan-India opt-out in the consent form. The formal order was "to be uploaded later", per [LiveLaw](https://www.livelaw.in/top-stories/will-ask-cbse-to-give-opt-out-option-in-apaar-consent-forms-for-students-supreme-court-in-plea-against-aadhaar-linkage-541982). SchoolOS's consent register already offers refusal. No new CBSE 2026-27 circular text was found.
- **CBSE SARAS 7.0** (2027-28 affiliation) closed 30 Sep 2026. The next cycle is likely to open around Feb 2027.
- **DPDP Rules:** main duties around 13-14 May 2027 (unchanged; docs/18). They apply to FO-07 and FO-08 if built.

## 6. Questions for the owner

1. FO-07 staff register and FO-08 admissions each add a new data class. Should either enter this year's scope (ADR and privacy review first)?
2. Reopen D10 for the RTE register (FO-13)?
3. FO-01: may a utility WhatsApp nudge to parents about Aadhaar updates ship, or should follow-up stay staff-side only?
4. FO-11: confirm that state scheme verification lists are within the school's stated purposes.
5. Pilot interview additions (docs/18 §5): the MEO correction-request format, the registers inspectors ask for, AP's Class 1 age cut-off, and the real UDISE+ download columns.
