# ADR-0030: In-house sign-in, MFA and sessions (replacing the managed OIDC provider)

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-09-29 |
| Deciders | Product owner (decision of 2026-09-29 below); security review required before acceptance (new database role, new non-tenant schema, changes to invariants 1, 2 and 7 wording) |
| Amends / supersedes | Once accepted: **supersedes** [ADR-0012](ADR-0012-managed-oidc-identity.md) and [ADR-0018](ADR-0018-mfa-and-step-up-with-cognito.md); **amends** [ADR-0004](ADR-0004-technology-stack.md) (identity row), [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (role `sos_auth`, schema `auth`, changed signatures of two allowlisted definer functions), [ADR-0017](ADR-0017-platform-admin-panel-architecture.md) (operator identity), [ADR-0019](ADR-0019-invitation-acceptance-on-first-sign-in.md) (acceptance now includes setting a password) and [ADR-0023](ADR-0023-operator-sign-in-for-break-glass-across-user-pools.md) (how a support session is signed in; what it may reach is unchanged) |

## Context

**Owner decision (2026-09-29):** "we are going to build our own auth sign up and login systems and
not going to integrate any third party things for sign up and login and account management".
CLAUDE.md §3 says the identity stack must not be substituted without a new ADR; this is that ADR.
It is design only. Nothing is built on it until it is accepted.

### What depends on OIDC today (checked in the code on 2026-09-29, base `7fe976f`)

| Area | Today | File(s) |
|---|---|---|
| Token verification | `TokenVerifier`: JWKS cache, RS256/ES256, issuer, `aud`/`client_id` + `token_use`, lifetime ≤ 15 min, `sos:mfa` / `amr`, `auth_time`, `origin_jti` as session id. Three verifiers: staff, operator (admin client), support (operator pool, support client) | `apps/api/app/identity/tokens.py` |
| Principal | `Principal(subject, issuer, kind ∈ {user, operator, support}, auth_time, mfa, session_id, expires_at)`; `get_principal`, `get_operator_principal`, `require_recent_auth()` (428 unless `mfa` and `auth_time` ≤ 5 min) | `apps/api/app/identity/principal.py` |
| BFF → API | Every call carries `X-Service-Token` (HS256, `iss=sos-web`, `aud=sos-api`, ≤ 60 s, `jti` replay store) **and** `Authorization: Bearer <OIDC access token>` | `identity/service_token.py`, `apps/web/src/server/bff/{upstream,service-token}.ts` |
| Resolution | `core.resolve_login(subject, issuer, support_only)` → memberships; authz checks `mfa_required` (403 `mfa_required`), suspended schools, break-glass grant; login audited via `POST /me/login-event` | `app/authz/resolver.py`, `identity/service.py` |
| Identity key | `core.users (idp_issuer, idp_subject)` (ADR-0023, `0027_identity_issuer` expand; contract pending); `platform.operators.idp_subject` | migrations `0003`, `0005`, `0027` |
| Invites | `POST /users` takes **`idp_subject`**: the inviter must first create the account in Cognito by hand and paste its `sub` (web hint "Create the account there first"); provisioning takes the owner's subject too (16 §5.4); acceptance on first sign-in via `core.accept_invitations(p_subject)` (ADR-0019) | `identity/schemas.py` `InviteIn`, `platform/provisioning.py`, `apps/web/src/features/users/InviteUserScreen.tsx` |
| Web BFF | `openid-client` Authorization Code + PKCE, callback, step-up by redirect with `prompt=login`, refresh with rotation and reuse detection (session families), IdP token revocation and end-session; sessions in Valkey (`sos:web:sess:*`, tokens sealed AES-256-GCM), `__Host-` cookies (`staff`, `operator`, `support`), CSRF synchronizer token + Origin/Sec-Fetch-Site | `apps/web/src/server/auth/*`, `server/session/*`, `app/bff/auth/**` |
| Break-glass sign-in | Support app client of the operator pool; `/bff/auth/support/*`; `POST /api/v1/breakglass/support-session` (step-up ≤ 5 min) | ADR-0023, `app/breakglass/` |
| Local dev | mock-oauth2-server stub (`infra/docker/oidc.json`, compose profile `dev`, `SOS_*OIDC_JWKS_URI`) | `docker-compose.yml`, `.env.example` |
| Terraform | `modules/cognito` (two pools, Essentials, pre-token Lambda), `modules/cognito_support_client`; wired in `shared_platform`, `envs/{staging,prod}`, `envs/dedicated-template`, `dedicated_host` (`oidc_*` variables) | `infra/terraform/**` |
| Settings | `SOS_OIDC_*`, `SOS_PLATFORM_OIDC_*`, `SOS_SUPPORT_OIDC_*`; web `OIDC_*`, `PLATFORM_OIDC_*`, `SUPPORT_OIDC_*` | `app/core/config.py`, `apps/web/src/server/config.ts` |

**Important fact:** the Terraform has been validated in CI but **never applied** (14 · M0 status,
task 10). No Cognito pool exists in any environment and no real person has an account. Moving
off Cognito therefore migrates code, schema and synthetic data, not real credentials.

Gaps the managed provider left open that we would have had to build anyway (14 · M0 status,
03 §3.13): breached-password screening (Cognito Essentials has no threat protection), FR-IAM-005
lockout/reset auditing, invite e-mails with a way to set a password (today the admin creates
the IdP account by hand), and ADR-0018's inferred MFA signal (`sos:mfa` means "has MFA", not
"used MFA for this sign-in").

### Standards consulted (checked 2026-09-29; re-check at implementation)

- **OWASP ASVS 5.0.0**: in 5.0 authentication is chapter **V6** and session management **V7**
  (the V2/V3 numbering belongs to ASVS 4.0.3); cookie rules are in V3.3. Target stays Level 2
  (NFR-SEC-001). Requirements used below by ID: 6.2.1–6.2.12, 6.3.1, 6.3.3, 6.3.6, 6.3.8,
  6.4.1–6.4.4, 6.4.6, 6.5.1–6.5.5, 6.6.1, 7.1.1–7.1.2, 7.2.1–7.2.4, 7.3.1–7.3.2, 7.4.1–7.4.5,
  7.5.1–7.5.3.
- **NIST SP 800-63B-4** (final, August 2025): password minimum 15 characters when it is the only
  factor, 8 when used in MFA, maximum ≥ 64, no composition rules, no periodic rotation, blocklist
  screening; at most **100** consecutive failed attempts per authenticator before it is
  disabled; email SHALL NOT be used as an out-of-band authenticator; PSTN/SMS is a *restricted*
  authenticator; AAL2 verifiers SHALL offer a phishing-resistant option; AAL2 reauthentication
  SHOULD happen within 24 h overall and 1 h of inactivity.
- **OWASP Password Storage cheat sheet**: Argon2id with at least m = 19 MiB, t = 2, p = 1 (or an
  equivalent listed trade-off); a pepper kept outside the database (HMAC or encryption of the
  hash). **OWASP Session Management cheat sheet**: ≥ 128-bit random ids, rotate on
  authentication and privilege change, server-side invalidation, `__Host-` cookies.
- **RFC 6238** TOTP (30 s step, 6 digits, HMAC-SHA-1 for authenticator-app compatibility; RFC
  4226 truncation). **WebAuthn Level 2/3** (passkeys): the Web Authentication API and conditional
  mediation (passkey autofill) are Baseline Widely Available on current Chrome/Edge (Windows 10+)
  and Android Chrome; `PublicKeyCredential.getClientCapabilities()` is only Baseline 2025 (newly
  available), so it is used with feature detection and a fallback (CLAUDE.md §10).
- **India**: DPDP Act 2023 + Rules 2025 (security safeguards, ≥ 1 year log retention, 08 §3);
  CERT-In Directions 2022 (ICT logs 180 days in India, 6-hour incident reporting, NTP; 08 §6);
  commercial SMS needs TRAI DLT registration of the entity, sender ID and templates (TCCCPR 2018)
  and an SMS aggregator.

## Decision

### D1 · Scope, realms and what "sign up" means

1. SchoolOS runs **its own identity service inside the API** (`apps/api/app/identity/`), with
   two **realms** that never share accounts:
   - **`staff`**: school users (every tenant role in 07 §6.2). One account per person across
     all schools of one deployment (FR-IAM-013, ADR-0028); memberships stay in `core`.
   - **`operator`**: SchoolOS staff for the control plane. **Exists only in the shared
     deployment** (ADR-0015, ADR-0017). Stricter rules everywhere (D4, D6).
2. **No public self sign-up for staff.** Staff accounts are created only by an invitation from a
   school (US-102, `POST /users`, `user.manage`, step-up) or, for a school's first owner, by an
   operator at provisioning (16 §5.4). "Sign up" in the owner's decision is read as **invite
   acceptance: the invitee sets their own password (and MFA) from an e-mailed link or an
   admin-issued setup code**. School self-registration (M7 "self-serve onboarding") would create
   a tenant, which is a control-plane action with fraud, KYC and billing questions; it is **out
   of scope** here and needs its own ADR (open question Q1). Parent logins remain a non-goal
   (02 §9).
