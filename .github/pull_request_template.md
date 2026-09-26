## What and why

<!-- One story or fix per PR (aim for < 400 changed lines, excluding generated files). -->

Requirement IDs: <!-- e.g. US-601, FR-CR-002, SEC-014 -->

## Risk and rollback

<!-- What could break, who is affected, how to roll back. Migrations: expand/contract step? -->

## Screenshots (UI changes)

<!-- Synthetic data only. Show en and te, 1366×768. -->

## Checklist

Tick each item or write "n/a" with a reason. See CLAUDE.md §6 and §8, docs/13 §3, docs/07 §16.

**Tests**
- [ ] Acceptance criteria are covered by automated tests (tests written first)
- [ ] Authz: allowed role succeeds, disallowed role gets 403, other tenant gets 404
- [ ] New routes declare `Depends(require(...))` and appear in the route-enumeration test
- [ ] New resources have BOLA and cross-tenant isolation tests; new tenant tables are in the RLS catalog test (ENABLE + FORCE + policy)

**Data protection**
- [ ] No PII in logs, traces, metrics or errors; log messages are constant event names; the redaction test covers any new fields
- [ ] No Aadhaar numbers stored or logged (only `aadhaar_last4` / as-printed fields)
- [ ] Audit events recorded in the same transaction for identity changes, role/permission changes, exports, AI queries, break-glass, logins
- [ ] Official records are never auto-corrected (mismatches create findings; identity changes go through maker-checker)
- [ ] AI changes: retrieval filtered by tenant + permissions in SQL before ranking; citations validated; tools read-only; `make eval` gates pass
- [ ] Only synthetic data in code, fixtures, screenshots and this PR

**Database**
- [ ] Migrations are backward compatible (expand → migrate → contract)
- [ ] Each migration has a working downgrade (or is marked irreversible with a reason) and upgrades/downgrades cleanly on a seeded synthetic DB

**Product**
- [ ] UI strings exist in `en` and `te`; screens work at 1366×768 and keyboard-only
- [ ] Docs (PRD/TRD/API/data model) updated; ADR added if a decision changed

**Supply chain**
- [ ] New dependencies are justified here (purpose, licence — no AGPL/SSPL — maintenance, known CVEs) and pinned in the lockfile
- [ ] No secrets in code or config; configuration read via `app.core.config`

**Security-sensitive paths** (`core/`, `authz/`, `audit/`, `platform/`, `knowledge/gateway/`, migrations, crypto, `infra/`, `deploy/`, `.github/`)
- [ ] Full checklist in docs/07 §16 reviewed; independent review pass done (fresh session / second reviewer on a different day)

## `make check` summary

```text
<!-- paste the summary output of `make check` -->
```
