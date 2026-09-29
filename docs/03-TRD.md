# 03 · Technical Requirements Document (TRD)

| Field | Value |
|---|---|
| Version | 0.4 · 2026-09-29 |
| Scope | Core platform (M0–M2) + platform admin panel (C14) + interfaces for M3–M6 |
| Related | 02-PRD (stories), 04-Architecture, 05-Data model, 06-RAG, 07-Security, 16-Platform admin panel |
| Changes | 0.4: §3.14 circulars, tasks and parent notices (FR-CIR-001..008, FR-TASK-001..008, FR-NOTICE-001..008; M4), proposed from the roadmap scope for the product owner to confirm; traceability row for BRD P4. 0.3: FR-IAM-013 (school picker, `/me/schools`, invitation acceptance) and FR-TEN-003 (keys, roles, owner invite and acceptance, ADR-0019) restated as built; FR-PLT-016 number format; M0 implementation status (§3.13). 0.2: FR-PLT-001..030 (§3.12); FR-IAM-010 role keys and platform roles; FR-TEN-010 permission; FR-TEN-011 moved to M1; FR-OPS-001 superseded; dedicated-tier NFRs (NFR-AVL-005, NFR-FLT-001..002); Valkey; interfaces and traceability updated. 0.1: baseline |

Normative keywords: **MUST / SHOULD / MAY** (RFC 2119). Every requirement has an ID and a verification method: **T** test · **I** inspection · **D** demonstration · **A** analysis.

---

## 1. System summary

A multi-tenant web application (Next.js BFF + FastAPI API + Celery workers) on AWS ap-south-1 with PostgreSQL (pgvector) as the system of record and S3 for files. An LLM gateway calls Anthropic Claude via commercial API; an embeddings provider interface serves retrieval. It is offered as a managed SaaS in two tiers from one codebase: the **shared tier** (pooled platform) and the **dedicated tier** (one isolated host per school). A control plane (platform admin panel, billing, fleet) runs only in the shared deployment (ADR-0015, ADR-0017). See 04-Architecture §16 and 16-Platform admin panel.

## 2. Technology standards

| Area | Standard |
|---|---|
| Languages | Python 3.12+, TypeScript (strict) |
| API | REST/JSON over HTTPS, OpenAPI 3.1, RFC 9457 errors |
| DB | PostgreSQL 16+, extensions: pgvector, pg_trgm, citext, pgcrypto |
| IDs | UUIDv7 (time-ordered) for all entities |
| Time | `timestamptz` in UTC; display in IST (Asia/Kolkata) |
| Text | UTF-8, Unicode NFC normalized on write |
| Auth | OIDC (Authorization Code + PKCE) via BFF; short-lived access tokens |
| Infra | Terraform; containers (OCI images); GitHub Actions |
| Telemetry | OpenTelemetry (traces, metrics, logs) |

---

## 3. Functional requirements

### 3.1 Identity & access (FR-IAM)

| ID | Requirement | V |
|---|---|---|
| FR-IAM-001 | Users MUST authenticate via the configured OIDC provider using Authorization Code + PKCE through the BFF. | T |
| FR-IAM-002 | MFA MUST be enforced for roles `owner`, `principal`, `office_admin` and for every platform operator (all platform roles). The API enforces it on every request (reference: `sos:mfa` claim; `403 mfa_required`; ADR-0018). | T |
| FR-IAM-003 | Sessions MUST expire after 15 min idle (configurable 5–30) and 12 h absolute; re-auth required for sensitive actions (exports, role changes, approvals). | T |
| FR-IAM-004 | Access tokens MUST live ≤ 10 min; refresh tokens MUST rotate on use with reuse detection revoking the session family. | T |
| FR-IAM-005 | Login attempts MUST be rate-limited per account and per IP; lockouts and resets audited. | T |
| FR-IAM-006 | Users MUST be able to see and revoke their active sessions. | D |
| FR-IAM-010 | The system MUST support these tenant role keys exactly: `owner`, `principal`, `office_admin`, `office_staff`, `accountant`, `exam_coordinator`, `class_teacher`, `teacher`, `auditor_readonly`. Platform roles (`platform_owner`, `platform_engineer`, `support_agent`, `billing_admin`, `platform_viewer`) are a separate set for SchoolOS staff (FR-PLT-028) and MUST NOT appear as tenant roles; `platform_support` is the tenant-side temporary break-glass role (FR-OPS-004). | T |
| FR-IAM-011 | Permissions MUST be `resource.action` strings; roles map to permission sets; tenants MAY clone and customize roles. | T |
| FR-IAM-012 | Memberships MUST support scopes: `school`, `classes[]`, `sections[]`; scope applies to every read path including search, exports and AI. | T |
| FR-IAM-013 | A user MAY hold memberships in multiple tenants; the active tenant is explicit per session. Before a school is chosen, the user MUST be able to list the schools they can work in (`GET /api/v1/me/schools`, school picker) and accept their own pending invitations (`POST /api/v1/me/accept-invitations`, ADR-0019); these and `POST /me/active-tenant` and `POST /me/login-event` are the only routes that work without an active school. The BFF sends the chosen school as `X-Active-Tenant`; several memberships and no choice → `409 active_tenant_required`; a school the user does not belong to → `403 no_membership`. | T |
| FR-IAM-014 | Role/permission/scope changes MUST take effect within 60 s and be audited. | T |