3. **Operators** are invited by a `platform_owner` (16 §5.16, `platform.operators.manage` ᴿ); the
   first one by `python -m app.platform.bootstrap_owner` (now takes an e-mail, not a subject).
4. **Dedicated hosts** run the same code with their **own `auth` schema** in their own database:
   a staff realm only, no operator realm, no federation with the shared deployment (the control
   plane never connects into a host, ADR-0015/0017, TB7). A person who works in a shared-tier
   school and a dedicated school has two accounts (Q14). Break-glass sign-in on dedicated hosts
   stays unavailable (fail closed) until M1 decision 6 (requests reaching hosts) is made; D7.11
   names the intended shape.
5. Out of scope: GitHub Actions OIDC to AWS (CI, ADR-0004) is machine identity, not user sign-in,
   and is unchanged. The fleet heartbeat HMAC (SEC-028) is unchanged.

### D2 · Architecture and trust boundaries

1. **The API owns authentication and the authoritative session.** Credentials, factors,
   sessions, throttling state and the authentication audit chain live in a new schema **`auth`**
   in the same PostgreSQL database, reached only through a new login role **`sos_auth`**
   (`SOS_AUTH_DATABASE_URL`, `core.db.auth_session()`), the same privilege-separation pattern as
   `sos_platform` (ADR-0013 §1, §4):
   - `sos_auth`: LOGIN, NOBYPASSRLS, DML on `auth` (audit tables INSERT + SELECT only), **no
     privileges on `core`, `sis`, `kb`, `audit`, `ops` or `platform`**, no EXECUTE on definer
     functions.
   - `sos_app`, `sos_platform`, `sos_readonly`, `sos_definer`: **no privileges on `auth`.** A bug
     in tenant or control-plane code cannot read a password hash, a TOTP secret or a session
     token hash; a bug in auth code cannot read student data.
   - Every `auth` table has RLS `ENABLE` + `FORCE` with one policy `auth_service_only USING
     (current_user = 'sos_auth') WITH CHECK (current_user = 'sos_auth')`, so a grant added by
     mistake still returns no rows. `auth` is not tenant-owned (no `tenant_id`; accounts span
     schools); the RLS catalog allowlist gets a new key `auth_tables` for it.
   - Only `app/identity` may open `auth_session()` (import-linter contract + a boundary test like
     `tests/platform/test_boundaries.py`). Other modules call `identity.service` public functions.
2. **The BFF stays the only browser-facing surface; no token or secret reaches browser JS.** The
   BFF keeps its `__Host-` cookie and Valkey record per browser session, but that record now
   holds the **sealed opaque API session token** instead of OIDC tokens. The browser posts
   credentials, codes and WebAuthn responses to BFF route handlers, which forward them to the
   API. WebAuthn challenge options and assertions pass through browser JS by nature; they are
   public-key protocol messages bound to one challenge, not bearer secrets.
3. **Per request (TB2):** BFF → API carries `X-Service-Token` (unchanged: HS256, 60 s, `jti`
   replay store) and `Authorization: Bearer sos1.<43 base64url chars>` (the session token).
   - New service-token claims: **`ath`** = base64url(SHA-256(session token)) on every call, so a
     leaked service token cannot be paired with another session within its 60 s (closes open
     item 4 of `identity/README.md`); **`cip`** = the client IP on `/api/v1/auth/*` calls, taken
     by the BFF from `X-Forwarded-For` with a configured trusted-hop count (ALB or Caddy = 1).
     The API only ever stores an HMAC of it (`ip_hash`).
   - The API looks the session up by SHA-256 of the token in `auth.sessions` (one indexed read
     through the `sos_auth` pool), checks it (D6) and builds the same `Principal` as today (D6.8).
     No JWT, no JWKS, no IdP call remains. Revocation is effective on the next request.
4. **Pre-authentication routes** (`/api/v1/auth/*`: login, MFA challenge, passkey options,
   invitation, password reset, e-mail verification, support redeem) cannot declare
   `require(...)` because there is no principal yet. They declare **`Depends(require_bff())`**
   (valid service token, per-flow rate limits, no session) and must be listed in
   `app/identity/public_routes.yaml`. The route-enumeration test (SEC-003, 12 §4.1) accepts
   `require_bff()` only for routes in that file. This changes the wording of invariant 2
   (health checks were the only exceptions).
5. **Configuration** (invariant 13): every number below (hash parameters, lengths, lifetimes,
   limits, lockout schedule, blocklist file digest) lives in `app/identity/auth.yaml`, loaded and
   validated at start-up, not inline in code. Secrets come only from `SOS_*` env vars / Secrets
   Manager (D11).

### D3 · Passwords

