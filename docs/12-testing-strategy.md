# 12 · Testing Strategy & Quality Gates

| Field | Value |
|---|---|
| Version | 0.1 · 2026-09-26 |
| Related | 03-TRD §9, 06-RAG §13, 07-Security §15, 13-Engineering standards |

---

## 1. Principles

- **Tests encode the invariants.** Tenant isolation, authorization, no-Aadhaar and audit rules are enforced by automated tests, not by memory.
- **Synthetic data only** outside production. No real student data in fixtures, screenshots, logs or AI sessions.
- **Requirement IDs in test names** (e.g., `test_FR_CR_002_self_approval_forbidden`) for traceability.
- **Fast feedback:** unit and integration suites run on every PR in < 10 minutes; heavier suites nightly.

## 2. Test pyramid and tooling

| Layer | Scope | Tools | When |
|---|---|---|---|
| Unit | Pure functions: name normalization, match classes, Verhoeff redaction, canonical resolution, chunking, RRF fusion, crypto helpers | pytest, Hypothesis (property-based), vitest | Every PR |
| Integration | Services + real Postgres (pgvector) + Redis + MinIO | pytest + testcontainers, factory_boy | Every PR |
| API | Routes with auth, validation, errors, authz | pytest + httpx TestClient | Every PR |
| Security suites | Route enumeration, authz matrix, BOLA, cross-tenant, RLS catalog, log redaction | pytest (generated cases) | Every PR |
| Contract | OpenAPI conformance and fuzzing | Schemathesis | Every PR (fast), nightly (deep) |
| Migrations | Upgrade/downgrade on seeded DB; RLS present on new tables | pytest + Alembic | Every PR touching migrations |
| Frontend | Components, forms, i18n keys | vitest + React Testing Library | Every PR |
| E2E | Critical journeys in a browser | Playwright (staging) | Merge to main + nightly |
| Accessibility | Automated WCAG checks on core screens | axe (Playwright integration) | Nightly + release |
| Visual/print | PDF and print views (Telugu rendering) | Playwright screenshots + PDF snapshot diff | Nightly + release |
| Performance | NFR-PERF targets | k6 or Locust (staging) | Weekly + before release |
| RAG evaluation | Retrieval, faithfulness, citations, leakage, injection | `evals/` harness | PR subset when knowledge changes; full nightly |
| Security scanning | SAST, deps, secrets, IaC, images, DAST | Semgrep, pip-audit, npm audit/OSV, gitleaks, Trivy, OWASP ZAP baseline | Every PR / nightly (ZAP) |
| Resilience | Provider outages, Redis loss, slow DB | Fault injection in staging (toggle-based) | Monthly |

## 3. Synthetic data

- `make seed-synthetic` generates one or more tenants with: classes Nursery–XII, 2,000 students, realistic Telugu and English names (surname-first, initials, variants), guardians, per-source values with **deliberate mismatches** at known rates (spacing, initials, variant spellings, DOB off-by-one, missing fields), sample register page images (rendered), circulars in EN/TE, minutes, fee policy.
- Aadhaar-like test numbers are generated as **invalid** Verhoeff sequences except in dedicated redaction tests, which use valid-checksum synthetic numbers that never leave the test process.
- Datasets are versioned; tests reference them by version to keep results stable.

## 4. Security test suites (must pass on every PR)

### 4.1 Route enumeration
Iterate FastAPI's route table; assert every route (except allowlisted health checks) has a `require()` dependency with a permission that exists in `core.permissions`.

### 4.2 Authorization matrix
Generated from the role defaults (07 §6.2): for each (role, permission-protected endpoint) pair, call the endpoint as a user with only that role. Expect success for granted permissions, `403`/`404` otherwise. Include step-up cases (`428`) and scope cases (class teacher inside vs outside their section).

### 4.3 BOLA per resource
For students, guardians, documents, findings, change requests, imports, exports: user in section 9A requests a resource belonging to 9C → `404`; resource IDs taken from another tenant → `404`.

### 4.4 Cross-tenant isolation
Two synthetic tenants with overlapping names. For every read path (lists, detail, search, exports, knowledge ask, document download URLs, audit viewer): tenant A never sees tenant B data. Also direct SQL tests as `sos_app`: with `app.tenant_id` unset, every tenant table returns zero rows; with tenant A set, inserting a row with tenant B's ID fails the policy's `WITH CHECK`.

### 4.5 RLS catalog test
Query `pg_class`/`pg_policies`: every table with a `tenant_id` column has `relrowsecurity` and `relforcerowsecurity` true and a `tenant_isolation` policy (or an approved variant). Verify `sos_app` lacks BYPASSRLS and superuser.

