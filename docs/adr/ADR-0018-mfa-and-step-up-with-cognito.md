# ADR-0018: MFA enforcement and step-up with Amazon Cognito

| Field | Value |
|---|---|
| Status | Accepted · Amended by [ADR-0023](ADR-0023-operator-sign-in-for-break-glass-across-user-pools.md) (a third app client: break-glass support, operator pool) |
| Date | 2026-09-26 |
| Deciders | Founder (on findings from the identity work for roadmap Task 9) |
| Amends / supersedes | Amends [ADR-0012](ADR-0012-managed-oidc-identity.md) (how MFA and step-up are enforced with the reference provider) and refines the operator identity in [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) |

## Context

FR-IAM-002 requires MFA for `owner`, `principal`, `office_admin` and every platform operator. Sensitive actions need step-up: MFA within the last 5 minutes (07 §5.2). ADR-0012 chose a managed OIDC provider with Amazon Cognito as the reference. Building Task 9 surfaced how Cognito actually behaves (checked September 2026; re-verify when Cognito changes):

- Cognito **access tokens have no `aud` claim**; the client is identified by `client_id`, and the token type by `token_use = "access"`.
- Access tokens carry `auth_time` but **no `amr`**. `amr` is reserved and cannot be set by the pre-token-generation Lambda.
- Customising access-token claims with the pre-token-generation Lambda requires the **Essentials** (or Plus) feature plan.
- MFA configuration is **pool-wide** (OFF / OPTIONAL / ON), not per role or group.
- Managed login supports `prompt=login` (since May 2025); `max_age` support is unverified.
- Access-token lifetime is configurable from 5 minutes to 1 day per app client.

So "MFA for some tenant roles only" and "MFA within 5 minutes" cannot be read directly from standard claims; we must add our own signal and check freshness ourselves.

## Decision

1. **Two user pools**, both on the **Essentials** feature plan, in ap-south-1:
   - **Operator pool** (platform admin panel, `SOS_PLATFORM_OIDC_*`): **MFA ON** for everyone.
   - **Staff pool** (school app, `SOS_OIDC_*`): **MFA OPTIONAL**; staff in privileged roles must enrol.
2. A **pre-token-generation Lambda** adds the custom claim **`sos:mfa` = `"true"`** to access tokens when the user has MFA enabled. Device remembering is **off** and adaptive authentication never skips MFA, so the claim means MFA was used for this sign-in.
3. **Token validation** in the API: signature (JWKS), issuer, `token_use = "access"`, `client_id` in the expected set for that surface (staff clients for tenant routes, operator client for `/api/v1/platform/*`), expiry. No `aud` check for Cognito access tokens; the `identity` module hides this behind its interface.
4. **Role-based MFA enforcement:** when the active membership holds `owner`, `principal` or `office_admin`, the API refuses the session unless `sos:mfa` is `"true"`: **`403` with `code: mfa_required`** and a link to enrol (FR-IAM-002). Operators always have MFA (pool ON) and the API still checks the claim.
5. **Step-up:** for step-up permissions the API requires `sos:mfa = "true"` **and** `auth_time` within the last 5 minutes; otherwise it returns **`428 step_up_required`**. The BFF then re-authenticates the user with `prompt=login` and retries.
6. **Access tokens live 10 minutes** on all app clients (FR-IAM-004).
7. Other providers behind `app/identity/` must supply equivalent signals (for example standard `amr` and `auth_time`); the claim name is config.

## Consequences

- Good: FR-IAM-002 and step-up are enforced by the API on every request, not only by the login screen.
- Good: staff without smartphones or on shared office PCs can still use non-privileged roles without MFA.
- Good: operators are fully separated from school users (own pool, own client, MFA always).
- Bad: Essentials pricing is per monthly active user; budget for it.
- Bad: we depend on a Lambda for a security claim; it is small, versioned in `infra/`, and covered by an integration test in staging.
- Bad: a privileged user without MFA is blocked until they enrol; onboarding must include MFA enrolment.
- Follow-up: verify in staging that `auth_time` survives refresh-token grants (if it resets, step-up must also check session start); verify `max_age` support; document the Lambda in 10 §5; tests in 12 §4.2 (428 cases) and a `mfa_required` API test.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| MFA ON for all staff in one pool | Simplest and strongest, but many office staff share PCs or lack smartphones; rejected for now, revisit after the pilot |
| One pool for staff and operators with groups | MFA is pool-wide, so operators would inherit OPTIONAL; weaker separation |
| Rely on `amr` in tokens | Not present in Cognito access tokens and cannot be added |
| Use ID tokens at the API | ID tokens are for the client, not for API authorization; audience semantics differ |
| Separate pool per privileged role | Users with several roles or schools would need several accounts |

## Related requirements

FR-IAM-002, FR-IAM-003, FR-IAM-004, FR-PLT-028, SEC-005, SEC-027, T1, T2, T22 (07 §4); 07 §5; 09 §1, §3; 10 §4–5.
