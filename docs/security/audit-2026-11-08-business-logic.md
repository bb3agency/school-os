# Audit 2026-11-08: business logic, concurrency and background jobs (BL)

Base `987ded97`. Scope: fees (ledger, receipt and voucher numbering, maker-checker, day close,
late fees, rounding, Razorpay inbox and reconciliation, UPI statements), certificates
(serials, one TC), change requests, the import revert, attendance and marks writes, WhatsApp
send runs, outbox and Celery retries, audit in the same transaction, IST against UTC, and
Tally dues (D54 shared ledgers, D63 bills). The audits of 2026-10-05 (app logic) and
2026-10-17 (logic) were read first. Their fixed items are not repeated here.

**Method.** Each finding below has a test that failed before the fix and passes after it. The
race tests use two sessions: a thread, or a nested run inside the fake provider call. Data is
synthetic only. Paths are relative to `apps/api/`.

## Findings

| ID | Severity | Area | File | Description | Status |
|---|---|---|---|---|---|
| BL-01 | **Medium** | messaging sends | `app/messaging/sends/service.py` `_claim`, `send_due` | A run reads its clock once, at the start. It then claims each message for `now + claim_seconds` (300 s). A run of up to 300 messages, with provider calls that can take up to 10 s each, reaches its last messages after that claim has already run out. The sweep a minute later then finds the message due, claims it again and calls Meta while the first run is still sending it. Two things follow. The parent gets the same message twice (a notice, a receipt or a fee reminder). The second claim also revokes the parent link token that the first message carries. | Fixed in 4321f174 and dafb8426. Test: `tests/messaging/test_sends.py::test_BL_01_FR_MSG_016_a_claim_late_in_a_long_run_is_not_sent_twice` |
| BL-02 | **Medium** | imports, students | `app/students/service.py` `plan_import_revert`; `repository.student_versions` | The revert read the created students' versions, and the batch's values, without a lock. A clerk might save a value on an imported student in a transaction that commits while the revert runs. The revert's `DELETE` then waits for that row and goes ahead, so the new value is deleted with the student and nobody is told. A value that replaces a batch value has the same race: the revert would restore the prior value over it. | Fixed in a45865a4. The planner now locks every student the batch touched (`FOR UPDATE`, in id order) before it checks them. Every value write locks its student first, so a concurrent write is either seen (409 `import_has_dependents`) or waits for the revert. Test: `tests/imports/test_commit_revert.py::test_BL_02_FR_IMP_005_a_value_saved_while_the_revert_runs_is_not_lost` |
| BL-03 | **Low-medium** | messaging sends | `app/messaging/sends/service.py` `_claim` | The same stale clock as BL-01 also decides quiet hours and the daily cap. A run that starts at 20:59 IST still sends the messages it reaches after 21:00, which breaks FR-MSG-011. A run that starts just before IST midnight counts its cap against the previous day. | Fixed in dafb8426. `_claim` now advances the run's clock by the database time that has passed since the run started, so tests can still pin the clock. Test: `tests/messaging/test_sends.py::test_BL_03_FR_MSG_011_a_run_that_reaches_a_message_in_quiet_hours_defers_it` |
| BL-04 | Low | fees concessions | `app/fees/approvals.py` `request_concession`, `approve_concession` | The room for a concession is charged less earlier concessions, and payments are not counted. A concession approved on an instalment that is already paid therefore leaves a negative due (a credit) that no screen shows and no refund offers. | Fixed in 12c6281b (lead decision 2026-11-08). A concession larger than what is still due on a line is refused with 422 `concession_exceeds_due` on `value`, both when it is asked and again when it is approved, because a payment can land in between. Money already paid goes back only through a refund. The field is `value` because that is the request's amount field, so the form shows the error under it. en/te `errors.field.concession_exceeds_due` are added and listed in the fees vitest. Test: `tests/fees/test_service.py::test_BL_04_FR_FEE_008_a_concession_may_not_take_a_due_below_zero` |
| BL-05 | Low | messaging sends | `sends/service.py` `_claim`; `sends/repository.py` | The daily cap counted only messages already `sent`. Two runs for one school at once could each pass the cap check with a message in flight. | Fixed in c626ba41 (lead decision). A claim sets `msg.messages.claimed_until`, and the cap counts sent template messages plus live claims. The check and the claim run under the per-school transaction advisory lock `pg_advisory_xact_lock(hashtextextended('messaging.cap:' || tenant_id, 0))` (`repository.lock_cap`). A transient retry clears the claim. Migration `0081_audit_bl_fixes`. Test (two sessions): `tests/messaging/test_sends.py::test_BL_05_FR_MSG_011_two_runs_at_once_cannot_both_pass_the_daily_cap` |
| BL-06 | Medium | online payments | `app/fees/online.py` `connect_manual` | Reported by the control-plane auditor. In production, manual-keys connect accepted a Razorpay test key (`rzp_test_`). A test key takes no real money, yet parents would "pay" and receipts would be issued for nothing. | Fixed in 09bcc5a8, 729e444c and 3ac4b848. With `SOS_ENV=prod`, a key id that is not `rzp_live_` is refused with 422 `test_keys_in_prod` on `key_id`, before any provider call. Staging and local are unchanged. en/te field messages are added. Test: `tests/fees/test_online_connection.py::test_BL_06_FR_PAY_003_test_keys_refused_in_production` |
| BL-07 | Low | Tally dues (privacy) | `app/tally/service.py` `list_dues`, `student_fee_dues` | `shared_with` counted every student linked to a family ledger. A finance reader whose student scope is narrower than the school (custom role) therefore learnt how many siblings exist outside that scope. The Ask tool `get_fee_dues` had the same leak. | Fixed in 88cb0eed. Only siblings the caller can see (`students.summaries` under the caller's scope) are counted. `shared_ledger` and a bill's `shared` stay as they were (D54). The `shared_with` field description is updated. Test: `tests/tally/test_api.py::test_BL_07_FR_TALLY_007_shared_with_counts_only_siblings_the_caller_can_see` |

## Checked and not a bug

- **Receipt and voucher numbers.**
  - `allocate_number` is an `INSERT … ON CONFLICT DO UPDATE` taken last in the transaction, so numbers stay gap-free under concurrency.
  - The UTR has its own advisory lock. The day close takes the day lock exclusively, while receipts, cancellations and refunds take it shared.
  - A deferred trigger checks that allocations and payments add up.
- **Online payments.**
  - `post_payment` locks the account, then the intent. It re-checks `receipt_for_payment` under that lock, so a webhook replay and the reconciliation run make one receipt.
  - Amount, currency, intent already paid, and a refund before the receipt each open a deduplicated exception.
  - `apply_refund` re-reads the refund row `FOR UPDATE` (`populate_existing`), so the webhook and the poll write one voucher.
  - A capture on a closed day gets a receipt dated today. If today is closed too, it is retried.
- **Maker-checker.** Status pending, not the requester, If-Match and step-up are all checked on approval. Approvals lock in the order request, receipt, account, the same order as the counter, which locks the account only. A cancellation is refused once a refund is committed, and a refund is refused after a cancellation.
- **Late fees.** These are keyed and unique (`ledger_entries_one_late_fee`) with `ON CONFLICT DO NOTHING`, and never compound. Only charges and late fees have unique keys, so payment and refund entries are never dropped silently.
- **Money.** Decimal throughout with no floats, `ROUND_HALF_UP` for percentages, and the financial year starts in April.
- **Certificates.** The serial counter row is locked until commit. `certificates_one_live_tc` allows one live TC per student. A duplicate copies the original's frozen content.
- **Change requests.** The student is locked before `old_value_id` is compared. Expiry is a timestamp.
- **Messaging.**
  - Each automatic run is claimed once per IST day and queues in the same transaction.
  - Dedupe keys work per message: the date for absences, instalment and offset per account for fee reminders, and one per receipt and guardian for receipts.
  - `deliver` re-checks `queued` under a lock.
- **Tally.**
  - Syncs serialise on the company's party rows. A shared ledger counts once in the school total and once in each class total (D54). Bills are replaced per ledger (D63).
  - Every party of other companies is marked missing, but only one company can be selected (`_check_snapshot`), so this is by design.
- **IST.**
  - Fees `c.today`, messaging `ist_today` and `ist_midnight`, Razorpay day bounds, and the beat at 19:00 UTC (00:30 IST) are all correct.
  - The UTC dates in `audit/` are the audit partition days, by design.
- **Audit.** Every change path read here calls `c.record` or `audit.record` in the writing transaction.

## Outside my area

- None left open: the Tally `shared_with` note became BL-07 (fixed).

## Schema and API

- **Migration** `0081_audit_bl_fixes` (down_revision `0079_audit_outbox_set_once`; the lead re-chains): adds the nullable column `msg.messages.claimed_until` with a column `UPDATE` grant to `sos_app`. It is expand only, and the downgrade drops it. `tests/extraction/test_migration.py`, the CLAUDE.md §4 range and docs/05 are updated.
- **API:** there are new 422 field codes, `concession_exceeds_due` (on `value`) and `test_keys_in_prod` (on `key_id`), and the description of the Tally `shared_with` field changed. Regenerate OpenAPI and the TS client: the description changes the schema text, while the shapes are unchanged.

## Verification

- `ruff check` and `ruff format --check` on the touched files: clean.
- `uv run mypy apps/api apps/worker evals`: no issues (1,241 files).
- `uv run lint-imports`: 52 contracts kept.
- `pytest apps/api/tests/messaging/`: 165 passed.
- `pytest apps/api/tests/imports/test_commit_revert.py`: 19 passed.
- The BL-01 to BL-07 tests each failed on the code before the fix.
- Follow-ups:
  - `pytest apps/api/tests/fees`: 309 passed.
  - `pytest apps/api/tests/messaging`: 166 passed.
  - `pytest apps/api/tests/tally`: 74 passed.
  - The security suites `test_bola.py` and `test_authz_matrix.py` with `-k tally`: 127 passed.
  - `pytest test_app_column_grants.py`: 13 passed.
  - `pytest tests/extraction/test_migration.py` and `tests/messaging/test_migration.py`: 24 passed.
  - Web vitest: `src/features/fees/fees.test.tsx` 17 passed, `src/i18n/messages.test.ts` passed.
  - `src/i18n/languages.test.ts` (Telugu font route) fails in this worktree because no font assets are installed here. It is unrelated to these changes.
