# 02 · Product Requirements Document (PRD): Core Platform

| Field | Value |
|---|---|
| Version | 0.4 · 2026-09-29 |
| Scope | Core capabilities C1–C14 (milestones M0–M2) + extension points |
| Related | 01-BRD (why), 03-TRD (how well), 06-RAG, 07-Security, 16-Platform admin panel |
| Changes | 0.4: C16 circulars, tasks and parent notices (M4) with US-1601..US-1606, proposed from the roadmap scope for the product owner to confirm. 0.3: US-1305 invoice number example uses the implemented 16-character format. 0.2: C14 platform admin panel (M0) with US-1301..US-1310; C13 folded into C14; C12 "Plan & billing" page (US-1204); US-202 uses `tenant.structure.manage`; promotions moved to M1. 0.1: baseline |

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
| C8 | Ask the school (RAG + read-only tools, citations, EN/TE) | M2 |
| C9 | Exports framework (board/portal pre-check sheets, versioned profiles) | M1 |
| C10 | Audit & activity (hash-chained log, viewer, export) | M0 |
| C11 | Notifications (in-app, bilingual templates) | M1 |
| C12 | School admin console (users, roles, retention, data export, plan & billing) | M0–M2 |
| C13 | Platform operator console: folded into C14 (ID kept for traceability; tenant-side break-glass stays under FR-OPS-004) | — |
| C14 | Platform admin panel / control plane: schools, provisioning (shared and dedicated tiers), plans, subscriptions, invoices, usage, flags, fleet, announcements, support, operators, platform audit (spec in 16) | M0 |
| C16 | Circulars → tasks, reminders and bilingual parent notices (AI suggestions confirmed by staff) | M4 |

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
- AC5: Before commit I can see the uploaded file as a sheet (every column with the field it fills, each row with its check result) and correct cells in place; a checked file re-checks the row at once, the uploaded file itself is kept as it was, every edit is recorded with who and when, and the commit adds the edited values. Restricted (C3) columns are never shown or edited there, a full Aadhaar number is refused, and nothing can be edited once the rows were added (corrections then go through the student profile or a change request). *(Written from the implementation; to be confirmed by the product owner.)*
- AC6: I can download the sheet with my edits as CSV or XLSX (one header row, the data rows) after confirming it's me (MFA within 5 minutes). Aadhaar-like numbers are masked, formulas are neutralised, restricted columns are empty unless I may see sensitive data, and every download is audited. *(Written from the implementation; to be confirmed by the product owner.)*

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
- AC5: An XLSX or CSV document opens as a table (first worksheet, row 1 as column names) with Aadhaar-like numbers masked and formulas shown as text. If I may upload, I can correct cells of a single-sheet XLSX without formulas and save them as the next version (history kept, scanned and indexed like an upload), and I can download the sheet, with unsaved edits, as CSV or XLSX; personal (C2) or restricted (C3) sheets ask me to confirm it's me first. Files uploaded for an import open from the import instead. *(Written from the implementation; to be confirmed by the product owner.)*

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

**US-1204** · As the owner, principal or accountant, I want to see our plan, usage and invoices so that we know what we pay for and what is due. [FR-PLT-030]
- AC1: Given I hold `tenant.billing.read`, when I open "Plan & billing", then I see the current plan, subscription status, billing period, trial end (if any), usage against plan limits, and a list of invoices with number, period, total, status and amount due.
- AC2: Given I lack `tenant.billing.read`, the menu item is hidden and the API returns 403.
- AC3: The page shows only my school's records (data comes from `core.current_subscription()`), never another school's.
- AC4: Given an invoice is past due, a banner explains the amount, due date and how to pay, in English and Telugu.

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
- AC3: Given either the English or the Telugu text is empty, then the announcement cannot be saved.

**US-1308** · As a platform engineer, I want to see fleet health so that dedicated hosts stay healthy, backed up and up to date. [FR-PLT-023..025]
- AC1: Given a dedicated host sends a correctly signed heartbeat, then its last-heartbeat time, version and health update.
- AC2: Given a heartbeat with a bad signature, a timestamp more than 5 minutes off, a replayed nonce or an unknown field, then it is rejected and nothing is stored.
- AC3: Given no valid heartbeat for 20 minutes, then the deployment shows "unreachable" and the on-call operator is alerted.
- AC4: Heartbeats never contain personal data.

**US-1309** · As school staff and as a support agent, I want support tickets with clear statuses and response times so that problems are handled without sharing student data. [FR-PLT-027]
- AC1: When a staff member opens a ticket, then the form warns (EN/TE) not to include student names, dates of birth, Aadhaar or phone numbers, and such numbers are masked before storage.
- AC2: Each ticket shows its SLA timers by priority; breaches appear on the dashboard.
- AC3: Given a ticket was closed a year ago, then it has been deleted.

**US-1310** · As a platform owner, I want to manage operators and read a tamper-evident platform audit log so that every control-plane action is accountable. [FR-PLT-028, FR-PLT-029]
- AC1: Given I hold `platform.operators.manage` and completed step-up, I can invite, assign roles to and deactivate operators; I cannot change my own roles; at least one active platform owner always remains.
- AC2: Given a new operator has not enrolled MFA, then they cannot use the panel.
- AC3: Every control-plane change writes exactly one platform audit event in the same transaction; "Verify chain" reports the first broken or missing sequence number, if any.

