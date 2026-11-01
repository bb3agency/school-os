# <Environment> restore drill, <YYYY-MM-DD>

Use this template for every restore drill (SEC-024, NFR-AVL-003, NFR-AVL-005; docs/10 §9,
§15.3; docs/11 R6, R11). Save it as `docs/security/drills/<YYYY-MM-DD>-<env>-restore-drill.md`.
The local drill (`make restore-drill`) writes this structure itself; staging and dedicated-host
drills are filled in by the operator who ran them. No personal data: IDs, table names, counts
and timings only.

Result: **PASS | FAIL**

## Set-up

| Item | Value |
|---|---|
| Kind | local (synthetic) / staging PITR / dedicated logical dump / dedicated WAL-G PITR |
| Date and operator(s) | |
| Release / commit | |
| Source (instance, snapshot or dump URI) | |
| PITR target timestamp (UTC) | |
| Target (new instance / scratch host / scratch database) | |
| Database size, tables, rows | |

## Procedure

Steps as run, with the commands (docs/11 R6 / R11). Note any deviation from the runbook.

## Checks

| Check | Result | Detail |
|---|---|---|
| Alembic revision equals the release | | |
| Per-table row counts match the source (or the expected PITR point) | | |
| RLS ENABLE + FORCE on every tenant table; no role with BYPASSRLS | | |
| `python -m app.audit.verify_all` on the copy (school and platform chains) | | |
| Tenant read returns the school's rows; another school's context returns none | | |
| Application health on the restored database (staging / dedicated only) | | |

## Timings and targets

| Step | Time |
|---|---|
| Detection to start (incident drills) | |
| Download / snapshot share | |
| Restore (instance available / pg_restore done) | |
| Verify | |
| Connection switch and post-restore checks | |
| **Total recovery** | |

- RTO target: shared tier ≤ 4 h (docs/10 §10), dedicated tier ≤ 8 h (NFR-AVL-005). Met: yes / no.
- RPO target ≤ 15 min: newest recovered transaction vs. the chosen point (PITR / WAL-G only).

## Findings and follow-ups

Defects found, with links to fixes or issues; runbook changes.