### 4.6 Redaction and logging
- Feed text with valid-checksum 12-digit sequences (with spaces/hyphens) through OCR post-processing, ingestion, import parsing, logging and prompt building: output contains only masked forms.
- Capture logs during API tests and assert no seeded names, DOBs or phone numbers appear.

### 4.7 Maker-checker and audit
- Self-approval blocked at API **and** DB level (direct SQL update violating the CHECK fails).
- Every audited action writes exactly one event in the same transaction (rollback test: failed transaction leaves no event).
- Chain verification detects a tampered event (test with a superuser fixture in an isolated DB).

## 5. Domain test highlights

| Area | Tests |
|---|---|
| Name matching | Table-driven cases per match class; property tests (order invariance, idempotent normalization); Telugu-script ↔ Latin transliteration cases |
| DQ engine | Rule outputs on seeded mismatches (precision/recall per rule); idempotent findings; reopen behaviour |
| Imports | Mapping suggestions from EN/TE headers; row errors; atomic commit; revert window; 2,000-row timing |
| Extraction queue | Nothing becomes a record without confirmation; evidence linked; low-confidence highlighting |
| Exports | Profile validation; field order; formula-injection escaping; watermark; audit event |
| Documents | Type sniffing, size limits, quarantine path, version switching, deletion removes chunks within SLA |

## 6. RAG evaluation (details in 06 §13)

- `make eval` runs on PRs touching `app/knowledge/**`, `prompts/**`, model config or retrieval SQL; full suite nightly and before releases.
- **Hard gates (block merge):** leakage = 0, injection resistance = 0 failures, citation precision ≥ 0.95, correct refusal ≥ 0.95.
- **Soft gates (block release, may merge with ticket):** Recall@10 ≥ 0.90, MRR@10 ≥ 0.70, faithfulness ≥ 0.95, correctness ≥ 0.85, language match ≥ 0.98, latency/cost within budget.
- Results stored as artifacts with per-category breakdowns (records, documents, mixed language, temporal, unanswerable, permissions, adversarial) and diffs against the last main-branch run.
- Judge calibration: ≥ 100 human-labelled items; re-calibrate when changing judge model or rubric.

## 7. Acceptance tests (examples)

```gherkin
Feature: Maker-checker for identity corrections (FR-CR-002)
  Scenario: Requester cannot approve their own correction
    Given office admin "Lakshmi" submitted a DOB correction with evidence for student "S-457"
    When "Lakshmi" tries to approve it
    Then the request is rejected with code "self_approval_forbidden"
    And the correction remains "pending"

  Scenario: Principal approves with fresh MFA
    Given the principal completed MFA within the last 5 minutes
    When the principal approves the correction
    Then a new verified admission-register value is recorded
    And the previous value is kept in history
    And DQ findings for "S-457" are re-evaluated
    And two audit events exist: "cr.approved" and "student.value.recorded"

Feature: Scope-limited Ask (FR-KB-010)
  Scenario: Class teacher asks about a student outside their sections
    Given class teacher "Ravi" is scoped to sections 9A and 9B
    When "Ravi" asks "What is the date of birth of the student with admission number 2019/0999?" (student is in 9C)
    Then the answer says the information was not found in records they can access
    And no citation references the 9C student
```

## 8. Performance tests

| Scenario | Target |
|---|---|
| 50 RPS mixed reads/writes (Stage 1 profile) | p95 read ≤ 300 ms, write ≤ 800 ms, 0% errors |
| Search students (2,000 per tenant, 100 tenants) | p95 ≤ 300 ms |
| Concurrent Ask (20 users) | first token p95 ≤ 3 s; complete ≤ 10 s (with recorded/stubbed LLM for infra, live for end-to-end) |
| DQ run 2,000 students | ≤ 2 min |
| Ingest 50-page text PDF | ≤ 3 min to ready |

## 9. Quality gates summary

| Gate | Blocks |
|---|---|
| Lint, format, typecheck | Merge |
| Unit/integration/API tests | Merge |
| Security suites (4.1–4.7) | Merge |
| SAST/deps/secrets/IaC/image scans (no critical/high without waiver) | Merge |
| Migration + RLS catalog | Merge |
| RAG hard gates | Merge |
| Coverage ≥ 80% on critical modules | Merge |
| E2E, a11y, visual, RAG soft gates, performance | Release |
| External pen test (annual / before paid go-live) | Paid go-live |

## 10. Definition of done (testing view)

A story is done when its acceptance criteria are automated, security suites cover its new routes/resources, logs are proven PII-free, migrations are reversible, UI strings exist in both languages, and all merge gates pass.