### C16 · Circulars, tasks and parent notices (M4)

*Proposed from the roadmap scope (14 · M4); PO to confirm.* The AI reads a circular and **suggests**; a person confirms before anything is created (invariant 9). Parent notices never carry student personal data (08 §4). Exit metric (14 · M4): at least 90% of a term's circulars processed with their deadlines captured.

**US-1601** · As an office admin, I want every circular I upload to be read for its issuer, reference number, date, subject, a short summary in English and Telugu and its deadlines, so that nothing in it is missed. [FR-CIR-001..003, FR-CIR-005..007] *(Proposed from the roadmap scope; PO to confirm.)*
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
- AC1: Given a task due in the configured number of days (default 2), then I get one in-app reminder (English or Telugu, my choice); given it is overdue, one overdue reminder; no duplicates when the job runs again.
- AC2: Done or cancelled tasks get no reminders. (Email reminders wait for general staff email templates; in-app only for now.)

**US-1605** · As an office admin, I want a short parent notice in English and Telugu drafted from a circular (or from my own text), which I can edit, so that I can post it in the existing parents' groups quickly. [FR-NOTICE-001..004, FR-NOTICE-007] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: The draft is made only from the circular's text (and deadlines I confirmed); no student records are ever sent to the AI, and the draft is marked "AI draft, check before use".
- AC2: A circular marked personal (C2) or restricted (C3) cannot be used for a notice; free text with phone numbers, email addresses or Aadhaar-like numbers is refused with a message saying what to remove.
- AC3: If AI is unavailable, an empty draft opens so I can write the notice myself.

**US-1606** · As a principal, I want to approve a notice and then copy its text or download it as a printable A4 page or an image. [FR-NOTICE-005, FR-NOTICE-006] *(Proposed from the roadmap scope; PO to confirm.)*
- AC1: Only holders of `notice.approve` can approve; both languages must be filled; an approved notice cannot be edited.
- AC2: After approval the A4 PDF and a PNG image are rendered (Telugu without clipped glyphs); download links last at most 5 minutes and every download is audited; the plain text can be copied for WhatsApp-style groups.
- AC3: SchoolOS never sends the notice to parents itself (no parent logins or messaging in core, §9).

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

Each class produces a bilingual explanation code (`NM-ORDER`, `NM-SPACING`, …). Thresholds live in config and are tuned with labelled examples from the design partner (with permission).

**Algorithm as implemented (M1, `app.dq.matching.classify`)**
- Tokens: split on spaces, dots, hyphens, underscores, commas and slashes. A single letter is an initial; a two-letter digraph from config (`CH. SH. TH. KH. GH. BH. PH. DH.`) or a single Telugu syllable (`కె.`) is an initial only when followed by a dot. Zero-width characters are dropped. Empty or punctuation-only input gives a separate class `MISSING` (`NM-MISSING`, no finding: missing values are reported by DQ-005/DQ-009).
- Across scripts (exactly one side contains Telugu), every comparison uses the phonetic key, so a transliteration difference alone is `EXACT`.
- `EXACT`, `ORDER` and `SPACING` ("equal after removing spaces") are decided on the whole token lists. Otherwise tokens are aligned: every token on each side is paired once, as equal words, adjacent words joined on one side (up to 3), an initial with a word it starts, words equal on the variant key (dictionary representative, then phonetic key), or words whose Jaro-Winkler **or** trigram similarity reaches the threshold. Each stage allows the pair kinds of the stages before it and any order, so a name with several differences gets the most severe class among them ("VENKATASAI K" vs "KOMMINENI VENKATA SAI" is `INITIALS`). From `INITIALS` on, at least one full word must pair ("K. V. S." alone is `DIFFERENT`).
- `TYPO` is decided per word, not on the whole name (whole-name scores over-rate a shared surname: "RAVI KUMAR"/"RAJU KUMAR" is 0.92 Jaro-Winkler but `DIFFERENT`). Defaults (`app/dq/config/match_classes.yaml`): Jaro-Winkler ≥ 0.92 (rapidfuzz, prefix weight 0.1) or trigram ≥ 0.80 (same formula as PostgreSQL `pg_trgm` `similarity()`); the finding reports which metric fired. Up to 10 tokens per name get the alignment; longer names are compared as whole strings only.
- The variant dictionary (`app/dq/config/variants.yaml`, 90+ groups of AP spellings) is looked up by phonetic key and is per-tenant extensible (groups sharing a spelling are joined). Pairs listed as `distinct` (gender markers such as KUMAR/KUMARI, other name forms such as KRISHNA/KRISHNAN, SRINIVASA/SRINIVASULU) are never a variant or a typo of each other.
- The result is symmetric and carries `details` with token positions, pair kinds and metrics only (no name text), safe to store with findings. Default severities per class are in `match_classes.yaml` and can be overridden per tenant; DQ-004 (parent names) clamps them to medium..high.

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
- Visual language, components and the contrast table: 17-UI Design System.

## 9. Non-goals for core

Dashboards for their own sake · parent logins · parent fee payments · automated portal submission · free-form AI actions. (Billing schools for SchoolOS itself is part of C14.)

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