### 3.2 Tenancy & school setup (FR-TEN)

| ID | Requirement | V |
|---|---|---|
| FR-TEN-001 | Each school MUST be a tenant with a UUID, and every tenant-owned row MUST carry `tenant_id`. | I/T |
| FR-TEN-002 | PostgreSQL RLS MUST enforce tenant isolation for every tenant-owned table (ENABLE + FORCE). | T |
| FR-TEN-003 | Tenant provisioning MUST create default roles (cloned from the system role templates), a per-tenant data encryption key and HMAC key (wrapped by KMS), and an owner invite (`core.create_owner_invite`: an `invited` owner membership with MFA required, only while the school is `provisioning` and has no members). A school MUST NOT go live without an unretired data key (`core.set_tenant_status` refuses `provisioning → active`). Invitees accept on first sign-in within 30 days of the invite, once the school is active (ADR-0019). | T |
| FR-TEN-010 | Academic years, classes, sections and enrolments MUST be modelled; exactly one current year per tenant. Writes to years, classes and sections require `tenant.structure.manage` (owner, principal, office_admin; no step-up). | T |
| FR-TEN-011 | **(M1)** Bulk promotion MUST offer preview, commit, and undo within 24 h. | T |
| FR-TEN-012 | Tenant settings MUST include languages, date format, retention policy, AI features on/off, monthly AI budget. | T |
| FR-TEN-013 | Multi-campus groups MAY be supported later via a `group_id` without schema breakage. | A |

### 3.3 Student record (FR-STU)

| ID | Requirement | V |
|---|---|---|
| FR-STU-001 | Students MUST have a stable internal ID independent of admission number. | T |
| FR-STU-002 | Attribute values MUST be stored per source (`admission_register`, `aadhaar_as_printed`, `udise_plus`, `board_registration`, `birth_certificate`, `parent_form`, `tc_incoming`, `manual_entry`). | T |
| FR-STU-003 | Each value MUST record: recorded_by, recorded_at, evidence document (optional), verification status, verifier, verified_at, and supersession link. | T |
| FR-STU-004 | Canonical values MUST be derived by attribute policy; identity attributes use the verified admission-register value (BR-01). | T |
| FR-STU-005 | History MUST be immutable: updates create new value rows; no in-place overwrite of identity values. | T |
| FR-STU-006 | Attributes MUST be defined in `attribute_definitions` (type, classification, identity flag, validation, export mappings) so new fields need no schema change. | I |
| FR-STU-007 | C3 (restricted) values MUST be encrypted at the application layer with the tenant DEK. | T |
| FR-STU-008 | Guardians MUST be modelled separately and linked to students with relationship type. | T |
| FR-STU-010 | Search MUST support partial names, transliteration (Telugu↔Latin), admission number, class/section, and parent name, filtered by scope. | T |
| FR-STU-011 | Search results for 2,000-student tenants MUST return in ≤ 300 ms p95. | T |
| FR-STU-012 | The system MUST reject input of full Aadhaar numbers in any field and store only `aadhaar_last4` plus as-printed fields. | T |

### 3.4 Import & onboarding (FR-IMP)

| ID | Requirement | V |
|---|---|---|
| FR-IMP-001 | Accept XLSX, CSV and Google Sheets exports (uploaded file) up to 10 MB. | T |
| FR-IMP-002 | Suggest column mappings from English/Telugu headers; mappings are saved as reusable templates per tenant. | T |
| FR-IMP-003 | Validate every row (types, dates, required fields, duplicates, scope) and show row-level errors before commit. | T |
| FR-IMP-004 | Commit MUST be atomic per batch and attribute values to the chosen source. | T |
| FR-IMP-005 | Batches MUST be reversible within 24 h if no dependent changes exist. | T |
| FR-IMP-006 | 2,000-row files MUST validate in ≤ 60 s. | T |
| FR-IMP-007 | Imported raw files MUST be retained per retention policy and then deleted. | T |
| FR-IMP-008 | Before commit the uploaded file MUST be viewable as a sheet (columns with their mapped field, rows in file order with their check result) and its cells editable with optimistic concurrency (`If-Match`). The raw file is never changed: edits are stored as an append-only, encrypted history (who, when, cell) applied whenever the file is read, a checked batch re-checks after each edit, and commit adds the edited values. Restricted (C3) columns (mapped to, or suggested for, a C3 field) are never shown or edited; full Aadhaar numbers are refused and never stored; no edits once the batch is committed, reverted or busy. *(As built; PO to confirm.)* | T |
| FR-IMP-009 | The staged sheet with its edits MUST be downloadable as CSV (UTF-8 with BOM) or XLSX (text cells, watermark), with Aadhaar-like numbers masked, formula injection neutralised (SEC-017), restricted columns empty without `student.read_sensitive`, step-up re-authentication (FR-EXP-004) and an audit event (counts only). *(As built; PO to confirm.)* | T |
| FR-IMP-020 | Register-page photos (JPG/PNG/PDF) MUST go through extraction into a verification queue; nothing becomes a record without human confirmation. | T |
| FR-IMP-021 | Extraction MUST output per-field confidence and bounding regions where available. | T |
| FR-IMP-022 | Aadhaar-like 12-digit numbers (Verhoeff-valid) MUST be masked in extracted text before storage. | T |
| FR-IMP-023 | Confirmed values MUST link the page image as evidence. | T |
| FR-IMP-024 | Extraction providers MUST be pluggable (interface) and selected by evaluation. | I |

