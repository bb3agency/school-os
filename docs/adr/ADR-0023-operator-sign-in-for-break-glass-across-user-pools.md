# ADR-0023: Operator sign-in for break-glass across the two user pools

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-09-27 |
| Deciders | Founder / product owner (security review before acceptance) |
| Amends / supersedes | Would amend [ADR-0012](ADR-0012-managed-oidc-identity.md), [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (definer allowlist: `core.resolve_login`, `core.find_user_id_by_subject`, `core.create_user_for_invite` gain an issuer) and [ADR-0018](ADR-0018-mfa-and-step-up-with-cognito.md) (a third app client) once accepted |

## Context

Break-glass (07 §6.4, US-103, FR-OPS-004, SEC-021, T17) gives a SchoolOS operator temporary,
read-only, audited access to one school after the school's owner or principal approves (or, in an
emergency, after two operators confirm). The school side (`app/breakglass`, M1) opens a
`platform_support` membership for the operator in the school's own transaction
(`identity.open_breakglass_membership`, via `core.create_user_for_invite` or, in an emergency,
`core.find_user_id_by_subject`).

ADR-0018 puts operators and school staff in **two Cognito user pools**: the operator pool (MFA ON,
operator app client, `SOS_PLATFORM_OIDC_*`, admin panel only) and the staff pool (MFA OPTIONAL,
staff app clients, `SOS_OIDC_*`, school app). Facts in the code on 2026-09-27:

1. The membership is attached to a `core.users` row whose `idp_subject` is the operator's
   **operator-pool** `sub` (`platform.operators.idp_subject`, passed as
   `SchoolBreakGlassRequest.operator_subject`).
2. Tenant routes accept only **staff-pool** tokens (`get_tenant_token_verifier`: staff issuer and
   client), and `core.resolve_login(p_subject)` looks users up by `sub` alone.
3. `core.users` has **no issuer column**: `idp_subject` is unique across all pools.

So today an approved grant cannot be used: no token the school app accepts carries the operator's
operator-pool `sub` (fails closed, which is safe but makes break-glass unusable). It also mixes two
subject namespaces in one column: a staff-pool account whose `sub` happened to equal an operator's
operator-pool `sub` would receive the support membership. Cognito `sub` values are random UUIDs, so
this is improbable in production, but the design should not depend on it (the local OIDC stub uses
readable subjects), and 07 threat T1/T2 (token confusion) applies to any fix.

What any fix must keep:

- Operators have **no standing access** to school data (T17); access exists only while a grant is
  active, only with `platform_support` (read-only, scoped), and every call is written to the
  school's chain as `breakglass.access` (07 §6.4).
- MFA for every operator and step-up (≤ 5 min) at the start of support access (FR-IAM-002,
  SEC-005). The operator pool stays MFA ON; the staff pool stays OPTIONAL (ADR-0018).
- Revocation works from both sides: the school revokes the grant; SchoolOS deactivates the
  operator (operator pool disable + `platform.operators.status`).
- `sos_platform` never gains tenant-table access; the control plane never writes tenant tables
  (CLAUDE.md §6.1, ADR-0020). No new `SECURITY DEFINER` function without this ADR.
- Tokens never reach browser JavaScript (BFF, ADR-0012). Dedicated hosts keep working (ADR-0015;
  16 §19 Q4).

## Options

### A. A second, staff-pool account per operator ("support identity"), linked in the control plane

Each operator also gets an account in the staff pool (created by the operator-invite runbook, MFA
required for it by the pre-token Lambda). Its `sub` is stored as
`platform.operators.support_subject` after a **linking ceremony** (the operator, signed in to the
admin panel with step-up, signs in to the school app once; the BFF posts the staff-pool token to a
platform endpoint that records the `sub`). Break-glass uses `support_subject` instead of the
operator-pool `sub`.

- Good: no change to token verification or definer functions; the school app works unchanged.
- Bad: two credentials and two MFA enrolments per operator; the staff pool is MFA OPTIONAL, so MFA
  for these accounts depends on our Lambda and enrolment discipline, not on the pool; deactivating
  an operator must also disable the second account (two kill switches to keep in sync); the linking
  ceremony is a new trust step to get right; the namespace problem (fact 3) stays.

### B. Federate the operator pool into the staff pool (OIDC identity provider)

