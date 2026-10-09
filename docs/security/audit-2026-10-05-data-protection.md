# Security audit 2026-10-05: data protection (crypto, privacy, data lifecycle, AI)

Auditor B of the second round (wave 5). Branch `wip/sec2-data-protection` from
`claude/friendly-ptolemy-0tl3br` (0236299). Scope: envelope encryption and per-tenant keys,
crypto-shredding at offboarding, the audit hash chain, DPDP rights, Valkey contents, and the AI
pipeline against the OWASP Top 10 for LLM Applications (2025). The round-one findings
(`audit-2026-10-04-*.md`) were read first and are not repeated unless a fix was incomplete.

Every fixed finding has a test that failed before the fix and passes after it. Paths are relative
to `apps/api/` unless they start with `apps/` or `docs/`. Synthetic data only.

## Findings

| ID | Severity (CVSS 3.1, why) | Reference | File:line | Exploit path | Status | Proving test |
|---|---|---|---|---|---|---|
| DP-01 | **Medium**, 4.4 `AV:N/AC:H/PR:H/UI:N/S:U/C:N/I:H/A:N`: needs code running with the app's database rights, but then forges audit history that every control reports as intact | ASVS 4.0.3 V7.3.3 (L3), V7.3.4; CWE-345, CWE-1390 adjacent (weak integrity binding); OWASP A08:2021 | `migrations/versions/0002_audit.py` (grants: `sos_app` INSERT on `audit.events`, UPDATE on `audit.chain_heads`; `sos_platform` the same on the platform chain), `app/audit/service.py:197` (`_check_row`), `app/audit/archive.py:131` | See note DP-01. A writer with `sos_app` rights (SQL injection, a compromised task, a bug) appends a correctly hashed event dated three days ago ("tenant export downloaded by user X") with `seq` = head + 1, then advances the head. The verification walks seq, prev_hash and hash only, so `verify_chain` returns `ok`; the day's signed archive is already under Object Lock and never contains it; the in-app viewer shows the fabricated event on that day. The same role could also rewind or skip the head (detected only at the next daily run). | **Fixed** in 58b2aef (tests tidied in 856e307), migration `0046_audit_append_guard`: for the writer roles only, the database accepts an event only as the next link (seq = head + 1, prev_hash = head hash) and with `occurred_at` within 5 minutes of the database clock; the head moves forward by exactly one, onto the event just written; a new head is the genesis head. Same for `sos_platform` on the control-plane chain. Owner and superuser sessions are not checked (restore drills; covered by the signed archive). | `tests/audit/test_app_role_forgery.py` (8 tests: backdated, future-dated, duplicate seq, gap, head rewind/skip/hash change, platform backdated, platform rewind; `record()` still appends) |
| DP-02 | **Low-Medium**, 3.5 `AV:N/AC:H/PR:L/UI:R/S:C/C:N/I:L/A:N`: indirect prompt injection reaches parents through a human who cannot see the hidden text | OWASP LLM01:2025 (indirect prompt injection), LLM05:2025 (improper output handling); ASVS V5.3.1; CWE-74 | `app/knowledge/circulars/notice.py:138` (`_field`), `app/knowledge/circulars/reading.py:252` (`_summary`) | Any `document.upload` holder (or an outside body whose circular the office uploads) plants white or tiny text in a circular PDF: "Parents must pay the ₹500 verification fee at https://fees-verify.example". The `parent_notice` model drafts the notice from the indexed passages and copies the link. The prompt says "Do not add links", but nothing enforced it: `validate_notice` only redacted phones, emails and Aadhaar numbers. Staff review a plausible draft (the hidden text is not visible in the PDF) and post it to the parents' groups. The circular summary had the same gap (staff-facing, copied into tasks, see DL-08). Ask answers already strip links (SEC-019). | **Fixed** in 8a5b58e: notice fields and circular summaries now lose HTML tags and links (Markdown links keep their label; bare `http(s)://`, `ftp://` and `www.` addresses are removed), with the same rules as `answer.sanitise`. Staff can still type a link while editing the draft. | `tests/knowledge/test_circular_reading.py::test_SEC_019_links_in_a_drafted_parent_notice_are_removed`, `::test_SEC_019_links_in_a_circular_summary_are_removed` |
| DP-03 | **Low**, 2.7 `AV:N/AC:H/PR:H/UI:N/S:U/C:N/I:L/A:N`: integrity evidence gap, no attacker needed | ASVS V7.3.3; DPDP Rules r.8 (evidence of erasure); FR-AUD-004, FR-PLT-005 | `app/audit/verify_all.py:29` (`TENANT_STATUSES`), used by `app/audit/tasks.py` (`archive_daily`, `verify_all_chains`) | The daily chain verification and the signed Object Lock archive listed only `active` and `suspended` schools. Every event written while a school is `offboarding` (`tenant.data_purged`, `tenant.keys_destroyed`: the evidence that the deletion happened, kept a year by ADR-0029) or `provisioning`, and the chain of a `deleted` school during its retention, were never verified and never archived. A tampered or truncated offboarding chain (round-one hardening 3, by a DBA) went unnoticed. | **Fixed** in 3916697: every status that can hold a chain is verified and archived; a purged chain verifies as empty and archives nothing. | `tests/audit/test_verify_all_and_tasks.py::test_SEC_007_every_school_that_can_hold_a_chain_is_verified_and_archived` |
| DP-04 | **Medium** (compliance; no exploit) | DPDP Act 2023 s.11 (access), s.12 (correction and erasure), s.13 (grievance); DPDP Rules r.14; NFR-PRV-004; docs/08 §2 PRV-010 | No implementation: no per-student data report, no erasure workflow for a student's record, no grievance log (`grep -ri grievance apps/api/app` finds nothing) | docs/08 PRV-010 promises "admin tools: per-student data report, correction via change requests, erasure workflow, grievance log", and NFR-PRV-004 "access, correction, erasure via school admin tools within 7 days". What a request actually does today: **access** = staff copy fields by hand, or the owner runs the whole-school export (`tenant.export_all`) and filters it; **correction** = change requests (built, maker-checker); **erasure** = only a behaviour note or insight flag (principal), a guardian link, an import revert within 24 h, retention jobs for working data, or the whole school at offboarding. A student record cannot be erased, and there is no record of who asked, when and what was answered. | **Left open:** a legal DPDP item (data-principal rights on a statutory register); the policy is not invented in code (owner, 2026-10-07, wave 6). Open questions: what a school may erase from an admission register (a statutory record) versus derived data (Ask answers, documents, notes), who handles the request, and the 7-day clock. Recommendation: a "data-principal request" register (requester relationship, type, received/answered dates, outcome, IDs only) with a per-student report (every value, source and document link of one student, audited, C3 masked unless `student.read_sensitive`) and an erasure of the derived and optional data (guardian contact, documents, notes, Ask answers that quote the student) that leaves the admission register row with a `erasure_requested` finding. DPDP duties start mid-May 2027. | none (decision first) |
| DP-05 | **Low** (compliance; no exploit) | DPDP Act s.9 (verifiable parental consent for children), DPDP Rules Fourth Schedule Part A; PRV-006 | No consent model anywhere (`grep -ri consent apps/api/app` finds only the staff invitation consent of 0045) | docs/08 relies on the Fourth Schedule exemption for educational institutions and promises PRV-006 consent records "where the school chooses consent (optional features)". Optional features that go beyond "educational activities or safety" exist today: Ask conversations quoting student records (sent to Vertex AI in India) and AI parent-notice drafting. No school can record a parent's consent or a withdrawal, and nothing gates a feature on it. | **Left open:** a legal DPDP item (children's consent); it needs counsel's written view before any code (owner, 2026-10-07, wave 6). Recommendation: confirm in writing which features fall under the exemption; if any does not, add a consent register (who, for which child, which purpose, when, withdrawal) and gate that feature per student. | none (decision first) |

