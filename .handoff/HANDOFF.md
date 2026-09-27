# SchoolOS: session handoff (cloud → local)

Written 2026-09-27 at the end of the cloud session. Branch: `claude/friendly-ptolemy-0tl3br`.
Paste the **prompt** at the bottom into Claude Code on your computer, in your clone of
`bb3agency/school-os`.

## 1. Where things stand

**Merged on the branch and pushed** (full suite: 3,296 API tests pass, web vitest 290 pass, ruff,
mypy strict and import-linter clean):

- M0 platform: identity/OIDC + BFF, tenancy, authz (`require()` on every route), audit hash chain,
  RLS on every tenant table, platform control plane (admin panel backend, billing, fleet), dedicated
  tier (Terraform + `deploy/dedicated`), CI.
- M1 backend, one Alembic revision per module, linear chain:
  `0008_sis_students → 0009_kb_documents → 0010_notifications → 0011_breakglass → 0012_imports →
  0013_dq → 0014_change_requests → 0015_platform_decisions → 0016_extraction → 0017_exports`.
- Product-owner decisions of 2026-09-27 (ADR-0020): suspended school keeps billing + export for the
  owner and principal only; school-chain audit copies via outbox; platform may call
  `tenancy.service` for lifecycle only; ADR amendments record facts only.
- Lead follow-ups done: imports tag created students with their batch; DQ findings cascade when an
  import revert removes students; cancelled/expired change requests release DQ findings; resolving
  a finding by change request is validated; memo CSP kept when strict; `/me.tenant_status`;
  redaction never masks UUIDs or 32-hex request/trace ids; extraction withholds a page version
  whose text shows a full Aadhaar number (`documents.withhold_version`).

**Unfinished work, saved as patches in `.handoff/wip/`** (the three agents were stopped mid-task;
none of it is reviewed or fully tested). All three were cut from commit `6ddb342`:

| Folder | What it is | State |
|---|---|---|
| `prv016-redaction/` | PRV-016 image redaction: core positions of Aadhaar-like numbers, documents redacted version + discarded original, extraction provider bounding boxes (WIP) | 2 finished commits + 1 WIP commit. `pyproject.toml`/`uv.lock` conflict with the exports Playwright pin: resolve by re-running `uv add "pillow==<pinned>"` after applying. |
| `web-students-imports-extraction/` | Web screens: students, imports, extraction queue | One WIP commit (≈3,000 lines); was building the imports list/upload screen |
| `web-findings-crs-notifications/` | Web screens: DQ findings, change requests, notifications bell, BFF proxy changes | One WIP commit (≈3,200 lines); was building the change-request submit form; break-glass page and suspended banner not started |

Apply one at a time on a new branch, for example:

```bash
git switch claude/friendly-ptolemy-0tl3br && git pull
git switch -c wip/prv016
git am -3 .handoff/wip/prv016-redaction/*.patch   # resolve pyproject/uv.lock, then: git am --continue
```

Once all three are applied, reviewed and merged, delete `.handoff/`.

## 2. Remaining work, in order

1. **Finish PRV-016 redaction** (`.handoff/wip/prv016-redaction`). Requirement: docs/08 PRV-016 and
   ADR-0007. The image is stored with the number regions blacked out, using OCR bounding boxes via
   Pillow (pin it; licence MIT-CMU/HPND). EXIF is stripped and the original object is deleted
   (guaranteed via outbox). The page's rows then use the redacted version as evidence and become
   confirmable. A page that can't be redacted stays withheld (confirm → 409). Update
   `tests/extraction/test_pipeline.py::test_FR_IMP_022_PRV_016_...` to the stricter behaviour; never
   weaken the "number never in DB/logs/API" assertions.
2. **Finish the web screens** (two WIP patches). Scope and rules:
   - Every string in `en` and `te`; usable at 1366×768 and with the keyboard only.
   - Generated API client only; TanStack Query; zod forms; permission-aware from `/me`.
   - Handle `step_up_required` (428) and `tenant_suspended` (403) globally.
   - Show a suspended banner from `/me.tenant_status`.
   - Break-glass page for school owners; notifications bell with polling and backoff.
   - Exports screens (`/api/v1/exports*`, `/api/v1/export-profiles`) are not started.
3. **Done (branch `wip/authz-require-any`):** `require_any()` / `AnyOfRequirement` now live in
   `app.authz.dependencies`; `app/changes/api.py` and `app/exports/api.py` import them (the
   duplicates are gone). Unit tests: `tests/authz/test_require_any.py`.
