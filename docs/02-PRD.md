# 02 · Product Requirements Document (PRD): Core Platform

| Field | Value |
|---|---|
| Version | 0.4 · 2026-09-29 |
| Scope | Core capabilities C1–C14 (milestones M0–M2) + extension points |
| Related | 01-BRD (why), 03-TRD (how well), 06-RAG, 07-Security, 16-Platform admin panel |
| Changes | 0.4: C15 certificates and registers (M3) with US-1101..US-1108, proposed from the roadmap scope (PO to confirm). 0.3: US-1305 invoice number example uses the implemented 16-character format. 0.2: C14 platform admin panel (M0) with US-1301..US-1310; C13 folded into C14; C12 "Plan & billing" page (US-1204); US-202 uses `tenant.structure.manage`; promotions moved to M1. 0.1: baseline |
| Changes | 0.8 (2026-10-21): R3 planned stories C26-C31 (US-2601..US-2609, US-2701..US-2703, US-2801..US-2804, US-2901..US-2904, US-3001..US-3004, US-3101..US-3104; ADR-0048..ADR-0053; lead decisions D36-D46); R3 personas; capability map rows C26-C31. 0.7 (2026-10-21): R2 status: what is built and what stays open after waves R2-0..R2-3H (R2 introduction, C20, C21, C23, C24, C25 notes). 0.6 (2026-10-14): R2 stories C20-C25 (US-2001..US-2006, US-2101..US-2112, US-2201..US-2210, US-2301..US-2303, US-2401..US-2405, US-2501..US-2504) from owner decisions D4-D9 (docs/18 §8; ADR-0042..ADR-0047); R2 personas; capability map rows C18-C25; §9 non-goals updated. 0.5: C17 student timeline and early warning (M5) with US-1701..US-1709, proposed from the roadmap scope (PO to confirm). 0.4: C16 circulars, tasks and parent notices (M4) with US-1601..US-1606, proposed from the roadmap scope for the product owner to confirm. 0.3: US-1305 invoice number example uses the implemented 16-character format. 0.2: C14 platform admin panel (M0) with US-1301..US-1310; C13 folded into C14; C12 "Plan & billing" page (US-1204); US-202 uses `tenant.structure.manage`; promotions moved to M1. 0.1: baseline |

---

## 1. Product principles

1. **Office first.** Design for the clerk at the counter, not the dashboard viewer.
2. **Enter once, check always.** Every value remembers its source; conflicts are shown, never hidden.
3. **Flag, don't fix.** The system explains problems and the correction route; humans decide.
4. **Answers with receipts.** Every AI answer shows where it came from.
5. **Print is a feature.** Outputs look like the formats offices and authorities already accept.
6. **Two languages, one product.** English and Telugu everywhere parents or staff read. **English first for now** (ADR-0036, product owner 2026-09-30): every Telugu output below stays specified but is deferred, hidden while `SOS_TELUGU_ENABLED` is off; Telugu *input* (a question, a search or a register value in Telugu script) is still accepted and answered or shown in English.
7. **Least privilege by default.** People see what their job needs.

## 2. Personas and jobs-to-be-done

| Persona | Top jobs |
|---|---|
| **Lakshmi, office admin (senior clerk)** | Register students correctly for boards/portals; find old records fast; issue certificates; not be blamed for errors |
| **Ravi, office assistant** | Enter data quickly; know what's wrong and how to fix it |
| **Principal** | Approve identity corrections confidently; get quick answers; avoid escalations |
| **Correspondent (management)** | Know the school's numbers; trust that data is safe |
| **Exam coordinator** | Clean candidate lists before board deadlines |
| **Class teacher** | Context on their students; follow up on flagged students (M5) |
| **Accountant** | Answer fee questions from Tally data without re-entry (M6); from R2 run the fee ledger, receipts and reconciliation (R2 personas in §4) |
| **Parent (receives WhatsApp)** | From R2: receive opted-in school messages, open notices and receipts, pay dues without an app or login (R2 personas in §4) |
| **Platform operator team** (owner, engineer, support agent, billing admin, viewer; 16 §2) | Provision schools on the shared or dedicated tier, bill them, keep the fleet healthy, support safely, never see school data without approval |

## 3. Capability map

| ID | Capability | Milestone |
|---|---|---|
| C1 | Identity & access (login, MFA, sessions, RBAC with scopes) | M0 |
| C2 | School setup (tenant, academic years, classes, sections, staff) | M0 |
| C3 | Student record with per-source values; guardians | M1 |
| C4 | Onboarding & import (Excel/CSV/Sheets; register-photo extraction with verification) | M1 |
| C5 | Data-quality engine (mismatch rules, findings workflow) | M1 |
| C6 | Change requests (maker-checker) for identity fields | M1 |
| C7 | Documents & knowledge base (upload, OCR, versions, ACLs) | M2 |
| C8 | Ask the school (RAG + read-only tools, citations; English answers, TE deferred per ADR-0036) | M2 |
| C9 | Exports framework (board/portal pre-check sheets, versioned profiles) | M1 |
| C10 | Audit & activity (hash-chained log, viewer, export) | M0 |
| C11 | Notifications (in-app, English templates; bilingual deferred per ADR-0036) | M1 |
| C12 | School admin console (users, roles, retention, data export, plan & billing) | M0–M2 |
| C13 | Platform operator console: folded into C14 (ID kept for traceability; tenant-side break-glass stays under FR-OPS-004) | — |
| C14 | Platform admin panel / control plane: schools, provisioning (shared and dedicated tiers), plans, subscriptions, invoices, usage, flags, fleet, announcements, support, operators, platform audit (spec in 16) | M0 |
| C15 | Certificates & registers (TC, bonafide, study, conduct; serial numbers; register entries; duplicates; register print views; certificate PDFs as documents) | M3 |

Extension points for later modules: circulars→tasks & notices (M4), student timeline & early warning (M5), Tally connector (M6).
| C16 | Circulars → tasks, reminders and parent notices, English (bilingual deferred per ADR-0036) (AI suggestions confirmed by staff) | M4 |
| C17 | Student timeline and early warning: attendance and marks, behaviour notes, ABC indicators, flags with an owner and an intervention log (rules only, no AI) | M5 |
| C18 | Tally read connector (fee ledgers read from the office PC) | M6 |
| C19 | Board and portal readiness: APAAR consent, PEN, transfer-in, readiness diff | R1 |
| C20 | Certificates: AP TC format and public verification by code or QR (ADR-0045) | R2 |
| C21 | Fee ledger, receipts, UPI QR matching and Razorpay payment links; the school is the merchant (ADR-0042) | R2 |
| C22 | WhatsApp parent messages from the school's own number, with opt-in and opt-out (ADR-0043) | R2 |
| C23 | Parent language per school (ADR-0044) | R2 |
| C24 | Parent link pages, no parent login (ADR-0046, ADR-0047) | R2 |
| C25 | Ask the school finish-up: answer usage, search-only explanations, source resolution, e2e | R2 |
| C26 | DPDP processor pack: legal templates (not legal advice), notice records, data-principal requests and grievances, record classes (ADR-0048) | R3 |
| C27 | Consent register for optional purposes, with one view of APAAR and WhatsApp opt-ins (ADR-0049) | R3 |
| C28 | Breach handling: SchoolOS incident records and the school's breach register with clocks (ADR-0050) | R3 |
| C29 | CBSE Appendix IX public disclosure page and file (ADR-0051) | R3 |
| C30 | RTE 25% register: admissions, documents, reimbursement claims (ADR-0052; designed, not scheduled while D10 stands) | R3 (gated) |
| C31 | Portal calendar and readiness reminders (ADR-0053) | R3 |

Extension points for later modules: certificates & registers (M3), circulars→tasks & notices (M4, C16 below), student timeline & early warning (M5), Tally connector (M6).

---

## 4. User stories and acceptance criteria

Format: **US-ID · As a … I want … so that …** followed by acceptance criteria (AC) in Given/When/Then. Requirement links in brackets.

### C1 · Identity & access

**US-101** · As staff, I want to sign in securely so that only I can act under my name. [FR-IAM-001..006]
- AC1: Given valid credentials, when I sign in, then I land on my school's home in my preferred language.
- AC2: Given a privileged role (owner, principal, office_admin), when I sign in, then MFA is required.
- AC3: Given 15 minutes of inactivity on any device, then the session locks and requires re-authentication (shared office PCs).
- AC4: Given 5 failed attempts, then further attempts are throttled and the event is audited.

**US-102** · As an office admin, I want to invite staff and assign roles so that everyone gets the right access. [FR-IAM-010..014]
- AC1: Given I hold `user.manage`, when I invite a user with role `class_teacher` scoped to sections 9A and 9B, then they can see only students enrolled in 9A/9B.
- AC2: Given I lack `role.assign`, then role controls are hidden and the API returns 403.
- AC3: Every invite, role change and deactivation creates an audit event showing who did what, when.
- AC-DL09: An invitation to someone who already has a SchoolOS account is never accepted at sign-in: they accept or decline it themselves (both audited), and the inviting school sees no email until they accept and cannot activate it by hand (owner decision 2026-10-04, ADR-0023 amendment).

**US-103** · As a principal, I want to approve temporary support access so that the vendor can help without standing access. [FR-OPS-004]
- AC1: A support request shows reason, scope and duration (max 8 hours).
- AC2: Access starts only after approval and ends automatically; all actions are audited and visible to me.

### C2 · School setup

**US-201** · As the platform operator, I want to provision a school tenant so that it is isolated from all others. [FR-TEN-001..003] *(Delivered through the platform admin panel: US-1301 shared tier, US-1302 dedicated tier.)*
- AC1: Provisioning creates the tenant, a per-tenant data encryption key, default roles and an owner invite.
- AC2: A user of tenant A can never read tenant B data through UI, API, search, exports or AI (verified by automated cross-tenant tests).

**US-202** · As an office admin, I want to define academic years, classes and sections so that records are organized the way the school works. [FR-TEN-010..013]
- Permission: writes need `tenant.structure.manage` (owner, principal, office_admin; no step-up). Reads need `student.read_basic`.
- AC1: I can create "2026-27" with classes Nursery–XII and sections A–D, and mark one year as current.
- AC2 (**M1**, FR-TEN-011): Promotions at year end move enrolments forward in bulk with a preview and undo within 24 hours.
- AC3: Given I lack `tenant.structure.manage`, the create and edit controls are hidden and the API returns 403.

**US-203** · As the school owner, I want to say which boards my school prepares students for (AP State Board, CBSE, CISCE), class by class when we run more than one, so that SchoolOS shows only the board checks we need. [FR-TEN-020, FR-TEN-021; R1, ADR-0041]
- Permission: `tenant.settings.manage` with a fresh MFA sign-in (owner, principal); everyone in the school sees the choice.
- AC1: I tick one or more boards in school settings; a school that ticks none keeps seeing every board's checks.
- AC2: When two boards are ticked I can say which board each of Classes IX-XII follows; a class can follow only a ticked board (422 `class_board_not_declared`).
- AC3: Board pre-check lists show the ticked boards' profiles and the UDISE+ portal profile; each profile says which of its classes follow its board here.
- AC4: Every change is audited with the boards chosen (codes only).

**US-204** · As the school owner, I want to run SchoolOS alongside our current ERP so that we keep fees and the parent app where they are and use SchoolOS for records and portal readiness. [FR-TEN-022, FR-IMP-033; R1, ADR-0041]
- Permission: `tenant.settings.manage` with a fresh MFA sign-in; the choice is audited.
- AC1: In school settings I choose "Alongside our current ERP" and may name the ERP; the Tally connector (and fee screens when they exist) is hidden for everyone, and no new Tally agent can be set up (409 `module_hidden`). Data already recorded is kept; switching back shows the modules again.
- AC2: The imports page offers "Refresh from your ERP export": the file is imported with the ERP preset as office records (`manual_entry`), never as the admission register.
- AC3: A refresh never changes an admission-register value; a difference becomes a DQ-030 finding with the route "correct it in your ERP" first (invariant 6, BR-01).

### C3 · Student record

**US-301** · As an office admin, I want each student's details stored with their source so that I can see where every value came from. [FR-STU-001..008]
- AC1: A student's name shows the admission-register value, the Aadhaar-as-printed value, the UDISE+ value and the board value side by side, each with who recorded it, when, and evidence.
- AC2: The canonical value for identity fields is the verified admission-register value (BR-01), visibly labelled.
- AC3: Sensitive fields (health, category, income, guardian phone/address) are hidden unless I hold `student.read_sensitive`.
- AC4: The APAAR ID and the UDISE+ PEN show with their source (UDISE+, parent form or entered by the office) and a verified/unverified badge; an APAAR ID counts only after someone verifies it against the portal or the APAAR card. A wrong one is corrected by recording the right value from its source and verifying it (history kept); they are not identity fields, so no change request is needed (ADR-0037). [FR-STU-013..015]

**US-302** · As staff, I want to find a student by partial name, admission number, class or parent name in English or Telugu. [FR-STU-010]
- AC1: "venkat sai 9b" finds "VENKATA SAI K." in 9B; Telugu script queries find transliterated matches.
- AC2: Results respect my scope. Class teachers and teachers find only students in their classes or sections this academic year, also when they look at an earlier year; last year's class is out of their reach (owner decision 2026-10-09). School-wide staff find students of every year.
- AC3: I can find a student by the exact APAAR ID (12 digits, with or without spaces) using a separate "APAAR ID" search option. It matches only the student's recorded or verified APAAR ID (not a rejected or replaced one, not other fields), within my scope; the number is never written to logs. [FR-STU-016, ADR-0037]

