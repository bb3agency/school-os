# Security audit 2026-11-09: identity, authorization and the control plane (round 2, YI)

Auditor YI of the 2026-11-09 round, on a worktree at `fad0b02c`. Area, end to end:

- `app/identity`: OIDC principals, staff accounts, invitations (DL-09, ADR-0019/0023/0056),
  role and scope changes, MFA and step-up (ADR-0018, SEC-005);
- `app/authz`: `roles.yaml`, `permissions.yaml`, `require()`/`require_any()`, scopes, the
  resolver and its cache;
- `app/platform`: operators, two-person rules (SEC-029), provisioning, plans, subscriptions,
  invoices and payments, usage, fleet and the heartbeat (incl. the signed answer, ADR-0058),
  feature flags, announcements, support tickets, incidents and the `tenant_audit` outbox.

Earlier reports (`audit-2026-10-*.md`, `audit-2026-11-08-*.md`) were read first; their findings
are not repeated. CP-01, AZ-03 and TI-05 (fixed in round 1) were not redone. Paths are relative
to `apps/api/`. Synthetic data only.

## Findings

| ID | Severity | Area | File:line | Description | Status |
|---|---|---|---|---|---|
| YI-01 | Medium | Last owner (SEC-003, FR-IAM-014) | `app/identity/service.py` `_guard_last_owner` | The guard counted the school's active owners without any lock. Two owners suspending, removing or demoting **each other** in concurrent requests each saw two owners (each transaction locks only its own target membership), both committed, and the school was left with **no active owner** (write skew under READ COMMITTED). Without an owner nobody holds `assign_any_role`, so only SchoolOS support could restore access. The control plane already serialises its owner set (`platform.owner_set` advisory lock); the school side did not. | **Fixed** in 9f248c92. When the target is an active owner, a per-school transaction advisory lock (`repo.lock_owner_set`, key `sos:owner_set:<tenant>`) is taken and the owners are counted again; the second change waits and answers 409 `last_owner`. No schema or API change. Test: `tests/api/test_users.py::test_US_102_YI_01_two_owners_removing_each_other_at_once_keep_one_owner[suspend,demote]` (failed before the fix: the second change committed and no owner was left). |
| YI-02 | Low-Medium | Two-person rule (SEC-029), critical announcements | `app/platform/announcements.py` `approve`; `app/platform/api.py` `approve_announcement` | `POST /platform/announcements/{id}/approve` took no `If-Match`. The submitter could change a pending critical banner's text after the second operator opened it (an edit keeps it `pending_approval` under the same submitter), and the approval then published text the second person never saw, to every school in the audience. That defeats the purpose of the two-person rule for banners. | **Fixed** in 75cb7a4a, completed in b954f933 and a98d104e (lead decision 2026-11-09). `If-Match` on approve is **required** (400 `if_match_required`, as for the other platform updates since AA-13) and checked (412 when stale, nothing approved); the answer carries the new `ETag`. The operator console (`AnnouncementsView.tsx`) sends `If-Match` with the version read when the approve dialog opened (hidden field, as `BillingViews` does). Web and API ship in the same release on the shared tier. **API schema change**: OpenAPI and the TS client regenerated in b954f933. Tests: `tests/platform/test_two_person_requests.py::test_SEC_029_YI_02_approval_with_a_stale_etag_is_refused` (failed before the fix: the stale approval returned 200), `apps/web/src/features/platform/two-person.test.tsx` "approves the version on screen: If-Match carries it" (failed before: no `If-Match`). |
| CP-02 | Low | Fleet key rotation (SEC-028) | `app/platform/fleet.py` `accept_heartbeat` | Round-1 lead follow-up: after a rotation the old heartbeat key stayed valid for the whole 7-day overlap even once the host signed with the new key, and a second rotation was refused meanwhile, so a leaked key that caused the rotation kept working for up to 7 days. | **Fixed** in 67429cb0. The first accepted heartbeat signed with the next key promotes it under the deployment lock (audit `deployment.heartbeat_key_promoted`, system); the old key stops working at once and a new rotation is accepted straight away. R-15 stays safe (the host already uses the promoted key). docs/16 §7, §12.2, §12.3 and §16 updated. No schema or API change. Test: `tests/platform/test_heartbeat.py::test_SEC_028_CP_02_first_heartbeat_with_the_new_key_ends_the_old_key` (failed before the fix). |
| CP-03 | Low (hardening) | Heartbeat signatures (ADR-0058) | `app/platform/fleet.py` `sign`; `app/platform/heartbeat_client.py` `verify_answer` | Round-1 lead follow-up: request and answer share the MAC `HMAC(key, ts "." body)` with no direction label. Reflection is blocked today only by the two schemas (`HeartbeatOut` needs `received_at`/`min_supported_version`/`announcements`/`request_nonce`; `HeartbeatIn` is `extra="forbid"`). | **Fixed** in d68336e8 (lead decision 2026-11-09; ADR-0058 amendment). Both directions add `X-SOS-Signature-V2: v2=HMAC(key, label "." ts "." body)` with `req` / `ans` and keep sending `v1`, so peers on the previous release still verify. Verifiers check v2 when present (a wrong v2 is refused, never retried as v1); v1 alone is accepted while `SOS_HEARTBEAT_ACCEPT_V1_SIGNATURE` (new, default `true`; docs/10 §11, `.env.example`), to be set `false` on both sides once every host runs this release. docs/16 §12 updated. Test: `tests/platform/test_heartbeat_answer.py::test_SEC_028_CP_03_requests_and_answers_carry_a_direction_labelled_v2_signature` (request and answer carry both headers; a request with the `ans` label, an answer with the `req` label and a v1-only signature with v1 switched off are refused). The existing answer-refusal tests now strip or forge both headers. |
| YI-03 | Low (design; needs owner decision) | Two-person rule (SEC-029) | `app/platform/two_person.py` `check_second_step` | The independence check looks only one grant deep: an approver is refused when the requester granted their role (or the reverse), but not across a chain. Owner A can make account X an owner, X makes Y an owner, and after 7 days Y can approve A's offboarding, emergency break-glass, incident confirmation or critical banner. It needs IdP accounts in the operator pool and two 7-day waits, all audited in the platform chain. | **Accepted risk** (lead decision 2026-11-09): a deeper ancestry check would make the two-person rule impossible to satisfy for a small operator team where one owner invited everyone. The one-level check plus the 7-day cooling period stays; every grant and approval remains in the platform audit chain. |