### 3.5 Data quality (FR-DQ)

| ID | Requirement | V |
|---|---|---|
| FR-DQ-001 | Rules MUST be declarative (registry with ID, version, severity, scope, explanation templates EN/TE). | I |
| FR-DQ-002 | The engine MUST run on demand (per class/section/batch/export profile) and incrementally after writes. | T |
| FR-DQ-003 | Name comparison MUST implement the match classes in PRD §6 with configurable thresholds and variant dictionary. | T |
| FR-DQ-004 | Findings MUST be idempotent per (student, rule, attribute, sources) and reopen when conflicts reappear. | T |
| FR-DQ-005 | 2,000 students MUST be checked in ≤ 2 min. | T |
| FR-DQ-006 | Findings MUST include suggested correction route and masked values for C3 attributes. | T |
| FR-DQ-020 | Resolve requires a linked change request or note; waive requires `dq.findings.waive` and a reason. | T |

### 3.6 Change requests (FR-CR)

| ID | Requirement | V |
|---|---|---|
| FR-CR-001 | Identity-attribute changes MUST be submitted as change requests with old/new value, reason and evidence. | T |
| FR-CR-002 | The approver MUST differ from the requester; approval requires `student.identity_change.approve` and recent MFA. | T |
| FR-CR-003 | Approval MUST atomically write the new verified value, supersede the old one, re-run affected DQ rules, and audit. | T |
| FR-CR-004 | Rejection MUST require a reason; requests expire after 30 days (configurable). | T |
| FR-CR-005 | A printable correction memo MUST be generated for the paper register. | D |

### 3.7 Documents (FR-DOC)

| ID | Requirement | V |
|---|---|---|
| FR-DOC-001 | Accept PDF, JPG, PNG, DOCX, XLSX ≤ 25 MB (configurable); type allowlist by magic bytes, not extension. | T |
| FR-DOC-002 | Files MUST be malware-scanned before processing; infected files quarantined and audited. | T |
| FR-DOC-003 | Originals MUST be stored in S3 under a tenant prefix with SSE-KMS; never publicly accessible. | I/T |
| FR-DOC-004 | Downloads MUST use presigned URLs valid ≤ 5 min with `Content-Disposition: attachment`. | T |
| FR-DOC-005 | Documents MUST carry metadata: type, title, date, issuer, academic year, language, sensitivity, visibility (roles/scopes). | T |
| FR-DOC-006 | Versioning MUST keep history; latest version is default for retrieval. | T |
| FR-DOC-007 | Deleting a document MUST remove its chunks and embeddings from retrieval within 5 min and purge storage per retention. | T |
| FR-DOC-008 | Processing status MUST be tracked (queued, scanning, extracting, chunking, embedding, ready, failed) with retry. | T |
| FR-DOC-009 | XLSX/CSV documents MUST open as a paged table (first worksheet of the newest scanned version, row 1 as column names) without evaluating formulas, with Aadhaar-like numbers masked, the same visibility and C3 rules as downloads, and a size limit (larger files are downloaded instead). Import files open from their import (FR-IMP-008). *(As built; PO to confirm.)* | T |
| FR-DOC-010 | Holders of `document.upload` MUST be able to save edited cells of a single-sheet XLSX without formulas as the next version (`If-Match`, base version must be current; values only; history kept; scanned and indexed like an upload); full Aadhaar numbers refused; audit with cell references, never values. *(As built; PO to confirm.)* | T |
| FR-DOC-011 | A document sheet, with unsaved edits, MUST be downloadable as CSV or XLSX with masking, formula neutralisation (SEC-017), watermark (FR-EXP-003) and an audit event; personal (C2) and restricted (C3) documents need step-up (FR-EXP-004). *(As built; PO to confirm.)* | T |

### 3.8 Knowledge & Ask (FR-KB) (details in 06-RAG)

| ID | Requirement | V |
|---|---|---|
| FR-KB-001 | Hybrid retrieval: vector similarity + full-text + trigram, fused with Reciprocal Rank Fusion. | T |
| FR-KB-002 | Retrieval MUST filter by tenant, document visibility and user scope **in SQL** before ranking. | T |
| FR-KB-003 | A router MUST choose between structured tools, document search, both, or refusal. | T |
| FR-KB-004 | Structured questions MUST be answered via whitelisted read-only tools, not free-form SQL generation. | I/T |
| FR-KB-005 | Answers MUST cite sources using search-result content blocks; server MUST validate every citation maps to retrieved content. | T |
| FR-KB-006 | Answers MUST be in the language of the question (EN/TE/mixed). | T |
| FR-KB-007 | When evidence is insufficient or forbidden, the answer MUST say so without speculation. | T |
| FR-KB-008 | Responses MUST stream (SSE); first token ≤ 3 s p95; completion ≤ 10 s p95. | T |
| FR-KB-009 | Every query MUST log: user, tenant, question hash + encrypted text, tools used, chunk IDs, model, tokens, latency, citations, feedback. | T |
| FR-KB-010 | Scope leakage tests (cross-section, cross-tenant) MUST be part of the eval gate with zero tolerance. | T |
| FR-KB-011 | Per-tenant monthly AI budgets MUST be enforced with graceful degradation (search-only mode). | T |
| FR-KB-012 | Conversation context MUST be session-scoped; no cross-user memory. | T |
| FR-KB-030 | Authorized users MAY promote answers to verified answers; verified answers are flagged for review when cited sources change. | T |

