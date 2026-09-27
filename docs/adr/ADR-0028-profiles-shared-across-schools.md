# ADR-0028: A person's profile is shared across schools

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-27 |
| Deciders | Product owner (accepted 2026-09-27: "go with recommendations") |
| Amends / supersedes | Amends [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (definer allowlist: adds `core.user_membership_count`) |

## Context

`core.users` holds one row per person (FR-IAM-013: a teacher may work at two schools). The row
carries the profile: display name, email and preferred language. `PATCH /api/v1/users/{id}`
(US-102, `user.manage`, step-up) lets a school edit those fields; RLS (`users_in_tenant_update`)
allows the update whenever the person has a membership in the current school.

Facts on 2026-09-27:

1. A person with memberships in schools A and B has ONE profile. When school A renames the
   person or changes their email, school B sees the change too: in its staff list, in the
   names shown next to its records, and in the email it may use to reach them.
2. School A cannot see that the person belongs to school B (RLS shows only A's membership), so
   today A has no way to know that its edit reaches another school.
3. A membership with status `removed` still makes the profile visible in that school (staff
   list, history), so a removed member is still "in" that school for this purpose.

The product owner decided on 2026-09-27 (the lead's recommendation): the profile is shared
across schools; a school may edit it only when the person belongs to that school alone.

## Decision

1. `PATCH /api/v1/users/{id}` MUST refuse a change to `display_name`, `email` or
   `preferred_language` with `409 profile_shared` when the person has a membership (any status,
   including `removed`) in any other school. The whole request is refused; nothing is written
   and nothing is audited. Values equal to the stored ones are not a change (as before).
2. Status changes (activate, suspend, remove) of that person's membership in the calling school
   stay allowed: they concern only this school.
3. Counting a person's memberships across schools crosses tenants, so it goes through ONE new
   allowlisted `SECURITY DEFINER` function (ADR-0013):
   `core.user_membership_count(p_user uuid) RETURNS integer`
   - owned by `sos_definer` (NOLOGIN, NOBYPASSRLS), `search_path` pinned, EXECUTE only for
     `sos_app`;
   - requires tenant context (`insufficient_privilege` otherwise);
   - returns NULL unless `p_user` has a membership in the current school, so a school cannot
     probe people who are not its members;
   - returns only the number of memberships of that person (all statuses). No IDs, no school
     names, no statuses.
   - reads only `core.memberships`, which already carries the `definer_access` policy and the
     `SELECT` grant for `sos_definer`. No new policy, no new grant.
4. Migration `0028_profile_scope` adds the function (expand-only; downgrade drops it). The
   definer allowlists (`tests/security/rls_allowlist.yaml`,
   `tests/security/test_definer_functions.py`) list it.

## Consequences

- Good: one school can no longer silently change what another school sees about a shared
  person; the refusal tells the office why and what to do.
- Good: the new cross-tenant path leaks one integer for the caller's own member only.
- Bad / costs: a person who works at two schools cannot have their name or email corrected by
  either school; until a self-service profile page exists (not built), SchoolOS support or the
  person's own sign-in provider is the way to change it. A person removed from another school
  still counts, which is conservative on purpose (fact 3).
- Follow-up work: the web user screen shows the `profile_shared` message (en/te); a
  self-service profile page (the person edits their own profile) is a later story; `UserOut`
  could say up front that the profile is shared so the form can disable those fields.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep today's behaviour (last writer wins) | One school changes another school's view of a person without either knowing (fact 2). |
| Per-school profile copies (name/email on the membership) | Schema change on every read path and divergent names for one person; larger than the problem. |
| Count only non-removed memberships | A removed member's profile stays visible to that school (fact 3); changing it would still reach that school. |
| Let the API read other schools' memberships directly | Breaks tenant isolation (CLAUDE.md §6.1); the definer allowlist exists for exactly this. |

## Related requirements

US-102, FR-IAM-010, FR-IAM-013, SEC-001, SEC-026, PRV-013; docs/05 §4 (core.users),
docs/07 §7, docs/09 (users).
