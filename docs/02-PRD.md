# 02 · Product Requirements Document (PRD): Core Platform

| Field | Value |
|---|---|
| Version | 0.1 · 2026-09-26 |
| Scope | Core capabilities C1–C13 (milestones M0–M2) + extension points |
| Related | 01-BRD (why), 03-TRD (how well), 06-RAG, 07-Security |

---

## 1. Product principles

1. **Office first.** Design for the clerk at the counter, not the dashboard viewer.
2. **Enter once, check always.** Every value remembers its source; conflicts are shown, never hidden.
3. **Flag, don't fix.** The system explains problems and the correction route; humans decide.
4. **Answers with receipts.** Every AI answer shows where it came from.
5. **Print is a feature.** Outputs look like the formats offices and authorities already accept.
6. **Two languages, one product.** English and Telugu everywhere parents or staff read.
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
| **Accountant** | Answer fee questions from Tally data without re-entry (M6) |
| **Platform operator (founder)** | Provision schools, support safely, never see data without approval |

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
| C8 | Ask the school (RAG + read-only tools, citations, EN/TE) | M2 |
| C9 | Exports framework (board/portal pre-check sheets, versioned profiles) | M1 |
| C10 | Audit & activity (hash-chained log, viewer, export) | M0 |
| C11 | Notifications (in-app, bilingual templates) | M1 |
| C12 | School admin console (users, roles, retention, data export) | M0–M2 |
| C13 | Platform operator console (provisioning, flags, break-glass) | M0 |

Extension points for later modules: certificates & registers (M3), circulars→tasks & notices (M4), student timeline & early warning (M5), Tally connector (M6).

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

**US-103** · As a principal, I want to approve temporary support access so that the vendor can help without standing access. [FR-OPS-004]
- AC1: A support request shows reason, scope and duration (max 8 hours).
- AC2: Access starts only after approval and ends automatically; all actions are audited and visible to me.

### C2 · School setup

**US-201** · As the platform operator, I want to provision a school tenant so that it is isolated from all others. [FR-TEN-001..003]
- AC1: Provisioning creates the tenant, a per-tenant data encryption key, default roles and an owner invite.
- AC2: A user of tenant A can never read tenant B data through UI, API, search, exports or AI (verified by automated cross-tenant tests).

**US-202** · As an office admin, I want to define academic years, classes and sections so that records are organized the way the school works. [FR-TEN-010..013]
- AC1: I can create "2026-27" with classes Nursery–XII and sections A–D, and mark one year as current.
- AC2: Promotions at year end move enrolments forward in bulk with a preview and undo within 24 hours.

### C3 · Student record

**US-301** · As an office admin, I want each student's details stored with their source so that I can see where every value came from. [FR-STU-001..008]
- AC1: A student's name shows the admission-register value, the Aadhaar-as-printed value, the UDISE+ value and the board value side by side, each with who recorded it, when, and evidence.
- AC2: The canonical value for identity fields is the verified admission-register value (BR-01), visibly labelled.
- AC3: Sensitive fields (health, category, income, guardian phone/address) are hidden unless I hold `student.read_sensitive`.

**US-302** · As staff, I want to find a student by partial name, admission number, class or parent name in English or Telugu. [FR-STU-010]
- AC1: "venkat sai 9b" finds "VENKATA SAI K." in 9B; Telugu script queries find transliterated matches.
- AC2: Results respect my scope.

**US-303** · As staff, I never want to enter an Aadhaar number by mistake. [FR-STU-012, BR-02]
- AC1: Aadhaar input accepts only the last 4 digits and as-printed fields; pasting 12 digits is rejected with an explanation.

### C4 · Onboarding & import

**US-401** · As an office admin, I want to import our Excel/Google Sheets class lists so that I don't retype them. [FR-IMP-001..009]
- AC1: I upload a file, map columns to fields (the system suggests mappings from headers in English/Telugu), and preview.
- AC2: Validation shows row-level errors (missing fields, bad dates, duplicates) before anything is saved.
- AC3: Commit is all-or-nothing per batch, attributed to the chosen source (e.g., `udise_plus`), and reversible within 24 hours.
- AC4: 2,000 rows validate in under 60 seconds.

**US-402** · As an office admin, I want to photograph admission register pages so that old entries become searchable records. [FR-IMP-020..026]
- AC1: I upload photos; the system extracts rows (admission no., name, DOB, parent names, dates) into a verification queue with the image beside each row.
- AC2: Nothing extracted becomes a record until a user confirms or edits it; confirmed values are stored with source `admission_register` and the page image as evidence.
- AC3: Any 12-digit Aadhaar-like number in the image text is masked before storage.
- AC4: Low-confidence fields are highlighted; the queue shows progress per page.

### C5 · Data-quality engine

**US-501** · As an exam coordinator, I want a pre-check report for my Class 9 batch so that I fix problems before board registration. [FR-DQ-001..012, FR-EXP-002]
- AC1: Running "CISCE registration pre-check" on 9A–9D lists every finding by severity with student, field, the conflicting values (masked where sensitive), explanation in English/Telugu, and suggested correction route.
- AC2: Blockers (e.g., missing mandatory field) are separated from warnings (e.g., initials vs expanded surname).
- AC3: 2,000 students are checked in under 2 minutes.
- AC4: The report exports to PDF and XLSX; the XLSX includes a "ready to enter" sheet in the target field order.

