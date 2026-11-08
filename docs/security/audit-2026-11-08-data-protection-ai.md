# Security audit 2026-11-08: data protection and the AI/knowledge module

Backend audit round of 2026-11-08, area "data protection and AI" (finding prefix DPA-). Branch
from `987ded97`. Scope: personal data in logs, audit summaries, traces, outbox payloads and
Celery arguments; Aadhaar masking on every ingestion path; field encryption and the DEK
rotation census; `include_sensitive` / `read_sensitive` in exports and access reports; the
D62/D70 e-mails; the knowledge module (retrieval filters, tools including `list_due_tasks`,
fees and Tally, citations, prompt injection, gateway-only LLM calls, config, quota); erasure
and retention jobs (D51, D58, D68, breach and tenant-audit purge). The October reports
(`audit-2026-10-*.md`) were read first; their fixed items are not repeated.

Every fixed finding has a test that failed before the fix and passes after it. Paths are
relative to `apps/api/` unless they start with `apps/` or `docs/`. Synthetic data only.

## Findings

| ID | Severity | Area | File:line | Description | Status |
|---|---|---|---|---|---|
| DPA-01 | **Medium** (invariant 4; a full Aadhaar number reaches the LLM provider, the embedding search and model output) | Aadhaar masking | `app/core/redaction.py:157` (`_SEP`); used by `app/knowledge/service.py:328` (Ask question), `app/knowledge/gateway/wire.py:61` (every request string), `gemini_wire.py:317`, `streaming.py`, `redact`/`redact_log` | `contains_full_aadhaar` (input refusal) collapses every whitespace run before it looks (DL-01), but `mask_aadhaar`, `redact` and `redact_log` joined digit groups only across 1-3 gap characters. Text that is masked rather than refused therefore kept a number typed or extracted with a wider gap: an Ask question `Is 2345    6789    0124 enrolled?` (four spaces, tabs, blank lines, `   -   `) went unmasked into `kb.queries`, the hybrid search query (embedding provider) and the Vertex AI prompt; model output and log lines with such a gap were not masked either. Document ingestion was safe only because `ingestion/clean.py` collapses whitespace first. | **Fixed** in ce3558d0: a separator is any number of gap characters, or a dash with any number of gaps on each side (gaps and digits are disjoint classes, so the scan stays linear). Tests: `tests/core/test_redaction.py::test_PRV_015_DPA_01_masking_joins_any_whitespace_gap_the_input_check_refuses` (7 gaps x `mask_aadhaar`/`redact`/`redact_log`/`find_aadhaar`), `::test_PRV_015_DPA_01_unbounded_gaps_are_scanned_in_linear_time` |
| DPA-02 | **Medium** (invariant 4; the full number is shown on screen while the answer streams; the stored answer was masked) | AI gateway streaming | `app/knowledge/gateway/streaming.py:20` (`_TAIL`) | `AadhaarStreamMasker` holds back the trailing "may still continue a number" characters until the run ends, but its class was `[+0-9  \-‐-–]`: no line break, tab, other Unicode spaces, em dash, minus, Cf characters, the rule-4 separators `. / _ ,`, and no non-ASCII digits. A model answer containing `2345\n6789\n0124`, `2345.6789.0124`, `2345—6789—0124` or the number in Telugu digits was emitted group by group; no single delta held a 12-digit window, the fallback in `_emit` masked the new piece alone, and the browser joined the deltas into the full number (the final `ModelTurn` was masked again, so only the live stream leaked). Seven of eight separators failed at some split point. | **Fixed** in 16e82df6: the tail uses `core.redaction.NUMBER_RUN_CHARACTER`, every digit (any script), gap, dash and rule-4 separator the maskers join. Test: `tests/knowledge/test_gateway_streaming.py::test_invariant_4_DPA_02_every_separator_the_masker_joins_is_held_back` (every split point of 8 separator forms) |
| DPA-03 | **Low-Medium** (invariant 4; extension decided by the lead on 2026-11-08, extends D52) | Aadhaar masking, web form guard | `app/core/redaction.py` (`_WIDE_SEP`, `NUMBER_RUN_CHARACTER`), `apps/web/src/lib/aadhaar.ts` (`AADHAAR_GROUP_SEPARATOR`), `evals/sos_evals/metrics.py` (`_GROUP_SEP`) | A number grouped 4-4-4 with `|`, `:` or `*` between the groups, as OCR and PDF extraction write table cells (`2345 | 6789 | 0124`, `2345:6789:0124`), was neither refused nor masked. The web form guard still used the old 1-3 character rule, so it missed the wide gaps of DPA-01 as well. The eval leak check was no longer a superset of the server rule. | **Fixed** in f1766542 (server, stream tail, eval leak check, docs/07 and docs/08 PRV-015) and 746355f0 (web). One table separator with any whitespace around it now joins 4-4-4 groups, in the input checks, all maskers, `find_aadhaar` and the stream tail. It applies only to the 4-4-4 rule, so clock times (`09:30`) and ratios never join. `x` and parentheses are not separators. The web guard now uses the same rule. Tests: `tests/core/test_redaction.py::test_PRV_015_DPA_03_*` (10 table forms; Verhoeff required; `x`, parentheses and times left alone; linear-time cases), the streaming test now covers ` | `, `:` and ` * `, `evals/tests/test_metrics.py` cases, and `apps/web/src/lib/aadhaar.test.ts` (wide gaps, table rows, linear time) |

