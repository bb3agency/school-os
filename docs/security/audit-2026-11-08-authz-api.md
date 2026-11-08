# Security audit 2026-11-08: authorization and the HTTP API (authz-api)

Worktree branch from `987ded97`. Area: every route's guard (`require()`, `require_any()`,
`require_platform()`, public, webhook and fleet guards) and whether the service also enforces
object scope; BOLA by id in path, query and body; step-up (SEC-005); maker-checker; idempotency;
rate limits; If-Match; problem+json leakage; suspended schools; operator, support and school
tokens; break-glass; pagination. Extra attention on routes added since 2026-11-01. The earlier
reports (`audit-2026-10-*.md`) were read first; their findings are not repeated. Paths are
relative to `apps/api/`. Synthetic data only.

**Status: complete. No defect of medium or higher severity was found. The three low findings
are fixed, each with a test that failed first**, following the lead's decisions of 2026-11-08
(AZ-02 amends D71). AZ-01 and AZ-03 change the API schema, so the lead regenerates OpenAPI and
the TS client. The automated guards that cover this area all pass.

## Method

- **Inventory:** 541 (method, path) pairs from `create_app()` via
  `tests/security/test_route_enumeration.api_routes`, with each route's guard, permission,
  alternatives, scope and step-up flag (scratch script, not committed). Then:
  - every `require_any()` route (65), whose service must re-check scope per object;
  - every mutating route without step-up (about 170), against the step-up list of docs/07 §5;
  - every `GET` answering an unpaged list (40);
  - every mutating route that also reads query parameters (idempotency hash covers the body only).
- **Automated guards run:** `tests/security/test_api_contract_guards.py`,
  `test_route_enumeration.py`, `test_rate_limit_routes.py`, `test_no_pii_in_urls.py`:
  34 passed.
- **Manual review** of the routes added since 2026-11-01 (`git log --since=2026-11-01`) and of
  the shared machinery they rely on.

## Findings

| ID | Severity | Area | File:line | Description | Status |
|---|---|---|---|---|---|
| AZ-01 | Low (lost update between two privacy managers; integrity of an internal register) | Concurrency (API6, ASVS V11.1.4) | `app/privacy/breach_api.py:105-109`, `app/privacy/breach_service.py:469-500` | `PUT /privacy/breaches/{id}/affected` replaces the whole affected list without `If-Match`, and does not bump the breach's `version`. Two managers editing the list at once: the later save silently drops the other's additions. The breach `PATCH` next to it uses `If-Match`. This is the same class as round-two A-17 / R-03 (`PUT /users/{id}/roles`). | Fixed in 16890529: `If-Match` is required, as on the breach `PATCH` (400 `if_match_required` when missing, 412 when stale, nothing changed). The breach `version` is bumped and the new `ETag` is answered. **API schema change** (new header parameter). The web does not call this route. Test: `tests/privacy/test_breach_api.py::test_FR_DPDP_033_AZ_01_affected_list_needs_if_match_and_bumps_the_version`. |
| AZ-02 | Low (availability of the `pdf` queue; needs one valid parent notice link) | Public routes, rate limits (API4) | `app/circulars/service.py:1972-2025` (`parent_notice_pdf`), `app/public/api.py:146-172` | `GET /public/links/current/document` re-queues a Chromium render for a notice whose last render **failed**, on every request after the failure (D71: "expired, failed or never ran is queued again"). A notice that fails to render deterministically (for example a renderer time-out on that content) is therefore rendered again for every parent tap, and a link holder can repeat it as fast as the per-address public limit allows. Only one render per notice is in flight (row lock and `queued` state), so the cost is bounded to one worker slot per notice, and the web page does not poll by itself. | Fixed in b4ca607d and 86bc7321 (amends D71, lead decision). After a failed render, a parent request re-queues the render only when the failure is older than `notices.failed_render_cooldown_s` (`app/circulars/config.yaml`, 900 s, validated 60..86400). The failure time is `updated_at`, which the `parent_notices_set_updated_at` trigger stamps. Inside the cooldown the route answers 202 `preparing` with `Retry-After` set to the time left (at least 60 s) and queues nothing. No schema change. Test: `tests/public/test_pages.py::test_D71_AZ_02_a_failed_render_is_queued_again_only_after_the_cooldown`. |
| AZ-03 | Low (operator-only; memory and response size) | Pagination (API4) | `app/platform/api.py:473-478, 533-541` (`_range`, `GET /platform/usage`) | `GET /api/v1/platform/usage` without `tenant_id` returns every school's daily usage rows for up to 401 days in one unpaged list (`list[UsageDailyOut]`). With 500 schools that is about 200,000 rows in one response. Operator token with `platform.usage.read` needed. The other unpaged platform lists added in November (`/usage/ai-quota-alerts`, `/usage/limit-alerts`, `/incidents/clocks`) are bounded by one row per school (and metric) and are fine. | Fixed in 509f7986: without `tenant_id` the range is at most 31 days. With no dates it defaults to the last 31 days; a longer range answers 422 `range_too_long` on `from`. With `tenant_id`, the 401-day limit stays. The operator usage screen no longer sends a longer all-schools query and shows a warning instead. **API behaviour change** (new 422; the docstring is now in OpenAPI). Tests: `tests/platform/test_flags_usage.py::test_FR_PLT_020_AZ_03_all_schools_usage_is_at_most_31_days`, `apps/web/src/features/platform/usage-range.test.ts`. |

