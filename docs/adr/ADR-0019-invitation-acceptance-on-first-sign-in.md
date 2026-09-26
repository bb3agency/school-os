# ADR-0019: Invitation acceptance on first sign-in

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-26 |
| Deciders | Lead engineer, for the founder (standard invite flow; recorded because it adds a definer function) |
| Amends / supersedes | Amends [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (adds one function to the pinned definer allowlist) |

## Context

Staff are invited with `POST /users` (and a new school's owner with `core.create_owner_invite` at provisioning). Both create a membership with status `invited`. `core.resolve_login` only returns **active** memberships, so an invitee could never sign in: someone holding `user.manage` had to activate them by hand, and a newly provisioned school's owner could not be activated by anyone at all (no active member exists yet). Accepting an invite needs to change a membership row before the user has any tenant context, which RLS forbids for `sos_app`.

## Decision

1. Add the SECURITY DEFINER function `core.accept_invitations(p_subject text) RETURNS TABLE (tenant_id, membership_id, user_id)`, owned by `sos_definer` (NOBYPASSRLS), `search_path` pinned, EXECUTE for `sos_app` only. It activates **only**:
   - memberships of the active user whose IdP subject is `p_subject`,
   - with status `invited` (never `suspended` or `removed`),
   - created within the last **30 days** (invite lifetime) and not past `expires_at`,
   - in schools whose status is `active`.
2. `sos_definer` gets `UPDATE (status, updated_at, version)` on `core.memberships` and nothing more. `core.memberships` already carries the `definer_access` policy.
3. API route `POST /api/v1/me/accept-invitations` (guard: authenticated principal, no active school needed). The subject comes **only** from the verified access token. A principal with an active privileged membership and no MFA gets `403 mfa_required`, as on every route.
4. The activation and one `membership.invitation_accepted` audit event per school happen in **one transaction**; the tenant context is switched per school with transaction-local `set_config`, so each event lands in that school's own chain (invariant 7).
5. The BFF calls the route after the OIDC callback, before `POST /me/login-event`.

## Consequences

- Invitees and new school owners can sign in without manual activation; the IdP account (created by an admin or the provisioning flow) is the proof of identity.
- An expired invite (older than 30 days) must be re-sent; manual activation with `PATCH /users/{id}` still works.
- The definer allowlist test (`rls_allowlist.yaml`) pins the new function; tests cover own-invites-only, expiry, school status, disabled users, non-invited statuses, idempotency and audit.

## Alternatives considered

- **Manual activation only** (status quo): no path for a new school's first owner; rejected.
- **Accept inside `core.resolve_login`**: a read function with a hidden write, and no audit event per school; rejected.
- **Grant `sos_app` a cross-tenant UPDATE policy on memberships**: widens RLS for every request; rejected.

## Related requirements

FR-IAM-013, FR-TEN-003, US-102, US-201, SEC-001, SEC-007.