No fix changes the API schema, a migration or a permission. The lead's D52 text in docs/14 is not edited: DPA-03 extends D52 (lead decision 2026-11-08).

### Note on design limits (not findings)

`mask_aadhaar` still does not join groups separated by `x` or parentheses (`(2345) 6789 0124`), or by 4+ punctuation characters other than whitespace (D52, pinned by `test_PRV_015_D52_four_separator_characters_are_not_a_group_join`). The table separators join only 4-4-4 groups, not other groupings. These limits are deliberate (lead decision 2026-11-08).

## Verified clean (with the evidence)

- **Logs, traces, metrics.** `core/logging.py` keeps only `ALLOWED_FIELDS` and redacts values;
  `core/telemetry.py` exports sanitised span copies (allowlisted attributes, SQL without bind
  values, exception events reduced to the type). No `log.*` call in `apps/api/app` or
  `apps/worker` passes a name, phone, e-mail, title or free text (grep of every call with such
  keys or f-strings).
- **Outbox and Celery.** All 55 `ops.enqueue_event` payloads carry IDs, codes and counts (the
  D70 e-mail event adds `notification_id` only); no `.delay`/`apply_async` with values; the
  worker reads values when it runs.
- **Audit summaries.** Every `summary=` holding a `reason` is a code (`IgnoreReason`,
  `FeedbackReason`, handle revoke codes, certificate reason codes) or a length (`chars`,
  `title_chars`). No free text.
- **E-mails (D62, D70).** `notifications/email_templates.yaml` holds the school name, sign-in
  URL, expiry date, counts and metric codes only ("Emails never contain student data"). The
  outbox event and `notification.email_requested` audit carry IDs. The worker reads the address
  at send time and logs `user_id` and outcome codes only. The control plane never reads the
  address (hook in the tenant transaction).
- **In-app notification params.** Every template in `notifications/templates.yaml` takes IDs,
  counts and codes.
- **Field encryption and the rotation census.** No ciphertext column was added since 0067.
  `students/rotation.CIPHERTEXT_COLUMNS` + `NOT_TENANT_DEK` equal the database's ciphertext
  columns (`test_SEC_012_census_lists_every_ciphertext_column`). Every module with tenant-DEK
  columns registers a re-encryptor (`fees_online`, `fees_statements`, `import_cell_edits`,
  `insights_notes`, `kb_*`, `messaging`, `privacy_breach`, `privacy_requests`; sis tables in
  students). `ops.tally_bills` (0077) holds C2 bill references masked by
  `tally/schemas._clean`, so it needs no encryption.
