# 03 · Technical Requirements Document (TRD)

| Field | Value |
|---|---|
| Version | 0.1 · 2026-09-26 |
| Scope | Core platform (M0–M2) + interfaces for M3–M6 |
| Related | 02-PRD (stories), 04-Architecture, 05-Data model, 06-RAG, 07-Security |

Normative keywords: **MUST / SHOULD / MAY** (RFC 2119). Every requirement has an ID and a verification method: **T** test · **I** inspection · **D** demonstration · **A** analysis.

---

## 1. System summary

A multi-tenant web application (Next.js BFF + FastAPI API + Celery workers) on AWS ap-south-1 with PostgreSQL (pgvector) as the system of record and S3 for files. An LLM gateway calls Anthropic Claude via commercial API; an embeddings provider interface serves retrieval. See 04-Architecture.

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
| FR-IAM-002 | MFA MUST be enforced for roles `owner`, `principal`, `office_admin` and all platform roles. | T |
| FR-IAM-003 | Sessions MUST expire after 15 min idle (configurable 5–30) and 12 h absolute; re-auth required for sensitive actions (exports, role changes, approvals). | T |
| FR-IAM-004 | Access tokens MUST live ≤ 10 min; refresh tokens MUST rotate on use with reuse detection revoking the session family. | T |
| FR-IAM-005 | Login attempts MUST be rate-limited per account and per IP; lockouts and resets audited. | T |
| FR-IAM-006 | Users MUST be able to see and revoke their active sessions. | D |
| FR-IAM-010 | The system MUST support tenant roles: owner, principal, office_admin, office_staff, accountant, exam_coordinator, class_teacher, teacher, auditor_readonly. | T |
| FR-IAM-011 | Permissions MUST be `resource.action` strings; roles map to permission sets; tenants MAY clone and customize roles. | T |
| FR-IAM-012 | Memberships MUST support scopes: `school`, `classes[]`, `sections[]`; scope applies to every read path including search, exports and AI. | T |
| FR-IAM-013 | A user MAY hold memberships in multiple tenants; the active tenant is explicit per session. | T |
| FR-IAM-014 | Role/permission/scope changes MUST take effect within 60 s and be audited. | T |

### 3.2 Tenancy & school setup (FR-TEN)

| ID | Requirement | V |
|---|---|---|
| FR-TEN-001 | Each school MUST be a tenant with a UUID, and every tenant-owned row MUST carry `tenant_id`. | I/T |
| FR-TEN-002 | PostgreSQL RLS MUST enforce tenant isolation for every tenant-owned table (ENABLE + FORCE). | T |
| FR-TEN-003 | Tenant provisioning MUST create default roles, a per-tenant data encryption key (wrapped by KMS), and an owner invite. | T |
| FR-TEN-010 | Academic years, classes, sections and enrolments MUST be modelled; exactly one current year per tenant. | T |
| FR-TEN-011 | Bulk promotion MUST offer preview, commit, and undo within 24 h. | T |
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
| FR-OPS-001 | Operator console: provision/suspend tenants, feature flags, health, usage/budgets, without access to tenant data. | T |
| FR-OPS-004 | Break-glass: school-approved, time-bound (≤ 8 h), scope-limited, fully audited, visible to the school. | T |

---

## 4. Non-functional requirements

### 4.1 Security (NFR-SEC), summary; controls in 07

| ID | Requirement |
|---|---|
| NFR-SEC-001 | Target OWASP ASVS (current version) Level 2 for web/API. |
| NFR-SEC-002 | TLS 1.2+ (prefer 1.3) everywhere; HSTS; no plaintext internal hops across networks. |
| NFR-SEC-003 | Encryption at rest with KMS for RDS, S3, backups, Redis (where supported); app-layer encryption for C3 fields. |
| NFR-SEC-004 | Secrets only in AWS Secrets Manager; rotation ≤ 90 days for DB credentials. |
| NFR-SEC-005 | Critical vulnerabilities patched ≤ 7 days, high ≤ 30 days. |
| NFR-SEC-006 | Independent penetration test before first paid go-live and annually. |

### 4.2 Privacy (NFR-PRV), summary; controls in 08

| ID | Requirement |
|---|---|
| NFR-PRV-001 | Data stored in India (ap-south-1; backups ap-south-2). Sub-processors outside India (e.g., LLM API) are disclosed to schools and minimized. |
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
| NFR-AVL-002 | RPO ≤ 15 min, RTO ≤ 4 h (Stage 0–1); RPO ≤ 5 min, RTO ≤ 1 h (Stage 2). |
| NFR-AVL-003 | Restore drills quarterly; results recorded. |
| NFR-AVL-004 | LLM/embedding outages MUST degrade gracefully (search-only, queued ingestion) without data loss. |

### 4.6 Maintainability, i18n, accessibility, compatibility

| ID | Requirement |
|---|---|
| NFR-MNT-001 | Backend unit+integration coverage ≥ 80% on `authz`, `audit`, `students`, `dq`, `changes`, `knowledge`. |
| NFR-MNT-002 | All modules follow the module structure and dependency rules (import-linter check in CI). |
| NFR-I18N-001 | All UI strings externalized; `en` and `te` complete; CI fails on missing keys. |
| NFR-A11Y-001 | WCAG 2.2 AA for core screens (automated axe checks + manual review per release). |
| NFR-CMP-001 | Current Chrome/Edge (Windows 10+), Android Chrome; 1366×768; usable at 1 Mbps. |

### 4.7 Observability & cost

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
| Embeddings provider (e.g., Voyage) | Out | HTTPS JSON | Behind interface; chosen by eval |
| OCR/extraction provider(s) | Out | HTTPS | Behind interface; Telugu support required |
| AWS S3, KMS, Secrets Manager, SES (optional email) | Out | AWS SDK | VPC endpoints where cost-justified |
| Tally (M6) | In (via edge agent) | TallyPrime XML over HTTP on the office PC → agent → HTTPS to SchoolOS | Tally is not internet-reachable; agent initiates outbound only |
| Government/board portals | — | None | No integration; exports + checklists only |

## 6. Data requirements

- Classification C0–C3 per 05-Data-model §8; C3 encrypted at app layer.
- Retention per 08-Privacy §7; tenant offboarding: export → delete → crypto-shred DEK → certificate of deletion.
- Backups: RDS automated backups with PITR (≥ 14 days), daily snapshots copied to ap-south-2; S3 versioning; audit exports under Object Lock.

## 7. Constraints and assumptions

- Solo developer: managed services, modular monolith, high automation (ADR-0001).
- No vendor portal APIs (exports only).
- AI outputs are advisory; official records change only through human workflows.

## 8. Traceability (objectives → requirements)

| Objective | Requirements |
|---|---|
| BO-01 zero avoidable submission errors | FR-DQ-*, FR-CR-*, FR-EXP-*, FR-IMP-020..023 |
| BO-02 fast answers | FR-KB-*, FR-DOC-*, NFR-PERF-003 |
| BO-03 less re-typing | FR-STU-002..006, FR-IMP-*, FR-EXP-001 |
| BO-05 trust | FR-IAM-*, FR-TEN-002, FR-AUD-*, NFR-SEC-*, NFR-PRV-* |
| BO-07 repeatable onboarding | FR-TEN-003, FR-IMP-002, FR-OPS-001 |

## 9. Verification approach

Each FR maps to automated tests named with its ID (e.g., `test_FR_CR_002_self_approval_forbidden`). NFR-PERF via load tests in staging (k6/Locust). NFR-SEC via CI scans, authz/RLS suites, ZAP baseline, and external pen test. RAG requirements via the eval harness (06 §13) as CI gates.
