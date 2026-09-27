# ADR-0021: Export access and step-up

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-27 |
| Deciders | Product owner |
| Amends / supersedes | none (applies ADR-0018's step-up to every export; changes the export rules in docs/05 §5.5, 07 §6.2, 09 Exports) |

## Context

The exports module (M1; US-501 AC4, US-901, FR-EXP-001..004) builds board and portal pre-check reports (CISCE, UDISE+) and generic student lists as XLSX, CSV and PDF files in S3. As first built:

- Only student lists and pre-checks with `include_sensitive` needed step-up (MFA within 5 minutes, ADR-0018). A plain pre-check still carries names, dates of birth and parents' names for up to 2,000 students.
- UDISE+ needs the social `category`, a restricted (C3) attribute. The code masked C3 values unless `include_sensitive` was set by a holder of `student.read_sensitive`, but the `export.requested` audit event recorded `sensitive_columns` only for student lists; for pre-checks it was always empty.
- Only the member who requested an export could see or download it (404 for everyone else). Principals and owners could not see what their staff had exported, nor fetch a file when the requester was away.

These were open questions 1-3 in `.handoff/HANDOFF.md` §3. The product owner decided them on 2026-09-27.

## Decision

1. **Step-up for every export.** Creating any export (board or portal pre-check, student list) MUST require step-up: a sign-in with MFA within the last 5 minutes, else `428 step_up_required`. `export.board` and `export.portal` become step-up permissions (✓ᴿ in 07 §6.2). `POST /exports` is guarded by `require_any("export.board", "export.portal", step_up=True)`; `require_any()` gains a `step_up` option that is allowed only when every listed permission is a step-up permission, and checks the permission (403) before step-up (428). The service checks step-up again for pre-checks and student lists. Downloading your own export keeps its rule (step-up for student lists and exports with restricted values).

2. **UDISE+ category (C3).** Restricted values such as `category` MAY be included in a pre-check only when the requester explicitly opts in (`include_sensitive=true`), holds `student.read_sensitive` (else `403 sensitive_not_allowed`, never a silent downgrade) and has stepped up (decision 1). Without the opt-in they are masked (`••••`). The `export.requested` audit event MUST list the restricted columns included (`sensitive_columns`, e.g. `["category"]`) and MUST NOT contain their values.

3. **Who sees whose exports.**
   - New permission **`export.read_all`** (sensitivity `sensitive`, no step-up): see the details of every export of the school: kind, profile, formats, status, requester, student count, created/finished/expiry times, files (format, type, size); never the files' contents. Granted to `owner`, `principal` and `office_admin` (the school-level administrative roles that already hold export permissions); not to `exam_coordinator`, office staff or teachers. `GET /exports` keeps returning only the caller's own exports by default; `GET /exports?requested_by=all` returns every export of the school and needs `export.read_all` (403 otherwise). `GET /exports/{id}` shows someone else's export only to holders of `export.read_all`.
   - New permission **`export.download_any`** (sensitivity `critical`, step-up): download the files of an export someone else requested, **always** with step-up. Granted by default only to `owner`; schools can give it through custom roles. It never widens what the downloader could otherwise see: it also needs school-wide `student.read_basic`, and school-wide `student.read_sensitive` when the export includes restricted values (403 otherwise).
   - Someone else's export stays **404** for a caller holding neither permission (existence is not revealed, as for other schools' ids). A holder of `export.read_all` without `export.download_any` gets **403 `not_own_export`** on the download (they can already see the export exists).
   - Every download stays audited: `export.downloaded` records `own_export` (true/false) and `requested_by_membership`.
   - Responses expose the minimum about the requester: `requested_by = {membership_id, display_name}`; plus `own` and `can_download` so screens do not have to guess. No e-mail, roles or other staff details. Logs never contain display names.

## Consequences

- Good: bulk personal data never leaves the system without a fresh MFA sign-in; the audit chain shows exactly which restricted columns were included and who downloaded whose file; principals and owners can supervise exports without asking staff.
- Good: `download_any` cannot be used to see students outside the downloader's reach.
- Bad / costs: exam coordinators and office admins now step up before every pre-check (at most once per 5 minutes). The owner downloads others' exports only with step-up each time.
- Bad / costs: **existing schools do not get the new grants automatically.** Migration `0019_export_access` updates the global catalog (`core.permissions`: the two new keys; `export.board`/`export.portal` marked step-up), but system roles are tenant rows under FORCE RLS that the migrator cannot see or write, and no allowlisted definer function grants permissions (docs/05 §3.3-3.4, §14). New schools get the grants at provisioning (`identity.service.clone_system_roles`, idempotent). The step-up requirement (decision 1) applies everywhere at once because the route guard and service enforce it.
- Follow-up work: (a) a safe way to deliver new `roles.yaml` grants to existing schools, for example an operator-run, idempotent, audited command that calls `clone_system_roles` in each school's own `tenant_session` through `core.list_tenant_ids`; it needs its own decision (it writes role grants in every school) and is not built here. (b) The web screens: an "All exports" view for `export.read_all` and a download button driven by `can_download`, with the 428 re-authentication flow.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Step-up only for sensitive pre-checks (as first built) | A plain pre-check still carries names, dates of birth and parents' names in bulk (decision 1). |
| Silently mask C3 values when a caller without `student.read_sensitive` asks for them | The caller would believe the file is complete; a clear 403 is safer and auditable. |
| Let `export.read_all` also download | Seeing that an export exists is much less sensitive than holding its file; downloads by someone other than the requester need their own, step-up permission. |
| Grant `export.download_any` to principals by default | The product owner limited it to the school owner; schools can extend it through custom roles. |
| Give the download_any holder whatever the export contains, regardless of their own reach | A custom role with a class-scoped read could then pull the whole school's data; the check keeps the permission from widening access. |
| Grant the new permissions to existing schools inside migration `0019` | The migrator cannot see or write tenant role rows (FORCE RLS) and adding a definer function or policy for it needs its own ADR (ADR-0013); stopped and reported instead. |

## Related requirements

FR-EXP-001..004, US-501, US-901, SEC-003, SEC-005, SEC-015, SEC-017, PRV-003; docs/05 §5.5, 07 §5.2 and §6.2, 08 §4, 09 Exports, `.handoff/HANDOFF.md` §3 items 1-3.

## Amendments (2026-09-27)

- Follow-up (a) is settled by [ADR-0022](ADR-0022-system-role-sync-for-existing-schools.md): operators run `python -m app.identity.sync_system_roles --apply` after the release's migrations, and existing schools then receive `export.read_all` (owner, principal, office_admin) and `export.download_any` (owner). The decision above is unchanged.
