# Audit 2026-11-09: knowledge, documents, exports and certificates (YK)

Backend audit round 2 (deep per-module pass), base `fad0b02c`. Scope: `app/knowledge`
(ingestion, chunking, embeddings, retrieval, tools, prompts, gateway, citation validation,
quota), `app/documents` (upload, storage, versions, ACLs, scan hook, page links),
`app/exports` (pre-check sheets, CSV/XLSX, print-job PDFs) and `app/certificates` (TC,
bonafide, study, conduct, serials, registers, public verification), with their tasks. Focus:
invariants 4, 8, 9 and 13. The October reports and the 2026-11-08 round (including
DPA-01..03 and the web round-2 API follow-ups) were read first; their fixed items are not
repeated.

Every fixed finding has a test that failed before the fix and passes after it. Paths are
relative to `apps/api/` unless they start with `docs/`. Synthetic data only.

## Findings

| ID | Severity | Area | File:line | Description | Status |
|---|---|---|---|---|---|
| YK-01 | **High** (invariant 8, SEC-018: the LLM receives passages the caller can no longer see) | Document ACL to chunk ACL copies | `app/knowledge/ingestion/hooks.py` `on_acl_changed` (was line 44) | `PUT /documents/{id}/acl` only queued `kb.document.acl_changed`; the chunks' `acl_*` copies, which `retrieval.acl.acl_predicate` filters on, were rewritten later by the `knowledge.refresh_acl` worker. Between the commit and the worker run (seconds with a healthy queue; for good if the five retries run out, the `ingest` queue is backed up, or `SOS_KB_ENABLED` was off when the ACL changed but on when the chunks were made) a member REMOVED from a document's ACL still found its passages in `POST /knowledge/search` and in Ask, and they were sent to the model. Citation validation does not help: the passages were "given in this request", so citations to them were accepted. Archive, delete and version discard were already applied in the same transaction; only the ACL change was not. | **Fixed** in 9dd8732a (tests in 1cb7fd22): `on_acl_changed` calls the new `apply_acl_now` in the `set_acl` transaction (index lock, the document's current ACL read through `documents.service`, `SqlChunkStore.update_acl`, which also invalidates cached answers). It runs whether or not knowledge is on (it only rewrites existing chunks). The queued refresh stays for the facets. Test: `tests/knowledge/test_index_lifecycle_db.py::test_SEC_018_YK_01_acl_narrowing_rewrites_chunk_acl_in_the_same_transaction` |
| YK-02 | **High** (invariant 8 and the C3 rule of docs/05 §6.1; one student's record, and where the school prints them the C3 religion and caste fields, reach every holder of four roles through Ask and the LLM) | Generated certificate PDFs in the index | `app/knowledge/config/chunking.yaml` `extraction.excluded_purposes`; `app/certificates/service.py:2273` (`store_generated_document`, `document_acl_roles: [owner, principal, office_admin, office_staff]`); `app/documents/service.py:281` (purpose `certificate` is C2) | Certificate PDFs are stored as `kb.documents` with purpose `certificate`, sensitivity C2 and a role ACL, and are queued for scan "and indexing like any upload". `certificate` was not an excluded purpose, so every TC, bonafide, study and conduct certificate (Chromium PDFs have a text layer) was chunked into `kb.document_chunks` unencrypted and retrievable by anyone holding one of those roles with `document.read`, whatever their `certificate.read` permission, their student scope or `student.read_sensitive`. With `print_restricted_tc_fields` on (FR-CERT-018, D15) the TC text holds religion and caste, which are C3 and never indexed otherwise. Cancelled certificates stayed searchable although their download answers 409 (A-07). Fee receipts were excluded for the same reason (R2-1B). | **Fixed** in 1cb7fd22: `certificate` is an excluded purpose (ingestion answers `excluded` and deletes any chunks). Chunks indexed before the fix: `python -m app.knowledge.backfill --apply` now also prunes chunks (and cached vectors) of every document that has chunks but is no longer indexable, audited `kb.backfill.pruned` (ids and counts); the dry run reports the count. **Operator action after deploy:** run the backfill with `--apply` once per environment. Tests: `tests/knowledge/test_index_lifecycle_db.py::test_SEC_018_YK_02_certificate_pdfs_are_never_indexed`, `::test_SEC_018_YK_02_backfill_apply_prunes_chunks_of_documents_no_longer_indexed` |

No fix changes the API schema, a permission, or needs a migration (no `0085_audit_yk_fixes`).
docs/06 §4 (index lifecycle, backfill) is updated.

## Verified clean (with the evidence)

- **Retrieval filter (invariant 8).** `retrieval/acl.py` `acl_predicate` is in the WHERE clause
  of every branch (vector ANN and the exact `MATERIALIZED` route, full text, trigram), of the
  authorised count and of the detail query; `is_latest` always; C3 excluded in SQL without
  `student.read_sensitive`; empty ACL only for school-wide readers. `tools/access.acl_keys`
  mirrors `documents.service._visibility` (same structure widening of section/class scopes).
  Verified answers (`retrieval/verified.py`) are filtered in SQL by a visible chunk of every
  cited document; citations are required (`min_length=1`).
- **Lifecycle.** Archive (demote), delete (cascade + embedding-cache forget + answer erasure),
  version discard (chunks, cached vectors, quoted answers) all run in the change's own
  transaction. Ingestion re-reads the facts under the per-document advisory lock before
  writing, so a delete or ACL change that committed meanwhile wins. Quarantined and failed
  versions are never `ready`, so never served or indexed.
- **Citations.** `answer.validate` keeps a citation only when its source is a block given in
  this request and the cited text is a substring of it; uncited factual sentences are cut;
  earlier answers, summaries and cache reuse re-check every source through `SourceVisibility`.
- **Tools (invariant 9).** No insert/update/delete/outbox/audit write in `knowledge/tools/*`;
  `list_documents` lists metadata only and hides evidence, import files and fee receipts.
- **Config (invariant 13).** No model id or provider name in Python under `app/` (grep for
  `gemini-*`, `claude-*`, `voyage-*`, `text-embedding`).
- **Documents.** Presigned POST bound to the declared size and kind, key per intent; downloads
  and page links go through `_servable_version` (ACL, scope, C3 / raw import rule, `ready`
  only), TTL from settings (<= 5 min), attachment vs inline, audited. Scan task idempotent
  (status transitions guarded by `from_statuses`). Sensitivity is fixed at registration.
- **Exports.** Every cell goes through `tables.safe_cell` (NFC, control characters, Aadhaar
  mask, formula neutralisation behind whitespace and full-width signs); XLSX cells are typed
  `s`. Download and listing re-check own/any permissions and frozen scope.
- **Certificates.** Serials come from `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` on the
  counter row (gap-free, rollback releases); unique `(tenant, type, year, serial_no)` and
  `(tenant, serial)`; one live TC per student and one pending duplicate per original are
  partial unique indexes; the frozen-row trigger keeps issued entries immutable. Approve
  re-checks blockers and the draft digest under the row lock, forbids self-approval and needs
  recent MFA. Registers need `register.read` at school scope with step-up and a row cap.
  Public verification re-reads handle, code and certificate under RLS, shows frozen content
  only and nothing for a cancelled certificate.

## Outside my area

None found.

## Checks run

- `ruff check` and `ruff format --check` on the touched files: clean. `uv run mypy apps/api
  apps/worker evals`: no issues (1244 files). `uv run lint-imports`: 52 kept, 0 broken.
- `tests/knowledge/test_index_lifecycle_db.py`: all pass, including the three YK tests (YK-01
  failed before its fix: the chunks still carried the old ACL roles).
- `tests/knowledge/test_ingestion_wiring.py`: the hook tests pass. Three Celery task tests
  (`test_tasks_accept_the_dispatcher_kwargs...`, `test_unwired_pipeline_fails_loudly`,
  `test_transient_failures_retry_then_give_up`) time out connecting to `localhost:5432` when the
  file runs alone or first; they passed in a run where a DB suite had started the test
  container first. They do not touch the changed code (ordering/environment, not YK).
- `tests/documents` (with the knowledge suites): 235 passed (one `caplog` error only because
  that run used `-p no:logging`). `tests/knowledge/test_chunking_config.py`: 13 passed.
  `uv run pytest evals -q`: 223 passed.