- **Aadhaar on ingestion paths.** These all refuse the number on input or mask it: imports,
  extraction (`extraction/sanitize.py`), document text (`ingestion/clean.py` collapses
  whitespace, then masks), document titles and metadata, prompts (gateway), circular summaries
  and notices, tasks (`_refuse_aadhaar`), behaviour notes and flag actions, fees (refuse),
  bank narrations (`redact`, grouped refusal), Tally names and bill references (mask),
  certificates, DQ, the calendar, APAAR, privacy nominee fields and Ask titles and memories.
  Messaging has no free-text input, and inbound text is never stored (FR-MSG-018).
- **Exports and access reports.** Exports mask C3 unless `include_sensitive` with school-wide
  `student.read_sensitive` (`exports/service.py:523-553`, re-checked at download `:792`). The
  privacy access report follows ADR-0048: Aadhaar only "held" or the last 4 digits. The Tally
  contribution shows linked ledgers and their pending bills only.
- **Knowledge.**
  - LLM SDK imports exist only in `knowledge/gateway` (tests excepted). No model id appears
    in code.
  - `list_due_tasks` (D74) reads through `circulars.service` under the caller's context:
    their own tasks, or every task with a school-wide `task.read_all`. It sends no names
    (roles only) and no details or AI summaries. A circular title appears only when the
    caller may open the circular.
  - `sos://task` citations are re-checked by `task_visible_for_ask`, and fail closed without
    a reader.
  - The fees and Tally tools need school-wide `finance.read`. Their sources carry a scope
    fingerprint that is re-checked when shown.
  - The Tally school summary has no class breakdown (D54). The fee-ledger class breakdown
    goes only to a school-wide finance reader.
  - `SourceVisibility` re-checks every cited source kind against the caller now (document ACL
    and C3, kept version, student scope and masked fields, finding and change scope, verified
    answers, own conversations).
  - The answer cache is reused only for an equal access fingerprint and current, visible
    sources.
- **Retention and erasure.**
  - D51 outbox purge: `dispatched_at` set and older than the retention period. A restrictive
    policy stops `sos_app` deleting undispatched rows.
  - D58 breach purge: restrictive delete policies allow only breaches closed more than 3 years
    ago (with their reminders). A closed breach's affected list is frozen. Open and reopened
    breaches are never deletable.
  - D68 tenant-audit copy purge deletes only copies whose `platform_event_id` is confirmed in
    the school's chain. Unconfirmed copies are kept and counted.
  - Knowledge erasure deletes the member's questions, conversations and memory, and records
    an audit with counts.
  - Documents erasure is a manual review step by design.

## Outside my area (not fixed)

None open. The web mirror noted in the first pass is fixed as part of DPA-03.

## Checks run

- After DPA-01: `uv run pytest` on `tests/core` and on the knowledge gateway, Gemini wire,
  streaming, ingestion extract and PDF, contextual and circular reading tests, plus
  `tests/extraction/test_sanitize.py`, `test_imaging.py`, `test_providers.py`,
  `tests/devtools/test_fake_ids.py` and the four `test_log_redaction.py` suites: 868 passed.
- After DPA-03: `tests/core`, the knowledge streaming, gateway, Gemini, circular reading,
  contextual and ingestion extract tests, `tests/extraction/test_sanitize.py` and
  `test_imaging.py`, `tests/imports/test_validation.py` and `tests/fees/test_upi_parse.py`:
  917 passed.
- DB-backed: `tests/knowledge/test_ask_api.py` and `test_ingestion_pipeline.py`: 62 passed.
- `cd evals && uv run pytest -q`: 223 passed.
- Web, using the tools in `D:\school-os\node_modules` because the worktree has no
  node_modules: `vitest run src/lib/aadhaar.test.ts` with a lib-only config (the project config
  needs `@vitejs/plugin-react` from the main checkout), 8 passed. `tsc --strict` on the two lib
  files: clean. `eslint`: clean. `prettier --check`: clean. The project-wide `tsc` and the
  vitest runs of the screens that use the guard were not run here.
- `ruff check` and `ruff format --check` on the touched files: clean. `uv run mypy apps/api
  apps/worker evals`: no issues (1241 files). `uv run lint-imports`: 52 contracts kept.