## Verified clean

**Identity and authz**
- Role grants: `_guard_grantable` lets nobody grant or revoke a role carrying a permission they do not hold (only `assign_any_role`, the owner, may). Invites of privileged (MFA) or custom roles need `role.assign` plus that rule; a `school` scope on a scoped role needs `role.assign` (A-19). Suspending, removing, reactivating or editing the profile of a privileged member follows the same reach rule (R-07). `platform_support` cannot be granted outside break-glass. There is no route to edit a role's permissions (`roles.yaml` and the sync job only), so role-edit escalation is not reachable.
- Own account: nobody suspends or removes their own membership (409 `own_account`). Demoting oneself is allowed only while another owner remains (and now serialised, YI-01).
- `set_roles` and `set_scopes` lock the membership row and bump its version; `If-Match` is checked when sent (A-17). `PATCH /users/{id}` requires `If-Match`.
- MFA for privileged memberships is derived from the roles at resolution time (`resolver.py`), so adding a privileged role later cannot skip FR-IAM-002.
- Invitations: the three `SECURITY DEFINER` functions (0045, 0078) answer only `status = 'invited'`, unexpired, younger than 30 days, for the verified `(issuer, subject)`; the `UPDATE` re-checks the row after a concurrent answer, so an invitation is answered once. Sign-in acceptance takes only a brand-new account's only invitation (DL-09); support sign-ins are 403 `breakglass_only`.
- Step-up: every mutating route whose permission is step-up in the catalogue declares it (`tests/security/test_route_enumeration.py::test_SEC_005_step_up_flags_match_catalog`); `require_recent_auth` needs the MFA claim and `auth_time` within 300 s (30 s future skew). Platform routes follow the catalogue unless explicitly downgraded on reads.