**US-303** · As staff, I never want to enter an Aadhaar number by mistake. [FR-STU-012, BR-02]
- AC1: Aadhaar input accepts only the last 4 digits and as-printed fields; pasting 12 digits is rejected with an explanation.
- AC2: The APAAR ID field is the one place a 12-digit number is accepted: it is stored only as the APAAR ID and never as, or compared with, an Aadhaar number. Everywhere else (including the free-text search box) 12 digits that look like an Aadhaar number are still refused; only the separate APAAR ID search option (US-302 AC3) takes 12 digits. [FR-STU-015, FR-STU-016, ADR-0037]

### C4 · Onboarding & import

**US-401** · As an office admin, I want to import our Excel/Google Sheets class lists so that I don't retype them. [FR-IMP-001..009]
- AC1: I upload a file, map columns to fields (the system suggests mappings from headers in English/Telugu), and preview.
- AC2: Validation shows row-level errors (missing fields, bad dates, duplicates) before anything is saved.
- AC3: Commit is all-or-nothing per batch, attributed to the chosen source (e.g., `udise_plus`), and reversible within 24 hours.
- AC4: 2,000 rows validate in under 60 seconds.
- AC5: Before commit I can see the uploaded file as a sheet (every column with the field it fills, each row with its check result) and correct cells in place; a checked file re-checks the row at once, the uploaded file itself is kept as it was, every edit is recorded with who and when, and the commit adds the edited values. Restricted (C3) columns are never shown or edited there, a full Aadhaar number is refused, and nothing can be edited once the rows were added (corrections then go through the student profile or a change request). *(Written from the implementation; to be confirmed by the product owner.)*
- AC6: I can download the sheet with my edits as CSV or XLSX (one header row, the data rows) after confirming it's me (MFA within 5 minutes). Aadhaar-like numbers are masked, formulas are neutralised, restricted columns are empty unless I may see sensitive data, and every download is audited. *(Written from the implementation; to be confirmed by the product owner.)*

**US-402** · As an office admin, I want to photograph admission register pages so that old entries become searchable records. [FR-IMP-020..026]
- AC1: I upload photos; the system extracts rows (admission no., name, DOB, parent names, dates) into a verification queue with the image beside each row.
- AC2: Nothing extracted becomes a record until a user confirms or edits it; confirmed values are stored with source `admission_register` and the page image as evidence.
- AC3: Any 12-digit Aadhaar-like number in the image text is masked before storage.
- AC4: Low-confidence fields are highlighted; the queue shows progress per page.

