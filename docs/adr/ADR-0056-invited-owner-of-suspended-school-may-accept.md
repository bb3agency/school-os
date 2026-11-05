# ADR-0056: An invited owner or principal of a suspended school may accept the invitation

| Field | Value |
|---|---|
| Status | Accepted (lead, 2026-11-05; owner may revisit; docs/14 D67) |
| Date | 2026-11-05 |
| Deciders | Lead engineer, applying the product owner's suspended-school decision of 2026-09-27 (docs/16 §5.5) |
| Amends / supersedes | None. Narrows a behaviour of the invitation functions of [ADR-0019](ADR-0019-invitation-acceptance-on-first-sign-in.md) and the [ADR-0023](ADR-0023-operator-sign-in-for-break-glass-across-user-pools.md) amendment (DL-09) within [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md)'s allowlist; ADR-0013 itself is unchanged |

## Context

A school is `suspended` for a security hold or for non-payment (docs/16 §5.5, §5.7). While it is suspended the **owner and principal** keep a short allowlist of routes (who am I, school choice, sign-in event, Plan & billing and the full data export: `SUSPENDED_SCHOOL_ALLOWLIST` and `SUSPENDED_ROLES` in `apps/api/app/authz/resolver.py`; BR-08, FR-PLT-004). Every other route answers 403 `tenant_suspended` for every role.

Lead decision D65 lets operators re-send the owner invitation while a school is suspended, so the owner can sort out the subscription. But the three invitation functions (`core.accept_invitations(text, text)`, `core.pending_invitations(text, text)` and `core.respond_to_invitation(text, text, uuid, boolean)`, all `SECURITY DEFINER` owned by `sos_definer`, last changed in migration `0045_invitation_consent`) answered an invitation only while its school was `active`. An owner invited into a suspended school therefore received the email but could not accept it, and so could not reach the billing page the allowlist keeps open for them (D65 recorded this as a limit in docs/16 §5.4).

## Decision

1. An invitation in a `suspended` school MUST be answerable (accepted at first sign-in, listed, accepted or declined explicitly) **only** when the invited membership holds a role whose key is `owner` or `principal`. This is the same test the suspended-school route allowlist applies (`SUSPENDED_ROLES`, matched on role keys; role keys are unique per school, and these two are system roles).
2. Every other invitation in a suspended school MUST stay unanswered, as before; it can be answered once the school is active again.
3. Invitations in `provisioning`, `offboarding` and `deleted` schools stay unanswerable. `active` schools are unchanged.
4. All other conditions of the 0045 functions are unchanged: the caller's own identity `(issuer, subject)`, an active user, an `invited` membership, created within 30 days and unexpired, and (for the sign-in acceptance) a brand-new account's only membership (DL-09).
5. The change is made by migration `0078_accept_invite_suspended`, which `CREATE OR REPLACE`s the three functions with the 0045 bodies and only the school-status condition replaced. Downgrade restores the 0045 bodies.
6. After accepting, the owner or principal is an ordinary member of a suspended school: the authz resolver still limits them to the allowlist. Acceptance is audited as before (`membership.invitation_accepted` in the school's chain, same transaction).

All three functions get the same condition, so the person who already has a SchoolOS account (who answers on `/me/invitations`, DL-09) is treated the same way as a brand-new account accepted at sign-in.

### Security reasoning

- **No new definer function and no new `definer_access` policy.** The three functions keep their signatures, owner (`sos_definer`, NOLOGIN, **NOBYPASSRLS**), `SECURITY DEFINER`, pinned `search_path = pg_catalog, pg_temp` and EXECUTE for `sos_app` only (re-applied by the migration). The allowlist in `apps/api/tests/security/rls_allowlist.yaml` and the signature and grant pins (`tests/security/test_definer_functions.py`, `tests/api/test_accept_invitations.py`, `tests/api/test_invitation_consent.py`) stay as they are and stay green.
- **No new reach.** The new condition reads `core.membership_roles` and `core.roles`, which `core.pending_invitations` already read under their existing `definer_access` policy and grants. It only widens *which* of the caller's own invitations can be answered; it never lets anyone answer someone else's invitation or see another school's data.
- **No new access in the school.** Acceptance makes the membership active; what it can do in a suspended school is still decided by the resolver's allowlist, pinned by `tests/authz/test_suspended_allowlist.py` and the route enumeration in `tests/api/test_suspended_school.py`.
- **One definition of "owner or principal".** The SQL names the same two role keys as `SUSPENDED_ROLES`; `tests/api/test_accept_invitation_suspended.py` asserts the two stay equal, so a change to the allowlist roles fails a test until the function is changed by a new migration.

## Consequences

- Good: the D65 re-send is useful; an invited owner of a suspended school can accept and then pay or export.
- Good: no new privileged surface; the change is one condition in three existing functions.
- Bad / costs: the role keys are written in two places (Python allowlist and SQL); a test ties them together.
- Bad / costs: a suspended school's invited principal can also become active. This matches the allowlist, which already gives the principal the same reach as the owner.
- Follow-up work: none. The lead re-chains the migration at merge.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| A new definer function only for owner invitations of suspended schools | Adds a function to the ADR-0013 allowlist and a second acceptance path for the same table; the existing functions already read the roles they need |
| Accept every invitation in a suspended school | Teachers and office staff would get active memberships they cannot use (every route is 403), and the school's staff list would change while it is held for a security reason |
| Owner only, not the principal | The suspended-school allowlist treats owner and principal alike (owner decision 2026-09-27); a different rule for acceptance would leave an invited principal with access they cannot claim |
| Change only `core.accept_invitations`, not the list and the explicit answer | An owner who already has a SchoolOS account (owner of another school) accepts on `/me/invitations` (DL-09) and would still be stuck |
| Let the operator activate the owner's membership from the control plane | The control plane never writes tenant tables (ADR-0020), and DL-09 requires the person's own consent |

## Related requirements

BR-08, FR-PLT-004, FR-IAM-013, US-102, SEC-001, SEC-026; docs/16 §5.4, §5.5; docs/05 §3 (definer functions); lead decisions D65, D67.