Register the operator pool as an OIDC identity provider of the staff pool ("SchoolOS support"
button on the school app's managed login). Cognito creates a federated staff-pool user
(`identities` claim) whose `sub` is new; break-glass would store that federated `sub`.

- Good: one credential per operator; MFA happens in the operator pool (ON).
- Bad: the staff pool's own MFA and step-up signals do not apply to federated users; `sos:mfa`
  and a fresh `auth_time` would have to be derived from upstream claims in the Lambda (Cognito
  does not pass `amr`; `prompt=login` must reach the upstream pool), which is fragile and hard to
  test. Any operator could sign in to the school app at any time and get a staff-pool user (no
  membership, but a standing identity in the staff namespace). The federated `sub` only exists
  after the first federated sign-in, so emergency access still fails closed for new operators.
  Every dedicated host's app client would need the provider enabled.

### C. Accept operator-pool tokens on tenant routes for support memberships only (recommended)

A dedicated **support app client** in the operator pool (`SOS_SUPPORT_OIDC_CLIENT_ID`, callback
URLs on the school app hosts, MFA ON through the pool, 10-minute access tokens) is the only way an
operator signs in to the school app. The API accepts its tokens on tenant routes through a second,
narrow resolution path, and identities are namespaced by issuer:

1. `core.users` gains `idp_issuer` (expand/migrate/contract: add nullable, backfill existing rows
   with the staff issuer, then `NOT NULL`, unique `(idp_issuer, idp_subject)` replacing the unique
   `idp_subject`). `core.resolve_login`, `core.find_user_id_by_subject` and
   `core.create_user_for_invite` take the issuer. These are changes to allowlisted definer
   functions, decided by this ADR; no new definer function and no new `definer_access` policy.
2. The tenant principal dependency accepts two token families: staff-pool tokens (unchanged) and
   support-client tokens (operator issuer, `client_id` = support client, `token_use = access`,
   `sos:mfa = "true"`). The operator admin client is **never** accepted on tenant routes, and the
   support client is never accepted on `/api/v1/platform/*`.
3. For a support-client principal, resolution returns **only** memberships that hold exactly the
   `platform_support` role and an unexpired `expires_at` (enforced in the resolver and by a filter
   in `core.resolve_login` when the issuer is the operator issuer). Any other membership of that
   identity is refused (`403 breakglass_only`). The read-only guard and `breakglass.access`
   auditing apply unchanged.
4. Starting a support session needs step-up (`auth_time` ≤ 5 min) and is audited as
   `breakglass.session_started` in the school's chain (grant ID, operator ID, session ID; no
   token) and, through the reporting path that already exists, in the platform chain. The admin
   panel links to the school app with the grant ID; the BFF runs Authorization Code + PKCE against
   the support client and keeps the tokens server-side in a separate `__Host-` support cookie.