## Verified clean

Routes added since 2026-11-01:

- **`GET /documents/{id}/pages/{n}` (D73):**
  - It shares `_servable_version` with downloads: ACL and scope through `repo.get_document(visibility=...)`, so another school's id or one outside scope is 404.
  - C3 and raw import files need `student.read_sensitive` or the uploader; only `ready` versions are served.
  - Inline disposition is limited to PDF, JPEG and PNG, and is enforced again in `storage.presigned_get`.
  - Links live at most 300 s, with `Cache-Control: no-store`; audited.
  - `n` is bounded in the path and against `page_count`.
- **`POST /privacy/breaches/{id}/affected:count` (D59):**
  - `privacy.breach.manage` with step-up, school-wide.
  - Single students are checked against the caller's `student.read_basic` reach (422 `not_found` otherwise).
  - Sections and classes are checked to be this school's, and students are counted only within reach.
  - Membership ids and role keys are checked; nothing is stored. Lists are bounded.
- **`GET /tally/dues`:**
  - `finance.read`, school-wide, plus `can_read_fees` in the service.
  - Rows are filtered through `students.summaries(ctx)`, and the cursor offset is validated.
- **`GET /public/links/current/document`:**
  - Its guard accepts only the `parent_notice` and `parent_receipt` kinds.
  - The handle is reloaded and must have an expiry.
  - A notice must be `approved` (withdrawn gives 404), and receipts go through `_receipt`.
  - Links live at most 5 minutes; audited as system `via: parent_link`. See AZ-02 for the re-render.
- **Platform usage and limit alerts, `GET /tenants/{id}/ai-answers`, incident clocks and the reopen routes:**
  - Each has `require_platform`, with step-up where the catalogue marks it.
  - A reopen confirm checks `same_operator`, plus the `two_person.check_second_step` eligibility and expiry rules.
  - `reopen:told` refuses free text.
- **Heartbeat (ADR-0058):**
  - The request is verified before anything is read: deployment, key id within the rotation overlap, ±300 s, size, key unwrap bound to the tenant and deployment, constant-time HMAC, ids match the deployment, nonce replay store, and one heartbeat per minute.
  - The answer is signed with the key that verified the request, and the route returns the signed bytes unchanged (`Response(content=body)`).
  - The host checks signature, key id, freshness and schema, and the `request_nonce` must equal the nonce it just sent, so an answer cannot be replayed.
  - A request body reflected back as the answer fails `HeartbeatOut` validation (required `received_at` and `min_supported_version`).
  - An unsigned answer applies announcements only, and only behind `SOS_HEARTBEAT_ANSWER_UNSIGNED_OK`; a wrong signature is never accepted.

Cross-cutting:

- **`require_any()` routes:** the services re-check scope or school-wide grants per object:
  - fees: `require_school_wide`;
  - receipts on WhatsApp: `_receipt_caller`;
  - certificates: `_reach` through the student;
  - exports: own or `export.read_all`;
  - knowledge resolve: `SourceVisibility`;
  - privacy close: `_decider`, which requires step-up and refuses the request's own logger;
  - messaging join slips: `ensure_in_scope` for each student or section.
- **Step-up:** the docs/07 §5 list matches the guards or service checks:
  - change approvals, role changes and waivers;
  - exports, with `include_sensitive` on slips;
  - fee approvals (concession, adjustment, cancellation, refund);
  - privacy decide and break-glass approve and revoke;
  - the support session (checked in the service).

  Notice approval without step-up is by design (`self_approved` is audited).
- **Maker-checker:**
  - fees compare `requested_by` with the membership (`_decide_checks`), backed by the DB `CHECK` (SEC-038);
  - privacy compares `logged_by` (DB `CHECK`, SEC-049);
  - incident reopen and offboarding use different operators.
- **Idempotency (`app/authz/http.py`):**
  - The key is `tenant:user:sha256(method path key)`, and the body hash is compared.
  - A replay after a grant change answers 409 `idempotency_access_changed`.
  - Routes that hold C3 keep only the id and re-read it on replay.
  - No creating POST takes query parameters, so hashing only the body is enough.
- **Tokens:**
  - The tenant verifier forbids the operator and support client ids.
  - Support tokens are picked only by a distinct issuer and must carry MFA.
  - The operator verifier is separate; Cognito ID tokens are refused.
- **Suspended and offboarding schools:**
  - The resolver allowlist (`SUSPENDED_SCHOOL_ALLOWLIST`) gives owner and principal billing and export only; everything else answers 403 `tenant_suspended`.
- **problem+json:**
  - CHECK violations answer a generic 422 `invalid_value`; the constraint name is only logged, never sent.
  - NUL and out-of-range values give clean 422s, with no stack trace.
- **Documents upload:** `_claim_intent` requires the caller's own intent and the tenant prefix.

## Outside my area

- None found that needs action. The Ask tool `list_due_tasks` (D74) was read only as far as its
  permission check (`task.read`, reader registered by `circulars.service`).

## Checks run

Fixes (after the lead's decisions):

- pytest: `tests/privacy/test_breach_api.py`, `test_consents_api.py`, `test_cross_module.py`,
  `test_breach_affected_count.py`; `tests/public` and `tests/circulars` (203 passed, 3
  skipped); `tests/platform/test_flags_usage.py`; `tests/platform -k usage`;
  `tests/security/test_api_contract_guards.py` and `test_route_enumeration.py`: all passed.
- `tests/security/test_authz_matrix.py` and `test_bola.py` with `-k breach`: 100 passed, and 2
  failed on `POST /api/v1/privacy/breaches`, the create route, which these fixes do not touch.
  The error is `ValueError: SOS_LOCAL_DEV_MASTER_KEY is required for the local-dev key
  wrapper`. It is a fixture or ordering artefact of the `-k` selection, not a result of these
  changes. The PUT affected and the breach PATCH cases pass.
- `ruff check` and `ruff format --check` on the touched files; `mypy apps/api apps/worker
  evals`: no issues; `lint-imports`: 52 kept.
- Web (packages from `D:\school-os\node_modules` through a scratch vitest config, because a
  junction was refused):
  - vitest on `src/features/platform` and `src/features/breaches`: 210 passed;
  - `src/i18n/messages.test.ts`: passed (en and te keys match);
  - eslint and prettier on the touched files: clean;
  - `tsc --noEmit`: no errors in `src/`. The only errors are in `e2e/`,
    `playwright.config.ts` and `vitest.config.ts`, which need web-local packages that are not
    in the worktree.

Audit:

- `uv run pytest apps/api/tests/security/test_api_contract_guards.py
  apps/api/tests/security/test_route_enumeration.py
  apps/api/tests/security/test_rate_limit_routes.py
  apps/api/tests/security/test_no_pii_in_urls.py`: 34 passed.