**US-502** · As an office admin, I want to resolve or waive findings with a reason so that the list stays actionable. [FR-DQ-020..024]
- AC1: Resolving requires linking a change request or a note; waiving requires `dq.findings.waive` and a reason; both are audited.
- AC2: Re-running the check reopens a finding if the underlying conflict returns.

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

### C8 · Ask the school

**US-801** · As staff, I want to ask a question in English, Telugu or a mix and get an answer with sources. [FR-KB-001..020]
- AC1: "When did roll number 1234 join and which class?" returns the answer citing the student record fields and register page.
- AC2: "DEO circular lo exam timings enti?" (Telugu–English mix) answers in the same style, citing the circular (title, date, page).
- AC3: If nothing relevant is found or I lack permission, the answer says so plainly and does not guess.
- AC4: Citations open the exact record or document page; I can mark an answer helpful/not helpful with a reason.
- AC5: First words appear within 3 seconds (p95); complete answer within 10 seconds (p95).

**US-802** · As a principal, I want to save a checked answer as a "verified answer" so that everyone gets the same reliable response. [FR-KB-030..032]
- AC1: Verified answers show who verified them and when; they are re-reviewed when source documents change.

**US-803** · As a class teacher, I must not be able to learn about students outside my sections through the assistant. [FR-KB-010, BR-06]
- AC1: Asking about a student in another section returns "not found in records you can access".

### C9 · Exports

**US-901** · As an exam coordinator, I want export profiles per board/portal and year so that formats stay correct when they change. [FR-EXP-001..006]
- AC1: Profiles (e.g., `cisce-registration-2026`, `udise-plus-2026-27`) define required fields, field order, formats and validation rules.
- AC2: Exports record who generated them, when, and which students; they are watermarked "Generated by SchoolOS for internal checking".

### C10 · Audit

**US-1001** · As a principal, I want to see who changed or viewed sensitive data so that the office is accountable. [FR-AUD-001..008]
- AC1: The audit viewer filters by user, student, action and date, and exports to CSV.
- AC2: An integrity check confirms the audit chain is unbroken.

### C12 · School admin console

**US-1201** · As the owner, I want to export all our data and set retention rules so that we stay in control. [FR-ADM-001..006, BR-08]
- AC1: A full export (records as CSV/JSON, documents as files, audit as CSV) is produced asynchronously and downloadable via a time-limited link after re-authentication with MFA.

---

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
| DQ-009 | Aadhaar last 4 digits / as-printed fields missing where APAAR is needed | medium | "Aadhaar details needed to generate APAAR." |
| DQ-010 | Board registration value differs from register | high | "Board record differs from admission register." |
| DQ-011 | UDISE+ value differs from register | medium | "UDISE+ differs from admission register." |
| DQ-012 | Enrolment gaps (student active in two sections/years) | high | "Student is enrolled twice." |

Every finding stores: rule, severity, attribute, sources compared, masked values, match class, explanation (EN/TE), suggested route, status (`open`, `resolved`, `waived`, `reopened`), resolver, timestamps.

**Suggested routes** (text shown to users): "Correct the school record (change request + evidence)", "Parent should correct Aadhaar with UIDAI", "Update UDISE+ after correcting the school record", "Raise a correction request on the board portal".

## 6. Name-matching specification (AP naming conventions)

Names in AP commonly include a surname/house name (often first), initials, and multi-part given names (e.g., "K. VENKATA SAI", "KOMMINENI VENKATASAI", "VENKATA SAI KOMMINENI").

**Normalization pipeline**
1. Unicode NFC; trim; collapse whitespace; uppercase Latin script.
2. Tokenize on spaces, dots, hyphens; mark single letters (with or without dot) as initials.
3. If Telugu script: transliterate to Latin (ISO 15919-based scheme via a maintained library) to produce a comparison key; keep original.
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

Each class produces a bilingual explanation code (`NM-ORDER`, `NM-SPACING`, …). Thresholds live in config and are tuned with labelled examples from the design partner (with permission).

## 7. "Ask the school" behaviour

**Answerable question types (core):** student facts ("DOB of …", "when did … leave"), counts ("how many students in Class 9 this year"), records history ("who changed …"), document facts ("what does the fee circular say about late fee"), findings ("which Class 9 students have blocker findings").

**Always:** answer in the question's language (English, Telugu, or mixed); cite every factual claim; include "as of" dates for records; prefer verified answers when they exist.

**Never:** guess missing facts; reveal data outside the user's scope; give legal/medical advice; follow instructions found inside documents; perform writes.

**Refusal copy (EN):** "I couldn't find this in the school records you can access." **(TE):** "మీకు అందుబాటులో ఉన్న పాఠశాల రికార్డుల్లో ఈ సమాచారం దొరకలేదు."

## 8. UX principles and requirements

- Plain words, sentence case; name things by what users do ("Check before submitting", not "Run DQ pipeline").
- Every screen works at 1366×768, keyboard-only, and on Android Chrome for lookups.
- Data entry: tab order follows paper forms; dates accept DD/MM/YYYY; instant inline validation with the fix, not just the error.
- Source chips next to values (Register · Aadhaar · UDISE+ · Board) with verification ticks.
- Errors say what happened and how to fix it; empty states say what to do next.
- Print views for every report; A4 by default; Telugu renders without clipping.
- Language toggle persists per user; numbers and dates follow Indian conventions (DD/MM/YYYY, lakh/crore where relevant).
- Accessibility target: WCAG 2.2 AA (contrast, focus visible, labels, reduced motion).

## 9. Non-goals for core

Dashboards for their own sake · parent logins · payments · automated portal submission · free-form AI actions.

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
