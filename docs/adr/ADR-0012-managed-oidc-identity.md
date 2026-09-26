# ADR-0012: Managed OIDC identity provider

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) · Amended by [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (separate OIDC client and session for platform operators) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

Staff sign in from shared office PCs and phones. Privileged roles need MFA; passwords must be screened against breach lists; lockouts and recovery must be safe. Building password storage, MFA and account recovery in-house is risky for a solo developer. Tokens must never reach browser JavaScript.

## Decision

- Use a **managed OIDC provider**, reference **Amazon Cognito** in ap-south-1, behind the `app/identity/` module interface so it can be replaced.
- The Next.js **BFF** runs Authorization Code + PKCE; tokens are held server-side; the browser gets only an `HttpOnly`, `Secure`, `SameSite=Lax` `__Host-` session cookie (FR-IAM-001; 07 §5).
- MFA (TOTP or passkey) is mandatory for `owner`, `principal`, `office_admin` and all platform accounts (FR-IAM-002).
- Access tokens ≤ 10 min; refresh tokens rotate with reuse detection; idle timeout 15 min (5–30 configurable), absolute 12 h (FR-IAM-003/004).
- Step-up authentication (MFA within 5 minutes) for sensitive actions.
- The API verifies JWT signature (JWKS cache), issuer, audience and expiry on every request.
- Locally, a dev OIDC stub is used; it is disabled in staging and production builds.

## Consequences

- Good: MFA, password policy, breach screening and recovery handled by a managed service in India.
- Good: the interface keeps an exit path to Keycloak or another provider.
- Bad: Cognito customisation limits (e.g., login UI, username rules) may need workarounds.
- Bad: staff without email need admin-created usernames and forced first-login password change.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keycloak (self-hosted) | Another stateful service to patch, back up and secure |
| Auth0 / Okta | Cost at scale; data location outside India for some tiers |
| In-house password + TOTP | High risk; not our core |

## Related requirements

FR-IAM-001..006, SEC-004..006, T1, T2 (07 §4); 07 §5; 09 §1.
