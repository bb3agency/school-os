# Audit 2026-11-09: messaging, notifications, circulars and the public surface (YM)

Base `fad0b02c`. Round 2, per module. Scope: `app/messaging` (connect, template catalogue,
opt-ins, join slips, STOP, the Meta webhook and inbox, sends, daily cap, delivery log),
`app/notifications` (in-app, e-mail, templates, `quota_sync`), `app/circulars` (task reminders,
parent notice drafts and renders) and `app/public` (handles, parent links, the three guards,
`public_routes.yaml`), with their tasks. The round-1 reports were read first. BL-01/03/05 (claim
clock, quiet hours, cap under the advisory lock), AZ-02 (failed-render cooldown) and the CP
"verified clean" list are not repeated.

**Method.** Each finding below has a test that failed before the fix and passes after it.
Synthetic data only. Paths are relative to `apps/api/`.

**Round-1 lead follow-up: Meta webhook timestamp.** Decided and fixed (YM-02, YM-03). A Meta
body has no signed delivery timestamp, but each message and status carries Meta's own
`timestamp`, and that value is covered by the body signature. Meta retries a failed delivery for
up to 7 days. Events older than 192 h are therefore dropped at intake, which leaves no replay
gap: the guard's body-hash window is 72 h, and the inbox dedupe by event key lasts 30 days after
processing. A config validator keeps the age limit below the dedupe retention. A replayed JOIN
was already harmless, because join codes are single use. The real ordering risk was a late JOIN
arriving after a STOP (YM-02).

## Findings

