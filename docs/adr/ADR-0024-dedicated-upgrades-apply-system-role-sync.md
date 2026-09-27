# ADR-0024: Dedicated upgrades apply the system-role sync

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-27 |
| Deciders | Product owner (decision of 2026-09-27, "go with recommendations", on ADR-0022's open follow-up) |
| Amends / supersedes | Amends [ADR-0022](ADR-0022-system-role-sync-for-existing-schools.md) decision 9 and its "apply automatically … as a side effect of a deploy" alternative, for dedicated hosts only. No definer function, policy, role or RLS change, so ADR-0013 is unchanged. |

## Context

ADR-0022 made the system-role sync (`python -m app.identity.sync_system_roles`) a manual post-release step and left open whether `deploy/dedicated/scripts/upgrade.sh` should run it. On the dedicated tier every school is its own host, upgraded in fleet waves through SSM Run Command (docs/10 §15.5). A manual step per host per release is easy to miss, and a missed run leaves a school without grants its release added (ADR-0021 was the first case). The command is idempotent, audits every change in the school's own chain, records nothing when the school is already in line, and on a dedicated host handles only `SOS_DEDICATED_TENANT_ID`.

## Decision

1. `upgrade.sh` runs `scripts/sync-system-roles.sh --apply` of the newly activated release right after `db-bootstrap` and `migrate`, before any service restarts (helper `upgrade_sync_system_roles` in `scripts/lib.sh`). It runs on every upgrade, not only when release notes flag a roles.yaml change.
2. It **never** passes `--prune`. Removing grants can lock staff out mid-term and stays a deliberate operator action after a dry run (ADR-0022 decision 3).
3. Exit `0` (in line or applied) continues the upgrade. Any other exit fails the upgrade loudly: logged at ERR with the reason, then the existing ERR trap rolls the host back to the previous release and publishes `UpgradeSuccess 0`. That covers `1` (refused: wrong database role or migrations missing), `4` (the school failed or a custom role holds a system role key), `2`, and `3`, which cannot occur with `--apply`.
4. A rollback does not remove grants the sync already added. They are additive, audited, and name permissions the release's (expand-only, also not rolled back) migrations put in the catalog.
5. Unchanged from ADR-0022: the command, its isolation (one `tenant_session` as `sos_app`), audit events, dry-run default, conflict handling, and the **shared tier**, where operators still run the one-off ECS task after each release's migrations (docs/10 §8 item 7).

## Consequences

- Good: dedicated schools get new system-role grants with the release that adds them; no per-host manual step.
- Good: a conflict or refusal stops the wave at the canary host instead of passing unnoticed.
- Bad / costs: a school with a custom role that uses a system role key (exit 4) cannot be upgraded until an operator resolves the conflict. This is intended: the upgrade would otherwise leave that school's access out of line with the release.
- Bad / costs: access changes happen as part of a deploy on dedicated hosts. They stay visible: every change is an audit event in the school's chain, and the upgrade log carries the command's per-change lines (keys only).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep it manual (ADR-0022 as written) | Easy to miss per host and per release; the fleet grows with each dedicated school. |
| Run only when release notes flag a roles change | Needs a flag carried in the bundle; the run is already a no-op when nothing changed. |
| Run with `--prune` | Removing access automatically can lock staff out; stays a reviewed step. |
| Warn but continue on exit 4 | The host would run a release whose access model it does not match, silently. |

## Related requirements

FR-IAM-010, FR-IAM-011, FR-IAM-014, SEC-003, SEC-007, NFR-FLT-002; ADR-0021, ADR-0022; docs/10 §8 item 7 and §15.5, `deploy/dedicated/README.md`, `apps/api/tests/deploy/test_upgrade_role_sync.py`.