### 3.9 Exports (FR-EXP)

| ID | Requirement | V |
|---|---|---|
| FR-EXP-001 | Export profiles MUST be versioned configs (required fields, order, formats, rules). | I |
| FR-EXP-002 | Pre-check reports MUST be produced as PDF and XLSX, EN/TE. | D |
| FR-EXP-003 | Exports MUST be audited (who, when, which students, profile version) and watermarked. | T |
| FR-EXP-004 | Bulk exports of personal data MUST require re-authentication. | T |

### 3.10 Audit (FR-AUD)

| ID | Requirement | V |
|---|---|---|
| FR-AUD-001 | Audit events MUST be written in the same DB transaction as the audited change. | T |
| FR-AUD-002 | The audit table MUST be append-only (no UPDATE/DELETE grants; trigger blocks modification). | T |
| FR-AUD-003 | Each event MUST include prev_hash and hash (SHA-256 over canonical JSON) forming a per-tenant chain. | T |
| FR-AUD-004 | A daily job MUST verify chains and export signed daily batches to S3 with Object Lock. | T |
| FR-AUD-005 | Audit viewer with filters and CSV export for `audit.read` holders. | D |

### 3.11 Notifications, admin, ops

| ID | Requirement | V |
|---|---|---|
| FR-NOT-001 | In-app notifications for approvals, findings, processing results; templates in EN/TE. | T |
| FR-ADM-001 | Full tenant data export (records CSV/JSON, files, audit CSV), async, MFA-gated, link valid 24 h. | T |
| FR-ADM-002 | Retention settings per data category within legal bounds. | T |
| FR-OPS-001 | *Superseded by FR-PLT-001..029 (platform admin panel, §3.12).* | — |
| FR-OPS-004 | Break-glass: school-approved, time-bound (≤ 8 h), scope-limited, fully audited, visible to the school. | T |

### 3.12 Platform admin panel / control plane (FR-PLT) (details in 16)

All FR-PLT routes are under `/api/v1/platform/*`, use `require_platform()` and the `sos_platform` database role (ADR-0013, ADR-0017), and exist only when `SOS_DEPLOYMENT_MODE=shared`. ᴿ = step-up MFA within 5 minutes.