**US-403** · As an office admin, I want ready-made Excel templates and column presets for the files I already have so that my first import maps itself. [FR-IMP-030..032; R1, ADR-0041]
- Permission: `import.run`.
- AC1: The imports page lists the template library: blank SchoolOS template, admission register in Excel, UDISE+ student list, and a student export from my current ERP; each says which source it is recorded as and whether its format is confirmed.
- AC2: I can download each as an Excel file with the header row only (no school data).
- AC3: In the mapping step I can pick a preset; the columns it knows are filled in (aliases count, a field once, never a field the import's source may not record), the others stay for me to map, and nothing is saved until I save the mapping.
- AC4: Formats owned by someone else (UDISE+, ERPs) are marked unconfirmed until a school checks them against a real file (owner decision D3). No vendor-specific ERP preset ships without a public column list.

### C5 · Data-quality engine

**US-501** · As an exam coordinator, I want a pre-check report for my Class 9 batch so that I fix problems before board registration. [FR-DQ-001..012, FR-EXP-002]
- AC1: Running "CISCE registration pre-check" on 9A–9D lists every finding by severity with student, field, the conflicting values (masked where sensitive), explanation in English/Telugu, and suggested correction route. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC2: Blockers (e.g., missing mandatory field) are separated from warnings (e.g., initials vs expanded surname).
- AC3: 2,000 students are checked in under 2 minutes.
- AC4: The report exports to PDF and XLSX; the XLSX includes a "ready to enter" sheet in the target field order.

**US-502** · As an office admin, I want to resolve or waive findings with a reason so that the list stays actionable. [FR-DQ-020..024]
- AC1: Resolving requires linking a change request or a note; waiving requires `dq.findings.waive` and a reason; both are audited.
- AC1a: A blocker (it stops certificates and submissions) closes when the change request that corrects the record is approved. Resolving or accepting it by hand needs `dq.findings.waive` and a fresh MFA sign-in (403 `blocker_needs_waive`, 428 otherwise); the app hides "Resolve" on a blocker from anyone else and says who can. So a clerk cannot resolve a blocker with a note and then print a certificate from the mismatched record (FR-CERT-002; owner decision 2026-10-04, audit DL-06).
- AC2: Re-running the check reopens a finding if the underlying conflict returns.

**US-503** · As an exam coordinator at a CBSE or ICSE school, I want board pre-checks for registration and the list of candidates so that the details pass the board portal the first time. [FR-DQ-030..032, FR-EXP-007; R1, ADR-0041]
- AC1: CBSE: "CBSE registration (Class IX and XI)" for Pariksha Sangam and "CBSE list of candidates (Class X and XII)"; both need name, mother's and father's names, date of birth, gender and category, and check APAAR readiness (APAAR is a mandatory field from 2026-27; a parent's written refusal is entered as REFUSED, so a missing APAAR ID is not a blocker).
- AC2: CISCE: "CISCE ICSE/ISC candidates 2026-27" (CAREERS portal registration and entry confirmation) replaces the 2026 profile for new checks; the 2026 profile still works for existing findings and exports.
- AC3: Each profile lists the board's form columns, marks the ones SchoolOS does not hold yet (subjects, minority, single girl child, photograph) for the clerk to enter on the portal, names its public sources and is shown as unconfirmed until a school checks it (owner decision D3).
- AC4: Profiles that ask parents to check printed details carry that flag (the per-student parent verification slip itself is built with the readiness check, R1.1).
- AC5: The pre-check report (PDF/XLSX) and "ready to enter" sheet work for each profile as for UDISE+ (US-501).

**US-504** · As an exam coordinator of an AP State Board school, I want to know months before the SSC window which Class 9 and 10 students' name, date of birth or gender do not match exactly across the admission register, Aadhaar and UDISE+, and who must fix each one, so that the nominal roll passes the first time. [FR-DQ-040..043, FR-DQ-046; ADR-0040; plan 2026-27 R1.1]
- AC1: Each difference says exactly what differs (an extra space, initials instead of a full word, two letters swapped, day and month swapped …), compared letter for letter.
- AC2: Each difference names who fixes it: the parent (Aadhaar at an Aadhaar centre), the school through the MEO/MIS coordinator (UDISE+), or the school's own register through a change request with evidence; never corrected automatically.
- AC3: A class/section dashboard shows "142 of 160 ready" and how many wait for the parent, the school or a decision; class teachers see only their sections.
- AC4: The board's format is marked "not yet confirmed" with its public sources until a school confirms it (owner decision D3).
- AC5: The SSC check is shown to schools that declared the AP State Board (or no board yet), and it has a nominal-roll pre-check sheet like the other boards. [FR-DQ-048, FR-EXP-008]

**US-505** · As an office clerk, I want to print a verification slip for each student (or a whole section) so that parents check the details and sign before the board window. [FR-DQ-045, FR-DQ-046]
- AC1: The student view shows each record's value and highlights the exact characters that differ; Aadhaar values only for staff allowed to see them (audited).
- AC2: The A4 slip lists each record's value, the differences and who fixes them, and has a signature line for the parent; it never shows an Aadhaar number; printing is audited.
- AC3: I can also get the slips of a student or a section as one PDF made by SchoolOS (to save, share or print later); it is kept for a few days like other exports, and in Telugu too when our parents read Telugu. [FR-DQ-047]

**US-506** · As an office admin, I want a list of students whose APAAR ID generation will fail and why, so that we fix UDISE+ or ask parents to fix Aadhaar first. [FR-DQ-044]
- AC1: The APAAR check compares UDISE+ with Aadhaar-as-printed (name, date of birth, gender) and names who fixes each difference; students with a verified APAAR ID are ready.

### C6 · Change requests (maker-checker)

**US-601** · As an office admin, I want to request a correction to a student's DOB with evidence so that the principal can approve it. [FR-CR-001..008, BR-04]
- AC1: The request shows old value, new value, source, evidence document (e.g., birth certificate scan) and reason.
- AC2: The requester cannot approve their own request. Approval requires `student.identity_change.approve`.
- AC3: On approval, a new verified value is recorded (the old one is kept in history), findings are re-evaluated, and both actions are audited.
- AC4: The request can be printed as a correction memo for the paper register.

### C7 · Documents & knowledge base

**US-701** · As an office admin, I want to upload circulars, policies, meeting minutes and register scans so that they become searchable. [FR-DOC-001..012]
- AC1: Accepted: PDF, JPG/PNG, DOCX, XLSX (≤ 25 MB each; configurable). Files are virus-scanned before processing.
- AC2: Each document has type, date, issuing body, academic year, language (auto-detected, editable) and visibility (roles/scopes).
- AC3: Processing status is visible (queued → extracting → indexing → ready / needs attention).
- AC4: Uploading a new version keeps history; search uses the latest unless I ask about older versions.
- AC5: An XLSX or CSV document opens as a table (first worksheet, row 1 as column names) with Aadhaar-like numbers masked and formulas shown as text. If I may upload, I can correct cells of a single-sheet XLSX without formulas and save them as the next version (history kept, scanned and indexed like an upload), and I can download the sheet, with unsaved edits, as CSV or XLSX; personal (C2) or restricted (C3) sheets ask me to confirm it's me first. Files uploaded for an import open from the import instead. *(Written from the implementation; to be confirmed by the product owner.)*

### C8 · Ask the school

**US-801** · As staff, I want to ask a question in English, Telugu or a mix and get an answer with sources. [FR-KB-001..020] English first (ADR-0036): a Telugu or mixed question is accepted, searched as written and answered in English while `SOS_TELUGU_ENABLED` is off.
- AC1: "When did roll number 1234 join and which class?" returns the answer citing the student record fields and register page.
- AC2: "DEO circular lo exam timings enti?" (Telugu–English mix) answers in the same style, citing the circular (title, date, page). *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC3: If nothing relevant is found or I lack permission, the answer says so plainly and does not guess.
- AC4: Citations open the exact record or document page; I can mark an answer helpful/not helpful with a reason.
- AC5: First words appear within 3 seconds (p95); complete answer within 10 seconds (p95).

**US-802** · As a principal, I want to save a checked answer as a "verified answer" so that everyone gets the same reliable response. [FR-KB-030..032]
- AC1: Verified answers show who verified them and when; they are re-reviewed when source documents change.
- AC2: The person who wrote a verified answer cannot also confirm it on review; another staff member who manages verified answers must. If nobody else in the school can, the writer may, and the audit log marks it as self-reviewed (owner decision 2026-10-09).

**US-803** · As a class teacher, I must not be able to learn about students outside my sections through the assistant. [FR-KB-010, BR-06]
- AC1: Asking about a student in another section returns "not found in records you can access".

### C9 · Exports

**US-901** · As an exam coordinator, I want export profiles per board/portal and year so that formats stay correct when they change. [FR-EXP-001..006]
- AC1: Profiles (e.g., `cisce-registration-2026`, `udise-plus-2026-27`) define required fields, field order, formats and validation rules.
- AC2: Exports record who generated them, when, and which students; they are watermarked "Generated by SchoolOS for internal checking".
- AC3: The UDISE+ pre-check "ready to enter" sheet has PEN and APAAR ID columns (empty when unknown); the APAAR ID column is printed in full, every other cell still masks Aadhaar-like numbers. [FR-EXP-005]

### C10 · Audit

**US-1001** · As a principal, I want to see who changed or viewed sensitive data so that the office is accountable. [FR-AUD-001..008]
- AC1: The audit viewer filters by user, student, action and date, and exports to CSV.
- AC2: An integrity check confirms the audit chain is unbroken.

### C12 · School admin console

**US-1201** · As the owner, I want to export all our data and set retention rules so that we stay in control. [FR-ADM-001..006, BR-08]
- AC1: A full export (records as CSV/JSON, documents as files, audit as CSV) is produced asynchronously and downloadable via a time-limited link after re-authentication with MFA.

**US-1204** · As the owner, principal or accountant, I want to see our plan, usage and invoices so that we know what we pay for and what is due. [FR-PLT-030]
- AC1: Given I hold `tenant.billing.read`, when I open "Plan & billing", then I see the current plan, subscription status, billing period, trial end (if any), usage against plan limits, and a list of invoices with number, period, total, status and amount due.
- AC2: Given I lack `tenant.billing.read`, the menu item is hidden and the API returns 403.
- AC3: The page shows only my school's records (data comes from `core.current_subscription()`), never another school's.
- AC4: Given an invoice is past due, a banner explains the amount, due date and how to pay, in English and Telugu. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*

### C14 · Platform admin panel (control plane)

Operators are SchoolOS staff with platform roles (16 §2, §6). None of these stories gives access to student data. Full specification: 16.

**US-1301** · As a platform engineer, I want to provision a school on the shared tier so that it can start using SchoolOS the same day. [FR-PLT-001, FR-PLT-002, FR-TEN-003]
- AC1: Given I hold `platform.tenants.provision` and completed MFA within 5 minutes, when I submit the provisioning wizard, then in one transaction the tenant, its encryption keys, system roles, audit chain, owner invite, billing account, subscription (trial or active) and deployment record are created.
- AC2: Given the same `Idempotency-Key` is sent twice, then only one school is created and both calls return it.
- AC3: Given any step fails, then nothing is created.
- AC4: The school's own audit log shows "tenant provisioned" with the platform as actor; the platform audit log shows which operator did it.

**US-1302** · As a platform engineer, I want to provision a school on a dedicated host so that premium schools get their own isolated server. [FR-PLT-003, FR-PLT-023]
- AC1: Given I choose the dedicated tier, when I confirm, then a deployment record in status "provisioning", a subscription, a billing account and a heartbeat key are created, and the key is shown to me once.
- AC2: Given the host has been built with the runbook (16 §13), when its first valid heartbeat arrives, then the deployment becomes "healthy" and shows its version.
- AC3: Given a custom domain is set, then the deployment record shows it and its certificate expiry is monitored.

**US-1303** · As a platform operator, I want to suspend, reactivate or offboard a school so that we can respond to security issues, non-payment or a school leaving. [FR-PLT-004, FR-PLT-005]
- AC1: Given I hold `platform.tenants.suspend` and completed step-up, when I suspend a school with a reason, then its staff see a suspension notice at sign-in, the owner can still download a full export and see Plan & billing, and no data is deleted.
- AC2: Given offboarding was requested by one operator, when the same operator tries to approve it, then the request is refused; a different operator holding `platform.tenants.offboard` must approve with step-up.
- AC3: Given offboarding is approved, then school data is deleted within 30 days, keys are destroyed (crypto-shredding) and a certificate of deletion is recorded.

**US-1304** · As a billing admin, I want to manage plans and subscriptions so that each school is on the right plan and price. [FR-PLT-010..014]
- AC1: Given a plan is published, when I try to change its price or limits, then I am asked to create a new version instead; existing subscriptions keep their version.
- AC2: Given a school on trial, when I activate it, then its status becomes "active" and the next invoice run includes it.
- AC3: Given I change a school's plan, then the change takes effect from the next billing period.
- AC4: Given a subscription is past due, when I try to suspend it before the 15-day grace period ends, then the request is refused; after grace it needs step-up and a reason; inside a protected board-exam window it also needs a platform owner's approval.
- AC5: No subscription is ever suspended automatically.

**US-1305** · As a billing admin, I want to generate, issue and record payment for GST invoices so that schools are billed correctly. [FR-PLT-015..019]
- AC1: Given it is the 1st of the month, when the invoice job runs, then one draft invoice exists per billable subscription for the coming period, and rerunning the job creates no duplicates.
- AC2: When I issue a draft, then it gets the next number in the financial year (e.g., `SOS/26-27/000123`, at most 16 characters), its contents are frozen, and numbers have no gaps.
- AC3: Given the school's state code equals ours, then CGST and SGST are charged equally; otherwise IGST; totals add up to the paisa.
- AC4: When I record a bank or UPI payment (amount, date, reference, TDS), then the balance updates and the invoice becomes "paid" once covered; a wrong entry is reversed with a reason, never deleted.
- AC5: Given an issued invoice is unpaid, I can void it with a reason; its number is never reused.

**US-1306** · As a platform operator, I want to see each school's usage against its plan so that we can spot growth and cost problems early. [FR-PLT-020, FR-PLT-021]
- AC1: Given yesterday has ended, then by 06:00 IST each school has a usage row (active users, staff users, students, storage, documents, AI queries, tokens and cost), counts only.
- AC2: When a school first crosses 80% or 100% of a limit in a billing period, then operators are notified and the school's billing contact is emailed.
- AC3: Crossing a limit never blocks the school's work (the existing AI budget fallback is the only automatic limit).

**US-1307** · As a platform engineer or support agent, I want to manage feature flags and announcements so that we can roll out changes safely and tell schools about them. [FR-PLT-022, FR-PLT-026]
- AC1: Given I hold `platform.flags.manage` and completed step-up, when I set a global flag to 10% rollout, then about 10% of schools (stable per school) see the feature, and a per-school override always wins.
- AC2: Given I post a maintenance announcement for the dedicated tier, when it starts, then staff at dedicated schools see the banner in their language and shared-tier schools do not.
- AC3: Given either the English or the Telugu text is empty, then the announcement cannot be saved. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*

**US-1308** · As a platform engineer, I want to see fleet health so that dedicated hosts stay healthy, backed up and up to date. [FR-PLT-023..025]
- AC1: Given a dedicated host sends a correctly signed heartbeat, then its last-heartbeat time, version and health update.
- AC2: Given a heartbeat with a bad signature, a timestamp more than 5 minutes off, a replayed nonce or an unknown field, then it is rejected and nothing is stored.
- AC3: Given no valid heartbeat for 20 minutes, then the deployment shows "unreachable" and the on-call operator is alerted.
- AC4: Heartbeats never contain personal data.

**US-1309** · As school staff and as a support agent, I want support tickets with clear statuses and response times so that problems are handled without sharing student data. [FR-PLT-027]
- AC1: When a staff member opens a ticket, then the form warns (EN/TE) not to include student names, dates of birth, Aadhaar or phone numbers, and such numbers are masked before storage. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC2: Each ticket shows its SLA timers by priority; breaches appear on the dashboard.
- AC3: Given a ticket was closed a year ago, then it has been deleted.

**US-1310** · As a platform owner, I want to manage operators and read a tamper-evident platform audit log so that every control-plane action is accountable. [FR-PLT-028, FR-PLT-029]
- AC1: Given I hold `platform.operators.manage` and completed step-up, I can invite, assign roles to and deactivate operators; I cannot change my own roles; at least one active platform owner always remains.
- AC2: Given a new operator has not enrolled MFA, then they cannot use the panel.
- AC3: Every control-plane change writes exactly one platform audit event in the same transaction; "Verify chain" reports the first broken or missing sequence number, if any.

### C15 · Certificates & registers (M3)

*(Proposed from the roadmap scope (14 §2 M3, BRD §9 and BR-11/BR-12); PO to confirm.)* Story IDs use the US-11xx block, which was unused because notifications (C11) have no stories of their own. Certificates are generated from the checked student record (C3, C5), never typed by hand; every issued certificate is a register entry with a serial number. Personas: Lakshmi (office admin) prepares and issues; the principal approves transfer certificates and signs the paper copy.

**US-1101** · As an office admin, I want to issue a bonafide, study or conduct certificate from the checked student record so that a parent at the counter gets a correct certificate in minutes. [FR-CERT-001..003, FR-CERT-006, FR-CERT-009..012, BO-04] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given I hold `certificate.issue` and can see the student, when I choose a certificate type from the student's page, then a preview shows every value the certificate prints with its source (admission register, verified or provisional) and the inputs the type needs (for example the purpose of a bonafide certificate).
- AC2: Given the student has an open blocker finding on a field the certificate prints, or a required printed field is empty, then I cannot issue it: the preview lists each problem with a link to the finding or to a new change request, and the API answers `409 certificate_blocked`. There is no override inside certificates: the value is corrected through a change request or the finding is waived by someone allowed to waive blockers (US-502, step-up).
- AC3: Given there are no blockers, when I confirm, then in one transaction the certificate gets the next serial number for its type and academic year, the register entry is written with the printed values frozen as they were, and the certificate is audited; the PDF is ready to print shortly after (median request-to-print under 5 minutes, BO-04).
- AC4: Provisional (unverified) values are printed but marked in the preview so I can check the paper register first.
- AC5: No certificate ever shows an Aadhaar number, and restricted (C3) fields such as caste, religion or category are not printed.

**US-1102** · As an office admin and a principal, I want transfer certificates to need the principal's approval so that no student leaves the rolls by one person's mistake. [FR-CERT-004, FR-CERT-005, FR-CERT-012, BR-04] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given I hold `certificate.issue`, when I prepare a TC with the date of leaving, reason, conduct and promotion status, then it waits for approval (no serial number yet) and everyone holding `certificate.approve` is notified in English and Telugu. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC2: Given I prepared the TC, I cannot approve it myself (checked by the service and by a database constraint); a different person holding `certificate.approve` approves or rejects it after a fresh MFA sign-in (step-up), rejection needs a reason, and I am notified either way. I can withdraw my own pending request.
- AC3: When the TC is approved, then in one transaction it gets its serial number, the TC register entry is written, the student's active enrolment ends (transferred, on the date of leaving) and the student is marked as left; blockers are checked again at approval.
- AC4: A student has at most one pending or issued original TC.

**US-1103** · As an office admin, I want certificate serial numbers that never repeat or skip so that the register reconciles with the paper counterfoils. [FR-CERT-006, FR-REG-005] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Numbers run per school, per certificate type and per academic year in the format set in versioned configuration (default `TC/2026-27/0001`).
- AC2: Two clerks issuing at the same moment get consecutive numbers, never the same one, and a failed issue leaves no gap.
- AC3: A cancelled certificate keeps its number, which is never given to another certificate.

**US-1104** · As an office admin, I want to issue a duplicate of a lost certificate so that the parent gets a copy that is clearly marked. [FR-CERT-007] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: A duplicate repeats the original's printed values exactly (it is a copy, not a new certificate), shows "DUPLICATE" with the original serial number, the copy number and the date of the duplicate, and needs a reason.
- AC2: A duplicate of a TC needs the principal's approval like the original; every duplicate is a line in the register and is audited.

**US-1105** · As a principal, I want to cancel a certificate that was issued in error so that the register shows it is no longer valid. [FR-CERT-008] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given I hold `certificate.approve` and signed in with MFA within 5 minutes, when I cancel an issued certificate with a reason, then it keeps its number, the register marks it cancelled with the date and reason, and its PDF leaves the searchable documents.
- AC2: Cancelling a TC does not re-admit the student; re-admission is a separate, deliberate step.
- AC3: To correct a certificate, the record is corrected through a change request and a new certificate is issued; the system never edits an issued certificate.

**US-1106** · As an office admin, I want to print the TC register, the certificate issue register and the admission and withdrawal register in familiar A4 formats so that the paper registers stay complete. [FR-REG-001..005, BR-11] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given I hold `register.read` and signed in with MFA within 5 minutes, I can open a print view of each register for an academic year: bilingual headings, dates as DD/MM/YYYY, A4 landscape, Telugu text never clipped. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC2: The TC register (counterfoil) and the certificate issue register list every serial number in order, including cancelled certificates and duplicates, with who issued and approved each.
- AC3: The admission and withdrawal register lists students in admission-number order with their admission and leaving details and the TC serial number.
- AC4: Every register view is audited (register, year, row count; never names).

**US-1107** · As an office admin, I want every issued certificate kept as a document so that it can be found and printed again later. [FR-CERT-010, FR-CERT-011] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: The certificate PDF is stored privately (encrypted) as a document of type "certificate", visible only to the office roles set in configuration, virus-scanned and indexed for "Ask the school" like other personal (C2) documents.
- AC2: Certificate documents cannot be uploaded, replaced or deleted by hand while the register entry exists; opening or downloading one is audited.

**US-1108** · As a principal, I want to set the school's letterhead once so that certificates show our name in English and Telugu, address and recognition details. [FR-CERT-013] *(Proposed from the roadmap scope; PO to confirm.)* *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC1: Given I hold `tenant.settings.manage` (step-up), I can set the Telugu school name, the address in English and Telugu, the recognition/affiliation line and the place printed on certificates; the English name is the school's name. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC2: A school logo is not supported yet (PO question).
### C16 · Circulars, tasks and parent notices (M4)

*Proposed from the roadmap scope (14 · M4); PO to confirm.* The AI reads a circular and **suggests**; a person confirms before anything is created (invariant 9). Parent notices never carry student personal data (08 §4). Exit metric (14 · M4): at least 90% of a term's circulars processed with their deadlines captured.

**US-1601** · As an office admin, I want every circular I upload to be read for its issuer, reference number, date, subject, a short summary in English and Telugu and its deadlines, so that nothing in it is missed. [FR-CIR-001..003, FR-CIR-005..007] *(Proposed from the roadmap scope; PO to confirm.)* *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC1: Given a document of type "circular" finishes indexing, when its current version has text, then reading starts by itself and the circulars inbox shows its status (waiting → reading → ready, or "needs manual review").
- AC2: Every suggested deadline shows the sentence it came from with a source chip that opens the circular at that page; a date that is not written in the circular is never suggested.
- AC3: The same version is read only once; a new version is read once more and earlier decisions stay as they were.
- AC4: Given AI is switched off for the school, the monthly AI budget is used up, the AI service is unavailable or the circular has no readable text, then the circular shows "needs manual review" with the reason and staff can still add tasks by hand; "Try again" re-reads it.
- AC5: Given I cannot see the circular (document visibility), then I cannot see its summary or suggestions either (404).

**US-1602** · As an office admin, I want to confirm, edit or dismiss each suggested deadline, so that only real work becomes a task. [FR-CIR-004, FR-TASK-001] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: When I confirm a suggestion I can change its title, details and due date and must choose an owner; one task is created, linked to the circular and its citation.
- AC2: When I dismiss a suggestion no task is created; either decision is recorded in the audit log and cannot be made twice.
- AC3: When every suggestion is decided (or there were none), I can mark the circular "reviewed"; the inbox shows reviewed and not-yet-reviewed circulars separately.

**US-1603** · As staff, I want to see my tasks, and as a principal or office admin the whole school's tasks, with due and overdue filters, so that deadlines are met. [FR-TASK-002..006] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: "My tasks" lists tasks I own, soonest due first, with overdue ones marked; I can mark a task in progress or done.
- AC2: The school view (permission `task.read_all`) lists every task with filters by status, owner and due window (overdue, this week).
- AC3: Holders of `task.manage` can add a task by hand, change its owner, title or due date and cancel it; the owner is told in the app.
- AC4: I never see another person's task unless I hold `task.read_all` (404 otherwise); another school's task is always 404.

**US-1604** · As a task owner, I want a reminder in my language before a task is due and when it becomes overdue. [FR-TASK-007, FR-TASK-008] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given a task due in the configured number of days (default 2), then I get one in-app reminder (English or Telugu, my choice); given it is overdue, one overdue reminder; no duplicates when the job runs again. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC2: Done or cancelled tasks get no reminders. (Email reminders wait for general staff email templates; in-app only for now.)

**US-1605** · As an office admin, I want a short parent notice in English and Telugu drafted from a circular (or from my own text), which I can edit, so that I can post it in the existing parents' groups quickly. [FR-NOTICE-001..004, FR-NOTICE-007] *(Proposed from the roadmap scope; PO to confirm.)* *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC1: The draft is made only from the circular's text (and deadlines I confirmed); no student records are ever sent to the AI, and the draft is marked "AI draft, check before use".
- AC2: A circular marked personal (C2) or restricted (C3) cannot be used for a notice; free text with phone numbers, email addresses or Aadhaar-like numbers is refused with a message saying what to remove.
- AC3: If AI is unavailable, an empty draft opens so I can write the notice myself.

**US-1606** · As a principal, I want to approve a notice and then copy its text or download it as a printable A4 page or an image. A principal may approve a notice they drafted themselves; the audit log marks it as self-approved. [FR-NOTICE-005, FR-NOTICE-006] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Only holders of `notice.approve` can approve; both languages must be filled; an approved notice cannot be edited.
- AC2: After approval the A4 PDF and a PNG image are rendered (Telugu without clipped glyphs); download links last at most 5 minutes and every download is audited; the plain text can be copied for WhatsApp-style groups. *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC3: SchoolOS never sends the notice to parents itself (no parent logins or messaging in core, §9). *(From R2 an approved notice may also be sent on WhatsApp to opted-in guardians from the school's own number, US-2204, ADR-0043; copying and downloading stay.)*

### C17 · Student timeline and early warning (M5)

*Proposed from the roadmap scope (14 · M5; BRD BO-08; 08 §4 PRV-003..005); PO to confirm.* Class teachers record attendance and marks once; SchoolOS turns them, with short behaviour notes, into three indicators (**A**ttendance, **B**ehaviour, **C**ourse performance) and raises a **flag** when a versioned rule says a child needs follow-up. Every flag has an owner (the class teacher by default), a due date 7 days out and an intervention log; nothing happens to a child without a person acting (PRV-005). Flags come from deterministic rules the teacher can read, never from AI. Exit metric (14 · M5): at least 90% of flags actioned within 7 days in the class-teacher pilot.

Purpose limit (08 §4): these features exist only for the school's educational activities and the safety of its enrolled children. Insights are visible only to the student's class teacher and the principal (PRV-004), never used for punishment, marketing or comparisons between schools, and never sent to an AI provider.

**US-1701** · As a class teacher, I want to mark today's attendance for my section on one screen so that the register is done in two minutes. [FR-ATT-001..003, FR-ATT-005] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given I hold `attendance.record` for section 9A, when I open 9A for a date (today by default, IST), then I see every student actively enrolled in 9A with their roll number and a status (present, absent, late, leave), all present by default for a new day.
- AC2: When I save, then the whole day is stored in one step (all or nothing) and audited with counts only; saving the same day again corrects it.
- AC3: A date in the future or outside the section's academic year is refused with a message saying why.
- AC4: Given I teach only 9A, then 9C is not found (404) and does not appear in my list.
- AC5: The month view shows the register grid (students × days) and prints on A4 landscape.

**US-1702** · As an office admin or class teacher, I want to import a month of attendance from our spreadsheet so that I don't retype the paper register. [FR-ATT-004] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: I upload an XLSX or CSV (first column admission number or roll number, then one column per date, cells P/A/L/LV); after the virus check I see a preview: rows matched to students, dates found and every problem (unknown student, bad code, future date) with its row and column.
- AC2: Formulas are never run; a file with a full Aadhaar number is refused; the uploaded file is deleted as soon as it has been read.
- AC3: I can add the rows only when the preview has no problems; they are added in one step.

**US-1703** · As an exam coordinator or class teacher, I want to enter or import marks per exam and subject with the maximum marks so that course performance is based on real results. [FR-MRK-001..005] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given I hold `exam.manage`, I can add an exam (name, date) for the current academic year.
- AC2: Given I hold `marks.record` for 9A, I can enter marks per student and subject with the maximum marks, or mark a student absent (AB); marks above the maximum are refused.
- AC3: I can import the same grid from a spreadsheet (row 1: admission number and subjects; row 2: maximum marks; then one row per student) with a preview, like US-1702.
- AC4: Each student's result shows the overall percentage for the exam and the change from the previous exam of the year.

**US-1704** · As a class teacher, I want to write a short behaviour note about a student so that concerns and progress are not lost in my memory. [FR-EW-010..012] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: A note has a category (positive, observation, concern), a date and up to 500 characters; a full Aadhaar number is refused.
- AC2: Notes are restricted (C3): stored encrypted and visible only to the student's class teacher and the principal; every view is audited.
- AC3: Notes are never sent to an AI provider and never leave the school except in the owner's full data export.

**US-1705** · As a class teacher, I want the system to flag a student who needs follow-up, with the reason in plain words, so that no child slips through. [FR-EW-001..006] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given a student was absent on 3 consecutive school days (AP follow-up rule), then a flag "Absent 3 school days in a row" is raised the same day, owned by the section's class teacher and due in 7 days.
- AC2: Other rules raise flags for low attendance over the last 30 school days, a low overall percentage or a large drop between two exams, and repeated concern notes; each flag shows the numbers that triggered it (for example "absent 3 days: 22/09 to 24/09").
- AC3: The same situation never raises two flags; a student has at most one open flag per rule.
- AC4: I get an in-app notification in my language when a flag is raised for me and once if it becomes overdue.
- AC5: Given the section has no class teacher who can act, the flag is unassigned and the principal is notified.

**US-1706** · As a flag owner, I want to record what I did and close the flag when it is resolved so that the school can show follow-up was done. [FR-EW-007..009] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: I can add an action (talked with the student, called a parent, met a parent, home visit, remedial support, referred to the principal or counsellor, other) with a date and an optional note; the first action marks the flag in progress and counts as "actioned".
- AC2: I close a flag with a reason (improved, support in place, parent informed, no concern, student left, raised in error) and an optional note.
- AC3: "My flags" lists my open flags, overdue first; I can also raise a flag myself for a student in my section.
- AC4: No flag changes a student's record, marks or attendance, and nothing is decided automatically.

**US-1707** · As a class teacher or principal, I want one timeline per student so that I understand the child before I talk to the parent. [FR-EW-013] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: The student's page has a Timeline tab listing, newest first: enrolment and section changes, monthly attendance, exam results, behaviour notes, flags and actions, and (if I may see certificates) certificates issued.
- AC2: The timeline prints on A4 so the principal can answer a parent's request for the data held about their child.
- AC3: Opening a timeline is audited; given the student is not in my sections, it is not found.

**US-1708** · As a principal, I want to see every flag, reassign owners and set the school's thresholds within safe limits so that follow-up works for our school. [FR-EW-014, FR-EW-015] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: The school view lists all flags with filters (status, indicator, overdue) and a summary: raised, actioned within 7 days (the M5 exit metric), overdue and open, as counts for this school only.
- AC2: With a fresh MFA sign-in I can reassign a flag to another staff member who may act for that student, and change thresholds only within the bounds SchoolOS sets (for example the consecutive-absence rule between 2 and 5 days); every change is audited.

**US-1709** · As the school (data fiduciary), I want insights to respect children's data rules so that we stay within DPDP's education exemption. [FR-EW-016..018] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: There is no export, download or share of flags, notes or indicators except the owner's full data export (restricted values masked unless explicitly included) and the printed timeline.
- AC2: Behaviour notes are deleted 1 year after they were written and closed flags 1 year after closing; attendance and marks are school records and are kept per school policy.
- AC3: With a fresh MFA sign-in the principal can erase a note or a flag on a parent's request or when it was entered in error (reason recorded, never the text).
- AC4: No figures are ever combined across schools, and no AI reads insights in M5.

---

### C18 · Tally read connector (M6)

*Proposed from the roadmap scope (14 · M6); PO to confirm. **Built behind the per-school flag `tally.connector.enabled` (default off); ADR-0032 Proposed.*** A small agent on the office PC reads fee ledgers from TallyPrime and sends them to SchoolOS; it only reads, and SchoolOS never connects into the school network. Ledger names are usually student or parent names: C2 personal financial data, readable with `finance.read` only. Exit (14 · M6): fee-due questions answered from synced Tally data; the accountant confirms the figures match Tally.

**US-1801** · As the owner, I want to connect the office PC that runs Tally with a one-time code, so that only that PC can send our Tally figures. [FR-TALLY-001, FR-TALLY-002, FR-TALLY-003] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Given I signed in with MFA recently, when I choose "Add an agent" and name the PC, then SchoolOS shows a one-time code once, with the command to run on the PC and the time it expires (30 minutes); only a hash of the code is kept.
- AC2: When the agent is enrolled with the code, then the code cannot be used again, the PC appears in the agent list with its version and Tally product, and the event is in the audit log.
- AC3: A wrong, used or expired code is refused without saying which; after 10 wrong codes in an hour from one address (or 50 from all addresses) further attempts are refused, so an outsider cannot lock the office PC out (AA-15); a school that is not active cannot enrol; a school has at most 2 active agents.
- AC4: The agent only reads from Tally on the same PC and only sends to SchoolOS over HTTPS; nothing personal is written to the PC's disk.

**US-1802** · As the owner, I want to see whether the agent is syncing and revoke it when the PC is replaced or lost. [FR-TALLY-002, FR-TALLY-009] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: The connector screen shows active agents, last sync, the Tally as-of date, the company, the groups selected and how many ledgers are linked.
- AC2: When an active agent has not called for 48 hours, the owner and finance readers get one in-app notice (EN/TE) and the screen marks it "Not syncing". *(Telugu part deferred: hidden while `SOS_TELUGU_ENABLED` is off, ADR-0036.)*
- AC3: Revoking (recent MFA sign-in) stops the agent at once; figures already synced stay and are marked with their date.

**US-1803** · As the accountant, I want to choose which Tally ledger groups are sent and link each ledger to the right student, so that only fee ledgers leave the PC and dues are counted for the right child. [FR-TALLY-004, FR-TALLY-005, FR-TALLY-006] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: I see the groups of the open Tally company (names only) and choose the fee groups; the screen tells me not to choose salary, supplier or bank groups; the server refuses ledgers from any other group.
- AC2: On the linking screen I see the ledgers not linked yet, with up to 5 suggested students found by searching the ledger name among the students I can see; I link or unlink each one myself (one family ledger may be linked to several siblings). SchoolOS and the AI never link on their own.
- AC3: Ledger and student searches never put names in the URL.

**US-1804** · As the accountant or principal, I want a list of students with dues and the school totals, with the Tally date, so that I can follow up and compare with Tally. [FR-TALLY-007] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: The fee dues screen lists students with dues from their linked ledgers, highest first, with admission number, class, the number of ledgers and the as-of date; totals show the students with dues, the total due and what sits on unlinked ledgers.
- AC2: Only school-wide `finance.read` holders see it; others see a plain explanation (403/404 from the API).

**US-1805** · As the accountant or management, I want to ask "What does this student owe?" or "What are the total fee dues?" in Ask the school and get the Tally figure with a source chip. [FR-TALLY-008] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: The answer gives the figure from the student's linked ledgers only, with the Tally as-of date and a source chip that opens the fee dues screen; with no linked ledger it says so and never guesses.
- AC2: Without school-wide `finance.read`, or while the connector is off, the fee tool is not offered and the answer says the records do not show it.

---

### C19 · Board and portal readiness: APAAR consent, PEN and transfer-in (R1)

Stories of the 2026-27 plan (docs/18 §3 R1.2-R1.3; owner decisions D1, D3, D9 of 2026-10-10; ADR-0039).

**US-1901** · As an office clerk, I want to record each parent's APAAR consent decision with the signed form so that the school can show who agreed, who refused and when. [FR-APC-001..003, PRV-021]
- AC1: I record `given` (only with the signed form uploaded as evidence), `refused`, `withdrawn` (only after a given consent) or `pending` (form sent home), with who decided and the date on the form.
- AC2: Every decision is added to the student's history; nothing is overwritten, and the history shows who recorded each entry and when.
- AC3: Typing an Aadhaar number into the note is refused; the form never shows one.

**US-1902** · As an office clerk or class teacher, I want to print the APAAR consent form for one student or a whole section, in the language our parents read, so that every parent gets a form that lets them say yes or no. [FR-APC-004]
- AC1: The form offers "I give consent" and "I do not give consent" side by side and says admission and studies do not depend on the answer (Supreme Court order of 20 July 2026, as reported; to check against the official form).
- AC2: The principal chooses English or Telugu for the printed form once for the school; the staff screens stay English.
- AC3: I can print only the students whose answer is still pending. Each print is in the audit log.
- AC4: I can also get the forms of a student or a section as one PDF made by SchoolOS, in the parent language (Telugu forms print correctly even though the staff screens are English). [FR-APC-007]

**US-1903** · As a principal or class teacher, I want given / refused / pending counts per section and the list of pending students so that I can follow up before the board and portal windows. [FR-APC-005, FR-APC-006]
- AC1: Class teachers see only their sections.
- AC2: A child whose parents refused is shown as refused, never as missing, and the APAAR readiness checks do not ask the office to generate an APAAR ID for that child.

**US-1904** · As an office clerk admitting a child from another school, I want SchoolOS to stop me creating a second record for a child whose PEN or APAAR ID we already have, and to tell me to import the child into UDISE+ by PEN, so that the child is never "active in two schools". [FR-STU-017..019, FR-IMP-010]
- AC1: The PEN is 11 digits (spaces allowed while typing); it can come from UDISE+, the transfer certificate or the office.
- AC2: A PEN or APAAR ID already on another current record is refused with a link to that record (also in imports, as a row error); the same number twice in one file is an error on both rows.
- AC3: A transfer-in admission needs the PEN; checking the number first tells me whether to open the existing record, re-admit a former student, import the child by PEN, or search UDISE+ by name and date of birth.
- AC4: I can find a student by exact PEN, like the APAAR ID search.

**US-1905** · As an exam coordinator, I want the UDISE+ check sheet to follow the 2026-27 Student Module so that I can type each child's details into the portal field by field. [FR-EXP-006]
- AC1: The sheet lists, in the portal's order, every field SchoolOS holds, including PEN and APAAR ID; fields SchoolOS does not hold (and the Aadhaar number, which it never stores) are named for me to enter from the card or the form.
- AC2: The profile says which public documents it came from and that it is not yet confirmed with a school (owner decision D3).

---

### R2 · Office essentials around the wedge (C20-C25)

Stories of the 2026-27 plan, release R2 (docs/18 §3 R2; owner decisions D4-D9 of 2026-10-10; ADR-0042..ADR-0047). The engineering choices these stories rely on were proposed in the ADRs and **accepted by the owner on 2026-10-14** (owner decisions D12-D30 of 2026-10-14, docs/18 §8); none is open. **Status (2026-10-21):** waves R2-0..R2-3H are built (docs/14 R2 status); each capability below ends with an "as built" note naming what is built and what stays open. Every story also carries the standing definition of done (CLAUDE.md §8): authz allowed / denied 403 / other school 404, audit in the same transaction, no personal data in logs or URLs, English strings (Telugu only on parent-facing surfaces, ADR-0044), usable at 1366×768 and keyboard-only.

**R2 personas** (in addition to §2):

| Persona | Who | Top jobs in R2 |
|---|---|---|
| **Ravi, office assistant at the fee counter** (`office_staff`) | Takes cash, cheques and UPI payments at the counter, often with a queue | Find the child in seconds, take the payment, print or send the receipt, never make an arithmetic mistake |
| **Padma, accountant** (`accountant`) | Sets up fee plans with the principal, reconciles the bank and Razorpay, chases dues | One receipt book for every mode, a daily collection report that matches the cash box, bank and Razorpay matched without retyping |
| **Principal** | Approves concessions, receipt cancellations, refunds, TCs; approves notices | Nothing changes money without a second person; parents get correct, timely messages |
| **Owner / correspondent** | Signs the Razorpay and Meta agreements for the school | Money goes straight to the school's account; the school's own number; knows what messages cost |
| **Srinivas, parent** | Father of a Class 7 child; reads WhatsApp on an Android phone, prefers Telugu | Gets notices, absence alerts, fee reminders and receipts from the school's number; opens a link and pays or reads without an app or password; can say STOP |
| **Verifier** (receiving school, employer, office) | Holds a paper TC from another school | Checks in seconds that the TC is genuine and unchanged |

#### C20 · Certificates: AP TC format and public verification (R2.1; ADR-0045)

**US-2001** · As an office admin in an AP State Board school, I want transfer certificates in the AP TC layout so that receiving schools and offices accept them without retyping. [FR-CERT-015, FR-CERT-016, FR-CERT-017, FR-CERT-018]
- AC1: Given the principal chose the `ap-tc-2027` format in certificate settings, when I prepare a TC, then the preview shows the AP fields in the AP order, each with its source (admission register, verified or provisional) or as a TC input I type (for example identification marks, languages studied, whether qualified for promotion, date of application).
- AC2: The format says it is built from public sources and not yet confirmed with a school (owner decision D3); a school can switch back to the generic format; certificates already issued keep the format they were issued in.
- AC3: The TC prints the UDISE+ PEN when the school holds a verified value; it prints the APAAR ID only when the parent consented and the ID is verified, never for a refused or withdrawn consent.
- AC4: Religion and caste status print as labelled blanks to fill by hand, unless the school asks for them to be printed (a principal's setting with step-up, audited; owner decision D15 of 2026-10-14). No Aadhaar data is ever printed.

**US-2002** · As an office admin, I want every certificate I issue to carry a verification code and a QR so that anyone holding it can check it is genuine. [FR-CERT-019, FR-CERT-020, FR-CERT-023]
- AC1: When a certificate or a duplicate is issued, then it gets a 16-character code (printed in four groups) and a QR that opens the verification page; the code is not derived from the serial, the student or the school.
- AC2: The PDF and the print view show the code, the QR and "Check this certificate at <address>/v".
- AC3: Given a certificate issued before R2, when the principal chooses "Add verification code" (step-up, reason), then a code is created and audited; the original paper has none, so the next duplicate carries it.

**US-2003** · As a verifier, I want to scan the QR or type the code on a public page so that I can see whether the certificate is genuine and what it says. [FR-CERT-021, FR-CERT-022, FR-PUB-004]
- AC1: Given a valid code, the page shows "Valid", the school's printed name and place, the certificate type, serial, date of issue and exactly the values printed on that certificate, nothing from today's records.
- AC2: Given a duplicate, the page also says "Duplicate copy n of serial …"; given a cancelled certificate, it shows school, type, serial and "Cancelled on <date>" without the printed values.
- AC3: Given an unknown, mistyped or withdrawn code, the page says "We could not find a certificate with this code. Check the code or contact the school that issued it." with no hint of any school; dashes, spaces and letter case in a typed code do not matter.
- AC4: There is no search by serial, name or date of birth. After 20 lookups in 10 minutes from one address the page asks to wait. The page is not indexed by search engines and sends no referrer.

**US-2004** · As a principal, I want to see when our certificates were checked online and withdraw a code that was misused, so that verification stays under the school's control. [FR-CERT-023, FR-CERT-024]
- AC1: The certificate detail shows how many times it was checked and when it was last checked (no visitor data beyond that).
- AC2: Given I hold `certificate.approve` with step-up, I can withdraw a code with a reason; the page then answers "not found" for it; a duplicate issued later gets a new code.
- AC3: Every successful check and every withdrawal is in the school's audit log.

**US-2005** · As an office admin preparing a TC, I want to see the student's outstanding fees for information only, so that I can remind the parent, without the system ever holding the TC back. [FR-CERT-025, FR-FEE-030] *(owner decision D16 of 2026-10-14)*
- AC1: Given fees are on and I hold `finance.read`, the TC preview shows the outstanding balance as a note; it is never printed on the TC and never blocks issue or approval.

**US-2006** · As an exam coordinator in a CBSE or CISCE school, I want the generic TC to stay available and later board formats to plug in without changing issued certificates. [FR-CERT-015]
- AC1: Formats are versioned files; adding one needs no migration; the certificate stores the format key and version it was issued with.

**C20 as built (wave R2-1A, 2026-10-17).** US-2001..US-2004 and US-2006 are built with their ACs as automated tests (docs/03 §3.19 "As built"; docs/14 R2-1A status). Refinements: a PEN or APAAR ID that may not be printed shows "—" on the TC; the code alphabet is Crockford base32 and a typed `O` reads as `0`, `I`/`L` as `1`; the code and QR are printed while the school's verification host is set; a code can be added later only to a certificate that never had one (a withdrawn code's paper is replaced by a duplicate). **US-2005 is not built yet**: it needs the fee ledger (wave R2-1B). *Update 2026-10-21:* the fee ledger is built, but the fee note on the TC preview (FR-CERT-025) is still open.

#### C21 · Fee ledger, receipts and payments (R2.2; ADR-0042)

**US-2101** · As an accountant with the principal, I want to set fee heads and a fee plan per class for the year with instalments, so that every student's dues are calculated the same way. [FR-FEE-002..FR-FEE-005, FR-FEE-023]
- AC1: Given I hold `fee.manage`, I can create fee heads (tuition, admission, exam, books, transport, other; each GST-exempt by default) and, per academic year and class (optionally per category such as "new admission"), a plan of heads with amounts and an instalment schedule (number, due date, amount per head).
- AC2: When I apply a plan to a class, then each active student of the class gets a fee account for the year and one charge per instalment and head, in one job with a summary (accounts created, already present, skipped and why); applying again changes nothing.
- AC3: A student admitted mid-year gets the instalments due from their admission date onwards unless I choose otherwise.
- AC4: Changing a plan after charges were posted does not rewrite them: the difference is proposed as adjustments that need approval (US-2104).
- AC5: A head marked taxable cannot be collected through receipts (the school invoices it in Tally); I am told why.

**US-2102** · As an office assistant at the counter, I want to take a fee payment and print or send the receipt in under a minute, using only the keyboard. [FR-FEE-010, FR-FEE-011, FR-FEE-022, NFR-PERF-008]
- AC1: Given I hold `fee.collect`, I open "Collect fees" (shortcut), type part of a name or the admission number and pick the child with the arrow keys; the dues show per instalment and head, oldest first, with late fees.
- AC2: I type the amount (pre-filled with the total due), choose the mode (cash; cheque with number and bank; bank transfer with reference; UPI QR with the UTR), and see how the amount is allocated (oldest due first; I may change it before saving).
- AC3: When I save, then in one transaction the receipt gets the next number of the school's single series for the financial year (for example `RCT/2026-27/000123`), the ledger records the payment, the content is frozen, and the receipt prints (A5 by default; A4 configurable) and, if a guardian opted in for fees, is sent on WhatsApp (US-2206).
- AC4: Two assistants saving at the same moment get consecutive numbers, never the same one, and a failed save leaves no gap.
- AC5: The receipt date is today (IST); there is no backdating. An amount above the total due is refused (no advances in R2).
- AC6: A UTR already on another issued receipt is refused with a link to that receipt. A full Aadhaar number typed into any note is refused.

**US-2103** · As a principal, I want concessions to need my approval so that no fee is reduced by one person. [FR-FEE-008]
- AC1: Given I hold `fee.concession.request`, I request a concession for a student (type such as sibling, staff ward, merit, hardship, RTE, other; an amount or a percentage of chosen heads and instalments; a reason; an optional evidence document).
- AC2: A different person holding `fee.concession.approve` (principal or accountant) approves (step-up) or rejects with a reason; I cannot approve my own request (service check and database `CHECK`). On approval the ledger records the concession entries and the student's dues change at once.
- AC3: An approved concession is never edited; to undo it, a reversing adjustment is requested and approved the same way.

**US-2104** · As an accountant, I want late fees added automatically by the school's rule, and waivers and adjustments approved by someone else. [FR-FEE-007, FR-FEE-009]
- AC1: Given the plan has a late-fee rule (a flat amount per overdue instalment after a grace period, or an amount per day with a cap), the nightly job adds each late fee once (running it again adds nothing) and never compounds.
- AC2: A waiver of a late fee or any manual adjustment is requested and approved by two different people, with a reason, and recorded in the ledger as its own entry.

**US-2105** · As an accountant, I want a wrong receipt cancelled only with a second person's approval, keeping its number, so that the receipt book always reconciles. [FR-FEE-012]
- AC1: Given I hold `fee.collect` or `fee.manage`, I request cancellation with a reason (wrong student, wrong amount, cheque dishonoured, duplicate entry, other).
- AC2: A different person holding `fee.receipt.cancel` approves (step-up); then in one transaction a cancellation record is written, the ledger reverses the payment, the receipt shows "Cancelled on <date>" everywhere, and its number is never reused.
- AC3: A cancelled receipt's parent link still opens and says it was cancelled. Nothing ever edits a receipt.

**US-2106** · As an accountant, I want to refund a payment with a voucher and a second person's approval. [FR-FEE-013, FR-PAY-011]
- AC1: I request a refund against a receipt (amount up to what was paid and not yet refunded, reason, mode). A different person holding `fee.refund.approve` (principal or accountant) approves with step-up.
- AC2: For a Razorpay payment, SchoolOS asks Razorpay to refund to the payer; the refund voucher (`RFD/2026-27/0001`) and the ledger entry are written when Razorpay confirms. For cash or cheque, the voucher is written on approval.

**US-2107** · As an owner, I want to connect the school's own Razorpay account so that online fees go straight to our bank account. [FR-PAY-002..FR-PAY-004, FR-PAY-012, FR-PAY-013, FR-PAY-016]
- AC1: Given I hold `payments.configure` with step-up, "Connect Razorpay" takes me to Razorpay to sign in to **our** account and allow SchoolOS; back in SchoolOS the account shows as connected with its Razorpay account id, and a webhook is set up for us.
- AC2: The page says plainly: money goes to the school's Razorpay account and bank; SchoolOS never holds it; Razorpay's own fees apply.
- AC3: Until SchoolOS is an approved Razorpay partner (it applies once a staging demo exists; D13), an operator can help us paste a key id and key secret from our own dashboard instead; the secret is stored encrypted and never shown again.
- AC4: Disconnecting stops new links at once, keeps all receipts and history, and is in the audit log. If we revoke SchoolOS in Razorpay's dashboard, SchoolOS marks the account disconnected and tells the owner.

**US-2108** · As an office assistant or accountant, I want to send a parent a payment link for what is due, so that the parent can pay from the phone and the receipt appears by itself. [FR-PAY-005..FR-PAY-009, FR-PAY-015]
- AC1: Given online payments are connected, from the student's fee page I choose instalments and "Send payment link"; SchoolOS creates a Razorpay link for that amount (no name, phone or email sent to Razorpay) and sends the dues page to opted-in guardians on WhatsApp, or shows the link for me to share.
- AC2: When the parent pays, then within a minute of Razorpay's webhook a receipt is issued in the same series (mode "Razorpay", with the payment id), the ledger records it, and the guardian gets the receipt message if opted in.
- AC3: The same payment reported twice creates one receipt. A payment whose amount differs from the link, or that SchoolOS cannot match, appears on the reconciliation list, never as a guessed receipt.
- AC4: I can see each link's status (created, paid, expired, cancelled) and cancel an unpaid link.

**US-2109** · As an accountant, I want UPI payments to our own QR matched with the bank statement, so that I know every UPI receipt really reached the bank. [FR-FEE-014..FR-FEE-017]
- AC1: Given I hold `fee.manage`, I upload the school's bank UPI QR image and payee name once; the counter and the parent dues page show it.
- AC2: Given I hold `fee.reconcile`, I upload a bank statement (CSV or XLSX); the file is read and deleted, credits are listed, and every exact UTR-and-amount match with a UPI receipt is proposed; I confirm the matches in one step (never automatically).
- AC3: UPI receipts not seen in a statement after 7 days (configurable) are listed as exceptions; unmatched credits are listed too, so I can record a payment the counter missed.

**US-2110** · As an accountant or principal, I want the daily collection report and the dues list, so that the cash box, the bank and Razorpay match the receipt book. [FR-FEE-018..FR-FEE-021, FR-FEE-024]
- AC1: The daily report shows receipts by mode and by collector, cancellations and refunds of the day, and the cash and cheque totals to hand over; it prints on A4 and downloads as CSV (the "day book" for entry into Tally).
- AC2: The accountant can close a day; after that no receipt or cancellation can be dated that day.
- AC3: The dues list per class or section shows students with overdue amounts, oldest first, with "Send reminder" and "Send payment link"; downloading it needs step-up (bulk personal financial data).
- AC4: A student's fee page shows the full account statement (charges, concessions, late fees, payments, cancellations, refunds) with the running balance.
- AC5: Class teachers and teachers see no fee data. Fee screens are hidden for a school running alongside its ERP unless the owner turns fees on.

**US-2111** · As an accountant, I want the reconciliation report for online payments every morning. [FR-PAY-010]
- AC1: At 06:00 IST SchoolOS compares yesterday's Razorpay payments, refunds and settlements with our receipts and lists: paid but no receipt, receipt but not captured, amount mismatch, refunded, settlement not seen after three working days.
- AC2: Each line links to the receipt or the Razorpay payment id; I can mark a line explained with a note (audited). Nothing is changed automatically.

*As built (R2-1B, 2026-10-18):* US-2101..US-2106 and US-2110 work for cash, cheque, bank transfer and UPI (UTR recorded at the counter); sending on WhatsApp (US-2102 AC3, US-2110 AC3), Razorpay (US-2107, US-2108, online refunds in US-2106 AC2), bank statements (US-2109) and the online reconciliation report (US-2111) follow in later R2 waves (docs/14).

*As built (R2-2D, 2026-10-18):* US-2107 (Settings > Online payments: the plain merchant statement, connect with the school's API keys for pilots, Razorpay sign-in once SchoolOS is an approved partner, test, new webhook secret shown once, disconnect; a revoked or refused connection tells the owner), US-2108 (a "Payment links" tab on the student's fee account: choose instalments, copy the link to share, see each link's status and receipt, cancel an unpaid one; WhatsApp sending of the link waits for R2-3G), US-2106 AC2 (online refunds back to the payer after a second person's approval, voucher on Razorpay's confirmation) and US-2111 (Fees > Online reconciliation, each morning at 06:00 IST, explain with a note). When the counter takes the same dues while a parent pays online, only the first becomes a receipt; the other payment is listed for the accountant to refund or explain, never a second receipt. Applying a plan shows a preview and then applies at once (no waiting job). Fees appear once SchoolOS enables them for the school and the owner or principal switches them on in school settings.

*As built (R2-2E, 2026-10-18):* US-2109 works. AC1: the accountant (`fee.manage`) types the school's payee name and UPI ID once in Settings > UPI QR; SchoolOS draws the school's static QR from them (the same `upi://pay` content a bank's static QR carries, with no amount), so no image is uploaded; the counter shows it on request and the parent dues page will (R2-3H). The counter also checks a typed UTR: already on another receipt (with a link) or already seen in an imported statement. AC2: Reconcile UPI > Bank statement reads the uploaded statement (bank detected automatically, or chosen from SBI, HDFC, ICICI, Axis, Kotak or a generic layout), deletes the file at once and lists the credits; exact UTR-and-amount matches are proposed and ticked, a credit without a UTR is proposed against the only unconfirmed UPI receipt of the same amount near its date (shown, not ticked), and "Confirm N matches" confirms the ticked ones; a credit can also be matched by hand with a receipt of the same amount, or marked "not a fee". AC3: the UPI exceptions tab lists UPI receipts not seen in the bank after the school's window (7 days by default, 1-30), credits nobody matched and confirmed credits whose receipt was cancelled afterwards, with "Find matches again" and the confirmed vs not-yet-confirmed counts; fee.reconcile holders get an in-app summary each morning. A credit whose UTR is on a receipt with a different amount is shown next to that receipt and never "corrected": a wrong receipt is cancelled and issued again.

*As built (R2-3G and R2-3H, 2026-10-18):* receipt messages go to opted-in parents automatically once the school switches them on (the sending part of US-2102 AC3, through the automatic receipt message, not a button), reminders follow the school's offsets (US-2205), and the parent dues page shows the UPI QR and "Pay online" (US-2403). Still open (wave R2-3J): "Send reminder" and "Send payment link" on the dues list (US-2110 AC3), "Send on WhatsApp" on the counter's success panel and the receipt and fee-account message tabs, editing reminder offsets on the web. US-2112 (the Ask fee tool on the SchoolOS ledger, FR-KB-044) is not built: Ask still reads Tally.

**US-2112** · As management, I want to ask "How much is due from Class 9?" in Ask the school and get the ledger figure with a source chip, when fees are on. [FR-KB-044]
- AC1: Given fees are on and I hold `finance.read`, the fee tool reads the SchoolOS ledger (as of now) instead of Tally; the source chip opens the dues list.
- AC2: Without `finance.read` the tool is not offered.

#### C22 · WhatsApp parent messages (R2.3; ADR-0043)

**US-2201** · As an owner, I want to connect the school's own WhatsApp Business number in a few steps, so that parents get messages from our name and number and we pay Meta directly. [FR-MSG-002..FR-MSG-006, FR-MSG-024]
- AC1: Given I hold `messaging.configure` with step-up, "Connect WhatsApp" opens Meta's sign-up window where I sign in with the school's Meta business login, choose or create the business account and verify the school's number; back in SchoolOS the number, display name, quality rating, messaging limit and billing currency show.
- AC2: The page tells me: the school pays Meta directly with its own payment method; INR billing must be set before 31 Dec 2026 (Meta stops delivering for non-INR accounts after that); new accounts can message 250 people a day until Meta verifies the business, so verify the business first.
- AC3: SchoolOS then submits its message templates to our account; I see each template's status (pending, approved, rejected, paused).
- AC4: Disconnecting stops all sending at once, revokes open parent links sent through it, and is audited. On the dedicated tier the WhatsApp page says it is not available yet.

**US-2202** · As an office assistant, I want to record each guardian's WhatsApp opt-in with evidence, so that we message only parents who agreed. [FR-MSG-008, FR-MSG-009]
- AC1: Given I hold `messaging.optin.record`, on a guardian I record opt-in or opt-out for notices, attendance alerts and fees, with the method (signed form uploaded as evidence, parent's join message, in person) and the date; every decision is a new entry and the history shows who recorded it.
- AC2: I can print a slip per student or section with the school's QR "Send JOIN to get school messages on WhatsApp" in the school's parent language; a parent who sends the join message is opted in for the categories on the slip and gets a confirmation message.
- AC3: A guardian without a valid mobile number cannot be opted in. Section counts show opted in, not yet asked, opted out.

**US-2203** · As a parent, I want to stop messages by replying STOP. [FR-MSG-010]
- AC1: When I reply STOP (or tap "Stop messages", or send ఆపు), then within a minute I am opted out of every category, I get one confirmation, and no further message is sent to me until I join again.
- AC2: The school sees the opt-out with its date on the guardian; staff cannot override it; re-joining needs a new opt-in.

**US-2204** · As an office admin, I want to send an approved notice to the parents of chosen classes on WhatsApp, so that every opted-in parent gets it from the school's number. [FR-MSG-011, FR-MSG-012, FR-MSG-017]
- AC1: Given the notice is approved (M4) and I hold `message.send`, I choose classes or sections and see before sending: recipients, skipped and why (no opt-in, no number, opted out), languages, and the estimated Meta cost (for example "412 messages, about ₹56 incl. GST, paid by the school to Meta").
- AC2: When I confirm, messages are queued and sent outside quiet hours (21:00-07:00 IST); each carries the notice title and a link to the full notice page; the delivery report shows sent, delivered, read, failed per class, updating live.
- AC3: A notice is sent at most once to the same guardian unless I choose "Send again" with a reason.

**US-2205** · As an accountant, I want fee reminders sent automatically before and after due dates to opted-in guardians, with a pay link. [FR-MSG-014]
- AC1: Given fee reminders are on, at 10:00 IST on the school's offsets (default 3 days before, on the due date, 7 days after), each guardian opted in for fees gets one reminder per account and offset with the amount due and a link to the dues page (with "Pay online" when connected).
- AC2: No reminder is sent for an instalment already paid, for amounts under the school's minimum, or on a day the school marked as a no-message day.

**US-2206** · As a parent, I want the receipt on WhatsApp right after I pay, so that I do not need to keep paper. [FR-MSG-015]
- AC1: When a receipt is issued (counter or online) and a guardian is opted in for fees, they get a message with the receipt number, amount and a link to the receipt page.

**US-2207** · As a principal, I want parents told the same morning when their child is marked absent. [FR-MSG-013]
- AC1: Given absence alerts are on, at the school's time (default 11:00 IST) each guardian opted in for attendance whose child is marked absent today gets one message with the child's first name and the date; late and leave are not alerts.
- AC2: A later correction sends nothing more; the message says "marked absent at the time of this message".

**US-2208** · As an office admin, I want to see what was sent to a guardian and whether it arrived. [FR-MSG-016, FR-MSG-017, FR-MSG-022]
- AC1: On a student or guardian I see each message: purpose, template, language, time, status (queued, sent, delivered, read, failed with a plain reason, skipped with why), and the linked notice or receipt; never the parent's phone in a URL.
- AC2: Permanent failures are not retried; temporary ones are retried up to three times within an hour.

**US-2209** · As an owner, I want sending to pause by itself when Meta lowers our quality or restricts the account, so that the school's number is not blocked. [FR-MSG-007, FR-MSG-021]
- AC1: When Meta reports a red or flagged quality rating, a restriction, or moves a template to marketing, sending of the affected messages stops, the owner and principal get an in-app notice explaining what happened and what to do, and a person resumes when it is safe.

**US-2210** · As a parent who writes to the school's number, I want a polite answer telling me how to reach the office, since nobody reads messages there. [FR-MSG-018]
- AC1: Any message that is not a keyword gets at most one automatic reply a day with the office phone number; the message text is not stored.

*Built in wave R2-1C (2026-10-17): US-2201, US-2202, US-2203, US-2209, US-2210 and the message history of US-2208; US-2204..US-2207 (sending) and the broadcast report are wave R2-3G (docs/14). A staff opt-in after a parent's STOP needs a signed form (US-2203 AC2); a new number needs a new opt-in.*

*Built in wave R2-3G (2026-10-18): US-2204 ("Send on WhatsApp" on an approved notice: sections, recipients, skips by reason, languages and the estimated Meta cost incl. GST, confirm with the consequence line, live delivery counts; "Send again" needs a reason), US-2205 (fee reminders at 10:00 IST on the school's offsets, collectable amounts only, no-message dates in WhatsApp settings), US-2206 (receipt message right after a receipt is issued), US-2207 (absence alerts once a day at the school's time, `absent` only), US-2208 AC2 (temporary errors retried after 5 and 20 minutes) and the delivery report per broadcast. Messages that would go out in quiet hours or above the daily cap wait for the next opening instead of being dropped; nothing is sent while the school is suspended (D31). Built in R2-3J (2026-10-21): "Send reminder" from the dues list for chosen rows or everyone in the list (FR-MSG-025), "Send on WhatsApp" on the counter's saved panel and the receipt page (FR-MSG-026), the reminder days on `/settings/fees` (US-2205 AC1), and withdrawing an approved notice, which stops its parent links and cancels messages not sent yet (FR-NOTICE-009).*

#### C23 · Parent language per school (R2.3; ADR-0044)

**US-2301** · As a principal, I want to choose English or Telugu for everything parents receive, while our staff screens stay English. [FR-LANG-001, FR-LANG-002, FR-LANG-005, FR-LANG-006]
- AC1: Given I hold `tenant.settings.manage` with step-up, I choose the parent language; messages, parent pages, opt-in slips, verification slips and the APAAR form use it; staff screens, receipts and certificates as documents stay English.
- AC2: While any Telugu text we would use is not yet reviewed by a fluent reviewer, the setting shows that warning.

**US-2302** · As an office assistant, I want to set one guardian's language when it differs from the school's. [FR-LANG-003]
- AC1: On a guardian I choose English, Telugu or "school default"; a parent can also choose on the opt-in page.

**US-2303** · As an office admin sending a notice in Telugu, I want SchoolOS to ask for the Telugu text before sending and tell me if any parent got English instead. [FR-LANG-004]
- AC1: When the parent language is Telugu, sending a notice needs its Telugu title and text (an AI draft may propose them; I confirm). If a Telugu template is not approved by Meta, English is sent and the delivery report says so.

*As built (R2-0, R2-1C, R2-3G, R2-3H; 2026-10-17..18):* US-2301 (the parent-language card in school settings, step-up and audited, with the "not yet reviewed" warning; staff screens, receipts and certificates stay English), US-2302 (the language on the guardian and in the opt-in panel; the parent can choose it on the opt-in page) and US-2303 (a notice goes out in Telugu only with a real Telugu title and an approved Telugu template, otherwise in English, and the delivery report shows the fallback) are built (docs/03 §3.24 "As built"). Open: a fluent reviewer's sign-off of the Telugu texts (D23).

#### C24 · Parent link pages (R2.3; ADR-0046)

**US-2401** · As a parent, I want to open the full notice from the WhatsApp message on my phone without installing anything or signing in. [FR-PUB-005, FR-PUB-006, FR-PUB-008..FR-PUB-010]
- AC1: Tapping the message button opens a fast mobile page in my language with the school's name, the notice title, date, text and its PDF.
- AC2: The link works for 30 days; after that the page says it has expired and how to get a new one. The same page appears for a wrong or revoked link, naming no school.

**US-2402** · As a parent, I want to open my receipt from the message and keep it. [FR-PUB-009]
- AC1: The receipt page shows the school, receipt number and date, my child's name, class and admission number, amounts per head and mode; it can be saved as PDF; it says "Cancelled on …" if the school cancelled it. The link works for 90 days.

**US-2403** · As a parent, I want to see what is due and pay online or by the school's UPI QR. [FR-PUB-009, FR-PAY-005]
- AC1: The dues page (link valid 7 days) shows outstanding instalments with due dates and late fees, the school's UPI QR and payee name if set, and "Pay online" if the school connected Razorpay; paying online opens Razorpay's page for the school's account and returns to a "Thank you, the school will send your receipt" page.
- AC2: The page never asks for card or bank details itself and lets me change nothing else.

**US-2404** · As an office admin, I want to revoke a link sent to the wrong parent. [FR-PUB-007, FR-PUB-012]
- AC1: Given I hold `message.send`, from the message history I revoke the link; it stops working at once; this is audited. Links are revoked automatically when a guardian is unlinked, changes number or opts out.

**US-2405** · As a principal, I want to know parent pages cannot be used to find other children or schools. [FR-PUB-011]
- AC1: Links cannot be guessed; repeated wrong links from one address are slowed and then refused; no page shows another child, a phone number, an address or a date of birth.

*As built (R2-3H, 2026-10-18):* US-2401 AC1 (the notice page: title, date, text, "Open the PDF" while the rendered PDF is kept) and AC2 (uniform expired page); US-2402 AC1 (the receipt card, "Save as PDF" once the PDF is stored, a red "Cancelled on …" banner); US-2403 AC1 and AC2 (first name and class, instalments with due dates, late fees and "Overdue", the school's UPI QR and payee, "Pay online" when Razorpay is connected, Razorpay's page for the school's account, the return page "Thank you … the school will send your receipt"; nothing else can be changed and no card or bank details are asked); US-2405 AC1 (another child's, guardian's or school's target is the uniform 404). US-2404's staff revocation is R2-3G's: the API `POST /messages/{id}/revoke-link` (`message.send`, audited) is built; a "Revoke link" button in the web message history is not built yet, and links are not revoked when a notice is withdrawn (M4 has no withdrawal yet). The opt-in page shows the categories and lets the parent choose the message language (US-2302 AC1); stopping stays STOP on WhatsApp.

#### C25 · Ask the school: finish-up (R2.4)

The conversation screens, source chips, follow-ups, feedback, verified answers, history and memory are **built** (docs/17 §5.3, `apps/web/src/features/ask/`; docs/14 M2 status). docs/18 §2 listed them as "not built"; that was stale. R2 adds what is missing.

**US-2501** · As a principal or owner, I want to see how many AI answers the school used this month against our bundle, so that we are not surprised by overage. [FR-KB-040]
- AC1: Given I hold `tenant.billing.read`, the Ask page and "Plan & billing" show "Answers this month: 312 of 1,000 included" (calendar month, IST), and from 80% a quiet notice; extra answers are billed at the bundle's rate (ADR-0038).
- AC2: Other staff see no numbers; they see a banner only when answers are limited (budget used up: search-only results until the month resets, and whom to ask).

**US-2502** · As staff, I want a clear explanation when Ask answers only with search results, so that I know it is not broken. [FR-KB-041]
- AC1: When an answer is search-only because the budget is used up, AI is off for the school, or the AI service is unavailable, the answer says which, in plain words, with what to do next.

**US-2503** · As staff, I want every `sos://` source link in an answer or verified answer to open the right screen, or say plainly that I can no longer open it. [FR-KB-042]
- AC1: Source links resolve through `GET /knowledge/resolve`; a source I may not open answers 404 and the chip reads "Source you can no longer open".

**US-2504** · As the product owner, I want end-to-end tests of the Ask journeys so that releases do not break them. [FR-KB-043]
- AC1: Playwright covers ask and stream, citations and popovers, feedback, follow-ups, rename/pin/delete, memory add/confirm/forget, search-only and budget banners, keyboard-only use at 1366×768 and a 375 px phone.

*As built (R2-2F, 2026-10-17):* US-2501..US-2504 are built (usage meter and banners, search-only reasons, `/knowledge/resolve`, Playwright Ask journeys in `e2e/ask.spec.ts`; docs/14 R2-2F status). US-2112 (C21) stays open.

---

### R3 · Compliance pack and differentiation (C26-C31)

Stories of the 2026-27 plan, release R3 (docs/18 §3 R3, planned Apr-Jun 2027, before DPDP's day-to-day duties start in mid-May 2027; docs/08 §2). **Status: planned (documented 2026-10-21), not built.** The engineering choices are in ADR-0048..ADR-0053, accepted by the lead engineer on 2026-10-21 because the owner asked not to be asked (lead decisions D36-D46, docs/18 §8; the owner may revisit any of them). **C30 (RTE register) is designed but not scheduled**: owner decision D10 left the RTE register out of this year's build and stands until the owner reopens it (D43). Everything about the law comes from docs/08 and is **not legal advice** (D30). US numbers: no `US` sequence exists in the id reservation tool, so these are the next free numbers by capability (US-2601 onwards). Every story also carries the standing definition of done (CLAUDE.md §8).

**R3 personas** (in addition to §2 and the R2 personas):

| Persona | Who | Top jobs in R3 |
|---|---|---|
| **Principal as grievance officer** | Named in the school's privacy notice as the contact (`privacy_contact`) | Answer parents' requests and complaints within 7 days, decide what can be erased, sign off access reports, record breaches and meet the 72-hour report |
| **Lakshmi, office admin** | Logs requests at the counter, prints forms, prepares the CBSE disclosure and RTE claims | Know what is due this week, never hand a child's data to the wrong adult |
| **Owner / correspondent** | Signs the DPA with SchoolOS, issues notices | Show an inspector or a parent that the school follows DPDP and CBSE rules |
| **Exam coordinator** | Owns UDISE+, SSC and CBSE submissions | Be reminded before each portal window with the number of students not yet ready |
| **Parent** | Asks the office what the school holds, wants a correction, withdraws a photo consent, complains | A clear answer by a date, in writing |

#### C26 · DPDP processor pack: notices, data-principal requests, grievances, record classes (R3.1; ADR-0048)

**US-2601** · As the owner, I want SchoolOS's DPA, privacy notice, terms and DPIA templates so that the school can sign and issue them before real data goes in. [FR-DPDP-001, FR-DPDP-002]
- AC1: The templates (`docs/legal/`) say "Not legal advice" at the top, carry a version, list their placeholders, and mark every point the law summary (docs/08) does not settle as "(to confirm)".
- AC2: In SchoolOS I can preview the parents' and staff notices filled with the school's name, address and privacy contact, and download them as A4 PDF.

**US-2602** · As the principal, I want to record which notice we issued, when and how, so that we can prove parents were told. [FR-DPDP-003, PRV-001]
- AC1: Given I hold `privacy.request.manage`, I record a notice issue (audience, template version, language, date, channel, the final PDF as a document); the list shows every issue with its version, newest first.
- AC2: When SchoolOS publishes a newer template version, the privacy page says "A newer notice version exists" until we record issuing it.

**US-2603** · As the principal, I want the school's privacy contact set once and printed everywhere it is needed. [FR-DPDP-004, PRV-011]
- AC1: Given I hold `tenant.settings.manage` with step-up, I set the contact (name or designation, office phone, e-mail); notices, letters and access reports print it; staff see it in the app footer.

**US-2604** · As office admin, I want to log a parent's request or complaint with a due date so that nothing is forgotten. [FR-DPDP-005..007, NFR-PRV-004]
- AC1: I choose the kind (access, correction, erasure, nomination, consent withdrawal, grievance, other), the child or guardian or staff member it is about, who asked and how I checked who they are (in person with a document seen, their phone matches the guardian's number on record, or a staff member signed in); the due date is 7 days after receipt (the school may set fewer, never more).
- AC2: The request text and my notes are never shown in logs or audit entries; the list shows kind, subject, status and days left, with overdue ones first.
- AC3: I can print an acknowledgement letter with the reference and due date.

**US-2605** · As the principal, I want an access report for one child, guardian or staff member so that I can answer "what do you hold about us?". [FR-DPDP-008..010, NFR-PRV-007]
- AC1: The report lists, per kind of record, what SchoolOS holds about that person, where it came from and how long it is kept; never another child's data and never an Aadhaar number (only "last 4 digits and as-printed details held").
- AC2: The report is released only by someone other than the person who logged the request, with step-up; it is downloadable for 24 hours to print or hand over; SchoolOS does not send it.
- AC3: Every module that holds personal data contributes to the report (a test fails when a new module forgets).

**US-2606** · As the principal, I want a correction request to lead to the usual change request with evidence, so that official records are corrected properly. [FR-DPDP-011]
- AC1: From the request I open a change request (identity fields) or the normal edit; the request shows its linked changes and closes when they are decided; SchoolOS never edits a record because a request says so.

**US-2607** · As the principal, I want to see what can lawfully be erased before I decide, so that I never delete an official record by mistake. [FR-DPDP-012..014, PRV-009]
- AC1: For an erasure request the screen lists each kind of record with "can be erased", "kept: legal obligation", "kept: official record" or "kept until <date>", with the reason in words from the record-class catalogue.
- AC2: Approving (step-up, not the person who logged it) erases what can be erased in one go and lists anything needing a manual step; the admission register, issued certificates, financial records in their 8-year period and the audit log are never erased on request.

**US-2608** · As a guardian or staff member, I want to name someone to act for me, and as the principal I want to answer complaints in writing, within the same register. [FR-DPDP-015, FR-DPDP-016]
- AC1: A nomination records the nominee's name, relationship and contact (restricted) and can be withdrawn.
- AC2: A grievance is closed only with a written response, which I can print from the letter template.

**US-2609** · As the owner, I want to see every kind of record SchoolOS keeps with its retention and whether it can be erased. [FR-DPDP-017]
- AC1: The Data retention screen shows the record classes read-only (owner module, retention, basis, erasure rule); they match docs/05 §13 exactly (test).

#### C27 · Consent register for optional purposes (R3.1; ADR-0049)

**US-2701** · As the principal, I want to choose the optional purposes the school asks parents to consent to (for example photos on the website) with a clear description. [FR-DPDP-020, PRV-006]
- AC1: Given `tenant.settings.manage` with step-up, I enable suggested purposes or add our own (label, what, why, who receives it, for how long); editing a purpose already answered makes a new version.

**US-2702** · As office staff, I want to record each family's decision with the signed form and print forms that offer "I do not agree" next to "I agree". [FR-DPDP-021..023]
- AC1: Same rules as the APAAR register: `given` needs the signed form; history is append-only; withdrawal only after a given consent; no Aadhaar number on forms or notes.
- AC2: No decision counts as "not given" wherever SchoolOS checks a purpose.

**US-2703** · As the principal, I want one view of a child's consents: optional purposes, APAAR and the guardians' WhatsApp opt-ins. [FR-DPDP-024..026]
- AC1: The view shows each with its own source and date; APAAR and WhatsApp are changed only on their own screens. Section counts per purpose for class teachers of their sections.

#### C28 · Breach handling (R3.1; ADR-0050)

**US-2801** · As a SchoolOS operator, I want to record an incident with its CERT-In and school-notice clocks so that we meet 6 hours and 24 hours. [FR-DPDP-030, FR-DPDP-031]
- AC1: The incident holds categories and counts only (a summary with names, numbers or e-mail addresses is refused); two operators confirm the affected schools before any school is notified.

**US-2802** · As the principal, I want SchoolOS's notice to appear in our breach register at once, marked urgent. [FR-DPDP-032]
- AC1: Within a minute of confirmation the owner and principal get an urgent in-app notification and the register shows the notice with its facts.

**US-2803** · As the principal, I want to record our own breaches (a lost register, a mis-sent message) with the Board and family duties and their clocks. [FR-DPDP-033..035, PRV-008]
- AC1: Each open breach shows the next duty and the time left: Board notice, detailed Board report at 72 hours, families notified; each records when and how it was done. Reminders reach the owner and principal at 24 and 60 hours while the report is not recorded.

**US-2804** · As the principal, I want draft notices for families and an outline for the Board filled from the record, so that I can act quickly. [FR-DPDP-036, FR-DPDP-037]
- AC1: Drafts come from the breach runbook templates; a family notice can become a parent-notice draft that is approved and sent like any notice; SchoolOS never sends one by itself.

#### C29 · CBSE Appendix IX public disclosure (R3.2; ADR-0051)

**As built (R3-1C, `wip/r3-disclosure`, migration `0067_disclosure`, 2026-10-21).** US-2901..US-2904 are built: `app/disclosure/` (format file `verified: false`), the staff screen `/settings/disclosure` (CBSE schools only) and the public page `{SOS_PUBLIC_BASE_URL}/d/{key}`; the HTML file and A4 PDF come from the `pdf` worker. Acceptance tests: `apps/api/tests/disclosure/`, `tests/public/test_surface_security.py`, web `features/disclosure/`, `server/public/disclosure.test.ts`.

**US-2901** · As the principal of a CBSE school, I want the Appendix IX disclosure filled from what SchoolOS already holds so that I only type what is missing. [FR-DISC-001..004]
- AC1: Given our boards include CBSE, the disclosure screen lists Appendix IX in CBSE's order; school details, sections, teacher-section ratio, staff counts by role and the fee structure (when fees are on) are filled from SchoolOS and marked "From SchoolOS"; the rest are fields to type, documents to choose, or a link.
- AC2: The format says it is built from public sources and not yet confirmed with a CBSE school (D3).

**US-2902** · As the principal, I want to publish the disclosure only when I decide, and to know exactly what is public. [FR-DISC-005..007]
- AC1: Publishing (step-up) freezes the page as it is; later changes to records do not change it until I publish again. I can withdraw it with a reason.
- AC2: Before publishing I confirm that the chosen documents contain no student's personal data; the page never shows a child's name or any count below 10 ("fewer than 10").

**US-2903** · As a parent or a CBSE inspector, I want to open the school's disclosure from a link on the school website. [FR-DISC-008..010]
- AC1: The link opens a read-only page on the public origin with the published content and the documents, "Published by <school> on <date>"; a withdrawn or unknown link shows the same "not found" page as other public links.

**US-2904** · As office admin, I want the disclosure as a file for our own website. [FR-DISC-011]
- AC1: I download the published version as one HTML file (no scripts) and as an A4 PDF.

#### C30 · RTE 25% register (designed; not scheduled while D10 stands; ADR-0052)

**US-3001** · As office admin, I want to record each child admitted under RTE Section 12(1)(c) with the lottery or allotment reference and the documents seen. [FR-RTE-001..004]
- AC1: Only when the school says RTE applies; category from the state file (AP: unverified, D3); documents are links to uploaded documents with who verified them; no Aadhaar number or income figure stored.

**US-3002** · As the principal, I want RTE status visible only to the people who need it. [FR-RTE-005, FR-RTE-006]
- AC1: Class teachers and teachers never see it; it is not printed on certificates or registers, not in messages, parent pages or Ask answers.

**US-3003** · As the accountant, I want to prepare each period's reimbursement claim from the register and track what the state paid. [FR-RTE-007..010]
- AC1: A claim lists RTE children enrolled in the period with the amounts the state file defines; marking it submitted freezes it; received amounts are recorded per claim and per child.

**US-3004** · As office admin, I want an RTE admission to propose the fee concession for a person to approve. [FR-RTE-011]
- AC1: "Request RTE concession" creates a pending concession of type `rte`; approval follows the normal maker-checker; nothing is approved automatically.

#### C31 · Portal calendar and readiness reminders (R3.3; ADR-0053)

**US-3101** · As exam coordinator, I want the year's portal windows (UDISE+ freeze, SSC nominal roll, CBSE registration and LOC, CISCE, APAAR drives) listed for our school. [FR-RDY-001..003]
- AC1: Events apply by board and classes; each shows "Expected around …" (shipped, unverified) or "Confirmed" (by a person, with where the date came from).

**US-3102** · As the principal, I want to confirm a date and choose who owns the work. [FR-RDY-004, FR-RDY-005]
- AC1: Given `readiness.calendar.manage`, I confirm opening and closing dates with a source (a circular document, a portal screen, a phone call) and an owner; I can add the school's own events.

**US-3103** · As the owner of a portal task, I want reminders 30, 14, 7 and 1 days before the window closes, with how many students are not ready. [FR-RDY-006..008]
- AC1: One task per event with the closing date as due date, and in-app notifications at each lead day with counts only ("UDISE+ freeze in 14 days: 142 of 160 ready"); no message to parents.

**US-3104** · As staff, I want the next window shown on the readiness screens. [FR-RDY-009, FR-RDY-010]
- AC1: Each readiness profile shows its next window and state (expected, confirmed, open now, closed); a confirmed circular deadline can be linked to an event.

## 5. Data-quality rules catalog (initial)

| Rule | Checks | Default severity | Explanation template (EN) |
|---|---|---|---|
| DQ-001 | Name: admission register vs Aadhaar-as-printed | by match class (see §6) | "Name differs between admission register and Aadhaar." |
| DQ-002 | DOB: admission register vs Aadhaar-as-printed | blocker | "Date of birth differs. Boards and APAAR need these to match." |
| DQ-003 | Gender: register vs Aadhaar | high | "Gender differs between records." |
| DQ-004 | Father/mother name across sources | medium/high | "Parent name spelled differently across records." |
| DQ-005 | Required field missing for selected export profile | blocker | "Required for {profile}: {field} is missing." |
| DQ-006 | Name format invalid for export profile (length, characters) | blocker | "{profile} does not accept {issue}." |
| DQ-007 | Age implausible for class (configurable bands) | medium | "Age {n} is unusual for class {c}. Check DOB." |
| DQ-008 | Possible duplicate student (same name+DOB+parent) | high | "Possible duplicate of {student}." |
| DQ-009 | Aadhaar last 4 digits / as-printed fields missing where APAAR is needed, for a student without a verified APAAR ID | medium | "Aadhaar details needed to generate APAAR." |
| DQ-010 | Board registration value differs from register | high | "Board record differs from admission register." |
| DQ-011 | UDISE+ value differs from register | medium | "UDISE+ differs from admission register." |
| DQ-012 | Enrolment gaps (student active in two sections/years) | high | "Student is enrolled twice." |
| DQ-021 | APAAR ID not 12 digits, or one APAAR ID on two students of the school (FR-DQ-021; the second case reads "Same APAAR ID as {student}. One of them is wrong.") | blocker | "The APAAR ID is not 12 digits." |
| DQ-022 | UDISE+ name, date of birth or gender differs from Aadhaar-as-printed, for a student without a verified APAAR ID (FR-DQ-022) | high | "UDISE+ details differ from Aadhaar. APAAR generation will fail until these match." |
| DQ-030 | Office records (source `manual_entry`, including a refresh from the school's current ERP) differ from the register for name, date of birth, gender or a parent's name (FR-DQ-033, ADR-0041) | medium | "Office records (or your other school system) differ from the admission register." |
| DQ-031 | Readiness profiles only (`bseap-ssc-2027`, `apaar`): name, date of birth, gender or a parent's name differs exactly (letter for letter, spaces count) between the register, Aadhaar-as-printed, UDISE+ and the board record; each difference names who fixes it, which sets its severity and text (FR-DQ-040..046, ADR-0040) | blocker | "Records must match exactly for {profile}." |

Every finding stores: rule, severity, attribute, sources compared, masked values, match class, explanation (EN; TE deferred per ADR-0036), suggested route, status (`open`, `resolved`, `waived`, `reopened`), resolver, timestamps.

**Suggested routes** (text shown to users): "Correct the school record (change request + evidence)", "Parent should correct Aadhaar with UIDAI", "Update UDISE+ after correcting the school record", "Raise a correction request on the board portal", "Check the APAAR ID on the UDISE+ portal or the APAAR card, then record and verify the right one", "Correct the value in your other school system (ERP) to match the admission register, then refresh from its export".

## 6. Name-matching specification (AP naming conventions)

Names in AP commonly include a surname/house name (often first), initials, and multi-part given names (e.g., "K. VENKATA SAI", "KOMMINENI VENKATASAI", "VENKATA SAI KOMMINENI").

**Normalization pipeline**
1. Unicode NFC; trim; collapse whitespace; uppercase Latin script.
2. Tokenize on spaces, dots, hyphens; mark single letters (with or without dot) as initials.
3. If Telugu script: transliterate to Latin (ISO 15919-based scheme) to produce a comparison key; keep original. **Decision (M1):** the scheme is an in-house, table-driven transliteration in `app.core.textnorm` rather than a library: the Telugu block is small and fixed, the key is for matching only (never displayed), and it adds no runtime dependency; it is property-tested (never fails, removes all Telugu letters). For matching, vowel length is folded before transliteration (ఈ/ీ, ఏ/ే, ఊ/ూ, ఓ/ో, ఆ/ా become short; vocalic r becomes "ri") because Latin school spellings write short vowels ("KOMMINENI" for కొమ్మినేని).
4. Apply a configurable variant dictionary (e.g., SRI/SREE/SHRI, LAKSHMI/LAXMI, VENKATA/VENKAT) and a light phonetic key (double letters, TH/T, DH/D, V/W, EE/I). The dictionary is per-tenant extensible.

**Match classes** (evaluated in order)

| Class | Condition | Default severity |
|---|---|---|
| `EXACT` | Normalized strings equal | none |
| `ORDER` | Same token multiset, different order | info |
| `SPACING` | Equal after removing spaces ("VENKATASAI" vs "VENKATA SAI") | low |
| `INITIALS` | One side has initial(s) compatible with the other's full tokens | medium |
| `VARIANT` | Equal after variant dictionary / phonetic key | medium |
| `TYPO` | Similarity ≥ threshold (Jaro-Winkler and trigram; thresholds tuned on pilot data) | high |
| `DIFFERENT` | Anything else | blocker |

Each class produces an explanation code (bilingual text deferred per ADR-0036) (`NM-ORDER`, `NM-SPACING`, …). Thresholds live in config and are tuned with labelled examples from the design partner (with permission).

**Algorithm as implemented (M1, `app.dq.matching.classify`)**
- Tokens: split on spaces, dots, hyphens, underscores, commas and slashes. A single letter is an initial; a two-letter digraph from config (`CH. SH. TH. KH. GH. BH. PH. DH.`) or a single Telugu syllable (`కె.`) is an initial only when followed by a dot. Zero-width characters are dropped. Empty or punctuation-only input gives a separate class `MISSING` (`NM-MISSING`, no finding: missing values are reported by DQ-005/DQ-009).
- Across scripts (exactly one side contains Telugu), every comparison uses the phonetic key, so a transliteration difference alone is `EXACT`.
- `EXACT`, `ORDER` and `SPACING` ("equal after removing spaces") are decided on the whole token lists. Otherwise tokens are aligned: every token on each side is paired once, as equal words, adjacent words joined on one side (up to 3), an initial with a word it starts, words equal on the variant key (dictionary representative, then phonetic key), or words whose Jaro-Winkler **or** trigram similarity reaches the threshold. Each stage allows the pair kinds of the stages before it and any order, so a name with several differences gets the most severe class among them ("VENKATASAI K" vs "KOMMINENI VENKATA SAI" is `INITIALS`). From `INITIALS` on, at least one full word must pair ("K. V. S." alone is `DIFFERENT`).
- `TYPO` is decided per word, not on the whole name (whole-name scores over-rate a shared surname: "RAVI KUMAR"/"RAJU KUMAR" is 0.92 Jaro-Winkler but `DIFFERENT`). Defaults (`app/dq/config/match_classes.yaml`): Jaro-Winkler ≥ 0.92 (rapidfuzz, prefix weight 0.1) or trigram ≥ 0.80 (same formula as PostgreSQL `pg_trgm` `similarity()`); the finding reports which metric fired. Up to 10 tokens per name get the alignment; longer names are compared as whole strings only.
- The variant dictionary (`app/dq/config/variants.yaml`, 90+ groups of AP spellings) is looked up by phonetic key and is per-tenant extensible (groups sharing a spelling are joined). Pairs listed as `distinct` (gender markers such as KUMAR/KUMARI, other name forms such as KRISHNA/KRISHNAN, SRINIVASA/SRINIVASULU) are never a variant or a typo of each other.
- The result is symmetric and carries `details` with token positions, pair kinds and metrics only (no name text), safe to store with findings. Default severities per class are in `match_classes.yaml` and can be overridden per tenant; DQ-004 (parent names) clamps them to medium..high.

## 7. "Ask the school" behaviour

**Answerable question types (core):** student facts ("DOB of …", "when did … leave"), counts ("how many students in Class 9 this year"), records history ("who changed …"), document facts ("what does the fee circular say about late fee"), findings ("which Class 9 students have blocker findings").

**Always:** answer in English while `SOS_TELUGU_ENABLED` is off (ADR-0036; with it on, in the question's language: English, Telugu, or mixed); cite every factual claim; include "as of" dates for records; prefer verified answers when they exist.

**Never:** guess missing facts; reveal data outside the user's scope; give legal/medical advice; follow instructions found inside documents; perform writes.

**Refusal copy (EN):** "I couldn't find this in the school records you can access." **(TE, deferred per ADR-0036):** "మీకు అందుబాటులో ఉన్న పాఠశాల రికార్డుల్లో ఈ సమాచారం దొరకలేదు."

## 8. UX principles and requirements

- Plain words, sentence case; name things by what users do ("Check before submitting", not "Run DQ pipeline").
- Every screen works at 1366×768, keyboard-only, and on Android Chrome for lookups.
- Data entry: tab order follows paper forms; dates accept DD/MM/YYYY; instant inline validation with the fix, not just the error.
- Source chips next to values (Register · Aadhaar · UDISE+ · Board) with verification ticks.
- Errors say what happened and how to fix it; empty states say what to do next.
- Print views for every report; A4 by default; Telugu renders without clipping (when `SOS_TELUGU_ENABLED` is on, ADR-0036).
- Language toggle persists per user; numbers and dates follow Indian conventions (DD/MM/YYYY, lakh/crore where relevant).
- Accessibility target: WCAG 2.2 AA (contrast, focus visible, labels, reduced motion).
- Visual language, components and the contrast table: 17-UI Design System.

## 9. Non-goals for core

Dashboards for their own sake · parent logins and parent accounts · SchoolOS receiving or holding parents' money · SMS · marketing messages to parents · a two-way chat inbox · automated portal submission · free-form AI actions. (Billing schools for SchoolOS itself is part of C14. From R2, parents paying fees **to the school's own account**, WhatsApp messages from the school's own number and read-only parent link pages are in scope: C21, C22, C24; owner decisions D4-D7; ADR-0042, ADR-0043, ADR-0046.)

## 10. Pilot release criteria (design partner)

- M0 invariants verified (cross-tenant, authz-on-every-route, audit chain)
- Pilot-ready gate passed (14-Roadmap §3): security checklist, restore drill, DPA signed, privacy notice issued by school
- Class 9 and 11 batches loaded and verified; pre-check report used for a real CISCE registration
- Staff trained; support channel defined

## 11. Open questions for the school visit

1. Which three office tasks take the most time, by season?
2. Which portals does the office touch (UDISE+, APAAR, AP Child Info, CISCE CAREERS, RTE, scheme portals)? Which accept Excel uploads?
3. Can the office download student lists from UDISE+ or CAREERS?
4. Formats: photos of TC, bonafide, study/conduct certificates, admission register pages.
5. Which Tally version and who maintains it?
6. Minority institution status (affects RTE applicability)?
7. Who approves identity corrections today? Who signs certificates?
8. Who decides purchases, and when is the budget set?
