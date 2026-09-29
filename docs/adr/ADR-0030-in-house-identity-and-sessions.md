# ADR-0030: In-house sign-in, MFA and sessions (replacing the managed OIDC provider)

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-09-29 (rewritten the same day after the product owner's answers) |
| Deciders | Product owner (decisions of 2026-09-29 below); security review required before acceptance (new database role, new non-tenant schema, changes to invariants 1, 2 and 7 wording) |
| Reference implementation | `bb3agency/calevate-site` at commit `847de80` (the owner's "proper user management, perfectly implemented"), read 2026-09-29. Paths below that start with `apps/api/authn/`, `apps/api/core/`, `apps/workers/`, `apps/web/` or `alembic/` are in that repository unless they say SchoolOS |
| Amends / supersedes | Once accepted: **supersedes** [ADR-0012](ADR-0012-managed-oidc-identity.md) and [ADR-0018](ADR-0018-mfa-and-step-up-with-cognito.md); **amends** [ADR-0004](ADR-0004-technology-stack.md) (identity row), [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (role `sos_auth`, schema `auth`, changed signatures of two allowlisted definer functions), [ADR-0017](ADR-0017-platform-admin-panel-architecture.md) (operator identity), [ADR-0019](ADR-0019-invitation-acceptance-on-first-sign-in.md) (acceptance now includes setting a password) and [ADR-0023](ADR-0023-operator-sign-in-for-break-glass-across-user-pools.md) (how a support session is signed in; what it may reach is unchanged) |

## Context

### The owner's decisions (2026-09-29)

1. "We are going to build our own auth sign up and login systems and not going to integrate any
   third party things for sign up and login and account management." CLAUDE.md §3 says the
   identity stack must not be substituted without a new ADR; this is that ADR. It is design
   only. Nothing is built on it until it is accepted.
2. Answers to the first draft of this ADR:
   - **Sign-up is invite only.** Staff are invited by their school; each school's first owner is
     invited by SchoolOS at provisioning; there is no public or school self-registration.
   - On "which of these count as acceptable (Amazon SES for e-mails / small crypto libraries
     argon2-cffi, py_webauthn, segno / offline HIBP breached-password list / self-hosted OSS IdP
     like Keycloak or Ory)": **"None of these. If you want some guide on how to build it, we have
     a proper user management thing perfectly implemented in this repo bb3agency/calevate-site —
     clone it and use it."**
   - Everything else: the ADR stays **Proposed**; the owner reviews it later.

This rewrite therefore takes calevate-site's user management as the blueprint and copies it
wherever it fits SchoolOS's constraints. Where it does not fit, the section
[Where SchoolOS must differ from calevate-site](#where-schoolos-must-differ-from-calevate-site)
says so point by point. The section
[Third-party dependencies](#third-party-dependencies-the-owners-none-of-these-against-reality)
reconciles "none of these" with what calevate-site itself depends on.

### What depends on OIDC in SchoolOS today (checked in the code on 2026-09-29, base `7fe976f`)

| Area | Today | File(s) (SchoolOS) |
|---|---|---|
| Token verification | `TokenVerifier`: JWKS cache, RS256/ES256, issuer, `aud`/`client_id` + `token_use`, lifetime ≤ 15 min, `sos:mfa` / `amr`, `auth_time`, `origin_jti` as session id. Three verifiers: staff, operator, support | `apps/api/app/identity/tokens.py` |
| Principal | `Principal(subject, issuer, kind ∈ {user, operator, support}, auth_time, mfa, session_id, expires_at)`; `get_principal`, `get_operator_principal`, `require_recent_auth()` (428 unless `mfa` and `auth_time` ≤ 5 min) | `apps/api/app/identity/principal.py` |
| BFF → API | `X-Service-Token` (HS256, `iss=sos-web`, `aud=sos-api`, ≤ 60 s, `jti` replay store) **and** `Authorization: Bearer <OIDC access token>` | `identity/service_token.py`, `apps/web/src/server/bff/{upstream,service-token}.ts` |
| Resolution | `core.resolve_login(subject, issuer, support_only)` → memberships; `mfa_required` (403), suspended schools, break-glass grant; login audited via `POST /me/login-event` | `app/authz/resolver.py`, `identity/service.py` |
| Identity key | `core.users (idp_issuer, idp_subject)` (ADR-0023, `0027_identity_issuer` expand; contract pending); `platform.operators.idp_subject` | migrations `0003`, `0005`, `0027` |
| Invites | `POST /users` takes **`idp_subject`** (the inviter creates the Cognito account by hand first); provisioning takes the owner's subject; acceptance on first sign-in via `core.accept_invitations(p_subject)` (ADR-0019) | `identity/schemas.py` `InviteIn`, `platform/provisioning.py`, `apps/web/src/features/users/InviteUserScreen.tsx` |
| Web BFF | `openid-client` Authorization Code + PKCE, step-up by redirect with `prompt=login`, refresh rotation with reuse detection; sessions in Valkey (`sos:web:sess:*`, tokens sealed AES-256-GCM), `__Host-` cookies (`staff`, `operator`, `support`), CSRF synchronizer token + Origin/Sec-Fetch-Site | `apps/web/src/server/auth/*`, `server/session/*`, `app/bff/auth/**` |
| Break-glass sign-in | Support app client of the operator pool; `POST /api/v1/breakglass/support-session` (step-up ≤ 5 min) | ADR-0023, `app/breakglass/` |
| Local dev | mock-oauth2-server stub (compose profile `dev`) | `docker-compose.yml`, `infra/docker/oidc.json` |
| Terraform | `modules/cognito`, `modules/cognito_support_client`, wired in `shared_platform`, `envs/*`, `dedicated_host` | `infra/terraform/**` |
| Settings | `SOS_OIDC_*`, `SOS_PLATFORM_OIDC_*`, `SOS_SUPPORT_OIDC_*`; web `OIDC_*`, `PLATFORM_OIDC_*`, `SUPPORT_OIDC_*` | `app/core/config.py`, `apps/web/src/server/config.ts` |

**The Cognito Terraform has never been applied** (14 · M0 status, task 10). No Cognito pool
exists and no real person has an account, so moving off Cognito migrates code, schema and
synthetic data, not real credentials.

### calevate-site's user management, as built (studied 2026-09-29)

calevate-site is a multi-tenant FastAPI + Next.js SaaS with two **realms**: `client` (business
owners and their staff, many-to-many `memberships`) and `admin` (the vendor's operators). It
replaced Clerk with a first-party implementation (its decision log `docs/ROADMAP.md` D-165,
D-170, D-177, D-178; design record `docs/AUTH-MIGRATION.md`). Everything credential-related is
in `apps/api/authn/` (~7,700 lines incl. docstrings); `apps/api/core/auth.py` turns a session
into a `Principal` per request.

| Area | What calevate-site does | Files |
|---|---|---|
| Sign-up / invitation | Client accounts are created by redeeming an invitation: one call `POST /v1/auth/client/invitations/accept {token, password, name}` creates the user, sets the password, creates the membership and issues a session. The address comes from the invitation row, never from the request. The token is **only e-mailed**, never returned to the inviter (D-190, after a squatting attack, D-185). An existing account keeps its password; joining a second organisation needs a verified address. A self-serve business signup exists but is behind a kill switch that defaults **off** | `authn/invitations.py`, `authn/routes.py` (`invite_router`), `tenancy/routes.py` (`/invitations`), `tenancy/signup.py`, `apps/web/src/app/(auth)/auth/accept-invitation/page.tsx` |
| First operator | `scripts/bootstrap_admin.py` → `authn/bootstrap.py`: creates the `admin_users` row with no credential and e-mails a single-use setup link (`admin_bootstrap`, 60 min). No password is ever printed. Refuses once any live operator has a password; deliberately no `--force` | `authn/bootstrap.py`, `POST /v1/auth/admin/bootstrap/confirm` |
| Operator management | A `superadmin` adds operators (setup link, same purpose and route as bootstrap), changes roles and revokes accounts. Nobody can change their own role or revoke themselves (`_refuse_self`). Revocation sets `deactivated_at`, deletes the password and every session; a role change revokes sessions. Authority is re-read from `admin_users` on every request. Operators can also list a client's members, change their roles and remove their access (D-602); client owners manage their own team (`/members`, `/invitations`) | `authn/operators.py`, `admin/operator_routes.py`, `admin/members_routes.py`, `tenancy/routes.py`, migration `f2c74b81a9d3` |
| Password hashing | **Argon2id** via **argon2-cffi 25.1.0**: `m = 19456 KiB, t = 2, p = 1`, 32-byte hash, 16-byte salt (OWASP configuration 2 of 5, chosen for memory). **Pepper** = HKDF-SHA-256 of the env-only `PLATFORM_KEK` (`info = calevate/password-pepper/v1`, pyca `cryptography`); stored value = `argon2id(base64(HMAC-SHA-256(pepper, NFC(password))))`. The KEK is a ring: a hash made under a retired pepper still verifies and is re-hashed on that sign-in (`needs_rehash`, also on parameter changes). Unknown accounts are verified against a dummy hash (once per pepper generation) so timing matches. Hashing runs off the event loop (`asyncio.to_thread`) | `authn/hashing.py`, `authn/credentials.py` |
| Password policy | Minimum **15** characters on the realm without a second factor (client), **12** on the MFA realm (admin), maximum 128; no composition rules; **NFC** normalisation (NIST SP 800-63B-4 wording, not NFKC); whole-password blocklist of context words (service name, e-mail-derived tokens, with decoration), keyboard walks and repetition. **No breach corpus** (recorded as an open gap). Enforced in the single writer `set_password`, never on verify | `authn/policy.py`, `authn/credentials.py` |
| MFA | **Admin realm only; the factor is a six-digit code e-mailed to the operator** (`login_challenge`, 10 min, 5 guesses on the row + a Redis budget, one live challenge per subject under an advisory lock, stored as HMAC under a KEK-derived key). TOTP, recovery codes and an encrypted secrets table were built and **removed** on the founder's decision (D-170), which records the cost: "the strength of the admin realm's second factor is the strength of the operator's mailbox". No passkeys. The client realm has no second factor | `authn/otp.py`, `authn/codes.py`, `authn/locks.py`, `authn/service.py`, migration `f1c8b7d5a903` |
| Step-up | Operator re-proves the factor (e-mailed `step_up` code) when `auth_sessions.mfa_verified_at` is older than **30 min** (was 5; D-473), plus an `X-Confirm-Action` intent echo on dangerous routes | `authn/stepup.py`, `core/stepup.py`, `apps/web/src/components/authn/stepUpPrompt.tsx` |
| Sessions | **Opaque server-side sessions.** 256-bit `secrets.token_urlsafe(32)`; stored as SHA-256 over a versioned domain **with the realm inside the digest**; row columns `family_id`, `realm`, `subject_id`, `token_hash` (unique), `last_seen_at`, `idle_expires_at` (slides, written at most every 60 s), `absolute_expires_at` (fixed, carried forward on rotation), `superseded_at`, `revoked_at`, `revoked_reason` (closed list), `mfa_verified_at`. A password sign-in on the MFA realm issues a session with `mfa_verified_at = NULL` that can only answer the code; answering **rotates** it. Rotation on second factor, step-up, password change and the idle-extension button (a census test pins the list). A replayed rotated token **revokes the whole family**, in a transaction `verify_session` owns so the revocation survives the refusal. Every failure gives one generic 401. Timeouts: admin 30 min idle / 8 h absolute, client 12 h idle / 14 days absolute | `authn/sessions.py`, `authn/models.py`, `core/auth.py` |
| Cookies / CSRF | The **API** sets the cookie directly (no BFF): `__Host-calevate_{admin,client}_session`, `HttpOnly; Secure; SameSite=Strict; Path=/`; `Max-Age` bounded by the absolute expiry on the client realm, session cookie on admin. Only the prefixed name is read over TLS (a sibling-subdomain fixation was found twice, D-198/D-330). No CSRF token: `Origin` checked **unconditionally** against an allowlist plus `Sec-Fetch-Site` (D-178), for every mutating request carrying a session cookie | `authn/cookies.py`, `core/middleware.py` |
| Password reset / change | Reset: `password/reset/request` always 202, throttle budget spent before the "does the account exist" branch; link token 256 bits, **1 h**, single use by compare-and-swap; confirming revokes every session and every other reset link. Change: current password required (plus step-up on admin); the caller's session is rotated and every other session revoked | `authn/service.py`, `authn/tokens.py` |
| E-mail verification | `users.email_verified_at`; proved by an e-mailed six-digit code (`email_verify`, 10 min) or link (24 h). Invitation acceptance does **not** mark the address verified | `authn/otp.py`, `authn/tokens.py`, `authn/subjects.py` |
| E-mail sending | Outbox job `deliver_auth_email` (ARQ worker) in the same transaction that minted the secret; the plaintext secret sits in the outbox payload until dispatch, then the payload is scrubbed. Transport chosen by `EMAIL_PROVIDER`: **Resend** HTTP API (`POST https://api.resend.com/emails` over httpx, no SDK; no Resend account exists yet, contract "unverified"), **SMTP** (stdlib `smtplib`, any provider), a local console sink that prints the body (so developers can finish MFA without a bypass, D-409), or a null transport that reports failure | `apps/workers/auth_email.py`, `core/transport.py` |
| Throttling / lockout | Request limit 20/min per client on `/v1/auth/**`; per-account **decaying** Redis budgets: password 10 consecutive failures / 15 min, e-mailed code 5 / 10 min (+ 5 per challenge on the row), reset requests 5 / 15 min; unknown identifiers counted against a keyed pseudo-subject; backoff 0, 0, 2, 4, 8 s (capped); **fail closed** when Redis is down; **no durable lockout** (argued as a DoS primitive) | `authn/throttle.py`, `core/ratelimit.py` |
| Roles / permissions | One `Permission` literal type; `ROLE_PERMISSIONS` for `owner`, `staff`, `operator`, `superadmin`; route → permission registry validated at boot | `core/rbac.py` |
| Operator access to a client | "View as client": the operator's own session plus a 15-minute HS256 grant (PyJWT) with an RFC 8693-style `act` claim; reads audited, coalesced per minute | `core/impersonation.py`, `core/auth.py` |
| Audit | Single `audit_log`, hash-chained (HMAC with a keyed secret, `pg_advisory_xact_lock`), INSERT-only, stores the raw client IP. Auth events (`auth.login_succeeded`, `auth.login_failed`, `auth.logout`, `auth.password_reset_requested`, `auth.password_changed`, `auth.mfa_failed`, `auth.email_verified`, `auth.sessions_revoked`, `auth.invitation_accepted`, `auth.admin_bootstrapped`, …) are written in **their own transaction after** the credential transaction commits | `compliance/audit.py`, `authn/service.py` `_audit` |
| Database | Four tables in the public schema: `auth_credentials`, `auth_sessions`, `auth_email_tokens`, `auth_otp_challenges`; not tenant-scoped; **FORCE RLS deny-by-default** readable only when the transaction sets the GUC `app.auth = 'on'`, which only `db/session.credential_session()` does (same database role as the rest of the app). `subject_id` has no FK (polymorphic over `users` / `admin_users`). Plus `users.email_verified_at`, `users.deactivated_at`, unique live e-mail `uq_users_email_lower`, `admin_users.email` | migrations `e9a4c1d70b52`, `b3d9f6a2c815`, `c7a1e93d40b8`, `f1c8b7d5a903`, `769a9152cb06`, `f2c74b81a9d3`; `apps/api/db/session.py` |
| Web | No auth library. A separate cookie-credentialed fetch transport for `/v1/auth/**`; sign-in, forgot/reset, accept-invitation, admin bootstrap, account pages; step-up prompt; admin idle-warning modal (25 min warning, 30 min sign-out); `Referrer-Policy: strict-origin` on pages that carry a link token in `?token=` | `apps/web/src/lib/authn/*`, `apps/web/src/components/authn/*`, `apps/web/src/app/(auth)/**`, `apps/web/src/lib/authn/linkTokenRoutes.ts` |

### Standards consulted (checked 2026-09-29; re-check at implementation)

- **OWASP ASVS 5.0.0**: authentication is chapter **V6** and session management **V7**; cookie
  rules in V3.3. Target stays Level 2 (NFR-SEC-001).
- **NIST SP 800-63B-4** (August 2025): 15-character minimum for a password that is the only
  factor, 8 when only used with MFA, maximum ≥ 64, no composition rules, no periodic rotation,
  blocklist screening of the whole password, **NFC** normalisation of Unicode passwords; at most
  **100** consecutive failed attempts per account; e-mail is not an acceptable out-of-band
  authenticator; SMS is restricted. (calevate-site's `authn/policy.py` quotes the relevant
  sentences from `usnistgov/800-63-4` at commit `4f2487bb`; the NFC point corrects the first
  draft of this ADR, which said NFKC.)
- **OWASP Password Storage, Session Management, Forgot Password, CSRF and MFA cheat sheets.**
- **RFC 6238** TOTP (30 s, 6 digits, HMAC-SHA-1 for app compatibility), **RFC 5869** HKDF,
  **RFC 9106** Argon2.
- **India**: DPDP Act 2023 + Rules 2025 (security safeguards, ≥ 1 year log retention, 08 §3);
  CERT-In Directions 2022 (ICT logs 180 days in India, 08 §6).

## Decision

### D1 · Scope, realms and sign-up (invite only)

1. SchoolOS runs **its own identity service inside the API** (`apps/api/app/identity/`), with
   two **realms** that never share accounts, like calevate-site's `client` and `admin`:
   - **`staff`**: school users (every tenant role in 07 §6.2). One account per person across all
     schools of one deployment (FR-IAM-013, ADR-0028); memberships stay in `core`.
   - **`operator`**: SchoolOS staff for the control plane; exists only in the shared deployment
     (ADR-0015, ADR-0017).
2. **Invite only** (owner, 2026-09-29). Staff accounts are created only by redeeming an
   invitation from a school (US-102, `POST /users`, `user.manage`, step-up) or, for a school's
   first owner, from SchoolOS at provisioning (16 §5.4). There is **no public sign-up and no school
   self-registration**: unlike calevate-site, SchoolOS does not build a self-serve signup behind a
   switch at all (M7 "self-serve onboarding" would need a new ADR). Parent logins remain a
   non-goal (02 §9).
3. **Operators**: the first by a bootstrap command that e-mails a setup link (calevate-site's
   `authn/bootstrap.py` shape, D7.3); later ones invited by a `platform_owner` (16 §5.16,
   `platform.operators.manage` ᴿ).
4. **Dedicated hosts** run the same code with their own `auth` schema: staff realm only, no
   operator realm, no federation with the shared deployment (ADR-0015/0017, TB7). A person in a
   shared-tier school and a dedicated school has two accounts. Break-glass sign-in on dedicated
   hosts stays unavailable (fail closed) until M1 decision 6.
5. Out of scope and unchanged: GitHub Actions OIDC to AWS (machine identity), the fleet heartbeat
   HMAC (SEC-028).

### D2 · Architecture and trust boundaries

1. **The API owns authentication and the authoritative session** (as calevate-site's
   `apps/api/authn/` + `core/auth.py`). Credentials, factors, sessions and single-use tokens live in
   a schema **`auth`** reached only through a new login role **`sos_auth`**
   (`SOS_AUTH_DATABASE_URL`, `core.db.auth_session()`):
   - `sos_auth`: LOGIN, NOBYPASSRLS, DML on `auth` (audit tables INSERT + SELECT only), **no
     privileges on `core`, `sis`, `kb`, `audit`, `ops` or `platform`**, no EXECUTE on definer
     functions. `sos_app`, `sos_platform`, `sos_readonly`, `sos_definer`: **no privileges on
     `auth`**.
   - Every `auth` table: RLS `ENABLE` + `FORCE`, one policy `auth_service_only USING
     (current_user = 'sos_auth') WITH CHECK (current_user = 'sos_auth')`. This is calevate-site's
     deny-by-default policy, keyed on the **role** instead of calevate's GUC `app.auth = 'on'`
     (difference 1 below). The RLS catalog test gets a new allowlist key `auth_tables`.
   - Only `app/identity` may open `auth_session()` (import-linter contract + boundary test), as
     only `apps/api/authn` may open calevate's `credential_session()`.
2. **The BFF stays the only browser-facing surface; no token reaches browser JS** (CLAUDE.md §3).
   The browser keeps the BFF's own `__Host-` cookie; the BFF's Valkey record holds the **sealed
   opaque API session token**. Browser forms post to BFF route handlers, which forward to the API
   (difference 2).
3. **Per request:** BFF → API carries `X-Service-Token` (unchanged) and `Authorization: Bearer
   sos1.<43 base64url chars>` (the session token). New service-token claims: **`ath`** =
   base64url(SHA-256(session token)), so a leaked service token cannot be paired with another
   session; **`cip`** = client IP on `/api/v1/auth/*` calls (trusted-hop count configured); the API
   stores only an HMAC of it (`ip_hash`). The API verifies the session in its own short
   `auth_session()` transaction that commits before any tenant transaction starts (calevate's
   `verify_session` owns its transaction for the same reason: a family revocation on replay must
   survive the refusal), then builds the same `Principal` as today (D6.8).
4. **Pre-authentication routes** (`/api/v1/auth/*`: sign-in, second factor, invitation, setup
   code, password reset, support redeem) cannot declare `require(...)`. They declare
   **`Depends(require_bff())`** (valid service token, per-flow limits) and must be listed in
   `app/identity/public_routes.yaml`; the route-enumeration test (SEC-003) accepts `require_bff()`
   only for those routes. This changes invariant 2's wording.
5. **Configuration** (invariant 13): every number in this ADR lives in `app/identity/auth.yaml`,
   validated at start-up. calevate-site keeps these numbers as Python constants with tests that
   mirror them; SchoolOS's invariant 13 requires versioned config instead (difference 18).

### D3 · Passwords (calevate-site's hashing and policy, with SchoolOS numbers where required)

| Rule | SchoolOS | calevate-site source |
|---|---|---|
| Hash | **Argon2id** via **argon2-cffi** (the version pinned at implementation; calevate pins 25.1.0), PHC string, **m = 19456 KiB, t = 2, p = 1**, 32-byte hash, 16-byte salt. Start-up refuses parameters below this floor; they may be raised in `auth.yaml` | `authn/hashing.py` (same numbers) |
| Pepper | **HKDF-SHA-256** (pyca `cryptography`) of the current key in `SOS_AUTH_KEYS` with `info = schoolos/password-pepper/v1`; stored value `argon2id(base64(HMAC-SHA-256(pepper, NFC(password))))`. The key set is a ring (current + retired); verification walks it newest first; a match under a retired key or old parameters is re-hashed in the same request (`needs_rehash`) | `authn/hashing.py` (`pepper_ring`, `_peppered`, `PasswordVerdict`) |
| Unknown login | Verified against a dummy Argon2id hash once per pepper generation, same response, same timing | `hashing.verify_password_blocking(..., None)` |
| Concurrency | Hashing runs in a worker thread (SchoolOS routes are sync); a process-wide semaphore (default 4 per API process, ≈ 76 MiB) bounds memory; overflow → `503` + `Retry-After` (difference 17) | `hashing.hash_password` (`asyncio.to_thread`, no bound) |
| Length | **15** characters minimum for staff (a staff password can be the only factor), **12** for operators (always used with MFA), maximum **128**, counted in code points after normalisation | `authn/policy.py` `MIN_CHARS_BY_REALM` (15 client, 12 admin) |
| Composition | None. Telugu script, spaces and passphrases allowed; guidance in en/te | `policy.py` |
| Normalisation | **NFC** before the policy check and before hashing, in the one function both set and verify go through (matches CLAUDE.md §9's NFC rule; the first draft's NFKC is dropped) | `hashing._peppered`, `policy.normalize` |
| Blocklist | Whole-password comparison only (never substrings): context words (`schoolos`, the names and codes of the person's schools, their e-mail/login name and derivatives, each with trailing digits/symbols), keyboard walks, repetition. **No breach corpus unless the owner allows one (Q4)**, exactly calevate-site's current state | `policy.py` `_reason_blocked` |
| Where enforced | Only in `identity` `set_password()`, the single writer of a password; never on verify (a new rule must not lock out a correct password) | `credentials.set_password` |
| UI | `type=password` with show toggle, paste and password managers allowed, `autocomplete` values, errors say how to fix (en/te) | `apps/web/src/components/passwordInput.tsx` |
| Rotation | Never forced; forced change only after a reset by an admin (setup code) or evidence of compromise | — |
| Change | Current password **and**, if the account has a factor, a fresh TOTP code; rotates the caller's session, revokes every other session and every outstanding reset link; notice e-mail | `service.change_password` (current password; step-up on admin) |

### D4 · Second factor: TOTP authenticator apps and recovery codes (not e-mailed codes)

calevate-site's only second factor is an **e-mailed six-digit code**, admin realm only. SchoolOS
cannot copy that (difference 5): FR-IAM-002, ADR-0018 and 07 §5.1 require a **TOTP
authenticator or passkey** for `owner`, `principal`, `office_admin` and every operator; NIST
800-63B-4 and ASVS 5.0 do not accept e-mail as an authentication factor; and calevate-site's own
decision log records that its factor is only as strong as the operator's mailbox. School
mailboxes are often shared office Gmail accounts, which makes that weakness worse here.

1. **TOTP (RFC 6238)**: 160-bit secret from `secrets`, HMAC-SHA-1, 6 digits, 30 s step, accept the
   current step ± 1, **each step usable once** (`last_used_step`). The algorithm is ~20 lines of
   protocol code on Python's standard-library `hmac`/`hashlib` (OpenSSL underneath); **no
   cryptographic primitive is written by us**. Tested against the RFC 6238 Appendix B vectors.
   Input accepts **ASCII digits only** (calevate-site found that `str.isdigit()` lets Devanagari
   and Telugu digits through to `hmac.compare_digest` and a 500 on a sign-in route).
   Enrolment shows the key as grouped text and, if Q1 allows `segno`, a server-rendered QR code
   (SVG); it is confirmed only by a valid code. The secret is stored **AES-256-GCM-sealed** with
   pyca `cryptography`, using calevate-site's envelope pattern (`core/envelope.py`: per-secret data
   key wrapped by the key ring; AAD = `totp:<account id>:<factor id>`).
2. **Guess limits** (calevate-site's two-counter doctrine from `authn/otp.py`, applied to TOTP):
   a Valkey budget of 5 failures per account per 10 min **and** 5 attempts recorded on the
   pending session row, so the limit survives a Valkey flush; after 5 the pending session is
   revoked and sign-in restarts.
3. **Recovery codes** (calevate-site removed them; 07 §5.1 requires them, and without them a
   school owner who loses a phone is locked out; difference 6): 10 single-use codes shown once
   at first enrolment, regenerable after full re-authentication (old set revoked). Each is 16
   Crockford base32 characters (80 bits) and is stored as **HMAC-SHA-256 under a key derived from
   the ring** (`info = schoolos/auth-code-key/v1`, domain `recovery_code`), calevate-site's
   `authn/codes.py` rule for machine-generated secrets: a keyed fast hash, not a slow KDF; redeemed
   by compare-and-swap (`used_at IS NULL`).
4. **Passkeys (WebAuthn): not in the first build.** calevate-site has none, and a correct
   WebAuthn verifier needs CBOR/COSE parsing and attestation handling that we would have to
   hand-write without a library such as `py_webauthn`; that is exactly the kind of security code
   the owner's answer would force us to write ourselves (see Third-party dependencies). They stay
   the recommended next factor (phishing-resistant, useful for operators) and need a later ADR.
5. **Not factors:** e-mail codes and links (used only for invitations, verification and reset,
   none of which bypasses MFA), SMS/voice OTP (restricted authenticator, needs an SMS aggregator
   and TRAI DLT registration, per-message cost).
6. Factors are listed, renamed and revoked by their owner after full re-authentication; the last
   factor of an account whose membership requires MFA cannot be removed (409
   `mfa_required_by_role`).

### D5 · MFA policy and step-up (keeps `require()` and step-up code working)

1. **Mandatory MFA** stays as FR-IAM-002: `owner`, `principal`, `office_admin`
   (`roles.yaml` `mfa_required`) and **every operator** (calevate-site: operators only). Other staff:
   optional and encouraged. A school can never turn it off for privileged roles.
2. **Partial session, as calevate-site does it.** A correct password issues a real session row
   with `mfa_verified_at = NULL` and state `mfa_pending` (account has a factor) or
   `enrolment_required` (a membership or the operator realm requires MFA and the account has
   none). Such a session can reach only the second-factor, enrolment, sign-out and `/me/*`
   routes. Completing the factor **rotates** the session and stamps `mfa_verified_at`
   (calevate `service.complete_second_factor`). No separate "pre-auth token" type exists.
3. **Enforcement stays in the API per request**: the resolver refuses a privileged membership when
   the session has no `mfa_verified_at` (`403 mfa_required`), as today.
4. **Signals, exact rather than inferred** (replaces ADR-0018's "enabled means used"):
   `Principal.mfa` = `mfa_verified_at IS NOT NULL`; `Principal.auth_time` = `mfa_verified_at`
   (else the password time). `require_recent_auth()` is unchanged: **5 minutes**, else `428
   step_up_required` (calevate-site uses 30 minutes; difference 7).
5. **Step-up** = one fresh TOTP code (or recovery code) in the current session via `POST
   /api/v1/auth/step-up` (authenticated); the BFF answers the 428 to the page, which shows an
   accessible step-up dialog (en/te, keyboard, 1366×768) and retries. On success the session is
   rotated and `mfa_verified_at` restamped (calevate `service.complete_step_up`). calevate-site's
   `X-Confirm-Action` intent echo is not adopted: SchoolOS's CSRF synchronizer token already binds
   requests to the page, and step-up routes already show explicit confirmation dialogs.
6. **Full re-authentication** (current password + a factor if enrolled) for: password change,
   adding/removing factors, regenerating recovery codes, changing the sign-in e-mail, "sign out
   everywhere".
7. No "remember this device", no risk-based MFA skipping (as ADR-0018 §2).

### D6 · Sessions (calevate-site's session model, inside the BFF pattern)

1. **Token and storage** (calevate `authn/sessions.py`): 32 bytes from `secrets.token_urlsafe`,
   prefixed `sos1.` so `redact()` and secret scanners can recognise it; stored only as
   **SHA-256(`schoolos/auth-session/v1/` ‖ purpose ‖ 0x00 ‖ token)**, so a staff token looked up as
   an operator or support token matches nothing by arithmetic, and a `purpose = :purpose`
   predicate sits beside it as a second check.
2. **Row model** (calevate `auth_sessions`, extended): `family_id`, `realm`, `purpose` (`staff`,
   `operator`, `support`), `account_id`, `token_hash` unique, `last_seen_at`, `idle_expires_at`,
   `absolute_expires_at`, `superseded_at`, `revoked_at`, `revoked_reason` (calevate's closed list:
   `signed_out`, `subject_revoked`, `reuse_detected`, `administrative`), `mfa_verified_at`, plus
   SchoolOS columns `state`, `mfa_attempts`, `idle_timeout_s`, `password_at`,
   `support_request_id`/`support_tenant_id`, `device_label`, `ip_hash`.
3. **Verification** (calevate `_verify_within`): no row → refuse; `superseded_at` set → **revoke
   the whole family** (`reuse_detected`) and refuse; revoked or past either bound → refuse;
   else slide the idle window, **writing `last_seen_at` at most once per 60 s**. Every refusal is
   the same `401` ("Your session has ended. Sign in again."); the reason is logged with IDs only.
4. **Timeouts** (API-enforced; the BFF mirrors them in its Valkey TTLs):

   | Purpose | Idle | Absolute | Concurrent per account |
   |---|---|---|---|
   | `staff` | 15 min; the active school's setting 5–30 min (FR-TEN-012), applied when the school is chosen | 12 h | 10 (oldest revoked; config) |
   | `operator` | 15 min | 8 h | 3 |
   | `support` (break-glass) | 15 min | 8 h and never past the grant's `expires_at` | 1 per grant |

   These are FR-IAM-003's numbers, not calevate-site's (difference 8). An idle-warning dialog 2
   minutes before expiry (calevate's `adminIdleTimeoutModal.tsx` pattern) offers "Stay signed in",
   which simply makes a request; activity during the warning does not dismiss it.
5. **Rotation** (a new row in the same family, old row `superseded_at`, `absolute_expires_at`
   carried forward, compare-and-swap so two rotations cannot both win): on second-factor
   completion, MFA enrolment, step-up, password change, recovery-code use and support redeem. A
   census test lists every rotation caller (calevate `test_the_only_rotation_callers_...`). There
   is no browser-driven refresh rotation: the BFF holds the token and serialises its own calls per
   session, so calevate's multi-tab race does not arise.
6. **Revocation** (effective on the next request): sign-out; sign out everywhere (full re-auth);
   revoke one session from "Your sessions" (FR-IAM-006; calevate-site has no session list,
   difference 12); automatic on password reset or change (others), admin MFA reset, account
   disabled, operator deactivated or role changed (calevate `operators.py`), 100-failure disable;
   **school-scoped force sign-out** by a holder of `user.manage` (step-up) through
   `core.memberships.sessions_valid_after = now()` (new column): the resolver refuses that
   membership for older sessions, so one school cannot sign a person out of another.
7. **Cookies and CSRF** (BFF): names unchanged (`__Host-sos_session`, `__Host-sos_platform_session`,
   `__Host-sos_support_session`); `Secure; HttpOnly; Path=/`, no `Domain`, **no `Max-Age` on any
   realm** (shared office PCs; calevate-site persists the client cookie for 14 days, difference 9).
   `SameSite=Lax` plus the existing CSRF synchronizer token (SEC-004) instead of calevate-site's
   `Strict` without a token (difference 10). Adopted from calevate-site: the `Origin` header is
   checked **unconditionally** against the exact host allowlist on every state-changing BFF route
   (the admin and app hosts share a registrable domain, the same-site hole calevate-site fixed in
   D-178), and the BFF reads **only the `__Host-` name** of its cookie (D-198). Login CSRF: sign-in,
   invitation, setup-code and reset forms carry a pre-session token bound to a 10-minute
   `__Host-sos_auth_tx` cookie.
8. **`Principal` mapping** (so `authz`, `require()`, `require_platform()`, the resolver and
   break-glass keep their contracts):

   | Principal field | Value |
   |---|---|
   | `subject` | `str(auth.accounts.id)` (UUIDv7) |
   | `issuer` | realm URN `urn:schoolos:auth:staff` or `urn:schoolos:auth:operator`, stored in `core.users.idp_issuer` |
   | `kind` | `user` (purpose `staff`), `operator`, `support` |
   | `auth_time`, `mfa` | D5.4 |
   | `session_id` | `str(auth.sessions.id)` (never the token) |
   | `expires_at` | min(idle expiry, absolute expiry) |

   `get_principal` accepts purposes `staff` and `support`; `get_operator_principal` only
   `operator`; `/api/v1/platform/*` never accepts `support` or `staff`.

### D7 · Flows

1. **Invitation, new person** (calevate `authn/invitations.py`, adapted). `POST /users` takes
   `email` (or `login_name` for staff without e-mail) instead of `idp_subject`. `identity.service`
   creates `auth.accounts` (`status = invited`, no credential) and the API uses its ID as the
   subject for `core.create_user_for_invite(..., p_issuer = staff URN)`. The invitation e-mail's
   token is minted **by the send task at send time** and is **never returned in any API
   response** (calevate D-190; a test asserts no response carries a credential-shaped value). The
   link carries the token in the URL **fragment** (`/accept-invite#t=…`), not calevate-site's
   `?token=` (difference 11). The page posts it via the BFF to `POST
   /api/v1/auth/invitations:inspect` (school names, whether MFA is required) and then
   `…:accept {token, password}`: one call burns the token (compare-and-swap), sets the password
   through `set_password`, marks the address verified and issues a session; the address is the
   one on the account, never one from the request (calevate rule). If a membership requires MFA
   the session is `enrolment_required` (D5.2). Then `POST /me/accept-invitations` (ADR-0019,
   unchanged) and the school picker. Token: 256 bits, **72 h** (calevate), resend mints a new one
   and burns the old; the membership invite window stays 30 days (ADR-0019).
2. **Staff without e-mail** (SchoolOS only; 07 §5.1): the inviting admin receives a **one-time
   setup code** once (12 Crockford base32 characters ≈ 60 bits, 72 h, single use, keyed HMAC,
   printable en/te hand-over slip; the admin never sees or chooses the password); the person
   enters it with their login name at `/[locale]/setup`.
3. **Invitation, existing account** (a second school): no token; the e-mail says "sign in to
   accept"; the password is never touched (calevate rule); `/me/accept-invitations` activates it.
4. **Owner at provisioning.** The wizard takes the owner's e-mail (16 §5.4 step 3);
   `platform` calls `identity.service.create_invited_account(realm="staff", …)` (a new pinned
   boundary exception; it touches only `auth`) and passes the account ID to
   `core.create_owner_invite`; the owner invitation e-mail is sent when the run completes.
   Dedicated: `python -m app.tenancy.provision_dedicated` does the same locally.
5. **Operators** (calevate `authn/bootstrap.py` and `authn/operators.py`):
   - First operator: `python -m app.platform.bootstrap_owner --email …` creates the operator row
     and account without a credential and e-mails an `operator_setup` link (60 min); no password is
     printed or stored by the command. Run again: resends while nobody has a password; **refuses
     once any live operator has one**; no `--force`.
   - Later operators: invited by a `platform_owner` (ᴿ, step-up) with the same link purpose and
     redemption route. MFA enrolment is required before any other route.
   - Nobody changes their own role or deactivates themselves (calevate `_refuse_self`); SchoolOS's
     two-person rules for ᴿ 2P permissions still apply (SEC-027, SEC-029).
   - Deactivation (`platform.operators.status`) deletes the credential, factors and recovery codes
     and revokes every session in the same transaction; a role change revokes sessions.
6. **Sign-in** (calevate `service.sign_in`). `/[locale]/sign-in` (app host) and
   `/[locale]/platform/sign-in` (admin host): login (e-mail or login name) + password → if the
   account has a factor or must enrol, a partial session (D5.2) → TOTP or recovery code. Then
   (staff) the school picker via `/me/schools` and `/me/login-event` (FR-IAM-013). One refusal
   for every failure (`401 invalid_credentials`, same body); unknown logins hash against the dummy
   and spend a throttle budget on a keyed pseudo-subject, so status, body, timing and throttling
   match (calevate `throttle.pseudo_subject`).
7. **Recovery** (no security questions, no hints):

   | Situation | Path | Audit |
   |---|---|---|
   | Forgot password, verified e-mail | `POST /auth/password:forgot` (always `202`; budget spent **before** the "does it exist" branch, calevate D-198) → e-mail link (fragment token, 256 bits, **1 h**, single use) → new password. All sessions and other reset links revoked. **MFA still required at the next sign-in** | `auth.password.reset_requested/completed` |
   | Forgot password, no e-mail | A school admin with `user.credentials.reset` (new, step-up; owner and principal by default) issues a new setup code (D7.2). Refused with `409 profile_shared` if the person belongs to other schools (ADR-0028 guard) | school chain `user.credentials_reset` + auth chain |
   | Lost phone, has a recovery code | Use it (D4.3), then re-enrol | `auth.mfa.recovery_code_used` |
   | Lost phone, no code | An owner or principal with `user.credentials.reset` resets the person's factors after checking identity **in person**, never their own; same shared-profile guard; re-enrol at next sign-in; all sessions revoked; the person and all owners notified | `user.mfa_reset` (school) + `auth.mfa.reset` |
   | Sole owner lost phone and codes, or shared-profile person | SchoolOS-assisted: written request, call-back to the registered contact, then **two different operators** (`platform.identity.recover`, new ᴿ 2P permission) reset factors; copied to the school's chain | platform chain + school chain copy (ADR-0020) |
   | Operator lost phone | Recovery code, else two other operators with `platform.operators.manage` (ᴿ, 2P); with a single platform owner, `python -m app.identity.recover_operator` via ECS Exec by someone with prod AWS access and MFA (calevate-site has no recovery path and says so in `authn/operators.py`) | platform chain |

8. **Throttling** (calevate `authn/throttle.py` mechanics, SchoolOS numbers; FR-IAM-005, US-101
   AC4). Defaults in `auth.yaml`:
   - **Per account** (Valkey, decaying): password budget **5** consecutive failures per 15 min
     (US-101 AC4; calevate-site uses 10), then `429` with `Retry-After` until the window lapses;
     backoff delay 0, 0, 2, 4, 8 s (capped); success clears it. TOTP budget 5 per 10 min (D4.2).
     Reset requests 5 per login per 15 min.
   - **Durable cap** (difference 13): `auth.accounts.consecutive_failures` counts across windows;
     at **100** password sign-in is disabled until a reset (NIST 800-63B-4). The 5th failure and
     the 100th notify the person and, for staff, the `user.manage` holders of each school.
   - **Per IP** (`ip_hash`): 60 requests per minute on `/api/v1/auth/*` (calevate-site: 20;
     raised because a school office shares one NAT address).
   - **Fail closed** when Valkey is unavailable: sign-in answers `503` rather than going
     unthrottled (calevate rule).
   - Edge: the WAF rate rule on `/bff/auth/` and the Caddy limit on dedicated hosts stay. No
     CAPTCHA.
9. **E-mail verification and change.** Addresses are verified by invitation acceptance. Changing
   the sign-in e-mail needs full re-authentication, a link to the new address (24 h, calevate's
   `email_verify` lifetime) and a notice to the old one. The profile e-mail in `core.users.email`
   (school-editable) is a separate field and does not change sign-in.
10. **Sign-out and sessions.** `POST /bff/auth/logout` revokes the API session and the BFF record;
    "Your sessions" (FR-IAM-006) lists sessions (coarse device label, sign-in time, last seen,
    never IPs) with revoke-one and sign-out-everywhere.
11. **Break-glass support sign-in** (replaces ADR-0023's support app client; what the session may
    reach is unchanged). calevate-site's operator access ("view as client") is a 15-minute signed
    grant on the operator's own session, with no approval by the client and read/write reach
    (difference 14). SchoolOS keeps approval by the school, two-person rules and a read-only
    `platform_support` membership, and signs the support session in by a one-time hand-off:
    1. The admin panel links to the app host `GET /bff/auth/support/start?request=<id>&tenant=<id>`
       (IDs only). The BFF creates a `code_verifier`, keeps it in `__Host-sos_support_auth_tx` and
       redirects to the admin host `GET /bff/platform/support-handoff?…&challenge=<S256>`.
    2. The admin BFF calls `POST /api/v1/platform/breakglass/requests/{id}/handoff`
       (`require_platform("platform.breakglass.request", step_up=True)`); `identity.service` issues
       a hand-off code (256 bits, **60 s**, single use, bound to operator, request, tenant and
       challenge). Redirect to `/support/redeem#code=…`.
    3. The page posts the code; the app BFF sends it with the verifier to `POST
       /api/v1/auth/support:redeem` (`require_bff()`), which creates a `support` session
       (`mfa_verified_at` = the operator's step-up time), then the unchanged `POST
       /api/v1/breakglass/support-session` and `/me/login-event` (`issuer_kind: operator_support`).
    - ADR-0023's resolution rules stay (`core.resolve_login(..., p_support_only => true)`,
      `403 breakglass_only` / `breakglass_grant_inactive`, read-only, every call audited).

### D8 · Data model, privileges, audit and retention

New migrations (next free revisions at implementation) in schema `auth`, owned by `sos_owner`,
DML for `sos_auth` only, RLS ENABLE + FORCE with `auth_service_only`, UUIDv7 IDs, `timestamptz`.
Tables follow calevate-site's split (credential separate from session separate from single-use
token), with an `accounts` table because `sos_auth` may not read `core.users`:

```sql
CREATE TABLE auth.accounts (                -- calevate: users / admin_users login columns
  id uuid PRIMARY KEY,                      -- = core.users.idp_subject / platform.operators.idp_subject
  realm text NOT NULL CHECK (realm IN ('staff','operator')),
  login_email citext, login_name citext,    -- at least one; unique per realm among live accounts
  email_verified_at timestamptz,
  pending_email citext, pending_email_expires_at timestamptz,
  status text NOT NULL CHECK (status IN ('invited','active','disabled')),
  consecutive_failures int NOT NULL DEFAULT 0, password_disabled_at timestamptz,
  must_change_password boolean NOT NULL DEFAULT false,
  created_at, updated_at, last_sign_in_at timestamptz,
  CHECK (login_email IS NOT NULL OR login_name IS NOT NULL)
);                                          -- partial unique indexes WHERE status <> 'disabled' (calevate uq_users_email_lower)
CREATE TABLE auth.credentials (             -- calevate auth_credentials
  id uuid PRIMARY KEY, account_id uuid NOT NULL UNIQUE REFERENCES auth.accounts,
  password_hash text NOT NULL,              -- Argon2id PHC string of the peppered input
  password_set_at timestamptz NOT NULL, created_at, updated_at timestamptz
);
CREATE TABLE auth.sessions (                -- calevate auth_sessions + SchoolOS columns (D6.2)
  id uuid PRIMARY KEY, family_id uuid NOT NULL, account_id uuid NOT NULL REFERENCES auth.accounts,
  realm text NOT NULL, purpose text NOT NULL CHECK (purpose IN ('staff','operator','support')),
  token_hash bytea NOT NULL UNIQUE,
  state text NOT NULL CHECK (state IN ('mfa_pending','enrolment_required','active')),
  mfa_verified_at timestamptz, mfa_attempts smallint NOT NULL DEFAULT 0, password_at timestamptz,
  last_seen_at, idle_expires_at, absolute_expires_at timestamptz NOT NULL, idle_timeout_s int NOT NULL,
  superseded_at, revoked_at timestamptz, revoked_reason text,
  support_request_id uuid, support_tenant_id uuid, device_label text, ip_hash bytea,
  created_at, updated_at timestamptz
);                                          -- indexes on family_id and (realm, account_id), as calevate
CREATE TABLE auth.mfa_factors (             -- SchoolOS only (calevate removed its TOTP table)
  id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES auth.accounts,
  kind text NOT NULL CHECK (kind = 'totp'), label text,
  secret_sealed bytea NOT NULL, wrapped_key bytea NOT NULL, key_id smallint NOT NULL,
  last_used_step bigint, created_at, confirmed_at, last_used_at, revoked_at timestamptz
);
CREATE TABLE auth.recovery_codes (          -- SchoolOS only
  id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES auth.accounts, batch_id uuid NOT NULL,
  code_hash bytea NOT NULL UNIQUE,          -- HMAC-SHA-256, code key (D4.3)
  created_at, used_at, revoked_at timestamptz
);
CREATE TABLE auth.tokens (                  -- calevate auth_email_tokens, purpose inside the HMAC domain
  id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES auth.accounts,
  purpose text NOT NULL CHECK (purpose IN
    ('invitation','setup_code','password_reset','email_change','operator_setup','support_handoff')),
  token_hash bytea NOT NULL UNIQUE, code_challenge bytea,
  subject_ref jsonb NOT NULL DEFAULT '{}',  -- IDs only (request_id, tenant_id)
  created_by_kind text, created_by_id uuid, expires_at timestamptz NOT NULL, used_at timestamptz,
  created_at timestamptz
);
CREATE TABLE auth.events (...);             -- append-only, hash-chained (like platform.audit_events)
CREATE TABLE auth.audit_chain_head (...);
CREATE TABLE auth.tenant_audit_outbox (     -- school-chain copies, delivered exactly once (ADR-0020 pattern)
  id uuid PRIMARY KEY, account_id uuid NOT NULL, action text NOT NULL,
  summary jsonb NOT NULL, created_at timestamptz NOT NULL, delivered_at timestamptz
);
```

Token lifetimes (calevate `authn/tokens.py` where a counterpart exists): `password_reset` 1 h,
`invitation` 72 h, `email_change` 24 h, `operator_setup` 60 min, `setup_code` 72 h,
`support_handoff` 60 s. Tokens are stored as HMAC-SHA-256 under the code key with the purpose
inside the domain (calevate `codes._fingerprint_under`), redeemed by one `UPDATE … WHERE used_at
IS NULL AND expires_at > now() RETURNING`, and every retire-then-issue pair takes a per-account
`pg_advisory_xact_lock` (calevate `authn/locks.py`, D-320). No `otp_challenges` table: SchoolOS
sends no e-mailed codes.

Plus `core.memberships.sessions_valid_after timestamptz NULL` (`sos_app` UPDATE on that column;
RLS unchanged). No other new table in `core`, `sis`, `kb`, `ops` or `platform`.

**Privileges and catalog tests:** `infra/db/bootstrap.sql` creates `sos_auth` (LOGIN,
NOBYPASSRLS, `search_path = pg_catalog, public`, `idle_in_transaction_session_timeout = 30s`); no
role holds privileges on `auth` except `sos_owner` and `sos_auth`; `sos_auth` holds none
elsewhere; `auth.events` append-only (grants + row trigger + `BEFORE TRUNCATE`).

**Definer allowlist (ADR-0013 §2): no new `SECURITY DEFINER` function and no new
`definer_access` policy.** Changes to allowlisted functions, in the contract release:
`core.accept_invitations(p_subject, p_issuer)`; `core.create_owner_invite(...)` gains `p_issuer`;
the four-argument `core.create_user_for_invite` wrapper and the NULL-issuer defaults of
`core.resolve_login` / `core.find_user_id_by_subject` are dropped (planned 0027 contract). The
data migration re-keys `core.users (idp_issuer, idp_subject)` with the temporary-grant pattern of
`0027_identity_issuer`.

**Audit (invariant 7; difference 15).** Every state change in `auth` writes its event in the
**auth chain** (`auth.events`, JCS/SHA-256, gapless `seq`, recorded by a new
`audit.service.record_auth()`) **in the same `auth` transaction**. calevate-site writes its auth
events to `audit_log` in a separate transaction after the credential commit; SchoolOS's invariant 7
does not allow that. Events a school must see are queued in `auth.tenant_audit_outbox` in the same
transaction and delivered exactly once to every school where the account has a membership
(`identity.deliver_auth_audit`). Actions that start in a school (invite, reset, force sign-out)
write their school-chain event in the tenant transaction as today. The daily verification job
covers the auth chain.

| Auth chain event (IDs and codes only) | Copied to school chains |
|---|---|
| `auth.signin.succeeded` (method `password`, `password+totp`, `recovery_code`), `auth.signin.failed` (reason `bad_credentials`, `throttled`, `mfa_failed`, `disabled`, `unknown_login` with `login_hash` only) | No (the school-chain `auth.login.succeeded/denied` stays, via `/me/login-event`) |
| `auth.account.throttled` (5th failure), `auth.account.password_disabled` (100th) | Yes |
| `auth.password.changed`, `auth.password.reset_requested`, `auth.password.reset_completed`, `auth.setup_code.issued/used` | Yes (except `reset_requested`) |
| `auth.mfa.enrolled`, `auth.mfa.removed`, `auth.mfa.reset`, `auth.mfa.recovery_code_used`, `auth.mfa.recovery_codes_regenerated` | Yes |
| `auth.session.created`, `auth.session.stepped_up`, `auth.session.revoked` (reason), `auth.sessions.revoked_all`, `auth.session.reuse_detected` | Only `revoked_all`, `reuse_detected` and admin force sign-out |
| `auth.email.change_requested/verified`, `auth.invitation.accepted` | Yes |
| `auth.operator.bootstrapped`, `auth.operator.setup_completed` | Platform chain |
| `auth.support.handoff_issued/redeemed` | Yes (with `breakglass.*` as today) |

**No PII in logs (invariant 5, SEC-008).** IDs and reason codes only. Never passwords, codes,
tokens, typed logins, e-mail addresses (calevate-site logs the recipient's domain; SchoolOS logs
none of it), IP addresses (only `ip_hash`; calevate-site stores raw IPs in `audit_log`,
difference 16), or user agents beyond the coarse device label. `redact()` gains a pattern for
`sos1.` tokens; the log-redaction and `test_no_pii_in_urls.py` suites cover every new field.

**Retention** (05 §13, 08 §6–7): `auth.events` ≥ 13 months online then archived with the audit
archive (3 years, Object Lock); `auth.sessions` deleted 30 days after they end; `auth.tokens`
7 days after expiry or use; staff accounts with credentials, factors and codes while any
membership exists, disabled when the last one ends and deleted 1 year later (coordinated with
[ADR-0029](ADR-0029-tenant-data-deletion-at-offboarding.md)); operator accounts life + 1 year;
Valkey counters their window (≤ 1 h).

**Privacy (08).** Staff sign-in data is processed for the school that invited the person (DPDP
processor role, as with Cognito). Remove "identity (Cognito)" from the sub-processor register.
The RoPA gets "authentication: login e-mail/name, credential hashes, factor metadata, session
metadata, IP hashes".

### D9 · E-mail delivery

calevate-site sends every auth e-mail through an outbox job and a transport selected by
configuration: the **Resend** HTTP API or any **SMTP** server, with a console sink locally
(`core/transport.py`, `apps/workers/auth_email.py`). SchoolOS already has the same shape:
`app/notifications/email.py` `EmailSender` with **Amazon SES v2** in ap-south-1, a fake sender
in local/CI, outbox events with IDs only and a Celery task. This ADR proposes to keep SES for
invitation, reset, e-mail-change, operator-setup and security-notice e-mails and asks the owner
to choose (Q2). Adopted from calevate-site: the local dev transport **delivers** the message to a
local-only drop (`var/dev-mail/`, refused outside `local`/`ci`) instead of any MFA or e-mail bypass
(D-409), and a missing transport fails visibly instead of pretending success. Different from
calevate-site (difference 11): the secret is minted **by the send task at send time**, so no
outbox row ever holds a usable token (calevate-site keeps it in the outbox payload until dispatch
and then scrubs it). Templates are bilingual (en/te) and say what to do if the message was
unexpected. A host without e-mail can still onboard staff with setup codes.

### D10 · Dedicated tier

Same code with `SOS_DEPLOYMENT_MODE=dedicated`: staff realm only, own `sos_auth` role and `auth`
schema on the host's PostgreSQL, own `SOS_AUTH_KEYS` under `schoolos/<tenant_code>/`, Caddy rate
limit on `/bff/auth/`. Nothing about sign-in reaches the control plane. 16 §19 Q4 (Cognito app
client per host) becomes obsolete.

### D11 · Settings and secrets

- Remove (contract): `SOS_OIDC_*`, `SOS_PLATFORM_OIDC_*`, `SOS_SUPPORT_OIDC_*`, web `OIDC_*`,
  `PLATFORM_OIDC_*`, `SUPPORT_OIDC_*`, the dev OIDC stub and its guards.
- Add: `SOS_AUTH_DATABASE_URL`; **`SOS_AUTH_KEYS`** (Secrets Manager; a ring of versioned 32-byte
  keys, one current, retired ones verify and unwrap only; calevate-site's `PLATFORM_KEK` +
  `PLATFORM_KEK_RETIRED`), from which HKDF derives separate keys per `info`:
  `password-pepper`, `auth-code-key`, `totp-wrap`, `login-hash`, `ip-hash`;
  `SOS_AUTH_STAFF_ISSUER` / `SOS_AUTH_OPERATOR_ISSUER`; web `TRUSTED_PROXY_HOPS`. Staging/prod
  start-up guards: no `dev-only` key, keys ≥ 32 bytes, Argon2 parameters at or above the floor.
- Key rotation: add a key version; password hashes re-hash lazily at sign-in (calevate); TOTP data
  keys are re-wrapped by `python -m app.identity.rewrap`; codes and tokens drain by expiry; a
  retired key is removed only when the job and a count of old-pepper hashes report zero (accounts
  that never sign in again get a forced reset).

### D12 · Migration plan (invariant 12: expand → migrate → contract)

**Release A — expand.** `bootstrap.sql`: role `sos_auth`. Migration `auth_schema`: schema,
tables, grants, RLS, append-only triggers, `core.memberships.sessions_valid_after`. Code: identity
service, routes, BFF handlers and pages behind `SOS_AUTH_MODE = local` (default in
`local`/`ci`/`staging`; `oidc` selectable for one release as rollback). `InviteIn.idp_subject`
optional; `email`/`login_name` accepted. New permissions in `app/authz/permissions.yaml`:
`user.credentials.reset` (tenant, step-up; owner and principal) and `platform.identity.recover`
(ᴿ, 2P; `platform_owner`). `accept_invitations` issuer-aware overload. `make seed-synthetic`
creates synthetic accounts with passwords from `SOS_DEV_SEED_PASSWORD` and fixed TOTP secrets
(refused outside `local`/`ci`). **Terraform: do not apply `modules/cognito` or
`cognito_support_client` anywhere**; add the `auth_keys` secret and the `sos_auth` credential.

**Release B — migrate.** Data migration (idempotent, counts only): an `auth.accounts` row per
`core.users` row (`invited`) and per operator; re-key `core.users (idp_issuer, idp_subject)`;
break-glass identities map to the operator's account. Only synthetic data exists, so people are
re-invited. Downgrade restores from a backup table kept until Release C. Switch every environment
to `local`; remove `oidc`.

**Release C — contract.** Delete `identity/tokens.py`, the JWKS cache and verifiers,
`openid-client`, `server/auth/{oidc,refresh,transaction}.ts`, the OIDC callback routes and
redirect step-up, `InviteIn.idp_subject` and the web "subject" fields and `subjectHint` strings.
DB: `core.users.idp_issuer NOT NULL`, drop `UNIQUE (idp_subject)`, definer signature changes (D8),
drop the backup table. Local: remove the `oidc` compose service and `infra/docker/oidc.json`.
Terraform: delete `modules/cognito` (incl. the pre-token Lambda), `modules/cognito_support_client`
and their wiring and variables in `shared_platform`, `envs/*`, `dedicated_host`; replace the
"operator pool MFA ON" assertion with assertions on the auth secrets and the dedicated hosts' SES
permission.

**Docs to update when this ADR is accepted** (and only then): CLAUDE.md §3 (identity line), §4
(identity module, `infra/docker`), §5 (dev OIDC stub), §6 invariants 1, 2 and 7; 01-BRD identity
lines; 02-PRD US-101, US-102; 03-TRD FR-IAM-001..006 (FR-IAM-004 becomes server-side session
rules with immediate revocation), new FR-IAM-007 (TOTP and recovery codes), FR-IAM-008 (account
recovery), FR-IAM-009 (invitation acceptance with credential set-up); 04 §3, §5, §7, §10, §16; 05
§3.1, §3.3, §3.4, §4, new `auth` section, §13; 07 §3, §4, §5 (incl. §5.1 password minimum 12 → 15
for staff), §6.4, §9, §16 (SEC-004/005/006/027, new SEC-031 credential storage, SEC-032 auth
schema separation, SEC-033 auth-focused pen test); 08 sub-processors, §6, §7, §11; 09 §1, §3 error
codes (`invalid_credentials`, `too_many_attempts`, `mfa_challenge_failed`, `enrolment_required`,
`session_revoked`, `token_invalid`), §4; 10 §4, §5, §5.2, §11, §15; 11 alerts and runbooks; 12 §4,
§7; 13 CODEOWNERS; 14; 15 glossary; 16 §4, §5.4, §5.16, §13.2, §19 Q4; status lines of ADR-0012,
0018 (superseded) and 0004, 0013, 0017, 0019, 0023 (amended); `apps/api/app/identity/README.md`;
`deploy/dedicated/*`.

**Tests to add or change** (names reference FR/SEC IDs). Every calevate-site test class named
below has a SchoolOS counterpart:

| Area | Tests |
|---|---|
| Security suites | Route enumeration accepts `require_bff()` only on `public_routes.yaml` (SEC-003); privilege catalog for `sos_auth` and `auth` (SEC-032); `auth.events` append-only incl. TRUNCATE; definer allowlist unchanged; no token, e-mail or login in URLs; log redaction of every new field (SEC-008); authz matrix, BOLA and tenant isolation unchanged except principal fixtures |
| Sessions | Purpose × route-family matrix (staff on `/platform/*` → 401, etc.); a token presented under another purpose matches nothing (calevate `authn_session_test.py`); replay of a rotated token revokes the family and the revocation survives the refusal; rotation census; absolute bound carried forward; idle write floor; school idle setting; `sessions_valid_after`; disable/deactivate/role change revoke (FR-IAM-003, FR-IAM-006, SEC-006) |
| Credentials | Argon2id parameters and floor; pepper ring, retired-key verify + rehash; dummy verification per generation; NFC incl. Telugu typed two ways; 15/12/128 limits; whole-password blocklist hits and misses (calevate `authn_password_policy_test.py`); policy only on set, never on verify |
| Factors | RFC 6238 vectors, ± 1 step, replay of a used step refused; non-ASCII digits refused with 401, never 500; attempts on the row survive a Valkey flush; recovery codes single use and regeneration; last factor of a privileged member cannot be removed (FR-IAM-002) |
| Flows | Identical status, body and timing for unknown accounts, and unknown accounts spend a real throttle budget (calevate `authn_enumeration_test.py`); 5th-failure throttle and 100-failure disable with audit (US-101 AC4, FR-IAM-005); Valkey down → 503; reset budget spent before the branch; reset does not bypass MFA and revokes sessions; invitation token never in any response, fragment only, 72 h, resend burns the old; setup codes; `enrolment_required`; step-up 428 → verify → retry (SEC-005); operator bootstrap refuses when an operator has a password; `_refuse_self`; two-operator recovery (SEC-029); break-glass hand-off (US-103, SEC-021) |
| Audit | Auth event in the same transaction (rollback test); school copies exactly once; chain verification detects gaps and edits (SEC-007) |
| Migrations | Round trips on a populated synthetic DB; data migration idempotent; contract leaves no OIDC-era rows |
| Web / E2E | BFF handlers, CSRF incl. login CSRF and the unconditional `Origin` check; `__Host-` name only; idle warning; password + TOTP sign-in with a seeded secret, invitation from the dev mail drop, forgot password, step-up on an export, school picker, operator sign-in, break-glass hand-off; axe, en and te, 1366×768, keyboard-only |
| Performance | Argon2id time on the API task size; semaphore returns 503 instead of exhausting memory; sign-in p95 |

## Where SchoolOS must differ from calevate-site

| # | calevate-site | SchoolOS (this ADR) | Why |
|---|---|---|---|
| 1 | Auth tables in the public schema, same DB role as the app, RLS gated by the GUC `app.auth = 'on'` | Separate `auth` schema and `sos_auth` role; RLS keyed on `current_user` | Any code holding an app connection can set a GUC; a separate role cannot be assumed by tenant or control-plane code. This is how SchoolOS already separates privileged data (`sos_platform`, ADR-0013) |
| 2 | The API sets the session cookie; browser JS calls the API directly | The BFF holds the (sealed) API session token; the browser only has the BFF cookie | CLAUDE.md §3 and SEC-004: tokens never reach the browser; the BFF, service token and CSRF layer already exist |
| 3 | Self-serve business signup exists (off by default) | Not built at all | Owner: invite only |
| 4 | Pre-auth routes are ordinary routes | `require_bff()` and a pinned `public_routes.yaml` | Invariant 2: a test enumerates every route and requires an authorization dependency |
| 5 | Second factor = e-mailed six-digit code, operators only | TOTP authenticator app for privileged staff and all operators | FR-IAM-002, ADR-0018, 07 §5.1; NIST 800-63B-4 and ASVS reject e-mail as a factor; calevate's D-170 records that its factor is only as strong as the mailbox; school mailboxes are often shared |
| 6 | No recovery codes | 10 single-use recovery codes | 07 §5.1; without them a lost phone locks out a school's only owner |
| 7 | Step-up window 30 min, via an e-mailed code | 5 min, via a TOTP code | ADR-0018, 07 §5.2, SEC-027 (step-up ≤ 5 min used by `require_recent_auth()`) |
| 8 | Client 12 h idle / 14 days absolute; admin 30 min / 8 h | Staff 15 min idle (school 5–30) / 12 h; operators 15 min / 8 h | FR-IAM-003, US-101 AC3: shared office PCs |
| 9 | Client cookie persistent (`Max-Age` to the absolute expiry) | Browser-session cookies on every realm | Shared office PCs: closing the browser must end the session |
| 10 | `SameSite=Strict`, no CSRF token (Origin + Sec-Fetch-Site only) | `SameSite=Lax` + synchronizer CSRF token + unconditional Origin check (the last adopted from calevate) | SEC-004 requires CSRF tokens; server-rendered pages opened from e-mail links must see the session on the first navigation |
| 11 | Link tokens in `?token=` (then removed from the address bar, `Referrer-Policy: strict-origin`); token stored in the outbox payload until dispatch; invite acceptance does not verify the address | Tokens in the URL fragment; minted at send time; acceptance verifies the address | Fragments never reach servers, logs or `Referer` (07 §11, ALB logs keep full URLs); SchoolOS outbox events carry IDs only (`notifications/email.py`); calevate's reason for not verifying (the token was once returned to the inviter, D-185) cannot occur when the token only ever exists in the e-mail |
| 12 | No session list; sign out everywhere only | "Your sessions" with revoke-one; school-scoped force sign-out | FR-IAM-006; 07 §5.2 "admins can force sign-out"; multi-school accounts (one school must not sign a person out of another) |
| 13 | Only decaying Redis budgets (10 per 15 min); a durable lockout is rejected as a DoS lever | Decaying budgets (5 per 15 min) **plus** a durable disable at 100 consecutive failures | US-101 AC4 names 5; NIST 800-63B-4 caps consecutive failures at 100; the DoS cost is bounded (≥ 5 hours of attempts, then a self-service reset) |
| 14 | Operator "view as client" with a 15-minute signed grant; no approval by the client; writes allowed | Break-glass: school approval, two-person rules, read-only `platform_support` membership, separate support session via a 60 s hand-off code | FR-OPS-004, US-103, SEC-021, SEC-029, ADR-0023 |
| 15 | Auth audit rows written in a separate transaction after the credential commit | Same transaction (auth chain), school copies through an outbox | Invariant 7 |
| 16 | `audit_log` stores the raw client IP; logs carry the recipient's e-mail domain | `ip_hash` only; no address fragments in logs | 07 T6, 08 §6 "IP hashes", invariant 5 |
| 17 | Argon2 in `asyncio.to_thread`, unbounded | Thread + semaphore, 503 on overflow | Sync FastAPI routes on memory-limited ECS tasks |
| 18 | Security numbers as Python constants mirrored by tests | `app/identity/auth.yaml` | Invariant 13 |
| 19 | Staff all have e-mail addresses | Login names and one-time setup codes for staff without e-mail | 07 §5.1; many school staff have no work e-mail |
| 20 | No recovery path when the only superadmin is lost | Two-operator recovery and an ECS Exec runbook command | SEC-029, 16 §19 Q1 |
| 21 | Local dev accepts `Bearer dev:<realm>:<uuid>` when two conditions hold | No dev bearer: seeded synthetic accounts (invariant 11) with passwords and fixed TOTP secrets; dev mail drop | One authentication path in every environment, so the real one is what developers and E2E tests exercise (calevate-site's own D-409 argument for delivering mail instead of bypassing MFA) |
| 22 | Operators can list a client's members, change their roles and remove their access (`admin/members_routes.py`, D-602) | Operators never manage school members; they only issue the first owner's invitation and run the two-operator recovery | ADR-0017, ADR-0020: the control plane never reads tenant data; schools manage their own staff (US-102) |

Adopted from calevate-site without change of substance: Argon2id parameters and the pepper
construction and ring; NFC; the whole-password blocklist rules; the single password writer;
opaque 256-bit sessions stored as a purpose-domain SHA-256; families, supersede, reuse detection
and the self-committing verification; the 60 s idle write floor; the absolute bound carried
forward; one generic refusal; the partial session for the second factor; keyed HMAC for every
non-password secret with the purpose in the domain; compare-and-swap redemption; per-account
advisory locks for retire-then-issue; invitation acceptance in one call with the address taken
from the account; tokens only ever e-mailed; existing passwords never touched by an invitation;
reset budget spent before the existence branch; revoke all sessions and reset links on reset and
change; the operator bootstrap command and `_refuse_self`; revoke credentials and sessions on
operator deactivation; pseudo-subject throttling of unknown logins; capped backoff; fail closed
when the throttle store is down; the `__Host-`-only cookie read and the unconditional Origin
check; the idle-warning dialog; dev mail that delivers instead of a bypass.

## Third-party dependencies: the owner's "none of these" against reality

**What calevate-site itself uses for user management** (versions from its `uv.lock` and
`pnpm-lock.yaml` at `847de80`):

| Dependency | Version | Used for (calevate-site) | SchoolOS status | SchoolOS reuses? |
|---|---|---|---|---|
| **argon2-cffi** (+ argon2-cffi-bindings 25.1.0, cffi 2.1.1) | 25.1.0 | Argon2id password hashing (`authn/hashing.py`) | **Not in SchoolOS yet** | **Yes, proposed (Q1)**: the only new dependency. MIT licence |
| **cryptography** (pyca) | 50.0.0 | HKDF for pepper/code keys; AES-256-GCM envelope encryption (`core/envelope.py`) | Already locked (50.0.1) | Yes (HKDF, AES-GCM) |
| Python standard library: `hmac`, `hashlib`, `secrets`, `unicodedata`, `base64`, `smtplib` | 3.12 | HMAC/SHA-256 fingerprints, CSPRNG tokens and codes, NFC, SMTP transport | Already | Yes (plus `hmac`/`hashlib` for TOTP) |
| **redis** client + a Redis server | 5.3.1 | Failure budgets, request limits | Valkey + `redis` 6.4.0 already | Yes |
| **arq** | 0.28.0 | Worker job that sends auth e-mails | SchoolOS uses Celery (CLAUDE.md §3) | No (Celery instead, same role) |
| **httpx** | 0.28.1 | Calls the Resend HTTP API | Already | Only if Resend is chosen (Q2) |
| **Resend** (e-mail API service) or any **SMTP** provider | account not yet created | Delivers invitation, reset, bootstrap and OTP e-mails (`core/transport.py`) | SchoolOS uses Amazon SES v2 via boto3 already | Proposed: keep SES (Q2) |
| PostgreSQL + SQLAlchemy 2.0.51 + psycopg 3.3.4 + Alembic 1.18.5 | — | Tables, RLS, migrations | Already (same stack) | Yes |
| FastAPI 0.140.0, Pydantic 2.13.4 (+ email-validator 2.3.0 for `EmailStr`) | — | Routes and IO models | Already (SchoolOS has its own `Email` type) | Yes (no email-validator needed) |
| PyJWT | 2.13.0 | The operator "view as" grant (not a sign-in credential) | Already (BFF service token) | Not for sign-in |
| Next.js 15.5.25, React 19.2.4 | — | Sign-in pages; **no auth library** in the web app | Already (Next.js) | Yes; `openid-client` is removed |

calevate-site does **not** use Clerk any more (removed, D-177), and has no TOTP library, no
WebAuthn library, no QR library and no breached-password list.

**What "none of these" would mean.** calevate-site is the owner's model, and it depends on
argon2-cffi, pyca `cryptography` and an e-mail sending service (Resend or SMTP). Following it
therefore means using the same kinds of dependency, not none. Without them we would have to
**write our own implementations of Argon2 (or bcrypt/scrypt), HKDF and AES-GCM. Writing our own
cryptographic primitives is unsafe**: they are easy to get subtly wrong (timing leaks, weak
randomness, broken constant-time comparison, nonce reuse), mistakes are invisible in normal
testing, and no reviewer or pen tester would accept home-made primitives guarding every staff
account and, through them, children's records. This ADR therefore proposes exactly the libraries
calevate-site uses (argon2-cffi, pyca `cryptography`, the standard library) and asks the owner to
confirm (Q1). TOTP needs no extra library: it is built on the standard library's HMAC, as
described in D4.1; only the optional QR code would use `segno` (BSD-3-Clause), and without it
staff type a 32-character key into their authenticator app. We do not propose `py_webauthn`
(passkeys are deferred, D4.4), a self-hosted identity server, or the HIBP data file (Q4 asks about a
breached-password list separately). Likewise, e-mail cannot be sent without a mail service:
calevate-site uses Resend or an SMTP provider; SchoolOS already uses Amazon SES (Q2). Staff choose
their own authenticator app (Google Authenticator, Microsoft Authenticator or any RFC 6238 app);
SchoolOS integrates with none of them.

## Threats (additions and changes to 07 §4)

| Threat | Controls in this design | Residual |
|---|---|---|
| Credential stuffing / password spraying | 15-character minimum for staff, whole-password blocklist, per-account budget from the 5th failure and disable at 100, per-IP and pseudo-subject budgets, fail closed, WAF/Caddy limits, uniform responses, MFA for privileged roles | Medium for non-privileged staff without MFA; higher than with a breach list (Q4) |
| Theft of the `auth` tables (backup, SQL injection elsewhere) | Separate role and schema, Argon2id with a pepper held outside the database, TOTP secrets sealed, session tokens and codes stored only as hashes/HMACs, KMS-encrypted backups | Low |
| Recovery abuse / social engineering of a school admin | No self-service MFA bypass; admin reset needs `user.credentials.reset` + step-up + in-person check, never own account, shared-profile guard, notices to the person and all owners, both chains; SchoolOS-assisted path needs two operators and a call-back | Low–medium |
| Session token theft from the BFF store | Token sealed in Valkey, 256-bit, hashed in the DB, rotation and family revocation, idle 15 min, `ath` binding, immediate server-side revocation | Low |
| Phishing of operators (T22) | TOTP for every operator, step-up ≤ 5 min for ᴿ, 2P rules, admin host only | Medium (TOTP is phishable; passkeys deferred) |
| Bugs in our own auth code | calevate-site's tested design as the blueprint, well-known libraries for primitives only (no home-made crypto), ASVS L2 checklist, CODEOWNERS second reviewer, property tests, ZAP baseline, **external auth-focused pen test before the pilot** (Q6) | Medium until the pen test is passed |
| E-mail account compromise used for password reset | Reset never removes MFA; notices; sessions revoked; privileged roles always have MFA | Medium for non-MFA staff |
| Hand-off code interception (break-glass) | Fragment only, 60 s, single use, bound to the requesting browser, step-up ≤ 5 min, grant/school bound | Low |

## Consequences

- Good: no sign-in third party; the design follows a working implementation the owner trusts,
  adapted where SchoolOS's requirements are stricter. One source of truth for sessions with
  **immediate revocation**; exact MFA signals instead of ADR-0018's inference; invites work by
  e-mail or setup code without anyone creating IdP accounts by hand; FR-IAM-005 becomes ours and
  testable; no Cognito MAU cost, Lambda or per-host app clients; dedicated hosts are
  self-contained.
- Good: `Principal`, `require()`, `require_platform()`, `require_recent_auth()`, the resolver,
  `core.resolve_login` and break-glass resolution are unchanged; BOLA and isolation suites are
  unaffected.
- Bad / costs: we own the most attacked part of the product (password storage, MFA, recovery,
  lockout, sessions), the reason ADR-0012 chose a managed IdP. The residual risk is real until an
  external pen test has been passed.
- Bad / costs: a third database role and a second non-tenant schema; invariants 1, 2 and 7 need
  rewording; an auth chain and an outbox to run; Argon2id CPU and memory per sign-in (bounded).
- Bad / costs: password resets and lost phones fall on schools and SchoolOS; recovery runbooks
  and training (en/te) are needed. Without passkeys operators rely on phishable TOTP.
- Follow-up: a sizeable work package (identity service, web pages and BFF handlers, migrations,
  Terraform removal, docs) that should land **before the first staging deployment** so Cognito is
  never applied (Q7).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep Amazon Cognito (ADR-0012/0018) | Contrary to the owner's decision |
| Copy calevate-site exactly (e-mailed OTP as the only factor, no recovery codes, 30-minute step-up, long persistent client sessions, cookie set by the API) | Breaks FR-IAM-002/003, ADR-0018, SEC-004/027, the BFF rule and invariants 2, 5, 7 and 13 (see the differences table) |
| Self-hosted open-source IdP (Keycloak, Ory Kratos) | The owner answered "none of these" |
| Our own OAuth 2.0 / OIDC authorization server with JWTs | Most of an IdP's attack surface for one first-party client (the BFF); self-contained tokens cannot be revoked immediately. calevate-site rejected stateless sessions for the same reason (`authn/sessions.py`) |
| Authenticate in the Next.js BFF | Puts credential storage outside the API's audit transaction, DB roles and Python tests |
| Credentials in `core` with RLS and definer functions | Would need new `SECURITY DEFINER` functions and put `sos_app` next to password hashes |
| E-mailed codes or SMS as factors | D4.5 |
| Hand-written Argon2/AES/WebAuthn to avoid libraries | Unsafe (see Third-party dependencies) |

## Open questions for the product owner (prioritised)

1. **Security libraries (blocks the build).** Writing our own password hashing or encryption is
   unsafe. May we use the same well-known libraries calevate-site uses: **argon2-cffi** (password
   hashing; the only new dependency) and **pyca `cryptography`** (already in SchoolOS), plus
   Python's standard library for TOTP? Optionally **segno** for the authenticator QR code; without
   it staff type a 32-character key.
2. **E-mail delivery (blocks invitations and password reset).** calevate-site sends its auth
   e-mails through **Resend** (or any SMTP server). SchoolOS already sends e-mail through **Amazon
   SES** in Mumbai. Keep SES for sign-in e-mails (our recommendation), or use Resend/SMTP like
   calevate-site? Without any e-mail service, staff can only be onboarded with printed setup codes
   and cannot reset a forgotten password themselves.
3. **Second factor.** calevate-site e-mails a six-digit code. Our requirements and the security
   standards need an **authenticator app (TOTP) with recovery codes** for owner, principal, office
   admin and all operators; passkeys later. Agreed?
4. **Breached-password screening.** calevate-site has none (an open gap it records). Accept that
   gap (ASVS Level 2 not fully met), or allow a list of the most common breached passwords from a
   named public source, bundled offline?
5. **Recovery when a phone or password is lost:** owner and principal may reset another member's
   MFA or issue a setup code (in-person check); a sole owner or a person in several schools is
   recovered by two SchoolOS operators after a written request and call-back. Agreed?
6. **Pen test timing:** an external, authentication-focused penetration test **before the pilot**
   (new SEC-033), not only before paid go-live?
7. **Timing:** build this before the first staging deployment and never apply the Cognito
   Terraform (our recommendation)?
8. **Dedicated hosts:** separate accounts per host, and break-glass sign-in on dedicated hosts
   unavailable until M1 decision 6. Agreed?

## Related requirements

FR-IAM-001..006 (and proposed FR-IAM-007..009), FR-IAM-010..014, FR-TEN-003, FR-TEN-012,
FR-OPS-004, FR-PLT-001, FR-PLT-028, US-101, US-102, US-103, US-1301; SEC-003..008, SEC-021,
SEC-022, SEC-025..027, SEC-029 (and proposed SEC-031..033); NFR-SEC-001, NFR-SEC-006; PRV-007,
PRV-017..019; T1, T2, T6, T16, T17, T22 (07 §4); docs 03 §3.1, 05 §3–4 and §13, 07 §3–5 and §6.4,
08 §1, §6–7, 09 §1–4, 10 §5 and §11, 12 §4, 16 §4, §5.4, §5.16, §19.

Sources (checked 2026-09-29): calevate-site `847de80` (`apps/api/authn/*`, `apps/api/core/{auth,
cookies,envelope,ratelimit,rbac,stepup,transport,impersonation}.py`, `apps/api/db/session.py`,
`apps/workers/auth_email.py`, `apps/web/src/{lib,components}/authn/*`, the migrations listed above,
`docs/AUTH-MIGRATION.md`, `docs/ROADMAP.md` D-165/D-170/D-177/D-178/D-185/D-190/D-198/D-409/D-473,
`uv.lock`, `pnpm-lock.yaml`); OWASP ASVS 5.0.0 V6/V7; NIST SP 800-63B-4; OWASP Password Storage,
Session Management, Forgot Password, CSRF and MFA cheat sheets; RFC 6238, RFC 5869, RFC 9106.