| ID | Requirement | V |
|---|---|---|
| FR-PLT-001 | Operators with `platform.tenants.read` MUST be able to list and view schools (plan, subscription and tenant status, tier, deployment, usage, invoices, flags, tickets) without any access to student data. | T |
| FR-PLT-002 | Provisioning a shared-tier school (`platform.tenants.provision` ᴿ) MUST, atomically per step and idempotently, as a resumable provisioning whose incomplete state is visible and blocks go-live (ADR-0024), create the tenant, wrapped DEK and HMAC key, audit chain head, system roles, owner invite, billing account, subscription and deployment record, via `core.provision_tenant()` and `core.create_user_for_invite()`. | T |
| FR-PLT-003 | Provisioning a dedicated-tier school MUST create the deployment record (`provisioning`), subscription, billing account and a per-deployment heartbeat key (shown once); the tenant row is created on the host by the runbook with the same tenant ID. | T/D |
| FR-PLT-004 | Suspend and reactivate (`platform.tenants.suspend` ᴿ) MUST require a reason, use `core.set_tenant_status()`, keep the owner's access to full export and Plan & billing while suspended, and delete nothing. | T |
| FR-PLT-005 | Offboarding (`platform.tenants.offboard` ᴿ) MUST need two different operators (request + approve), then delete tenant data within 30 days, destroy keys (crypto-shredding) and record a certificate of deletion. | T |
| FR-PLT-010 | Plans MUST be versioned (code + version), priced in INR `numeric(14,2)` with GST rate and SAC code, and immutable once published (`platform.plans.manage` ᴿ). | T |
| FR-PLT-011 | Subscriptions MUST follow the states `trial`, `active`, `past_due`, `suspended`, `cancelled` and the transitions in 16 §9; at most one non-cancelled subscription per school. | T |
| FR-PLT-012 | Trials MUST have an end date; activation and trial extension need `platform.subscriptions.manage` ᴿ; nothing happens automatically when a trial ends. | T |
| FR-PLT-013 | Plan changes MUST take effect at the next period start (no proration in M0); negotiated prices MUST record a reason. | T |
| FR-PLT-014 | A subscription MUST become `past_due` automatically when an issued invoice is unpaid after its due date; suspension MUST never be automatic: only `platform.subscriptions.manage` ᴿ, after a 15-day grace period, with a reason, and inside a protected board-exam window only with a `platform_owner` approval. | T |
| FR-PLT-015 | A monthly job MUST generate draft invoices for billable subscriptions, idempotently (one live invoice per subscription and period). | T |
| FR-PLT-016 | Issuing MUST assign a gapless sequential number per Indian financial year of at most 16 characters (`SOS/26-27/000123`, CGST Rule 46) and freeze the invoice; drafts have no number; numbers are never reused. | T |
| FR-PLT-017 | Invoices MUST carry supplier and recipient GST details (legal names, GSTINs, state codes, place of supply) and compute CGST+SGST (intra-state) or IGST (inter-state) with half-up rounding to paise; billing accounts validate GSTIN format and state code. | T |
| FR-PLT-018 | Payments MUST go through a provider interface; M0 supports only `manual` (bank transfer, UPI, cheque) with amount, date, reference and TDS; partial payments allowed; errors reversed with a reason, never deleted. | T |
| FR-PLT-019 | Issued unpaid invoices MAY be voided with a reason (number kept); payment reminders MUST be emailed at issue, 3 days before due, on the due date and 7 and 14 days after. | T |
| FR-PLT-020 | Daily per-school usage aggregates (active users, staff users, active students, storage, documents, AI queries, tokens, cost) MUST be collected as counts only: via `core.tenant_usage_summary()` on the shared tier and via heartbeat on dedicated hosts. | T |
| FR-PLT-021 | Plans MUST define limits; first crossing of 80% and 100% per metric per period MUST notify operators and the school's billing contact; limits MUST NOT block school work (except the AI budget, FR-KB-011). | T |
| FR-PLT-022 | Feature flags MUST support global values with % rollout (stable per school) and per-school overrides in `platform.feature_flags`; changes need `platform.flags.manage` ᴿ and are audited. | T |
| FR-PLT-023 | The fleet registry MUST record per deployment: mode, region, host, host name, custom domain, running and target version, last heartbeat, status, backup and certificate state. | T |
| FR-PLT-024 | `POST /api/v1/fleet/heartbeat` MUST authenticate with HMAC-SHA256 over timestamp and body using a per-deployment key, reject timestamps outside ±5 minutes, replayed nonces, unknown fields and oversized bodies, and accept no personal data. | T |
| FR-PLT-025 | A deployment without a valid heartbeat for 20 minutes MUST become `unreachable` and alert; stale backups, low disk, expiring certificates and version skew MUST mark it `degraded`. | T |
| FR-PLT-026 | Announcements MUST have English and Telugu text, a severity, an audience (all, tier, listed schools) and a schedule; they reach shared-tier schools within one minute and dedicated hosts at their next heartbeat. | T |
| FR-PLT-027 | Support tickets MUST have statuses, priorities and SLA timers; the form MUST warn against student data; messages MUST pass `redact()` before storage; tickets are deleted 1 year after closing. | T |
| FR-PLT-028 | Operators MUST sign in through a separate OIDC client with MFA; roles come from the fixed platform role set; operators cannot change their own roles; at least one active `platform_owner` remains; platform permissions can never be granted to tenant roles. | T |
| FR-PLT-029 | Every control-plane change MUST write one event to the hash-chained `platform.audit_events` in the same transaction; operators with `platform.audit.read` can filter, export and verify the chain. | T |
| FR-PLT-030 | School users with `tenant.billing.read` (owner, principal, accountant) MUST see their own plan, usage vs limits and invoices through `core.current_subscription()`. | T |

### 3.13 Implementation status (M0, 2026-09-26)

Status of the requirements M0 touches. **Built** = implemented with tests named by ID (`apps/api/tests/`); **Partial** = some acceptance points open; open items are tracked in 14 · M0 status. Requirements not listed are not started (M1+).

| Requirements | Status | Notes |
|---|---|---|
| FR-IAM-001..004, FR-IAM-006 | Built | BFF (PKCE, encrypted server sessions, CSRF, refresh rotation with reuse detection, timeouts, session list/revoke); API token checks, MFA (`403 mfa_required`) and step-up (`428`) |
| FR-IAM-005 | Partial | Login rate limits and lockouts rely on the IdP; lockout/reset auditing and breached-password screening (Cognito Essentials) not built |
| FR-IAM-010..014 | Built | Role keys pinned to 07 §6.2; permission catalog in `core.permissions`; scopes; school picker and invitation acceptance; changes effective within 60 s and audited |
| FR-TEN-001..003, FR-TEN-010, FR-TEN-012 | Built | Provisioning via the control plane (16 §5.4); academic structure; settings (retention settings are M1) |
| FR-AUD-001..004 | Built | Tenant and platform chains; daily signed archive and verification. School-chain copies of platform actions are queued in the platform transaction and delivered exactly once (`platform.tenant_audit_outbox`, ADR-0020) |
| FR-AUD-005 | Built | Viewer with filters and chain verification; CSV export (`GET /audit/export`, step-up, streamed, audited `audit.exported`); the web export button is pending |
| FR-OPS-004 | Partial | `ops.break_glass_grants` and `platform.breakglass_requests` with the 8-hour and two-person rules in the database; workflow M1 |
| FR-PLT-001, FR-PLT-003, FR-PLT-005, FR-PLT-010..018, FR-PLT-020, FR-PLT-022..030 | Built | 16 §8 route catalog. FR-PLT-005: two-person request/approval built; data deletion, key destruction and certificate are M1. FR-PLT-020: students, storage, documents and AI meters are 0 until `sis`/`kb` exist |
| FR-PLT-002 | Built | Resumable provisioning (ADR-0024, migration `0020_provisioning_runs`): each step atomic and idempotent, state visible to operators, resume route, go-live blocked until complete |
| FR-PLT-004 | Partial | Suspend/reactivate built; while suspended the owner and principal keep `/me`, Plan & billing and the full data export (FR-ADM-001; pinned allowlist, 16 §5.5) |
| FR-PLT-019, FR-PLT-021 | Partial | Void built; reminder and threshold emails not built (no email delivery in M0); threshold crossings recorded and audited |

