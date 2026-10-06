# Security audit 2026-10-06: every API route (api-routes) — CHECKPOINT, NOT FINAL

Branch `wip/sec3-api-routes` from `claude/friendly-ptolemy-0tl3br` (b2ac233). Round three,
route by route, against the OWASP API Security Top 10 (2023) and ASVS V4/V5/V13. The round-one
(`audit-2026-10-04-*.md`) and round-two (`audit-2026-10-05-*.md`) findings were read first and
are not repeated. Paths are relative to `apps/api/`. Synthetic data only.

**Status: work stopped at the owner's request.** The fixes below are committed with tests. The
remaining items are listed under "Open" and still need to be verified or fixed. The full checks
(whole pytest, mypy, lint-imports, OpenAPI regeneration, web typecheck/vitest) have **not** been
run yet.

## Method

- **Inventory:** 323 routes, generated from the live app (`app.routes`). The generator script is
  in the session scratchpad. The appendix table is still to be generated into this file.
- **Automated guards:** `tests/security/test_api_contract_guards.py` runs over every route:
  - `extra="forbid"`;
  - bounded strings, lists and maps;
  - `limit` at most 200;
  - UUID path parameters;
  - no secret or storage-named response fields;
  - Idempotency-Key on creating POSTs;
  - If-Match on versioned PUT/PATCH.

  Each exception is allowlisted with its reason.
- **Manual review:** six read-only reviewers, one per module group. Every candidate they raised
  is verified here before it is reported or fixed.

## Fixed (each with a test that failed first)

| ID | Severity | Ref | Fix | Commit | Test |
|---|---|---|---|---|---|
| R-02 | Low | API4 | Bounds on retention rule keys, export columns, insights rule keys (an unknown key of any length was echoed in the 422 field) and PlanPatch.features | ea0bf07 | `tests/security/test_api_contract_guards.py` |
| R-03 | Low (lost update; round-2 A-17) | API6, ASVS V11.1.4 | `PUT /users/{id}/roles` and `/scopes` take an optional If-Match. The membership is locked and its version bumped, so a stale list cannot restore a revoked role | 71b258e, 6f1b306 | `tests/api/test_users_roles_if_match.py` |
| R-04 | Low (round-1 AA-13 class) | API6 | Optional If-Match on price-override and ai-bundle PUT/DELETE and on flag PUTs (a stale form could re-enable a switched-off flag). GET /subscriptions/{id} now sends an ETag | e85a77e | `tests/platform/test_route_audit_concurrency.py` |
| R-05 | Low | API6, docs/09 §2 | Optional Idempotency-Key on platform break-glass requests, operator and school ticket replies, and knowledge memory items | e85a77e, 80c4e31, 57250d0 | same file; `tests/knowledge/test_memory_api.py::test_R_05_*` |
| R-06 | Medium-low | API1/API3 (BOPLA), SEC-015 | Import mapping: a column ever mapped to a C3 field stays restricted. Without `read_sensitive` it may only go to ignore or another C3 field (before: unmap it, then read or edit C3 values in the sheet) | 3365dc6, 57a819d | `tests/imports/test_sheet_editor.py::test_SEC_015_a_column_once_mapped_to_a_restricted_field_stays_hidden` |
| R-08 | Medium (invariant 4) | API10, invariant 4 | Tally agent text: Aadhaar-like numbers are masked, and control and bidi characters removed, from ledger, group and company names (before: stored and shown in clear) | a489477 | `tests/tally/test_agent_text.py` |
| R-09 | Low-medium (invariant 4) | invariant 4, PRV-015 | Document title, issuer and upload filename refuse a full Aadhaar number | e006850 | `tests/documents/test_title_aadhaar.py` |
| R-12 | Low | API8 | A NUL in any text gives 422 `invalid_characters` instead of a 500 (central handler). Control-plane free text (tickets, announcements, notes, flag description) refuses control characters | 9c0462d, 80c4e31 | `tests/core/test_errors.py`, `tests/platform/test_text_inputs.py` |

## Open (verified or candidate, not yet done)

- **R-07 (candidate, needs a fix):** `PATCH /users/{id}` profile edits (email, display name) of an owner or principal by an office admin skip the AA-02 reach guard (`identity/service.py` `update_user`).
- **R-10 (candidate):** Enrolment PATCH/end are not scoped to the enrolment's own section or year. This affects custom scoped roles only (`students/service.py` `_owned_enrollment`).
- **R-11 (candidate):** `DELETE /tally/parties/{id}/links/{student_id}` does not check that the student is visible (custom roles).
- **R-13 (candidate):** Several 500s where a 409 or 422 is expected:
  - PlanPatch with explicit nulls;
  - unbounded `included_students`;
  - invoice line overflow;
  - date-edge overflows;
  - concurrent exam create.
- **R-14 (reported):** Platform lists of deployments (one per school) are unpaged. The announcements and break-glass lists are cut at 200 with `next_cursor: null`. Fixing this changes the operator UI.
- **R-15 (reported):** Rotating a heartbeat key twice drops the key the host is still using. The answer is a one-time secret, so idempotency cannot replay it. Needs a design decision.
- **Notices (latent, Telugu hidden):** `*_te` text can be planted that no approver sees.
- **Needs decisions:**
  - Every staff role reads every school support ticket.
  - A billing-suspended school cannot be given a security hold (the reverse of A-08).
  - `/audit/verify` (tenant and platform) re-hashes the whole chain synchronously with no limit.
  - Notice drafts have no per-user AI admission (rate-limit area).
- **Hardening:**
  - Naive datetimes on the audit filters.
  - Client `X-Request-Id` is stored in audit records.
  - Enrolment and academic-year dates are not bounded.
  - DQ resolve accepts a change request in any state.
  - The Tally device-cap race.
  - Bidi characters in tenant free text.
- **Still to do:**
  - generate the inventory appendix;
  - run the full checks;
  - regenerate OpenAPI and the client (schemas changed: bounded keys and columns, new If-Match/Idempotency headers);
  - store the rememory decision.