| ID | Severity | Area | File | Description | Status |
|---|---|---|---|---|---|
| YM-01 | **Medium** | Meta webhook (FR-MSG-020, FR-MSG-010) | `app/messaging/api.py` `meta_webhook` | The guard claims the body hash in the replay store before the route stores the events. If `store_events` failed (database error, 5xx), the claim stayed. Meta's retry of the same body then counted as a duplicate and got a 200 with nothing stored for 72 h. A parent's STOP was lost, so messages kept going to a parent who had opted out. The Razorpay route already released the claim (`forget_event`); the Meta route did not. | Fixed in b70affea. The route calls `forget_event("meta", event_id)` on any exception and re-raises. Schools stored earlier in the same body dedupe the retry by event key. Test: `tests/messaging/test_webhook_forgery.py::test_YM_01_FR_MSG_020_a_delivery_not_stored_is_not_a_replay_when_meta_retries` |
| YM-02 | **Medium** | Opt-in ordering (FR-MSG-009, FR-MSG-010) | `app/messaging/service.py` `_join`, `_apply_inbound` | Meta does not guarantee delivery order. Suppose a parent sent `JOIN <code>` and then `STOP`, and the JOIN delivery arrived late (a Meta retry) after the STOP was applied. The JOIN then opted the parent back in. The inbox sorts by `event_at` only within one batch. | Fixed in b70affea. `_join` gets the event's Meta timestamp. If the guardian's current decision for a category is a `whatsapp_stop` recorded after that time, the outcome is `join_before_stop`: nothing is recorded, no reply is sent, and the code stays unused. A JOIN sent after the STOP still works. Edge case: a JOIN sent in the few seconds between a STOP and the processing of that STOP is refused, and the parent can send it again. Test: `test_webhook_forgery.py::test_YM_02_FR_MSG_010_a_join_sent_before_the_parents_stop_does_not_opt_back_in` |
| YM-03 | Low-Medium | Webhook replay (SEC-034, FR-MSG-020) | `app/messaging/service.py` `store_events` / `_fresh_events`; `messaging/config.yaml` `inbox.max_event_age_hours` | The round-1 lead. A captured, validly signed body replayed more than 30 days after processing passed the 72 h replay window and the purged inbox dedupe, and was applied again. A replayed STOP undid a later signed-form opt-in and sent the parent a fresh "stopped" confirmation. Replayed inbound messages also triggered auto-replies. | Fixed in b70affea. Messages and statuses whose Meta timestamp is older than `inbox.max_event_age_hours` (192 h, validated ≥ 168 and < `provider_events_days`×24) are dropped and counted (`messaging.webhook.stale`, counts only), including while the school is suspended. Template and account events carry no timestamp and are kept, since they only mirror Meta's state. Test: `test_webhook_forgery.py::test_YM_03_FR_MSG_020_a_signed_delivery_older_than_metas_retry_period_is_dropped` |
| YM-04 | Low-Medium | Opt-in after STOP (FR-MSG-008, FR-MSG-010) | `app/messaging/service.py` `record_optin` | After a parent's WhatsApp STOP, staff may opt the parent back in only with a new parent action (a signed form). `decided_on` may be back-dated up to 400 days (`max_decision_age_days`). A clerk could therefore upload a form the parent signed **before** the STOP and override it. | Fixed in b70affea. A `signed_form` opt-in dated before the STOP's `decided_on` is refused with 409 `opted_out_by_parent`. A form dated on or after the STOP day is still accepted. Test: `tests/messaging/test_api.py::test_YM_04_FR_MSG_008_a_signed_form_dated_before_the_parents_stop_does_not_override_it` |
| YM-05 | Low | Template eligibility (FR-MSG-006, D32) | `app/messaging/eligibility.py` `template_usable` | A template whose `category` Meta had not reported yet (`None`) was treated as utility. Every catalogue template is utility by construction, and provisioning stores `UTILITY` when Meta reports nothing. This was still a fail-open rule for D32 (utility only). | Fixed in 63797901 (lead decision: fail closed). Only `UTILITY` (any case) is usable. A missing category gives `template_not_approved`. Tests: `tests/messaging/test_catalogue_and_rules.py::test_YM_05_D32_FR_MSG_006_only_a_template_meta_reports_as_utility_is_usable` and two new rows in `test_FR_MSG_011_eligibility_matrix` |
| YM-06 | Low | E-mail delivery (FR-NOT-001) | `app/notifications/service.py` `_deliver`, `tasks.send_email`; `email.py` `SesEmailSender` | `notifications.send_email` (`acks_late`) relied on the Valkey event lease of `TenantTask`. That lease ends after 2 h and fails open. A worker that died after SES accepted an e-mail, or a lost lease, let a redelivery send the same invitation or 100 % alert again. The SES sender also mapped every transport error (including a read time-out after the request left) to a retryable error, so a retry could send twice. | Fixed in 63797901 (lead decision: at most once). Migration `0084_audit_ym_fixes` adds `ops.outbox.consumed_at`, with a column `UPDATE` grant to `sos_app` (expand only, with a downgrade). The worker claims its outbox event in its own committed transaction (`ops.claim_event_consumer`) right before the provider call. A later run of the same event answers `already_sent` without sending, and logs `notifications.email.skipped` with ids only. A definite refusal (`EmailUnavailable`: an SES error answer, or a connection that never opened) releases the claim, so the retry sends. A possible send (`EmailOutcomeUnknown`: a read time-out or a dropped connection) keeps the claim; the task logs `notifications.email.outcome_unknown` and does not retry. A permanent refusal keeps the claim. An event row that is missing (purged 30 days after delivery) cannot be claimed and is sent as before. Tests: `tests/notifications/test_invitation_email.py::test_YM_06_FR_NOT_001_an_email_is_sent_at_most_once_per_outbox_event`, `test_YM_06_an_unknown_outcome_from_ses_is_not_retried` |
| YM-07 | (withdrawn) | Task reminders (FR-TASK-007) | `app/circulars/service.py` `send_reminders` | First reported as: a task reassigned after its reminder went out does not remind the new owner. Testing showed this is not a defect. The notification dedupe is unique per `(tenant, recipient, dedupe_key)` (`notifications/repository.py` `insert_new`), so the new owner gets the reminder once. The key was left unchanged: adding the owner would remind every task again once after deploy. | Not a defect. Regression test added: `tests/circulars/test_api.py::test_YM_07_FR_TASK_007_a_reassigned_task_reminds_its_new_owner` |