### 3.14 Circulars, tasks and parent notices (FR-CIR, FR-TASK, FR-NOTICE) (M4; details in 06 §4.10, §10.4-10.5, §13)

Every row below is proposed from the roadmap scope (14 · M4; stories US-1601..US-1606) and waits for the product owner to confirm it.

| ID | Requirement | V |
|---|---|---|
| FR-CIR-001 | When the current version of a document of type `circular` is indexed, a reading job MUST be queued in the same transaction (outbox); holders of `circular.review` MAY request it again while it is not `ready`. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-CIR-002 | Reading MUST go through `knowledge.gateway` (role `circular`, prompt `circular_reading` in `app/knowledge/prompts/`, model and caps in `models.yaml`) with only that version's indexed, Aadhaar-masked passages as input; the strict-JSON output (issuer, reference number, date, subject, EN and TE summary, deadlines) MUST be validated server-side. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-CIR-003 | Every suggested deadline MUST cite a passage of the same version with a quote that is a substring of it and contains the due date; issuer, reference number and date MUST appear in the passages. Anything else is dropped (counted, never stored) or left empty; nothing is guessed. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-CIR-004 | Suggestions MUST NOT create tasks: a person holding `circular.review` confirms (optionally editing title, details, due date; choosing the owner) or dismisses each one, once; decisions are audited in the same transaction (invariant 9). *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-CIR-005 | Reading MUST be idempotent per document version (one row per version); a failure (AI off, budget used up, provider unavailable, invalid output, no text) MUST end in `needs_review` with an error code, never in silence. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-CIR-006 | Reading results MUST be visible only to users who can see the document (documents visibility, 404 otherwise). *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-CIR-007 | Every reading MUST be metered (`kb.llm_calls`, feature `circulars`) and audited (`circular.read_completed` / `circular.read_failed`: ids, counts, codes only); logs MUST NOT carry prompt, passage or completion text. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-CIR-008 | The eval harness MUST gate reading on synthetic EN, TE and code-mixed circulars: deadline recall ≥ 0.90, deadline precision ≥ 0.90, citation validity = 1.00, hallucinated deadlines = 0 (hard). *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-TASK-001 | A task MUST have title, optional details, owner (an active membership of the school), due date, status (`open`, `in_progress`, `done`, `cancelled`) and source (`manual` or `circular` with document and citation). *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-TASK-002 | Owners MUST see their own tasks (`task.read`); only holders of `task.read_all` see every task; others get 404 for a task that is not theirs. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-TASK-003 | Holders of `task.manage` MUST be able to create, reassign, edit and cancel tasks with optimistic locking (`If-Match`). *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-TASK-004 | Owners MUST be able to move their task between `open`, `in_progress` and `done`; `done` records who and when. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-TASK-005 | Task lists MUST filter by status, owner, source circular and due window (`overdue`, `week`) and page with cursors. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-TASK-006 | Creating, assigning, completing and cancelling a task MUST be audited in the same transaction (ids and codes only). *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-TASK-007 | A daily job MUST send each open task's owner one in-app reminder (EN/TE) N days before the due date (N in `app/circulars/config.yaml`) and one when it becomes overdue; reruns MUST NOT duplicate them. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-TASK-008 | Email reminders are out of scope until the notifications email interface supports staff templates beyond invitations. *(Proposed from the roadmap scope; PO to confirm.)* | I |
| FR-NOTICE-001 | A parent notice MUST be drafted only from a confirmed circular's passages (and its confirmed deadlines) or from staff text; no student record is ever sent to the model. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-NOTICE-002 | Circulars classified C2 or C3 MUST NOT be used for notices; staff text and edited notices with phone numbers, email addresses or Aadhaar-like numbers MUST be refused with a fix-it message. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-NOTICE-003 | Drafting MUST go through `knowledge.gateway` (role `notice`, prompt `parent_notice`); the draft is marked AI-drafted and is editable; if AI is unavailable an empty draft opens. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-NOTICE-004 | A notice MUST have English and Telugu title and body within configured lengths before approval. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-NOTICE-005 | Only holders of `notice.approve` may approve; approved notices are immutable. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-NOTICE-006 | After approval the worker MUST render an A4 PDF and a PNG with headless Chromium and the bundled Noto Sans Telugu; downloads use presigned links ≤ 5 min and are audited. *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-NOTICE-007 | Drafting, editing, approving and downloading MUST be audited (ids and codes only) and every model call metered (feature `notices`). *(Proposed from the roadmap scope; PO to confirm.)* | T |
| FR-NOTICE-008 | SchoolOS MUST NOT send notices to parents; staff copy or download them. *(Proposed from the roadmap scope; PO to confirm.)* | I |