### Notes on findings

**DP-01: how the forgery worked.** `audit.record()` locks the head, computes
`hash = sha256(prev_hash || jcs(event))` and inserts. The hash is not keyed and the database
checked nothing on insert, so any holder of the INSERT grant can compute the same hash for any
content. Round one tested tampering by a DBA (`tests/audit/test_tamper.py`, triggers off) and the
append-only grants, but not a forged append through the app's own grants. Before the fix:

1. as `sos_app` in the school's `tenant_session`, read `last_seq` and `last_hash`;
2. insert `{seq: last_seq + 1, prev_hash: last_hash, occurred_at: now() - 3 days, action:
   "tenant.export.downloaded", actor_id: <victim user>, ...}` with the matching hash;
3. `UPDATE audit.chain_heads SET last_seq = last_seq + 1, last_hash = <hash>`.

`verify_chain` returned `VerifyResult(ok=True, checked=N+1)`. The new tests committed in this
state before 0046 (pytest "DID NOT RAISE"). After 0046 the forged event can still be appended by
code that holds the grant, but only as "now", so it lands in today's archive and cannot rewrite a
past day. A keyed MAC would not help here (the writer would hold the key); the archive signature
plus the timestamp guard are the right layer.

**DP-02: why a code check and not only the prompt.** Prompt rules are advisory (LLM01). The
notice is the one AI output that leaves the school (posted to parents' WhatsApp groups) and a
phishing link is the payload with the highest value. `strip_links` drops bare URLs entirely and
keeps the label of a Markdown link, the same behaviour as SEC-019 for Ask answers.

## Verified clean (with the evidence)

- **AEAD binding (the question asked first).** `app/core/crypto.py` uses AES-256-GCM with a
  random 96-bit nonce per value and associated data `header || tenant_id|table|column|row_id`
  (`field_aad` refuses `|` in table and column, so the AAD is unambiguous). Every caller passes
  the row's own primary key and its own column: `sis.attribute_values` (`value_id`),
  `sis.guardians` (phone and address, `guardian.id`), `sis.change_requests` (old/new,
  `request_id`), import cell edits (`edit_id`), behaviour notes and flag actions, `kb.queries`
  (question, answer, citations, follow-ups), `kb.conversations` (title, summary) and
  `kb.user_memories`. Re-encryption at rotation keeps the same AAD. A ciphertext therefore
  cannot be swapped between rows, columns, tables or schools, and the key-version header cannot
  be edited. Proven by existing tests, re-run: `tests/students/test_crypto.py::test_SEC_012_tampered_or_swapped_ciphertext_fails`
  (row swap in the database), `::test_SEC_012_roundtrip_and_ciphertext_format` (other row,
  column, table and the other school's key), `tests/tenancy/test_key_wrapping.py::test_SEC_012_aead_rejects_row_swap_and_tampering`
  (same DEK, other tenant id in the AAD).
- **Key wrapping.** KMS `Encrypt`/`Decrypt` carry `EncryptionContext={"tenant_id": ...}`; the
  local-dev wrapper uses the tenant id as GCM AAD and refuses to construct in staging/prod even if
  settings validation is bypassed. DEK and HMAC key are 256-bit from `secrets`. No other AEAD,
  `random` (only retry jitter) or hand-rolled crypto in product code.
- **Data key cache.** Per `(tenant_id, key_version)`, at most 15 minutes (constructor refuses a
  longer TTL), tenant taken from the session's RLS context, `forget()` on key destruction in the
  purging process; other processes keep a DEK at most 15 minutes after offboarding, when no row
  is left to decrypt.
- **Crypto-shredding.** `destroy_keys` refuses while any tenant row (catalog-driven count) or file
  under `t/<tenant_id>/` remains; every export, import file, certificate and upload lives under
  that prefix of the one files bucket, and `purge_prefix` tags every version (W3-07 fix). Copies
  that outlive it are owner-accepted and documented: the ap-south-2 replica (Object Lock 90 days,
  docs/08 §7), PITR and cross-region backups (round-one hardening 6), and the signed audit
  archive (IDs and codes only).
- **Valkey.** Keys and TTLs: `sos:authz:gen|snap:*` (role and scope ids, 60 s), `sos:idem:*`
  (whole response bodies, 24 h; see hardening H-01), `sos:rl:*` (counters; login digest of the
  subject, not the subject), `sos:kb:spend|resv|exhausted:*` (money, month), `sos:svc-jti:*`
  (token ids). Outbox and Celery messages carry ids only (every `ops.enqueue_event` payload was
  read). Prompts, answers and passages are never put in Valkey; the Gemini context-cache index is
  per process and the cached prefix holds only the static system prompt and tool definitions
  (never cached when memory items are present). Celery results: fixed under H-02.
- **AI pipeline (OWASP LLM Top 10).**
  - LLM02 sensitive information: C3 values never reach the model (`tools/students.py` masks
    them, the tool says how a person may reveal one); `get_student_facts` returns only fields the
    model names, at most 8; the question is Aadhaar-masked before the model, the embeddings and
    storage; every request body passes `redact_payload`.
  - LLM05 output handling: Ask answers keep only `sos://` links; the web renders text nodes only
    (round one); notices and summaries fixed under DP-02.
  - LLM06 excessive agency: the tool registry is a fixed read-only whitelist (ADR-0008); no tool
    writes, sends or fetches a URL.
  - LLM07 system-prompt leakage: the system prompt holds the school name, today's date, the
    role and scope labels; no secret, no other user's data. Memory items go in a separate block
    and only for their owner.
  - LLM08 vector and embedding weaknesses: vectors and the embedding cache are per school (RLS)
    and deleted with their source (docs/08 erasure chain); retrieval filters by ACL in SQL before
    ranking (round one); the answer cache needs the same access fingerprint and re-checks every
    source (round one).
  - LLM10 unbounded consumption: per-user and per-school rate limits, monthly budget reservation
    (round one W3-03, W3-10).
  - Vertex Zero Data Retention in code matches docs/10 §11: staging/prod refuse live AI without
    `SOS_LLM_ZDR_CONFIRMED`, and the transport reads the project's `cacheConfig` before the first
    call and every `VERIFY_EVERY_S`, sending nothing unless implicit caching is disabled. Explicit
    context caching (ADR-0033) caches only the static prefix. Anthropic needs
    `SOS_ANTHROPIC_ZDR_CONFIRMED` in staging/prod.
- **Full data export contents.** Staff table goes through `identity.list_users` (so DL-09's
  `contact_hidden` blanking applies; break-glass support staff are dropped); C3 values, guardian
  contacts, note and action text are masked unless `include_sensitive`; Aadhaar-as-printed fields
  are always withheld; the query log and conversations are not exported; Ask memory items are
  (ADR-0034, the person's own work context). The round-one gap DL-07 (files ignored
  `include_sensitive`) is fixed in ef2d0b0d.
- **Retention jobs delete.** Query log (180 days; summaries cleared, empty conversations deleted),
  memory items (pending TTL, leavers), orphan vectors, import raw files, exports, read
  notifications all delete rows in their own `tenant_session`.

## Hardening (no exploit path found)

- **H-01 (round-one hardening 5).** `authz/http.py` `Idempotency` keeps the whole
  response body in Valkey for 24 hours, including decrypted text the caller sent (behaviour notes,
  guardian phone and address). A replay requires the same user, key and body, so it reveals
  nothing new to the caller, but the plaintext survives an erasure (DPDP) and an offboarding for
  up to 24 hours. Recommendation: for routes whose response carries C3 text, store status, ids
  and headers only and rebuild the body on replay. **Fixed in fffc1995.** `Idempotency.run` takes
  `refetch`. Routes whose response carries C3 text (behaviour notes, flags, guardians, change
  requests) store only status, id and headers, and re-read the body on replay with the caller's
  current permissions. Since 6768f8d8 (via f9e5f22d), a replay after the caller's access changed
  gets 409 `idempotency_access_changed`. Tests: `tests/students/test_api.py::test_H_01_*`,
  `tests/insights/test_api.py::test_H_01_*`, `tests/changes/test_api.py::test_H_01_*`.
- **H-02 (fixed in c6783cd).** Celery used Valkey as result backend; every task without
  `ignore_result` stored its return value and, on failure, the exception message and traceback
  for 24 hours. Nothing reads results, and a database error message can quote row values
  (invariant 5). Now `task_ignore_result=True`, `task_store_errors_even_if_ignored=False`; test
  `apps/worker/tests/test_celery_app.py::test_SEC_008_no_task_result_or_error_is_kept_in_valkey`.
- **H-03.** Tally device secrets and dedicated-host heartbeat keys are wrapped with only the
  tenant id as KMS encryption context. Within one school a wrapped secret could be copied to
  another device row by someone with database write access. Add the device or deployment id to
  the context at the next rotation. **Fixed in 0c555d0f.** New wraps add the device or deployment
  id to the KMS encryption context. Old values still unwrap; a secret copied to another device row
  is refused. Tests: `tests/tenancy/test_key_wrapping.py::test_H_03_*`,
  `tests/tally/test_api.py::test_H_03_*`, `tests/platform/test_heartbeat.py::test_H_03_*`.
- **H-04 (round-one hardening 3).** Truncating the newest events and rewinding the
  head as a DBA is still detected only against the archive by hand. Now that every chain is
  archived (DP-03), compare yesterday's DB head with the last signed manifest's `last_seq` and
  `last_hash` in `verify_all_chains`. **Fixed in c82c469e.** `verify_all_chains` compares each
  school chain with its last signed manifest (after checking its signature) and raises P1
  `audit.chain.broken` (`head_behind_archive`, `archive_hash_mismatch` or
  `archive_signature_invalid`). Tests: `tests/audit/test_archive_head_and_backfill.py::test_H_04_*`.
- **H-05.** The archive job writes only "yesterday". A day that fails all five retries is never
  archived. Record the last archived day per school and backfill. **Fixed in c82c469e.** The
  archive job backfills every missed day of the last 31, oldest first. Tests:
  `tests/audit/test_archive_head_and_backfill.py::test_H_05_*`.
- **H-06.** The signed audit archive keeps a school's events for 3 years under Object Lock
  COMPLIANCE, while ADR-0029 deletes the database chain one year after offboarding. The events
  hold ids and codes only, so this is consistent with the no-PII rule, but docs/08 §7 should say
  so. **Fixed in c82c469e** (docs). docs/08 §7 states the 1-year database chain versus the 3-year
  signed archive. Docs only, no test.

## Checks run

| Check | Result |
|---|---|
| `uv run lint-imports` | 38 contracts kept |
| `uv run mypy apps/api apps/worker evals` | no issues (821 files) |
| `ruff check` / `ruff format --check` (apps/api, apps/worker) | clean |
| `pytest tests/audit tests/knowledge tests/circulars tests/security tests/migrations tests/core tests/tenancy tests/platform tests/extraction/test_migration.py ../worker/tests` | 4575 passed, 6 skipped (Chromium not installed), 1 failed: `tests/platform/test_operators.py::test_FR_PLT_028_invite_assign_roles_and_deactivate`. It passes alone; it is a test-isolation issue unrelated to this branch (the operator list is ordered by UUIDv7 and paginated, so after more than 200 operators in the shared session database the new one is on page 2 of `?limit=200`). |
| Migration round trips (`tests/migrations`, both fresh and populated) | pass, including 0046 |
| `make eval EVAL_ADAPTER=app-fake EVAL_SUITE=full` | before the knowledge change (base notice.py/reading.py): exit 0, 36 gates pass; after: exit 0, the same 36 gates pass (one informational "worse than baseline: retrieval_latency_p95_ms", a timing figure on a shared machine, not a gate) |

## Migration

`0046_audit_append_guard` (after `0045_invitation_consent`): expand only (four SECURITY INVOKER
trigger functions with a pinned `search_path`, executable by no one directly, and their
triggers); no grant, policy or definer function changes. The downgrade drops them. The head is
pinned in `tests/extraction/test_migration.py`. CLAUDE.md §4 still names `0045_invitation_consent`
as the last revision; the owner should update it.