**Control plane**
- Two-person flows (offboarding approve, emergency break-glass, incident confirm and reopen, critical announcements): each checks `same_operator` under the row lock, then `check_second_step` (72 h expiry, requester still active and entitled, approver role 7 days old and not granted by the other). Identity is the operator id, so a second session of the same operator is the same operator. Adding a school to an incident clears a pending first confirmation.
- Operators: own roles and own deactivation refused; the last platform owner is kept under the `platform.owner_set` lock; kept roles keep their original grant time and grantor (A-14).
- Invoices: numbers come from a row-locked per-financial-year sequence in the issuing transaction (gapless); recipient details and totals are recomputed at issue; a non-positive total is refused; payments are row-locked against the invoice and cannot exceed the balance; only an issued, unpaid invoice is voided.
- Heartbeat: ±300 s skew, nonce TTL 600 s, per-deployment rate, constant-time HMAC, strict schema, ids bound to the registry; the answer is signed with the verifying key and echoes the nonce. Usage only for today/yesterday IST.
- Invariant 1 / §11: `tests/platform/test_boundaries.py` pins the platform module's tenant-side imports and `tenant_session` users; the changes here add none.

## Outside my area

None found.

## Schema and API changes

- No migration.
- YI-02: `POST /api/v1/platform/announcements/{announcement_id}/approve` requires `If-Match` and answers an `ETag`; `apps/api/openapi.json` and `packages/api-client` regenerated.
- CP-03: new setting `SOS_HEARTBEAT_ACCEPT_V1_SIGNATURE` (default `true`) and the `X-SOS-Signature-V2` header on the heartbeat and its answer; the heartbeat route's docstring (OpenAPI) mentions it.

## Checks run

- `uv run pytest apps/api/tests/api/test_users.py apps/api/tests/api/test_users_own_and_shared.py`: 36 passed (YI-01 failed before the fix: `'done' == 'last_owner'`, both parametrisations).
- `uv run pytest` on `tests/platform/test_heartbeat.py`, `test_heartbeat_answer.py`, `test_two_person_requests.py`, `test_announcement_edit.py`, `test_if_match_required.py`, `test_boundaries.py`: 88 passed (CP-02 failed before the fix: `DID NOT RAISE Unauthenticated`; YI-02: `assert 200 == 412`).
- `uv run pytest tests/security/test_route_enumeration.py tests/security/test_api_contract_guards.py`: 21 passed.
- `ruff check` and `ruff format --check` on the touched files: clean.
- `uv run mypy apps/api apps/worker evals`: no issues in 1244 files.
- `uv run lint-imports`: 52 contracts kept.

Second pass (lead decisions 2026-11-09: YI-02 required, CP-03 built, YI-03 accepted):

- `uv run pytest tests/platform/test_two_person_requests.py test_if_match_required.py test_announcement_edit.py test_school_side.py`: 47 passed.
- `uv run pytest tests/platform/test_heartbeat.py test_heartbeat_answer.py`: 41 passed.
- `uv run pytest tests/core/test_config.py tests/deploy`: 438 passed; the OpenAPI freshness tests (`-k openapi`): 2 passed.
- `npx vitest run src/features/platform/two-person.test.tsx src/features/platform/announcements.test.tsx`: 25 passed (the new If-Match test failed first).
- `tsc --noEmit`, `eslint` and `prettier --check` on the touched web files: clean.
- `ruff check`, `ruff format --check`, `mypy apps/api apps/worker evals` (1244 files), `lint-imports` (52 kept): clean.