---

## 4. Non-functional requirements

### 4.1 Security (NFR-SEC), summary; controls in 07

| ID | Requirement |
|---|---|
| NFR-SEC-001 | Target OWASP ASVS (current version) Level 2 for web/API. |
| NFR-SEC-002 | TLS 1.2+ (prefer 1.3) everywhere; HSTS; no plaintext internal hops across networks. |
| NFR-SEC-003 | Encryption at rest with KMS for RDS, S3, backups, ElastiCache for Valkey (where supported) and dedicated-host EBS volumes and buckets; app-layer encryption for C3 fields. |
| NFR-SEC-004 | Secrets only in AWS Secrets Manager; rotation ≤ 90 days for DB credentials. |
| NFR-SEC-005 | Critical vulnerabilities patched ≤ 7 days, high ≤ 30 days. |
| NFR-SEC-006 | Independent penetration test before first paid go-live and annually. |

### 4.2 Privacy (NFR-PRV), summary; controls in 08

| ID | Requirement |
|---|---|
| NFR-PRV-001 | Data stored in India for both tiers (ap-south-1; backups ap-south-2); dedicated hosts run in ap-south-1 with backups in ap-south-2. Sub-processors outside India (e.g., LLM API) are disclosed to schools and minimized. |
| NFR-PRV-002 | Access/security logs retained ≥ 1 year (DPDP Rules) and ICT logs ≥ 180 days within India (CERT-In). |
| NFR-PRV-003 | Data minimization for LLM calls: only fields required by the question; no Aadhaar data ever. |
| NFR-PRV-004 | Support data-principal requests (access, correction, erasure) via school admin tools within 7 days. |

### 4.3 Performance (NFR-PERF)

| ID | Metric | Target |
|---|---|---|
| NFR-PERF-001 | API read p95 | ≤ 300 ms |
| NFR-PERF-002 | API write p95 | ≤ 800 ms |
| NFR-PERF-003 | Ask: first token / complete p95 | ≤ 3 s / ≤ 10 s |
| NFR-PERF-004 | Import validate 2,000 rows | ≤ 60 s |
| NFR-PERF-005 | DQ check 2,000 students | ≤ 2 min |
| NFR-PERF-006 | Text-layer PDF (50 pages) to ready | ≤ 3 min |
| NFR-PERF-007 | Web LCP on 4G / mid-range PC | ≤ 2.5 s |

### 4.4 Scalability (NFR-SCAL): capacity model

| Stage | Schools | Students | Chunks (docs) | Peak concurrent users |
|---|---|---|---|---|
| 0 · Pilot | ≤ 5 | ≤ 10k | ≤ 250k | 25 |
| 1 · Growth | ≤ 100 | ≤ 200k | ≤ 5M | 300 |
| 2 · Scale | ≤ 1,000 | ≤ 2M | ≤ 50M | 3,000 |

- NFR-SCAL-001 API and workers MUST be stateless and horizontally scalable.
- NFR-SCAL-002 The design MUST reach Stage 1 without schema redesign; Stage 2 triggers are defined in 04 §11.
- NFR-SCAL-003 Tenant-level load MUST be isolated via rate limits and per-tenant job concurrency caps.

### 4.5 Availability & DR (NFR-AVL)

| ID | Requirement |
|---|---|
| NFR-AVL-001 | Monthly availability ≥ 99.5% (Stage 0–1), ≥ 99.9% (Stage 2), excluding announced maintenance outside 08:00–18:00 IST Mon–Sat. |
| NFR-AVL-002 | Shared tier: RPO ≤ 15 min, RTO ≤ 4 h (Stage 0–1); RPO ≤ 5 min, RTO ≤ 1 h (Stage 2). |
| NFR-AVL-003 | Restore drills quarterly; results recorded. |
| NFR-AVL-004 | LLM/embedding outages MUST degrade gracefully (search-only, queued ingestion) without data loss. |
| NFR-AVL-005 | Dedicated tier: monthly availability target ≥ 99.5% (single host, no automatic failover); RPO ≤ 15 min (continuous WAL archiving with WAL-G) and RTO ≤ 8 h (rebuild host from Terraform and restore); nightly base backup and `pg_dump` to ap-south-2; restore tested before go-live and quarterly. |
| NFR-AVL-006 | The control plane is not on the school's critical path: schools MUST keep working (sign-in, records, Ask) when the control plane is unavailable. |

### 4.6 Maintainability, i18n, accessibility, compatibility