| Rule | Value | Source |
|---|---|---|
| Hash | **Argon2id** (`argon2-cffi`, MIT), PHC string, 16-byte random salt, 32-byte tag. Default **m = 65536 KiB (64 MiB), t = 3, p = 1**; start-up refuses anything below OWASP's floor (m ≥ 19456 KiB with t ≥ 2, or an OWASP-listed equivalent). Benchmark on the API task size before pilot: target 250–500 ms per hash | OWASP cheat sheet; RFC 9106 |
| Hash concurrency | A process-wide semaphore (default 2 concurrent hashes per API worker) bounds memory; overflow → `503` + `Retry-After` (never queue unboundedly) | DoS (T14) |
| Pepper | The PHC string is **sealed with AES-256-GCM** under a key from `SOS_AUTH_KEYS` (never in the database), AAD = `password:<account id>` (a hash cannot be moved to another account); `password_key_id` records the key version. Sealing (not HMAC pre-hash) lets us rotate the pepper offline by re-sealing, without waiting for users to sign in | OWASP (pepper by encryption) |
| Rehash | On successful sign-in, if the parameters or key version are outdated, rehash/re-seal in the same transaction | — |
| Length | **Minimum 15 characters** for everyone (NIST-4: a password that can be the only factor; many staff will have no MFA, D5) — Q6; **maximum 128 characters** (≥ 64 required; the upper bound caps hashing cost). Counted in Unicode code points after normalisation | 800-63B-4; ASVS 6.2.1, 6.2.9 |
| Composition | None. All Unicode allowed, including Telugu script and spaces; no truncation, no case folding; passphrases encouraged in the UI (en/te guidance) | ASVS 6.2.5, 6.2.8 |
| Normalisation | **NFKC** before hashing and before the blocklist check (NIST's recommendation for passwords; a deliberate exception to the NFC default of CLAUDE.md §9 because input methods differ on shared PCs). Tests include Telugu input typed two ways | 800-63B-4 |
| Blocklist | Checked on every new password (set, change, reset), offline, no network: (1) **context words** (`schoolos`, school names and codes of the user's schools, parts of the e-mail/login name and display name, repeated/sequential strings, a curated en/te list), matched after normalisation; (2) **common and breached passwords**: a build-time file of the ~10 million most frequent SHA-1 hashes from the Have I Been Pwned *Pwned Passwords* dataset (CC BY 4.0, attribution in the NOTICE file), stored as sorted 8-byte prefixes (~80 MB), memory-mapped and binary-searched; SHA-256 of the file pinned in `auth.yaml`; refreshed quarterly. No k-anonymity API call at runtime (that would be a third-party call) — Q16 | ASVS 6.2.4, 6.2.11, 6.2.12 |
| UI | `type=password` with a show toggle, paste and password managers allowed, `autocomplete` = `username` / `current-password` / `new-password`; the error says why and how to fix it (en/te) | ASVS 6.2.6, 6.2.7 |
| Rotation | Never forced; forced reset only on evidence of compromise (breach match at sign-in → must change, admin/incident reset) | ASVS 6.2.10 |
| Change | Needs the current password **and** a fresh MFA factor if the account has one (full re-authentication); revokes all other sessions by default (checkbox, default on); e-mail notice | ASVS 6.2.2, 6.2.3, 7.4.3, 7.5.1 |

### D4 · Second factors

1. **TOTP (RFC 6238)** — the baseline factor everyone can use with a free authenticator app on
   an Android phone. 160-bit secret from `secrets`, SHA-1, 6 digits, 30 s step, accept the
   current step ± 1, **each step usable once** (`last_used_step` stored; replays refused),
   server time only. Implemented with the standard library (`hmac`), tested against the RFC 6238
   Appendix B vectors. Enrolment shows a QR code (rendered server-side as SVG with `segno`,
   BSD-3-Clause — licence to be re-checked, CLAUDE.md §11) and the key as text; it is confirmed
   only by a valid code. Secret stored AES-256-GCM-sealed (D11). ASVS 6.5.1, 6.5.3, 6.5.5.
2. **Passkeys / WebAuthn** — the preferred, phishing-resistant option (800-63B-4 requires AAL2
   verifiers to offer one). Server: `webauthn` (py_webauthn, BSD-3-Clause). Browser: the native
   `navigator.credentials` API (Baseline Widely Available), no JS library needed.
   - `userVerification: "required"`; only a UV assertion counts as MFA. A passkey sign-in with
     UV is a complete two-factor sign-in on its own (no password needed).
   - `attestation: "none"`; store credential ID, COSE public key, sign count (a regression
     marks the factor suspicious and requires another factor), AAGUID, transports,
     backup-eligible/backup-state flags, RP ID.
   - **RP IDs**: staff realm = the app host (`APP_BASE_URL`); operator realm = the admin host
     (`admin.<domain>`); dedicated host = its canonical host (custom domain when set; the other
     name redirects to it, because a passkey works only on its RP ID).
   - **Shared office PCs**: a passkey saved in Windows Hello on a Windows login that several
     staff share is not a personal factor. Enrolment asks "Is this your own device?"; on "no" it
     requests `authenticatorAttachment: "cross-platform"` (phone via hybrid, or a USB security
     key). A school setting `auth.passkeys.allow_platform` (default **true**) can require
     cross-platform authenticators only (Q8).
3. **Recovery codes** — 10 single-use codes issued at first MFA enrolment, shown once,
   regenerable after full re-authentication (old set revoked). Format `XX-XXXXX-XXXXX`
   (Crockford base32): a 2-character public index plus 50 random bits (ASVS 6.5.4 ≥ 20 bits).
   Stored per code as Argon2id (m = 19456 KiB, t = 2) of the secret part with its own salt,
   looked up by index, so one sign-in checks one hash (ASVS 6.5.2). Using one ends the sign-in
   in "MFA verified" and prompts to review factors.
4. **Not factors:**
   - **E-mail OTP / magic links: never an authentication factor** (800-63B-4 forbids e-mail as
     out-of-band; ASVS 6.3.6). E-mail is used only for invitation, e-mail verification and
     password-reset links, none of which bypasses MFA (D7.5).
   - **SMS / voice OTP: not offered.** Reasons: restricted authenticator in 800-63B-4 (SIM swap,
     number recycling; ASVS 6.6.1 would require risk disclosure and a stronger alternative
     anyway); it needs an SMS aggregator, which is exactly the kind of sign-in third party the
     owner excluded; TRAI DLT registration of entity, header and templates before the first
     message; a per-message cost on every sign-in; unreliable delivery in rural AP; and it
     would make us store staff phone numbers for authentication. Revisit only by a new ADR.
5. Every factor can be listed, renamed and revoked by its owner after full re-authentication;
   the last factor of an account whose membership requires MFA cannot be removed (409
   `mfa_required_by_role`) — ASVS 6.5.6, 7.5.1.

### D5 · MFA policy and step-up (keeps `require()` and step-up code working)

1. **Mandatory MFA** stays exactly as FR-IAM-002 says: `owner`, `principal`, `office_admin`
   (roles.yaml `mfa_required`, `memberships.mfa_required`) and **every operator**. Other staff:
   optional, encouraged at every sign-in (dismissible banner) — Q5. MFA can never be turned off
   for privileged roles by a school (07 §1.4).
2. **Enforcement stays in the API**, per request, as today: the resolver refuses a privileged
   membership when the session is not MFA-verified (`403 mfa_required`). In addition, the login
   service asks `identity.service` whether any active or invited membership of the account
   requires MFA and, if the account has no factor, ends the sign-in in state
   `enrolment_required`: the session is created with `aal = 1` and may call only the MFA
   enrolment, sign-out and `/me/*` routes until a factor is confirmed (then the session token is
   rotated and `aal = 2`). Operators without a factor are always in `enrolment_required`.
3. **Signals, now exact rather than inferred** (removes ADR-0018's "enabled means used"):
   - `Principal.mfa` = the session was established or stepped up with a verified second factor
     (`auth.sessions.aal = 2`).
   - `Principal.auth_time` = `auth.sessions.mfa_at` (time of the latest successful second-factor
     verification in this session) when `aal = 2`, else `password_at`.
   - `require_recent_auth()` is unchanged: `mfa` and `auth_time` within **5 minutes**, else
     `428 step_up_required`.
4. **Step-up** = verify **one MFA factor again** (TOTP code or UV passkey assertion) in the
   current session: `POST /api/v1/auth/step-up` (guard: authenticated session). No password
   re-entry, no redirect: the BFF answers the 428 to the page, the page shows an accessible
   step-up dialog (en/te, keyboard, 1366×768), then retries the original request. On success the
   session token is **rotated** (ASVS 7.2.4) and `mfa_at` set. ASVS 7.5.3. A user without a
   factor who hits a step-up permission is sent to enrolment (same as today, since step-up
   needed `mfa` already).
5. **Full re-authentication** (current password + a factor if enrolled) is required for:
   password change, adding/removing factors, regenerating recovery codes, changing the sign-in
   e-mail, "sign out everywhere" (ASVS 7.5.1, 7.5.2).
6. No "remember this device", no risk-based MFA skipping (as ADR-0018 §2).

### D6 · Sessions

1. **Token**: 32 bytes from a CSPRNG, sent as `sos1.<base64url>`; stored only as SHA-256
   (`auth.sessions.token_hash`, unique). The BFF keeps the token sealed (AES-256-GCM, existing
   `SESSION_SECRET` derivation) in Valkey; the browser holds only the BFF's own 256-bit cookie
   id, as today. ASVS 7.2.1–7.2.3.
2. **Cookies** (unchanged names and attributes): `__Host-sos_session` (staff),
   `__Host-sos_platform_session` (operator, admin host), `__Host-sos_support_session`
   (break-glass); `Secure; HttpOnly; SameSite=Lax; Path=/`, no `Domain`, no `Max-Age` (browser
   session cookies on shared PCs). `SameSite=Lax` is kept (e-mail links and the admin → app
   hand-off are top-level navigations) together with the CSRF controls below. ASVS V3.3.
3. **Timeouts** (API-enforced, BFF mirrors them for its Valkey TTLs):

   | Session purpose | Idle | Absolute | Concurrent per account |
   |---|---|---|---|
   | `staff` | 15 min; the active school's setting 5–30 min (FR-TEN-012), applied when the school is chosen (`POST /me/active-tenant` stores it on the session) | 12 h | 10 (oldest revoked; config) |
   | `operator` | 15 min | 8 h | 3 |
   | `support` (break-glass) | 15 min | 8 h and never past the grant's `expires_at` | 1 per grant |

   `last_seen_at` is written at most once per 60 s per session (idle precision ± 60 s). This is
   stricter than 800-63B-4's AAL2 SHOULDs (24 h / 1 h); documented per ASVS 7.1.1–7.1.2, 7.3.
4. **Rotation** — a new token (old one revoked, `rotated_from` kept) on: sign-in completion,
   MFA enrolment, step-up, password change, recovery-code use, support redeem (ASVS 7.2.4).
   Role or scope changes need no rotation: permissions are resolved per request (≤ 60 s cache,
   FR-IAM-014).
5. **Revocation** (effective on the next request; ASVS 7.4.1–7.4.5):
   - sign-out (this session); "sign out everywhere" (all sessions of the account; full
     re-auth); revoke one session from "Your sessions" (FR-IAM-006; handles, not tokens);
   - automatic: password reset or change (others), MFA reset by an admin, account disabled,
     operator deactivated (`platform.operators.status`), 100-failure lock (D7.6);
   - **school-scoped force sign-out** by a holder of `user.manage` (step-up): sets
     `core.memberships.sessions_valid_after = now()` (new column, tenant table, RLS unchanged);
     the resolver refuses that membership for sessions created earlier (`401
     session_revoked`). The person's sessions in other schools are untouched, so one school
     cannot sign a person out of another. Suspending or removing a membership keeps today's
     behaviour (resolver refuses it) and also sets `sessions_valid_after`.
6. **CSRF**: unchanged synchronizer token (`X-CSRF-Token`, constant-time) + same-origin
   `Origin` / `Sec-Fetch-Site` on all state-changing BFF routes, plus **login CSRF** protection:
   the sign-in, invitation and reset forms carry a pre-session token bound to a short-lived
   `__Host-sos_auth_tx` cookie (sealed, 10 min), replacing today's OIDC transaction cookie.
7. **BFF state** (`apps/web/src/server/session/store.ts`): `TokenSet` becomes `{ sessionToken }`;
   refresh, token families, reuse detection, IdP revocation and end-session are removed
   (`server/auth/refresh.ts`, `oidc.ts`); "Your sessions" and revoke-one call the API, which is
   authoritative across BFF instances and browsers.
8. **`Principal` mapping** (so `authz`, `require()`, `require_platform()`, the resolver and
   break-glass code keep their contracts):

   | Principal field | Value |
   |---|---|
   | `subject` | `str(auth.accounts.id)` (UUIDv7) |
   | `issuer` | realm URN: `urn:schoolos:auth:staff` or `urn:schoolos:auth:operator` (config), stored in `core.users.idp_issuer` |
   | `kind` | `user` (staff session), `operator` (operator session, admin host), `support` (operator account, support session) |
   | `auth_time`, `mfa` | D5.3 |
   | `session_id` | `str(auth.sessions.id)` (never the token) |
   | `expires_at` | min(idle expiry, absolute expiry) |

   `get_principal` (tenant routes) accepts purposes `staff` and `support`; `get_operator_principal`
   accepts only `operator`; `/api/v1/platform/*` never accepts `support` or `staff`. This replaces
   ADR-0023's issuer × client matrix with a purpose × route-family matrix (tests, D12).

### D7 · Flows

1. **Invitation, new person (ADR-0019 equivalent).** `POST /users` takes `email` (or, for staff
   without e-mail, `login_name`) instead of `idp_subject` (expand: `idp_subject` optional and
   deprecated; contract: removed). The API creates an `auth.accounts` row (`status = invited`,
   no password) and uses its ID as the subject for `core.create_user_for_invite(...,
   p_issuer = staff URN)`; membership, roles, scopes and audit as today. The two writes are in
   two transactions (auth first); an invited account without any membership after 30 days is
   purged by a daily job. The **invitation e-mail** (existing SES sender, template
   `invitation.staff`, new link) carries a single-use token in the URL **fragment**
   (`/accept-invite#t=…`), never in a path or query string (ALB access logs keep full URLs,
   07 §11). The page posts it to the BFF → `POST /api/v1/auth/invitations:inspect` (school
   name(s), display name, whether MFA is required) → `…:accept` (new password, then MFA
   enrolment if required) → session created → `POST /me/accept-invitations` (unchanged; ADR-0019
   definer) → school picker. Token: 256 bits, SHA-256 stored, **valid 7 days**, resend issues a
   new one and invalidates the old; the membership invite window stays 30 days. Accepting
   verifies the e-mail address. **Staff without e-mail**: the inviting admin receives a
   **one-time setup code** once (12 characters Crockford base32 ≈ 60 bits, 72 h, single use,
   printable hand-over slip in en/te; the admin never sees or chooses the password) and the
   person enters it with their login name at `/[locale]/setup` (ASVS 6.4.1, 6.4.6).
2. **Invitation, existing account** (a person joining a second school): no token needed; the
   e-mail says "sign in to accept"; `/me/accept-invitations` activates it as today.
3. **Owner at provisioning.** The wizard takes the owner's **e-mail** (16 §5.4 step 3), not a
   subject. `platform` calls `identity.service.create_invited_account(realm="staff", …)` (a new,
   pinned boundary exception in `tests/platform/test_boundaries.py`; it touches only `auth`,
   never tenant data) and passes the account ID to `core.create_owner_invite`. The owner
   invitation e-mail is sent after the run completes (closes "owner invite e-mail not built",
   16 §5.4). Dedicated: `python -m app.tenancy.provision_dedicated` on the host does the same
   locally.
4. **Sign-in.** `/[locale]/sign-in` (staff, app host) and `/[locale]/platform/sign-in` (operators,
   admin host). Either (a) login (e-mail or login name) + password → if the account has factors,
   `mfa_required` with a challenge (5 min, 5 attempts) → TOTP code, passkey assertion or
   recovery code; or (b) **passkey first** (conditional mediation / "Sign in with a passkey"
   button): a UV assertion completes the sign-in alone. Then (staff) school picker via
   `/me/schools` and `/me/login-event` as today (FR-IAM-013). Responses never reveal whether an
   account exists: same message, and unknown logins are hashed against a dummy Argon2id hash so
   timing matches (ASVS 6.3.8). A password found in the breach list at sign-in forces a change
   before continuing.
5. **Recovery** (ASVS 6.4.2–6.4.4; no security questions, no hints):

   | Situation | Path | Audit |
   |---|---|---|
   | Forgot password, has verified e-mail | `POST /auth/password:forgot` (always `202`, same text) → e-mail link (fragment token, 256 bits, **30 min**, single use) → new password. **MFA is still required at the next sign-in**; all sessions revoked; notice to the address | `auth.password.reset_requested/completed` |
   | Forgot password, no e-mail | A school admin with `user.credentials.reset` (new permission, ᴿ, owner and principal by default) issues a new setup code (D7.1). Refused with `409 profile_shared` if the person has memberships in other schools (ADR-0028 guard, `core.user_membership_count`) | school chain `user.credentials_reset` + auth chain |
   | Lost MFA, has a recovery code | Use it (D4.3), then re-enrol | `auth.mfa.recovery_code_used` |
   | Lost MFA, no code | An owner or principal with `user.credentials.reset` (ᴿ) resets the person's factors after checking identity **in person** (the UI asks them to confirm that), never their own; same shared-profile guard; the person must enrol again at next sign-in; all sessions revoked; the person and all owners are notified | `user.mfa_reset` (school) + `auth.mfa.reset` (auth) |
   | Sole owner lost MFA and codes, or shared-profile person | **SchoolOS-assisted**: written request from the school, call-back to the owner's registered contact, then **two different operators** (`platform.identity.recover`, new ᴿ 2P permission for `platform_owner`) reset factors; delivered to the school's chain as a platform action | platform chain + school chain copy (ADR-0020 outbox) |
   | Operator lost MFA | Recovery code, else two other operators with `platform.operators.manage` (ᴿ, 2P; never the subject). With a single platform owner (16 §19 Q1) the fallback is `python -m app.identity.recover_operator` run through ECS Exec by someone with prod AWS access and MFA; CloudTrail + platform chain | platform chain |

6. **Throttling and lockout** (FR-IAM-005, US-101 AC4, ASVS 6.3.1, 800-63B-4 100-attempt cap).
   Numbers are defaults in `auth.yaml`:
   - **Per account** (password and each factor): failures 1–4 free; from the **5th consecutive
     failure** the account is throttled for 1 min, doubling per further failure up to 15 min;
     while throttled the password is not checked and the answer is the same generic message
     with the wait time. **100 consecutive failures** disable password sign-in until a reset
     (D7.5). Reset to 0 on success. The 5th failure and the 100th notify the person (e-mail) and,
     for staff, the holders of `user.manage` in each school (in-app notification, 07 §5.1).
   - **Per IP** (`ip_hash`, Valkey sliding window): 50 failed sign-ins per 15 min → 429 for 15 min
     on password endpoints. Generous because a whole school's office shares one NAT address.
     Unknown-login failures count per IP and per `login_hash` (HMAC of the normalised typed
     login; the raw value is never stored or logged, since people type passwords into the login
     field).
   - **Flows**: forgot-password 3 per login per hour and 20 per IP per hour; invitation/setup
     code attempts 10 per IP per 15 min; MFA challenge 5 attempts then restart.
   - **Edge (SEC-022)**: the existing WAF rate rule on `/bff/auth/` (`alb_waf`) stays as the outer
     layer; Caddy on dedicated hosts gets the same path limit. No CAPTCHA (a third party).
7. **E-mail verification and change.** Addresses are verified by the invitation link. Changing the
   **sign-in e-mail** (`auth.accounts.login_email`) needs full re-authentication, a link to the new
   address (24 h) and a notice to the old one; the old address stays until verified. The
   **profile e-mail** in `core.users.email` (shown to school admins, editable by them) is a
   separate field and does **not** change how the person signs in (Q13).
8. **Multi-school users.** One account; `/me/schools`, `POST /me/active-tenant`, `X-Active-Tenant`
   and `/me/login-event` are unchanged (FR-IAM-013). The school's idle timeout is applied to the
   session when the school is chosen.
9. **Sign-out and sessions.** `POST /bff/auth/logout` (CSRF) revokes the API session and the BFF
   record; "Your sessions" (FR-IAM-006) lists sessions (coarse device label such as "Chrome on
   Windows", sign-in time, last seen, never IPs) with revoke-one and sign-out-everywhere.
10. **Operator sign-in.** Admin host only, operator realm, MFA always (enrolment before any
    route), passkey or TOTP (Q7: passkeys required for `platform_owner` and `platform_engineer`?),
    step-up ≤ 5 min for ᴿ permissions, 2P rules unchanged (SEC-027, SEC-029). The operator row
    (`platform.operators.idp_subject`) holds the operator account ID; `status` is still checked
    per request.
11. **Break-glass support sign-in (replaces ADR-0023's support app client; what the session may
    reach is unchanged).** A PKCE-style hand-off between the two hosts, no second password entry:
    1. The admin panel links to the app host `GET /bff/auth/support/start?request=<id>&tenant=<id>`
       (IDs only). The BFF creates a `code_verifier`, keeps it in `__Host-sos_support_auth_tx`
       and redirects to the admin host `GET /bff/platform/support-handoff?request=&tenant=&challenge=<S256>`.
    2. The admin BFF (operator session) calls `POST /api/v1/platform/breakglass/requests/{id}/handoff`
       (`require_platform("platform.breakglass.request", step_up=True)`): the API checks the
       request is approved and active for that school, and `identity.service` issues a hand-off
       code (256 bits, **60 s**, single use, bound to operator account, request ID, tenant ID and
       the challenge; pinned boundary exception). Redirect to the app host
       `/support/redeem#code=…` (fragment).
    3. The page posts the code; the app BFF sends it with the `code_verifier` to
       `POST /api/v1/auth/support:redeem` (`require_bff()`): the API verifies S256, creates a
       `support` session for the operator account (`aal = 2`, `mfa_at` = the operator's step-up
       time, so the existing ≤ 5 min check at session start passes only if redeemed promptly),
       bound to that grant and school, then the BFF calls the unchanged
       `POST /api/v1/breakglass/support-session` and `/me/login-event` (`issuer_kind:
       operator_support`).
    - Resolution rules of ADR-0023 stay: a support principal reaches only unexpired memberships
      holding exactly `platform_support` with an active grant (`core.resolve_login(...,
      p_support_only => true)`, `403 breakglass_only` / `breakglass_grant_inactive`), read-only,
      every call audited as `breakglass.access`; a staff session never reaches a
      `platform_support` membership.
    - ADR-0023 rejected SchoolOS-minted grant tokens (its option D) because they made SchoolOS a
      token issuer; under this ADR SchoolOS is the identity provider anyway, and the hand-off
      code is a one-time reference, not a bearer token that outlives 60 s.
    - Dedicated hosts: not available until requests can reach hosts (M1 decision 6). Intended
      shape then: the shared deployment signs a grant-bound Ed25519 assertion that the host
      verifies with a public key pinned in its configuration; a later ADR.

### D8 · Data model, privileges, audit and retention

New migrations (next free revisions at implementation; `0031_auth_schema` at the time of
writing), all in schema `auth`, owned by `sos_owner`, DML for `sos_auth` only, RLS ENABLE +
FORCE with `auth_service_only`. UUIDv7 IDs, `timestamptz` UTC. Sketch (column names binding,
details to the implementation PR):

```sql
CREATE TABLE auth.accounts (
  id uuid PRIMARY KEY,                     -- = core.users.idp_subject / platform.operators.idp_subject
  realm text NOT NULL CHECK (realm IN ('staff','operator')),
  login_email citext, login_name citext,   -- at least one; unique per realm
  email_verified_at timestamptz,
  pending_email citext, pending_email_expires_at timestamptz,
  status text NOT NULL CHECK (status IN ('invited','active','disabled')),
  password_sealed bytea, password_key_id smallint, password_set_at timestamptz,
  password_disabled_at timestamptz,        -- 100 consecutive failures (D7.6)
  failed_count int NOT NULL DEFAULT 0, throttled_until timestamptz,
  must_change_password boolean NOT NULL DEFAULT false,
  created_at, updated_at, last_sign_in_at timestamptz, version int,
  UNIQUE (realm, login_email), UNIQUE (realm, login_name),
  CHECK (login_email IS NOT NULL OR login_name IS NOT NULL)
);
CREATE TABLE auth.mfa_factors (
  id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES auth.accounts,
  kind text NOT NULL CHECK (kind IN ('totp','webauthn')), label text,
  totp_secret_sealed bytea, totp_last_step bigint,
  webauthn_credential_id bytea UNIQUE, webauthn_public_key bytea, webauthn_sign_count bigint,
  webauthn_aaguid uuid, webauthn_transports text[], backup_eligible boolean, backup_state boolean,
  rp_id text, failed_count int NOT NULL DEFAULT 0,
  created_at, confirmed_at, last_used_at, revoked_at timestamptz
);
CREATE TABLE auth.recovery_codes (
  id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES auth.accounts,
  batch_id uuid NOT NULL, code_index text NOT NULL, code_hash text NOT NULL,   -- Argon2id PHC
  created_at, used_at, revoked_at timestamptz, UNIQUE (account_id, batch_id, code_index)
);
CREATE TABLE auth.sessions (
  id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES auth.accounts,
  purpose text NOT NULL CHECK (purpose IN ('staff','operator','support')),
  token_hash bytea NOT NULL UNIQUE, rotated_from uuid,
  aal smallint NOT NULL CHECK (aal IN (1,2)), password_at timestamptz, mfa_at timestamptz,
  mfa_method text, state text NOT NULL CHECK (state IN ('enrolment_required','active')),
  idle_timeout_s int NOT NULL, created_at, last_seen_at, absolute_expires_at timestamptz,
  support_request_id uuid, support_tenant_id uuid,      -- purpose = 'support' only (IDs)
  device_label text, ip_hash bytea,
  revoked_at timestamptz, revoked_reason text
);
CREATE TABLE auth.challenges (          -- MFA-at-sign-in, step-up, enrolment, WebAuthn ceremonies
  id uuid PRIMARY KEY, account_id uuid, session_id uuid, kind text NOT NULL,
  token_hash bytea UNIQUE, webauthn_challenge bytea, attempts int NOT NULL DEFAULT 0,
  expires_at, consumed_at timestamptz
);
CREATE TABLE auth.tokens (              -- single-use links and codes
  id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES auth.accounts,
  purpose text NOT NULL CHECK (purpose IN
    ('invitation','setup_code','password_reset','email_change','support_handoff')),
  token_hash bytea NOT NULL UNIQUE, code_challenge bytea,
  subject_ref jsonb NOT NULL DEFAULT '{}',   -- IDs only (e.g. request_id, tenant_id)
  created_by_kind text, created_by_id uuid, expires_at, used_at, revoked_at timestamptz
);
CREATE TABLE auth.events (...);          -- append-only, hash-chained (like platform.audit_events)
CREATE TABLE auth.audit_chain_head (...);
CREATE TABLE auth.tenant_audit_outbox (  -- school-chain copies, delivered exactly once (ADR-0020 pattern)
  id uuid PRIMARY KEY, account_id uuid NOT NULL, action text NOT NULL,
  summary jsonb NOT NULL, created_at timestamptz NOT NULL, delivered_at timestamptz
);
```

Plus, in `core`: `core.memberships.sessions_valid_after timestamptz NULL` (`sos_app` column
UPDATE; RLS unchanged). No new table in `core`, `sis`, `kb`, `ops` or `platform`.

**Privileges and catalog tests (extend 12 §4.5, §4.8, §4.9):** `infra/db/bootstrap.sql` creates
`sos_auth` (LOGIN, NOBYPASSRLS, `search_path = pg_catalog, public`,
`idle_in_transaction_session_timeout = 30s`); no role may hold privileges on `auth` except
`sos_owner` and `sos_auth`; `sos_auth` has none elsewhere; `auth.events` is append-only (grants
+ row trigger + `BEFORE TRUNCATE` trigger); no role has `BYPASSRLS`.

**Definer allowlist (ADR-0013 §2):** **no new `SECURITY DEFINER` function and no new
`definer_access` policy.** `sos_auth` has no EXECUTE on any definer function. Changes to
allowlisted functions, decided here, all in the contract release:
- `core.accept_invitations(p_subject text)` → `core.accept_invitations(p_subject text,
  p_issuer text)` (issuer-aware, as ADR-0023 made the others);
- `core.create_owner_invite(...)` gains `p_issuer` (already the planned 0027 contract step);
- the four-argument `core.create_user_for_invite` wrapper and the NULL-issuer defaults of
  `core.resolve_login` / `core.find_user_id_by_subject` are dropped (planned 0027 contract step).
The data migration (D12) re-keys `core.users.idp_issuer/idp_subject` with the temporary-grant
pattern of `0027_identity_issuer` (grant `UPDATE (idp_issuer, idp_subject)` to `sos_definer`
inside the migration transaction, `SET ROLE sos_definer`, update, revoke).

**Audit (invariant 7).** Every state change in `auth` writes an event in the **auth chain**
(`auth.events`, hash-chained with JCS/SHA-256 and gapless `seq`, like the platform chain;
recorded by a new `audit.service.record_auth()`) **in the same `auth` transaction**. Events that
a school must see are also queued in `auth.tenant_audit_outbox` in that transaction and delivered
exactly once to every school where the account holds a membership, by the task
`identity.deliver_auth_audit` (resolves schools with `core.resolve_login`, writes with
`audit.record()` in each `tenant_session`). Actions that start in a school (invite, reset, force
sign-out) write their school-chain event in the tenant transaction as today and the auth-side
event in the auth transaction. The daily verification job covers the auth chain.

| Auth chain event (IDs and codes only) | Copied to school chains |
|---|---|
| `auth.signin.succeeded` (method: `password`, `password+totp`, `password+passkey`, `passkey`, `recovery_code`), `auth.signin.failed` (reason code: `bad_credentials`, `throttled`, `mfa_failed`, `disabled`, `unknown_login` with `login_hash` only) | No (the school-chain `auth.login.succeeded/denied` stays, written by `/me/login-event`) |
| `auth.account.throttled` (5th failure), `auth.account.password_disabled` (100th) | Yes |
| `auth.password.changed`, `auth.password.reset_requested`, `auth.password.reset_completed`, `auth.setup_code.issued/used` | Yes (except `reset_requested`) |
| `auth.mfa.enrolled`, `auth.mfa.removed`, `auth.mfa.reset` (by whom), `auth.mfa.recovery_code_used`, `auth.mfa.recovery_codes_regenerated` | Yes |
| `auth.session.created`, `auth.session.stepped_up`, `auth.session.revoked` (reason), `auth.sessions.revoked_all` | Only `revoked_all` and admin force sign-out |
| `auth.email.change_requested/verified`, `auth.invitation.accepted` | Yes |
| `auth.support.handoff_issued/redeemed` | Yes (with `breakglass.*` as today) |

**No PII in logs (invariant 5, SEC-008).** Logs and events carry account, session, factor and
request IDs and reason codes. Never: passwords, codes, tokens, typed logins, e-mail addresses,
IP addresses (only `ip_hash`), user agents beyond the coarse device label. `redact()` gains a
pattern for `sos1.` tokens; the log-redaction test covers every new request field. Auth routes
never take secrets or personal data in query strings (`test_no_pii_in_urls.py` extended).

**Retention** (05 §13, 08 §6–7):

| Data | Retention |
|---|---|
| `auth.events` | ≥ 13 months online in ap-south-1 (CERT-In 180 days in India, DPDP ≥ 1 year), then archived with the audit archive (3 years, Object Lock) |
| `auth.sessions` | Deleted 30 days after they end |
| `auth.challenges`, `auth.tokens` | Deleted 7 days after expiry or use |
| `auth.accounts` + factors + codes (staff) | While the account has any membership; disabled when the last membership ends; deleted 1 year after that (and at school purge when it was the only school, coordinated with [ADR-0029](ADR-0029-tenant-data-deletion-at-offboarding.md)); events keep the ID only |
| `auth.accounts` (operators) | Life of account + 1 year (08 §14) |
| Valkey throttling counters | Their window (≤ 1 h) |

**Privacy (08).** Staff sign-in data is processed for the school that invited the person (DPDP
processor role, as today with Cognito). Remove "identity (Cognito)" from the AWS row of the
sub-processor register; SES stays (D9). The RoPA gets "authentication: login e-mail/name,
credential hashes, factor metadata, session metadata, IP hashes".

### D9 · E-mail delivery

Invitation, password-reset, e-mail-change and security-notice e-mails use the existing
`app/notifications/email.py` `EmailSender` interface (Amazon SES v2 in ap-south-1,
`modules/ses_email`; fake sender in local/CI). Tokens are minted **by the send task at send
time**, so no queue or outbox row ever holds a usable secret. Templates are bilingual (en/te)
and say what to do if the message was unexpected. We read SES as **AWS infrastructure we already
use and disclose, not a "sign-up/login third party"**: it only carries messages; accounts,
credentials and decisions stay in our database (Q2). Dedicated hosts need an SES sending
permission in their instance role and `SOS_EMAIL_*` settings (today they have neither); a host
without e-mail can only use setup codes. Local dev: the fake sender additionally writes messages
to a local-only drop directory (`var/dev-mail/`, refused outside `local`/`ci`) so a developer can
open invitation links without a mail server.

### D10 · Dedicated tier

Same code with `SOS_DEPLOYMENT_MODE=dedicated`: staff realm only (operator-realm routes not
mounted), own `sos_auth` role and `auth` schema on the host's PostgreSQL, own `SOS_AUTH_KEYS`
secret under `schoolos/<tenant_code>/`, WebAuthn RP ID = the canonical host, Caddy rate limit on
`/bff/auth/`. Nothing about sign-in reaches the control plane (heartbeat unchanged; at most a
count of accounts with MFA could be added later as a number). 16 §19 Q4 (Cognito app client per
host) becomes obsolete.

### D11 · Settings and secrets

- Remove (contract): `SOS_OIDC_*`, `SOS_PLATFORM_OIDC_*`, `SOS_SUPPORT_OIDC_*`, web `OIDC_*`,
  `PLATFORM_OIDC_*`, `SUPPORT_OIDC_*`, the dev stub and its guards.
- Add: `SOS_AUTH_DATABASE_URL` (`sos_auth`); `SOS_AUTH_KEYS` (Secrets Manager; JSON of versioned
  32-byte keys with one marked current; HKDF subkeys per purpose: `password-seal`, `totp-seal`,
  `login-hash`, `ip-hash`); `SOS_AUTH_STAFF_ISSUER` / `SOS_AUTH_OPERATOR_ISSUER` (URNs, defaults
  above); `SOS_AUTH_WEBAUTHN_RP_ID` (+ operator RP ID on the shared tier); web
  `TRUSTED_PROXY_HOPS`. Staging/prod start-up guards (as for `SOS_SERVICE_TOKEN_KEY`): no
  `dev-only` key, keys ≥ 32 bytes, RP ID equals the configured public host, Argon2 parameters at
  or above the floor, blocklist file present with the pinned digest.
- Key rotation: add a key version, re-seal job for password hashes and TOTP secrets
  (`python -m app.identity.reseal`), retire the old version after the job reports zero rows.

### D12 · Migration plan (invariant 12: expand → migrate → contract)

**Release A — expand (no behaviour change for existing code paths).**
- `bootstrap.sql`: role `sos_auth`. Migration `auth_schema`: schema, tables, grants, RLS,
  append-only triggers, `core.memberships.sessions_valid_after`. Downgrade drops them.
- Code: the new identity service, routes, BFF handlers and pages behind `SOS_AUTH_MODE = local`
  (default in `local`/`ci`/`staging`; `oidc` still selectable for one release as a rollback
  path). `InviteIn.idp_subject` becomes optional; `email`/`login_name` accepted. New
  permissions in the one catalog (`app/authz/permissions.yaml`, seeded by a migration):
  `user.credentials.reset` (tenant, step-up; `owner` and `principal` in `roles.yaml`, 07 §6.2)
  and `platform.identity.recover` (ᴿ, two-person; `platform_owner` in `app/platform/roles.yaml`,
  07 §6.5 / 16 §6).
  `accept_invitations` gets the issuer-aware overload alongside the old one.
- Dev: `make seed-synthetic` creates synthetic accounts with passwords from
  `SOS_DEV_SEED_PASSWORD` and fixed TOTP secrets (refused outside `local`/`ci`); the OIDC stub
  stays available under `SOS_AUTH_MODE=oidc` only.
- **Terraform: stop.** Do not apply `modules/cognito` or `cognito_support_client` anywhere
  (they have never been applied). Add the `auth_keys` secret and the `sos_auth` database
  credential to `modules/secrets`/`shared_platform` and `dedicated_host`.

**Release B — migrate.**
- Data migration (idempotent, logged by counts): for each `core.users` row create an
  `auth.accounts` row (`status = invited`, e-mail from `core.users.email` when present) and
  re-key `core.users (idp_issuer, idp_subject)` to (staff URN, account ID); for each
  `platform.operators` row create an operator account and set `idp_subject`; existing
  break-glass identities (operator issuer) map to the operator's account. Only synthetic data
  exists, so affected people are re-invited (set-password e-mail or setup code); nobody keeps a
  Cognito credential. Downgrade: restore the previous values from a backup table kept until
  Release C.
- Switch every environment to `SOS_AUTH_MODE=local`; remove the `oidc` choice.

**Release C — contract.**
- Code: delete `identity/tokens.py`, the JWKS cache, the support/platform/tenant token verifiers,
  `openid-client` and `server/auth/{oidc,refresh,transaction}.ts`, the `/bff/auth/callback`,
  `/bff/auth/platform/callback`, `/bff/auth/support/callback` routes and the redirect-based
  step-up; `InviteIn.idp_subject` and the web "subject" fields (`InviteUserScreen`,
  `ProvisionSchoolForm`, `OperatorsView`, `subjectHint` in `messages/{en,te}.json`).
- DB: `core.users.idp_issuer NOT NULL`, drop `UNIQUE (idp_subject)`; definer signature changes
  listed in D8; drop the backup table. Column names `idp_issuer`/`idp_subject` are kept (they now
  name our own identity service; renaming would touch three definer functions for no safety
  gain).
- Local: remove the `oidc` compose service, `infra/docker/oidc.json`, `SOS_*OIDC_JWKS_URI` from
  `docker-compose.yml` and `.env.example`.
- Terraform: delete `modules/cognito` (incl. `lambda.tf` and the pre-token Lambda),
  `modules/cognito_support_client` and their tests; remove the `cognito` module, outputs,
  variables (`cognito_domain_prefix`, `operator_user_pool_id`, `oidc_*`, `support_oidc_*`) and
  env wiring from `shared_platform`, `envs/staging`, `envs/prod`, `envs/dedicated-template`,
  `dedicated_host` (and the "Cognito" egress comment); replace `prod.tftest.hcl`'s "operator pool
  MFA ON" assertion with assertions on the auth secrets and SES permissions; dedicated hosts get
  `ses:SendEmail` for their identity.

**Docs to update when this ADR is accepted** (and only then): CLAUDE.md §3 (identity line), §4
(identity module description, `infra/docker`), §5 (dev OIDC stub line), §6 invariants 1 (schema
`auth` isolated by grants and `auth_service_only`), 2 (`require_bff()` for listed pre-auth routes)
and 7 (`audit.record_auth()`); 01-BRD (identity risk/assumption lines); 02-PRD US-101 (AC
wording for password + MFA, lockout), US-102 (invite by e-mail/login name); 03-TRD FR-IAM-001
(in-house, via BFF), FR-IAM-002 (signal), FR-IAM-003, FR-IAM-004 (replace access/refresh token
lifetimes with server-side session token rules and immediate revocation), FR-IAM-005 (numbers of
D7.6), FR-IAM-006, new **FR-IAM-007** (MFA factors and recovery codes), **FR-IAM-008** (account
recovery), **FR-IAM-009** (invitation acceptance with credential set-up and e-mail verification),
§3.13 status, interfaces/traceability; 04 §3 containers, §5 request lifecycle, §7 new sign-in flow,
§10 caching (no JWKS), §16; 05 §3.1 roles (`sos_auth`), §3.3 allowlist (`auth_tables`), §3.4
signatures, §4 users/memberships columns, new `auth` schema section, §13 retention; 07 §3 (TB5
no IdP; new boundary API → `auth`), §4 threats (T1, T2 rewritten; new ones from "Threats" below),
§5 rewritten, §6.4 support sign-in, §9 secrets, §16 SEC-004/005/006/027 wording and new
**SEC-031** (credential storage: Argon2id + sealed pepper + offline breach list),
**SEC-032** (auth schema privilege separation, catalog test), **SEC-033** (auth-focused external
pen test before pilot, Q15); 08 sub-processor register (drop Cognito), §6 (auth logs), §7, §11;
09 §1 topology, §3 new error codes (`invalid_credentials`, `throttled`, `mfa_challenge_failed`,
`enrolment_required`, `session_revoked`, `password_breached`, `token_invalid`), §4 identity
endpoints; 10 §4 services, §5 Terraform list, §5.2 SES (auth e-mails, dedicated hosts), §11
settings, §15 dedicated; 11 alerts (throttling spikes, `password_disabled`, auth chain
verification failure, SES bounce rate) and runbooks (operator recovery, SchoolOS-assisted owner
recovery, key re-seal); 12 §4 suites and §7 acceptance examples; 13 (CODEOWNERS: `app/identity/`
and `apps/web/src/server/{auth,session}` need a second reviewer); 14 roadmap; 15 glossary (OIDC,
Cognito, passkey, TOTP, AAL); 16 §4 identity row, §5.4 step 3, §5.16, §13.2 step 5, §19 Q4;
ADR-0012 and ADR-0018 status lines (Superseded by ADR-0030); ADR-0004, ADR-0013, ADR-0017,
ADR-0019, ADR-0023 status lines (Amended by ADR-0030); `apps/api/app/identity/README.md`;
`deploy/dedicated/README.md`, `compose.yaml`, `Caddyfile` (the `code`/`state` query redaction of
the OIDC callback can go in Release C; tokens of the new flows are only ever in fragments or
bodies, which never reach the logs).

**Tests to add or change** (names reference FR/SEC IDs):

| Area | Tests |
|---|---|
| Security suites (`tests/security/`) | Route enumeration: `require_bff()` only on `public_routes.yaml` routes, every other route unchanged (SEC-003). Privilege catalog: `sos_auth` has no privileges outside `auth`; no other runtime role has any on `auth`; `auth` tables RLS ENABLE+FORCE with `auth_service_only`; `auth.events` append-only incl. TRUNCATE (SEC-026, SEC-032). Definer allowlist: unchanged list, new signatures after contract (12 §4.9). No-PII-in-URLs: no `/auth/*` query parameter carries tokens, e-mails or logins. Log redaction: every new field (SEC-008). Authz matrix and BOLA: unchanged assertions; only the principal fixtures move from signed JWTs to seeded sessions. Tenant isolation: unchanged |
| Session/principal (`tests/identity/`) | Purpose × route-family matrix replacing the token-confusion matrix: staff session on `/platform/*` → 401, operator session on tenant routes → 401, support session only on `platform_support` memberships with an active grant, forged/unknown/revoked/expired/idle token → 401 (T2); `ath` mismatch → 401; rotation on sign-in, enrolment, step-up, password change (ASVS 7.2.4); idle and absolute limits per purpose incl. the school's 5–30 min setting (FR-IAM-003); school-scoped force sign-out via `sessions_valid_after`; account disable and operator deactivation revoke immediately (FR-IAM-006, SEC-006) |
| Credentials | Argon2id parameters and floor guard, rehash on outdated parameters, pepper sealing with AAD (hash moved to another account fails), re-seal job; NFKC incl. Telugu; min/max length; context words and breach filter hits/misses; must-change on breached password at sign-in (SEC-031) |
| Factors | TOTP RFC 6238 Appendix B vectors, ± 1 step, replay of a used step refused; WebAuthn (py_webauthn fixtures): UV required, wrong origin/RP ID, sign-count regression, `attestation: none`; recovery codes single use, index lookup, regeneration revokes the old set; last factor of a privileged member cannot be removed (FR-IAM-002, FR-IAM-007) |
| Flows (API) | Sign-in success/failure with identical responses and timing within tolerance for unknown accounts (ASVS 6.3.8); throttling from the 5th failure with audit (US-101 AC4, FR-IAM-005), 100-failure disable, per-IP window, forgot-password limits; `enrolment_required` for privileged invitees and all operators; `403 mfa_required` still enforced per request; step-up `428` → `POST /auth/step-up` → retry succeeds (SEC-005); invitation link (fragment token), expiry, reuse, resend invalidates; setup code for no-e-mail staff; password reset does not bypass MFA and revokes sessions (ASVS 6.4.3); admin reset refused for shared profiles (`409 profile_shared`); operator MFA reset needs two different operators (SEC-029); break-glass hand-off: S256 mismatch, reuse, > 60 s, other operator, non-approved request, step-up older than 5 min (US-103, SEC-021) |
| Audit | Every auth state change has its auth-chain event in the same transaction (rollback test); school-chain copies delivered exactly once and in order; auth chain verification detects gaps and edits (SEC-007) |
| Migrations (`tests/migrations/`) | Upgrade/downgrade round trips of each new revision on a populated synthetic DB; data migration re-keys users and operators and is idempotent; contract step leaves no OIDC-era rows |
| Web (vitest) | BFF handlers: sign-in, MFA challenge, passkey ceremonies, step-up, logout, sessions, invitation, reset; CSRF incl. login CSRF; the session store without refresh; cookies unchanged; `X-Forwarded-For` hop handling for `cip` |
| E2E (Playwright) | Password + TOTP sign-in (seeded secret), passkey sign-in and enrolment with Chrome's virtual authenticator (CDP `WebAuthn.addVirtualAuthenticator`), invitation acceptance from the dev mail drop, forgot password, step-up dialog on an export, throttling message, school picker, operator sign-in, break-glass hand-off; axe checks, en and te, 1366×768 and keyboard-only |
| Performance | Argon2id time budget on the API task size; concurrency limit returns 503 rather than exhausting memory; sign-in p95 |
| Terraform | `terraform test` for the removed Cognito modules replaced by assertions on the auth secrets and the dedicated hosts' SES permission |

## Threats (additions and changes to 07 §4)

| Threat | Controls in this design | Residual |
|---|---|---|
| Credential stuffing / password spraying against our own verifier (replaces part of T1) | 15-character minimum, offline breach list, per-account throttling from the 5th failure and 100-failure disable, per-IP and per-login-hash windows, WAF/Caddy path limits, uniform responses, MFA for privileged roles and passkeys encouraged | Medium for non-privileged staff without MFA (as today's T1) |
| Theft of the `auth` tables (backup, SQL injection elsewhere) | Separate role and schema (no other role can read them), Argon2id at 64 MiB, pepper-sealed hashes (key outside the DB), TOTP secrets sealed, session tokens and codes stored as hashes, backups KMS-encrypted | Low |
| Recovery abuse / social engineering of a school admin | No self-service MFA bypass; admin reset needs `user.credentials.reset` + step-up + in-person check, never own account, shared-profile guard, notices to the person and all owners, audited in both chains; SchoolOS-assisted path needs two operators and a call-back | Low–medium |
| Session token theft from the BFF store | Token sealed in Valkey, 256-bit, hashed in the DB, rotation, idle 15 min, `ath` binding, immediate server-side revocation | Low |
| Phishing of operators (T22) | Passkeys (phishing-resistant, RP ID = admin host) recommended/required (Q7), TOTP fallback, step-up for ᴿ, 2P rules | Low–medium (TOTP is phishable) |
| Bugs in our own auth code (owning auth increases attack surface) | Small, well-tested libraries for primitives only (argon2-cffi, py_webauthn), no home-made crypto, ASVS L2 checklist in review, CODEOWNERS second reviewer, fuzz/property tests, ZAP baseline, **external auth-focused pen test before the pilot** (Q15) | Medium until the pen test is passed |
| E-mail account compromise used for password reset | Reset never removes MFA; notice e-mails; sessions revoked; privileged roles always have MFA | Medium for non-MFA staff (their e-mail is effectively a recovery factor) |
| Hand-off code interception (break-glass) | Fragment only (not logged), 60 s, single use, PKCE-style binding to the requesting browser, step-up ≤ 5 min, grant/school bound | Low |

## Consequences

- Good: no sign-in third party, as the owner decided; one source of truth for sessions with
  **immediate revocation** (today a Cognito access token lived up to 10 minutes after
  revocation); exact MFA signals (`aal`, `mfa_at`) instead of ADR-0018's inference; invites work
  by e-mail or setup code without an admin creating IdP accounts by hand; FR-IAM-005 and
  breached-password screening become ours and testable; no Cognito MAU cost, pre-token Lambda or
  per-host app clients; dedicated hosts become self-contained for sign-in.
- Good: the `Principal` contract, `require()`, `require_platform()`, `require_recent_auth()`,
  the resolver, `core.resolve_login` and break-glass resolution are unchanged; BOLA and tenant
  isolation suites are unaffected.
- Bad / costs: we now own the most attacked part of the product (password storage, MFA,
  recovery, lockout, sessions): more code to secure and maintain for a small team (the reason
  ADR-0012 chose a managed IdP). Mitigated by the controls above, but the residual risk is real
  until an external pen test has been passed.
- Bad / costs: a third database role and a second non-tenant schema; invariants 1, 2 and 7 need
  rewording; an auth chain and an outbox to run; ~80 MB blocklist file in the API image;
  Argon2id adds CPU and memory per sign-in (bounded by the semaphore).
- Bad / costs: support burden of password resets and lost MFA falls on schools and SchoolOS
  (no IdP-hosted self-service); recovery runbooks and training (en/te) are needed.
- Follow-up: the build is a sizeable work package (API identity service, web sign-in pages and
  BFF handlers, migrations, Terraform removal, docs) and should land **before the first staging
  deployment**, so Cognito is never applied.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep Amazon Cognito (ADR-0012/0018) | Contrary to the owner's decision of 2026-09-29. It would also still need our own breach screening (Essentials) and lockout auditing, a Lambda for the MFA signal and one app client per dedicated host |
| Self-hosted open-source IdP: Keycloak (Apache-2.0), Ory Kratos (Apache-2.0, enterprise features under a separate licence), authentik (MIT core + enterprise licence), Zitadel (**AGPL-3.0 since v3, March 2025: not allowed in core**, CLAUDE.md §11) | A separate stateful service to patch, back up, secure and run on every dedicated host; its own admin console and database; and arguably still "integrating a third-party thing for sign-up and login" in the owner's words (Q3). Keycloak/Kratos remain the fallback if Q3 says a self-hosted OSS component is acceptable and the pen test finds our implementation weak |
| Build our own OAuth 2.0 / OIDC authorization server (JWT access tokens + refresh tokens, JWKS), keeping the BFF and API token code | Most of the attack surface of an IdP (authorization endpoint, redirect validation, key rotation, refresh families) for a first-party app that has one client, the BFF; self-contained tokens cannot be revoked immediately. Opaque server-side sessions are simpler and stronger here (ASVS 7.2.1 "trusted backend") |
| Authenticate in the Next.js BFF (e.g. Auth.js credentials provider) | Puts credential storage in the Node tier, outside the API's audit transaction, DB roles and Python test suites; Auth.js itself deliberately limits its credentials provider to discourage password handling there |
| Credentials in `core` with RLS and definer functions | Login happens before any tenant context, so every credential read/write would need a new `SECURITY DEFINER` function and `sos_app` would sit next to password hashes; a separate role and schema isolate secrets from tenant code without widening the definer allowlist |
| SMS OTP / e-mail OTP as factors | D4.4 |
| Hardware security keys mandatory for all privileged staff | Cost and logistics for schools; kept optional (passkeys preferred), revisit after the pilot |

## Open questions for the product owner

1. **Sign-up scope:** we read "sign up" as invite acceptance only: staff are invited by their
   school, a school's first owner by SchoolOS at provisioning, and there is no public sign-up
   page. Should schools be able to **self-register** (the M7 "self-serve onboarding wizard")?
   If yes, that needs its own ADR (fraud checks, trial limits, billing); we recommend not before M7.
2. **E-mail delivery:** is Amazon SES (AWS infrastructure we already use and disclose, only
   carrying invitation and reset e-mails) acceptable, i.e. not a "third party for sign-up and
   login"? If not, we would need to run our own mail server, with poor deliverability.
3. **What counts as "third party"?** Are these acceptable: small open-source libraries for
   cryptographic primitives (argon2-cffi, py_webauthn, segno), the downloadable HIBP Pwned
   Passwords data file (used offline), and the authenticator apps and passkey providers that
   staff choose themselves (Google/Microsoft Authenticator, Google Password Manager, Windows
   Hello, security keys)? And would a **self-hosted open-source identity server** (Keycloak, Ory
   Kratos) count as a third party, or is it an acceptable alternative to writing our own?
4. **MFA methods:** TOTP authenticator apps and passkeys only; no SMS and no e-mail codes. Agreed?
5. **Who must use MFA:** keep it mandatory only for owner, principal, office admin and all
   operators (others optional but encouraged), or require it for all staff from the start?
6. **Password minimum length:** 15 characters (NIST 2025 guidance for passwords that may be the
   only factor; our recommendation), 12 (the current docs), or 8 for accounts with MFA?
7. **Operators and passkeys:** require passkeys (phone or security key) for `platform_owner` and
   `platform_engineer` from the pilot, with TOTP only as a backup? For all operators?
8. **Shared office PCs:** may staff save passkeys in Windows Hello on shared PCs, or should
   schools be able to require phone/security-key passkeys only (setting default "allowed")?
9. **Recovery policy for staff:** may both owner and principal issue a new setup code and reset
   MFA for another member (new permission `user.credentials.reset`, step-up, in-person check),
   or only the owner? For people who work in several schools, is SchoolOS-assisted recovery (two
   operators) the right path, and which school's request counts?
10. **Sole owner locked out** (lost MFA and recovery codes): is a SchoolOS-assisted reset
    (written request on letterhead, call-back to the registered contact, two operators)
    acceptable?
11. **Session lengths:** keep idle 15 min (schools may set 5–30) and absolute 12 h for staff,
    8 h for operators and support; cap of 10 concurrent sessions per staff account?
12. **Staff without e-mail:** acceptable that they get an admin-chosen login name and a one-time
    setup code, and can recover only through a school admin?
13. **Two e-mail fields:** the sign-in e-mail (changed only by the person, with re-authentication)
    stays separate from the profile e-mail that school admins can edit. Agreed?
14. **Dedicated hosts:** each host has its own separate accounts (someone working in a shared
    school and a dedicated school has two logins), and break-glass sign-in on dedicated hosts
    stays unavailable until M1 decision 6. Agreed?
15. **Pen test timing:** move an external, authentication-focused penetration test **before the
    pilot** (new SEC-033), instead of only before paid go-live (SEC-025)?
16. **Breached-password list:** ship an offline list of the ~10 million most common breached
    passwords (~80 MB in the image, CC BY 4.0 attribution to Have I Been Pwned), refreshed
    quarterly?
17. **Timing:** build this before the first staging deployment and never apply the Cognito
    Terraform (our recommendation), or bring staging up on Cognito first and migrate later?

## Related requirements

FR-IAM-001..006 (and proposed FR-IAM-007..009), FR-IAM-010..014, FR-TEN-003, FR-TEN-012,
FR-OPS-004, FR-PLT-001, FR-PLT-028, US-101, US-102, US-103, US-1301; SEC-003..008, SEC-021,
SEC-022, SEC-025..027, SEC-029 (and proposed SEC-031..033); NFR-SEC-001, NFR-SEC-006; PRV-007,
PRV-017..019; T1, T2, T6, T16, T17, T22 (07 §4); docs 03 §3.1, 05 §3–4 and §13, 07 §3–5 and §6.4,
08 §1, §6–7, 09 §1–4, 10 §5 and §11, 12 §4, 16 §4, §5.4, §5.16, §19.

Sources (checked 2026-09-29): OWASP ASVS 5.0.0 V6 and V7
(github.com/OWASP/ASVS, `5.0/en/0x15-V6-Authentication.md`, `0x16-V7-Session-Management.md`);
NIST SP 800-63B-4 (csrc.nist.gov/pubs/sp/800/63/b/4/final); OWASP Password Storage and Session
Management cheat sheets; RFC 6238; RFC 9106; W3C WebAuthn and MDN Baseline data for the Web
Authentication API, conditional mediation and `getClientCapabilities()`; Have I Been Pwned
Pwned Passwords licence (CC BY 4.0); Zitadel v3 licence announcement (AGPL-3.0, 2025-03-31).