**Schema / API.**
- Migration `0084_audit_ym_fixes` (down_revision `0081_audit_bl_fixes`; the lead re-chains) adds the nullable column `ops.outbox.consumed_at` and `GRANT UPDATE (consumed_at) ON ops.outbox TO sos_app`. It is expand only; the downgrade revokes the grant and drops the column.
- Updated with it: `tests/extraction/test_migration.py` revision set, the CLAUDE.md §4 range and the docs/05 migration chain.
- No OpenAPI change. YM-04 reuses the existing 409 code, and YM-06 is worker-only.
- New config key: `messaging/config.yaml` `inbox.max_event_age_hours`.
- Outside the four modules, `app/ops` gains `claim_event_consumer` and `release_event_consumer` (service) and `claim_consumer` and `release_consumer` (repository) for the YM-06 claim.

## Verified clean

- **Public guards:** the BFF service token is required and a user token is refused. The
  failed-lookup budget is checked before any lookup and charged with cost 2 on a miss. The
  shape check runs before the hash lookup, and every kind is asked in one statement (constant
  work). Unknown, expired, revoked and wrong-kind keys get the same 404. Each handle is re-read
  under RLS after the resolve, so a revocation wins. The `public_pay` budget is charged only
  for new links, and the redirect host is allowlisted.
- **Parent pages:** receipt, dues and payment pages check that the link's guardian is still
  linked to the student (BOLA across guardians). The opt-in page points at its own guardian
  only and never records an opt-in or opt-out. Every response is an explicit allowlist model.
- **Meta webhook:** constant-time HMAC over the raw body happens before any WABA lookup.
  Unknown WABAs are dropped with a 200. Events are routed per WABA to the owning school only,
  and the account and handle are re-read under RLS. Inbound text is classified and discarded
  (FR-MSG-018), and profile names are dropped. The verify token is compared in constant time
  and the challenge is pattern-checked. The 3 MB limit is enforced while streaming.
- **STOP honoured before every send:** `deliver` re-reads the current opt-in and phone under
  the message's row lock at send time (`rules.skip_reason`). STOP revokes the guardian's links
  at once, including while the school is suspended (D35), and sends no prompt (D32). A staff
  opt-in after STOP needs a parent action (now also dated after the STOP, YM-04). JOIN codes
  are single use, hashed, expire after 30 days and must come from the guardian's own number.
- **Inbox:** `pending_events` uses `FOR UPDATE SKIP LOCKED`, and each event runs in a
  savepoint with bounded attempts. Statuses only move forward. Confirmations are one-shot
  (`retry=False`), and auto-replies are capped per number per IST day.
- **Sends:** the round-1 fixes (BL-01/03/05) hold. A link token is re-issued on each claim,
  and the old one is revoked on retry, skip or failure. Revoking a link needs the school-wide
  `message.send` permission and is audited.
- **Notifications:** every read and write is filtered by `recipient_membership_id`. Inserts
  dedupe by `(tenant, recipient, dedupe_key)`, so `quota_sync` reruns add nothing. E-mail
  tasks carry IDs only.
- **Circulars tasks:** read, draft and render are gated by status (idempotent on retry), with
  bounded retries and an abandon step at the last attempt. Reminders are per school in their
  own `tenant_session` (active schools only) and deduped per task and due date.

## Outside my area

None found.

## Checks run

- After all fixes, one fresh run of `pytest tests/messaging tests/notifications tests/circulars tests/public tests/ops tests/extraction/test_migration.py tests/security/test_rls_catalog.py tests/security/test_app_column_grants.py`: 730 passed, 3 skipped (headless Chromium is not installed for the notice render tests).
- `ruff check` and `ruff format --check` on the touched files: clean.
- `mypy apps/api apps/worker evals`: clean.
- `lint-imports`: 52 contracts kept.