| ID | Requirement |
|---|---|
| NFR-MNT-001 | Backend unit+integration coverage ≥ 80% on `authz`, `audit`, `students`, `dq`, `changes`, `knowledge`. |
| NFR-MNT-002 | All modules follow the module structure and dependency rules (import-linter check in CI). |
| NFR-I18N-001 | All UI strings externalized; `en` and `te` complete; CI fails on missing keys. |
| NFR-A11Y-001 | WCAG 2.2 AA for core screens (automated axe checks + manual review per release). |
| NFR-CMP-001 | Current Chrome/Edge (Windows 10+), Android Chrome; 1366×768; usable at 1 Mbps. |

### 4.7 Fleet (dedicated tier) (NFR-FLT)

| ID | Requirement |
|---|---|
| NFR-FLT-001 | Each dedicated host MUST send a signed heartbeat every 5 minutes (±30 s); a gap of 20 minutes MUST raise an alert (P2 in school hours). Heartbeats carry versions, health, backup state and aggregate counts only, never personal data. |
| NFR-FLT-002 | Dedicated hosts MUST run a supported release: security patches within 7 days (critical) / 30 days (high) as in NFR-SEC-005; no more than one release behind 14 days after a release; OS patches applied monthly. |

### 4.8 Observability & cost

| ID | Requirement |
|---|---|
| NFR-OBS-001 | Every request/job traced with tenant ID (not user PII); RED metrics per route; structured logs with redaction. |
| NFR-OBS-002 | SLO dashboards and alerting per 11-Operations. |
| NFR-CST-001 | LLM spend tracked per tenant/feature; alerts at 80% and 100% of budget. |
| NFR-CST-002 | Infra cost per active school tracked monthly; design choices favour managed, low-ops services. |

---

## 5. External interfaces

| Interface | Direction | Protocol | Notes |
|---|---|---|---|
| OIDC provider (Cognito reference) | Out | OIDC/OAuth 2.1 | BFF handles code exchange |
| Anthropic Messages API | Out | HTTPS JSON, streaming | Commercial org API keys; ZDR requested; model IDs in config |
| Fleet heartbeat (dedicated host → control plane) | In (to shared) | HTTPS JSON, HMAC-SHA256 signed | `POST /api/v1/fleet/heartbeat`; outbound from host only; no personal data (FR-PLT-024) |
| Payment provider (Razorpay candidate) | Out | HTTPS | **Proposed only** (ADR-0016); not built; M0 uses manual payments |
| Email (AWS SES) | Out | AWS SDK | Invites, billing reminders, usage alerts; templates EN/TE. Built: provider interface with a local fake and SES v2, staff invitation emails (queued at invite and on resend, sent by a worker); off by default (`SOS_EMAIL_PROVIDER`) |
| Embeddings provider (e.g., Voyage) | Out | HTTPS JSON | Behind interface; chosen by eval |
| OCR/extraction provider(s) | Out | HTTPS | Behind interface; Telugu support required |
| AWS S3, KMS, Secrets Manager, SSM Parameter Store | Out | AWS SDK | VPC endpoints where cost-justified |
| Tally (M6) | In (via edge agent) | TallyPrime XML over HTTP on the office PC → agent → HTTPS to SchoolOS | Tally is not internet-reachable; agent initiates outbound only |
| Government/board portals | — | None | No integration; exports + checklists only |

## 6. Data requirements

- Classification C0–C3 per 05-Data-model §8; C3 encrypted at app layer.
- Retention per 08-Privacy §7; tenant offboarding: export → delete → crypto-shred DEK → certificate of deletion.
- Backups: RDS automated backups with PITR (≥ 14 days), daily snapshots copied to ap-south-2; S3 versioning; audit exports under Object Lock.

## 7. Constraints and assumptions

- Solo developer: managed services, modular monolith, high automation (ADR-0001).
- One codebase for both tiers; dedicated hosts are one-tenant installs with RLS still on (ADR-0015).
- No vendor portal APIs (exports only).
- AI outputs are advisory; official records change only through human workflows.

## 8. Traceability (objectives → requirements)

| Objective | Requirements |
|---|---|
| BO-01 zero avoidable submission errors | FR-DQ-*, FR-CR-*, FR-EXP-*, FR-IMP-020..023 |
| BO-02 fast answers | FR-KB-*, FR-DOC-*, NFR-PERF-003 |
| BO-03 less re-typing | FR-STU-002..006, FR-IMP-*, FR-EXP-001 |
| BRD P4 missed circular deadlines (M4 exit, 14 · M4) | FR-CIR-*, FR-TASK-*, FR-NOTICE-* |
| BO-05 trust | FR-IAM-*, FR-TEN-002, FR-AUD-*, FR-PLT-028..029, NFR-SEC-*, NFR-PRV-* |
| BO-06 willingness to pay | FR-PLT-010..019, FR-PLT-030 |
| BO-07 repeatable onboarding | FR-TEN-003, FR-IMP-002, FR-PLT-001..005, FR-PLT-020..026 |

## 9. Verification approach

Each FR maps to automated tests named with its ID (e.g., `test_FR_CR_002_self_approval_forbidden`). NFR-PERF via load tests in staging (k6/Locust). NFR-SEC via CI scans, authz/RLS suites, ZAP baseline, and external pen test. RAG requirements via the eval harness (06 §13) as CI gates.