5. Break-glass stores the operator issuer with the subject (`open_breakglass_membership` gets it
   from `SchoolBreakGlassRequest`), so the emergency path can find or create the right user
   without a prior school sign-in (the operator row is the account's source of truth).
6. Dedicated hosts: the support client is configured per host (`SOS_SUPPORT_OIDC_*`, off by
   default); a host that does not enable it keeps break-glass unavailable (fail closed).

- Good: one identity and one MFA per operator, from the MFA-ON pool; disabling the operator in
  the operator pool ends support sign-in everywhere; subjects can no longer collide across pools;
  emergency access works for any active operator; no second account, no federation, no token
  minting by SchoolOS.
- Bad: the tenant API trusts a second issuer, so the verifier and resolver grow and must be tested
  hard (token-confusion tests below); a migration on `core.users` and changes to three definer
  functions; the web app needs a support login flow and cookie.

### D. SchoolOS mints a short-lived, grant-bound token (token exchange, RFC 8693 style)

The admin panel exchanges the operator's token (step-up) for a SchoolOS-signed JWT bound to one
grant, verified by the tenant API with a KMS-held key.

- Good: very tight binding (one grant, one school, minutes).
- Bad: SchoolOS becomes a token issuer (key management, rotation, revocation lists, replay),
  exactly the in-house identity work ADR-0012 avoided; a new high-value signing key.

### E. One pool for operators and staff

Rejected already by ADR-0018 (MFA is pool-wide; operators would inherit OPTIONAL).

## Threats and how option C answers them

| Threat | Answer in option C |
|---|---|
| Operator admin token replayed on tenant routes (T1/T2 token confusion) | Tenant routes accept the operator issuer only with the support `client_id`; admin-client tokens are refused. Tests enumerate issuer × client × route family. |
| Support token used on the control plane | `/api/v1/platform/*` accepts only the admin client (existing check); test. |
| Operator reaches data without an active grant (T17) | Resolution returns only unexpired `platform_support` memberships for operator-issuer principals; the membership expires with the grant; the school can revoke; tests for expired, revoked and non-support memberships. |
| Subject collision between pools | `(idp_issuer, idp_subject)` is the identity; a staff account with the same `sub` is a different user. |
| Deactivated or departed operator | Disabled in the operator pool (no new tokens; access tokens live ≤ 10 min) and `platform.operators.status` checked when the request is pulled (already done: `operator_status`); the school also sees and can revoke the grant. |
| Stolen support session | `__Host-` HttpOnly cookie, 10-minute tokens, refresh rotation, idle timeout (ADR-0012); step-up at session start; every call audited in the school's chain. |
| Writes through support access | `platform_support` is read-only (`403 breakglass_read_only`) and holds no write permission. |
| Operator on a school they have ordinary access to | `open_breakglass_membership` already refuses people with ordinary access; with issuers the check compares the operator identity, not a staff account. |

## Audit

- School chain (existing): `breakglass.requested`, `breakglass.approved` / `breakglass.denied`,
  `breakglass.emergency_opened` / `breakglass.emergency_access_opened`,
  `membership.breakglass_opened` / `membership.breakglass_closed`, every call as
  `breakglass.access` (`via_breakglass: true`), `breakglass.revoked`, `breakglass.expired`,
  `breakglass.request_expired`. New: `breakglass.session_started` (grant, operator and session
  IDs), and `auth.login.succeeded` for the support principal carries the issuer kind
  (`operator_support`).
- Platform chain: request and confirmation events (existing) and the outcomes reported by the
  school side, including the session start (new report status or event, IDs only).
- No tokens, emails or names in either chain; IDs and codes only (CLAUDE.md §6.5).

## Decision (proposed)

Adopt **option C**. Until it is implemented, break-glass stays unusable by design (fail closed);
no operator gets a staff-pool account in the meantime. Option A is the fallback if the product
owner does not want the tenant API to trust a second issuer.

## Consequences

- Good: operators keep one identity with MFA always on; break-glass becomes usable, including the
  emergency path; the subject-namespace ambiguity in `core.users` is removed for everyone.
- Bad / costs: migration on `core.users` (expand/contract over two releases); changes to three
  allowlisted definer functions and the definer allowlist test; a second verifier on tenant routes;
  a support login flow in the web app (en/te strings, keyboard use, 1366×768); Cognito support
  client per environment and per dedicated host.
- Follow-up work (after acceptance): migration `core.users.idp_issuer`; definer changes and their
  tests (12 §4.9); `app/identity` verifier + resolver for the support client; `app/breakglass`
  passes the issuer; web support login and banner; docs 05 §3.3–3.4, 07 §5 and §6.4, 09 §1,
  10 §5 and §11 (`SOS_SUPPORT_OIDC_*`), 16 §5.15; tests: token confusion matrix, expired / revoked
  / non-support membership refusal, step-up at session start, emergency path for an operator who
  never signed in to a school, dedicated host with the client disabled.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| A. Second staff-pool account per operator | Two credentials and kill switches per operator; MFA depends on enrolment in an OPTIONAL pool; namespace problem remains. Kept as fallback. |
| B. Federate the operator pool into the staff pool | MFA and step-up signals do not survive federation reliably; standing federated identities in the staff pool; per-host provider setup. |
| D. SchoolOS-minted grant tokens | Makes SchoolOS a token issuer (keys, rotation, revocation); contrary to ADR-0012. |
| E. One pool | Rejected in ADR-0018 (pool-wide MFA). |

## Related requirements

US-103, FR-OPS-004, FR-IAM-001..004, FR-PLT-028, SEC-005, SEC-021, SEC-026, SEC-027, SEC-029,
T1, T2, T17 (07 §4); 05 §3.3–3.4; 07 §5, §6.4; 09 §1; 10 §5, §11; 16 §5.15, §19 Q4;
`.handoff/HANDOFF.md` §3.5.
