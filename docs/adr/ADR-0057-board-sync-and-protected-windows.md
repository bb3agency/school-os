# ADR-0057: Board codes reach the control plane, and protected exam windows come from the portal calendar

| Field | Value |
|---|---|
| Status | Accepted (lead engineer, 2026-11-08; owner may revisit; docs/14 D78) |
| Date | 2026-11-08 |
| Deciders | Lead engineer |
| Amends / supersedes | Amends [ADR-0020](ADR-0020-control-plane-boundaries-and-guaranteed-audit-copies.md) §2 (what `platform` may read from schools now includes board codes). Supersedes the deferral recorded in docs/14 D77 |

## Context

Checked on 2026-11-08 in the code:

- `platform.deployments.boards` is written only at provisioning (`app/platform/provisioning.py`, `app/platform/provision_dedicated.py`). A school that changes its boards in its settings (`tenancy.service.update_tenant_settings`, FR-TEN-020) changes `core.tenants.boards` only; the control plane keeps the provisioning value forever.
- `apps/api/app/platform/billing.yaml` had `protected_windows: []`, so `billing.in_protected_window` never protected any school: a billing suspension (docs/16 §9.3, FR-PLT-014) could cut a school off during its board registration or list-of-candidates window, which docs/16 principle 5 forbids.
- The only maintained list of board windows is the portal calendar `app/dq/config/portal_calendar.yaml` (ADR-0053). Every date in it is an expectation from secondary sources (`verified: false`, owner decision D3).
- `apps/api/tests/platform/test_boundaries.py` forbids `platform` from importing `app.dq` at all (data modules), not only its repository and models; import-linter (`dq-internals`) forbids only `app.dq.repository`/`app.dq.models`.
- Dedicated hosts: the signed heartbeat (`HbUsage`, `HbHealth`, ... in `app/platform/schemas.py`; `app/platform/fleet.py`) carries no board codes today, and a dedicated school is not in the shared database's `core.tenants`, so the daily collector cannot read its boards.

Board codes (`CBSE`, `BSEAP`, `CISCE`, ...) are school-level configuration, not personal or student data. Billing needs them; nothing else on the control plane does.

## Decision

1. **Board codes are a usage-style read.** `app.tenancy.service.tenant_board_codes(tenant_id)` opens the school's own `tenant_session` (sos_app, RLS `own_tenant`) and returns the board codes only (no name, no settings, no class-to-board map). `platform` MAY call it; it is added to `TENANCY_ALLOWED` in `test_boundaries.py`, and only `platform/usage.py` may call it (pinned by `test_ADR_0057_board_codes_are_read_only_by_the_usage_collector`). ADR-0020 §2's list of allowed calls gains this one entry; nothing else in the boundary changes (no new `tenant_session` file, no new tenant relation in raw SQL).
2. **The daily collector keeps `platform.deployments.boards` current.** `usage.collect_daily` (01:30 IST) calls `usage.sync_boards(tenant_id)` for every active or suspended shared school, in its own `try` so a failure never stops the usage counts. When the set of codes differs (order is not a change), it updates the deployment row under `FOR UPDATE` and records `deployment.boards_synced` (actor `system`; summary `boards` and `previous`, codes only) with `audit.record_platform` in the same transaction. An unchanged set writes and audits nothing.
3. **Protected windows are a copy of the calendar's board windows in `billing.yaml`.** Every portal calendar event that names boards (registration, list of candidates, nominal roll) becomes one `protected_windows` item with the event's `key`, English label as `name`, `boards`, `opens_on`/`closes_on` as `start`/`end`, and `verified` carried as is (`false`). Events for every school (UDISE+, APAAR: `boards: []`) are NOT copied: they run for months and would block every billing suspension for most of the year. `tests/platform/test_board_sync.py::test_ADR_0057_protected_windows_stay_equal_to_the_portal_calendar` fails until both files agree, so a calendar release refreshes `billing.yaml` in the same PR.
   - Why a copy and not a runtime read through a `dq` public function: the control plane never imports `app.dq` (ADR-0020 §2 and the boundary test, which is stricter than import-linter), it must start and decide billing without the tenant-side data modules, and a copy keeps an operator decision reviewable in one file. The sync test removes the drift risk of a copy.
4. **`billing.protected_window(boards, day)`** returns the first window containing `day` whose boards meet the school's `deployments.boards` (a window without boards applies to every school). A billing suspension inside it needs a `platform_owner` with `exam_window_override` (unchanged rule). The `409 exam_window` refusal names the window and, when `verified` is false, says "expected <start> to <end>, not verified"; the owner's override records `exam_window` (the key) and `exam_window_verified` in the `subscription.suspended` audit summary. Security suspensions stay immediate (AA-16). A hand-written window without `verified` counts as verified.
5. **Dedicated hosts: gap, recorded.** Their `deployments.boards` stays the provisioning value until the heartbeat carries board codes (a heartbeat change, owned by the heartbeat work in progress, ADR-0058). Until then there is no operator action that edits `deployments.boards` (none is built); a billing suspension of a dedicated school changes only its subscription and does not switch its environment off (docs/16, "Suspend for non-payment"), so the gap cannot cut a dedicated school off during an exam window.

## Consequences

- Good: a school is protected in its own boards' windows from the day after it changes boards; operators see that the dates are unverified expectations before overriding; the control plane still reads no personal data and imports no data module.
- Bad / costs: two files carry the same dates (kept equal by a test); board exam sittings themselves (February to April) are not in the portal calendar, so they are not protected until the calendar (or a later release of `billing.yaml` with a sourced, separately marked window) adds them; dedicated schools are not synced.
- Follow-up work: add board codes to the dedicated heartbeat and sync them in `fleet.ingest`; add exam sitting windows to the calendar with sources; a future automatic dunning step (none exists today: suspension is always manual, docs/16 §9) MUST check `billing.protected_window` too.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Load `portal_calendar.yaml` at runtime through `app.dq.service` | `platform` may not import `app.dq` (ADR-0020 §2, boundary test); it would couple control-plane billing to a tenant-side module |
| Read the calendar file by path from `platform` without importing `dq` | Hides the same coupling from the boundary test; a packaging change in `dq` would silently empty the windows |
| A definer function returning boards for all schools | A new `SECURITY DEFINER` function needs its own ADR and a migration; the per-school usage-style read already exists as a pattern (B2, B3) |
| Sync boards on every settings save (tenant side pushes to platform) | Tenant-side modules must not import `app.platform`; the daily pull is enough for windows that last weeks |
| Protect the all-school portals (UDISE+, APAAR) too | Would block billing suspensions for most of the year for every school |

## Related requirements

FR-PLT-014, FR-PLT-020, FR-TEN-020, FR-RDY-001; docs/16 §5.2, §7 (`deployments.boards`), §9.3, §16; docs/04 (control-plane boundary); ADR-0020, ADR-0053, ADR-0017.