4. **Infra for exports:**
   - The worker image needs Chromium revision 1194 for Python Playwright 1.56.0 on the `pdf` queue
     workers.
   - Confirm the Chromium sandbox works non-root on ECS Fargate and dedicated hosts (ADR if not).
   - Add an S3 lifecycle rule deleting `t/*/exports/` after 7 days.
5. **Students owner follow-ups from imports:**
   - `refresh_profiles`/`revert_import` helper, so the revert SQL moves out of
     `imports/repository.py` (it writes `sis.students` directly today).
   - Expose attribute validation rules to imports.
   - A system delete path in documents for retention.
6. **Notification templates:** `import.reverted` (if wanted).
7. **Docs:** docs/12 testing strategy updates for M1, and the docs/14 roadmap status.
8. **Final check:**
   - `make check`, `make migration-check` and `make dev` with the web app.
   - Then open a PR to the default branch. It has not been opened yet.

## 3. Open questions for the product owner (don't guess; ask)

1. **Decided 2026-09-27 (ADR-0021 decision 1):** every export (board/portal pre-checks and
   student lists) needs step-up MFA to be created. Built on `wip/exports-access`.
2. **Decided 2026-09-27 (ADR-0021 decision 2):** UDISE+ `category` (C3) is included only by
   explicit opt-in (`include_sensitive`) by `student.read_sensitive` holders with step-up; the
   audit event lists the included columns. Built on `wip/exports-access`.
3. **Decided 2026-09-27 (ADR-0021 decision 3):** `export.read_all` (owner, principal, office
   admin) sees every export's details; `export.download_any` (owner only by default, always
   step-up) downloads others' exports. Built on `wip/exports-access`. Still open: delivering
   new `roles.yaml` grants to schools provisioned before migration `0019_export_access` (see
   ADR-0021 "Follow-up work").
4. CISCE/UDISE+ profile layouts in `app/dq/config/profiles/` and `app/exports/config.yaml` are
   placeholders. The real templates are needed before the pilot.
5. Operator sign-in across the two Cognito pools for break-glass needs an ADR.
6. FR-PLT-002 provisioning atomicity is still open.

## 4. Local environment notes

- Requirements: Docker, uv 0.12.19 (pinned in `pyproject.toml`), Node (npm workspaces), Python
  3.12. `make install`, then `make dev`; `make test` uses testcontainers Postgres.
- `sos-api` now depends on Playwright 1.56.0 (Python). Run `uv run playwright install chromium`
  locally for the real PDF test (in the cloud it used `/opt/pw-browsers`).
- The cloud-only workarounds (proxy CA secret, `--network host` docker builds, `INSTALL_PSQL=false`)
  are not needed locally.

---

## Prompt to paste into Claude Code locally

```text
You are the lead engineer for SchoolOS (repo bb3agency/school-os), continuing work from a cloud
session. First read CLAUDE.md completely and obey it; its §6 invariants are non-negotiable. Then
read .handoff/HANDOFF.md, which describes what is merged, what is unfinished and in what order to
finish it.

Work on branch claude/friendly-ptolemy-0tl3br (pull it first). Rules for every task:
- Small Conventional Commits that include requirement IDs.
- Write tests first. Never weaken, skip or delete a test to get green.
- Synthetic data only. No secrets or API keys anywhere.
- Nothing the docs rule out without an ADR I approve. That includes microservices, Kubernetes,
  GraphQL, a separate vector DB, and LLM calls outside knowledge/gateway.
- Pin dependency versions and justify each new one in one line (purpose, licence).
- If the docs and the code disagree, or you're unsure, stop and ask. Never guess on security,
  tenancy or privacy.
- At the end of each task, report: files changed, commands run and their results, requirement IDs
  covered, open issues, and the proposed next task.

Start with step 1 of HANDOFF.md §2. Apply .handoff/wip/prv016-redaction on a new branch, review it
critically against docs/08 PRV-016 and ADR-0007, finish it with tests, run ruff, mypy,
lint-imports and the full pytest suite, then merge it into claude/friendly-ptolemy-0tl3br. Then
continue with the web patches (step 2) and the rest in order. Ask me the open questions in
HANDOFF.md §3 when you reach the work they affect. Don't open a pull request until I ask.
```
