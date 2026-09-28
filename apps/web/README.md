# @schoolos/web — school console, platform admin panel and BFF

Next.js 16 (App Router) app. The browser never holds a token: it talks only to the
same-origin BFF under `/bff/*`, which keeps the OIDC tokens server-side in Valkey
(docs/07 §5, docs/09 §1, ADR-0012, ADR-0018).

## Layout

| Path                               | What                                                                                                                                                                                                                                                                                                       |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/server/config.ts`             | Env parsing; refuses short secrets, plain http outside localhost, dev placeholders on https                                                                                                                                                                                                                |
| `src/server/session/`              | Session store (Valkey, `sos:web:sess:*`), AES-256-GCM token sealing, cookies, RSC helpers (`getSession`, `requireStaff`, `requireOperator`)                                                                                                                                                                |
| `src/server/auth/`                 | OIDC (openid-client v6: code + PKCE S256, refresh, end session), `next` validation, refresh rotation + reuse detection, route handlers                                                                                                                                                                     |
| `src/server/bff/`                  | `/bff/api/v1/*` proxy, service token (jose HS256), CSRF checks, upstream call                                                                                                                                                                                                                              |
| `src/app/bff/**/route.ts`          | Thin route files that call the handlers above                                                                                                                                                                                                                                                              |
| `src/lib/bff/`                     | Browser side: session info, typed BFF client (CSRF header, 401 → sign-in, 428 → step-up), TanStack Query hooks                                                                                                                                                                                             |
| `src/components/session/`          | "Lock now" button and the idle-timeout `<dialog>`                                                                                                                                                                                                                                                          |
| `src/instrumentation.ts`           | Validates the BFF config once at server start                                                                                                                                                                                                                                                              |
| `src/features/platform/`           | Platform admin panel screens (C14): TanStack Query + `ActionDialog` forms against `/api/v1/platform/*`, incl. a school's provisioning state and "Resume provisioning"                                                                                                                                      |
| `src/features/school/`             | School console screens (audit log and chain check, plan & billing, support, home, announcements banner)                                                                                                                                                                                                    |
| `src/features/academic-structure/` | Academic structure (US-202, FR-TEN-010): years, classes, sections; add/edit/archive (If-Match), "Show archived", class teacher from `/staff` (`/settings/structure`)                                                                                                                                       |
| `src/features/settings/`           | School profile and settings (FR-TEN-012): languages, date format, idle timeout, AI switch and budget for `tenant.settings.manage`; step-up per call (`/settings/school`)                                                                                                                                   |
| `src/features/users/`              | Users and roles (US-102): staff list, invite, profile edits, status (not on own account), roles by API `grantable`, scopes (`/settings/users/*`; step-up)                                                                                                                                                  |
| `src/features/documents/`          | Documents (US-701, FR-DOC-001..008): list/filters in the URL, presigned upload, versions, who can see it, edit details, archive, uploader (`/documents/*`)                                                                                                                                                 |
| `src/features/auth/`               | School picker (`/choose-school`), "no access yet" re-check, signed-out view                                                                                                                                                                                                                                |
| `src/lib/forms.ts`                 | `useApiForm`: native `<form>` + zod, server 422 `errors[].field` → inputs, Idempotency-Key per intent                                                                                                                                                                                                      |
| `src/lib/api-errors.ts`            | Problem `code` → plain-language message keys (`errors.api.*`, en/te), incl. `same_operator`, 428 step-up                                                                                                                                                                                                   |
| `src/lib/date-format.ts`           | Display dates in the school's `date_format` from GET /me (FR-TEN-012); `formatDate`/`formatDateTime` in `lib/format.ts` use it (browser only; the server renders DD/MM/YYYY). Typed dates too: `useDateInput` (placeholder, hint values, starting value) and `typedDateToIso` (reads D/M/YYYY or YYYY-M-D) |
| `src/lib/school-class.ts`          | `classLabel`: a class's name in the UI language (Telugu name in `te`, else English)                                                                                                                                                                                                                        |
| `src/features/promotions/`         | Year-end promotion (FR-TEN-011, US-202 AC2): preview, commit (Idempotency-Key, fingerprint), undo in 24 h, history with who did it (`/settings/structure/years/[yearId]/promotions`); menu entry "Promotions" under the structure lists the years (`/settings/structure/promotions`)                       |

## Sessions and security (summary)

- Cookies: `__Host-sos_session` (staff) and `__Host-sos_platform_session` (operators),
  `HttpOnly; Secure; SameSite=Lax; Path=/`, no `Max-Age` (end with the browser). Only when
  `APP_BASE_URL` is `http://localhost…` (or 127.0.0.1 / [::1]) are they named without the
  `__Host-` prefix and sent without `Secure`; any other http URL is a startup error.
- Cookie value: 256-bit random id. Valkey keys use its SHA-256. Tokens are sealed with
  AES-256-GCM (HKDF-SHA256 key from `SESSION_SECRET`, AAD binds each ciphertext to its session).
- Idle timeout 15 min (sliding on each authenticated request; the session-info GET does not
  slide it), absolute 12 h for staff, 8 h for operators. Warning dialog 1 min before idle expiry.
  Staff sessions take the active school's `idle_timeout_minutes` (5–30, clamped; FR-IAM-003)
  from the API's answer to `POST /me/active-tenant` and from `GET /me` in the school layout;
  a new school starts at the 15-minute default until its value is known, step-up keeps it,
  and it never runs past the absolute limit. A staff `GET /bff/api/v1/me` answer about the
  active school applies it too, so after the settings screen saves a new value (and re-reads
  /me and the session info) it applies without a reload. Operators always have 15 min.
- CSRF: synchronizer token (from `GET /bff/auth/session`, memory only) in `X-CSRF-Token` on
  every POST/PUT/PATCH/DELETE, compared in constant time; plus SameSite=Lax and Origin /
  `Sec-Fetch-Site` checks.
- Refresh: when the access token has < 60 s left, or the API says `token_expired`, under a
  Valkey `SET NX PX` lock (one IdP call across all web tasks). Spent refresh-token hashes are
  kept per family; a token presented twice revokes every session in the family (FR-IAM-004).
- API calls carry `Authorization: Bearer <access token>` and a fresh `X-Service-Token`
  (HS256, `iss=sos-web`, `aud=sos-api`, 60 s, random `jti`; key `SOS_SERVICE_TOKEN_KEY`),
  plus `X-Request-Id`, `X-Active-Tenant` (staff) and `Accept-Language`. Only allowlisted
  headers cross in either direction; `Set-Cookie` from the API is never forwarded.
- Operators may call only `/bff/api/v1/platform/*`; staff never. `SOS_DEPLOYMENT_MODE=dedicated`
  turns the platform panel and its BFF routes off (404).
- Step-up: the API's `428 step_up_required` becomes a problem with `step_up_url`
  (`/bff/auth/step-up?next=…`), which re-runs the sign-in with `prompt=login&max_age=0`.
  The session keeps its family, CSRF token and active school but gets a new id.
- Logout (`POST /bff/auth/logout`, CSRF) revokes the session, revokes the refresh token at
  the IdP when it has a revocation endpoint, and returns the IdP end-session URL (with
  `client_id` and `post_logout_redirect_uri`, never the ID token) or `/signed-out`.

## Staff sign-in: invitations and school choice (ADR-0019, FR-IAM-013)

After the OIDC callback (staff, not step-up) the BFF calls, in order:

1. `POST /api/v1/me/accept-invitations` — a `403 mfa_required` ends the sign-in without a
   session and shows the MFA message (`/signed-out?error=mfa_required`).
2. `GET /api/v1/me/schools` —
   - exactly one **active** school: it becomes the active school (`X-Active-Tenant`) and the
     user lands on `next`;
   - several schools (or a single suspended one): `/[locale]/choose-school?next=…`, which
     lists them with their status (suspended/offboarding disabled, with the reason) and
     POSTs `/bff/auth/active-tenant`;
   - none: `/[locale]/no-access` ("ask the office to send the invitation again", with a
     "Check again" button that re-runs accept-invitations).
3. `POST /api/v1/me/login-event` once the school is known (right away for one school, or on
   the first choice in the picker), so the API can audit it in that school's log.

The school layout redirects to the picker when the session has no active school (or `/me`
answers `active_tenant_required`), hides menu items the user lacks (from `/me` effective
permissions; UX only, the API checks every call) and shows "Switch school" when `/me`
lists more than one school. Platform menus are filtered the same way from `/platform/me`.

## BFF routes

| Route                                                                  | Notes                                                        |
| ---------------------------------------------------------------------- | ------------------------------------------------------------ |
| `GET /bff/auth/login?next=` · `GET /bff/auth/platform/login?next=`     | Staff / operator sign-in                                     |
| `GET /bff/auth/callback` · `GET /bff/auth/platform/callback`           | Redirect URIs to register at the IdP                         |
| `GET /bff/auth/step-up?next=` · `GET /bff/auth/platform/step-up?next=` | Re-authenticate now                                          |
| `POST /bff/auth/logout?kind=`                                          | CSRF; returns `{redirect_to}`                                |
| `GET /bff/auth/session?kind=` · `POST` (CSRF)                          | Non-secret session facts / stay signed in                    |
| `GET /bff/auth/sessions?kind=` · `DELETE ?id=` (CSRF)                  | List / revoke your own sessions                              |
| `POST /bff/auth/active-tenant` (CSRF) `{tenant_id}`                    | Switch school (checked with `POST /api/v1/me/active-tenant`) |
| `/bff/api/v1/*`                                                        | Proxy to `API_INTERNAL_URL/api/v1/*`                         |

Register these at the IdP: redirect URIs `<APP_BASE_URL>/bff/auth/callback` (staff client)
and `<APP_BASE_URL>/bff/auth/platform/callback` (operator client); post-logout URIs
`<APP_BASE_URL>/signed-out` and `<APP_BASE_URL>/signed-out?kind=operator`.

## Environment

`APP_BASE_URL`, `SESSION_SECRET` (≥ 32 bytes), `SOS_SERVICE_TOKEN_KEY` (same value as the
API), `REDIS_URL`, `API_INTERNAL_URL`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
`PLATFORM_OIDC_ISSUER`, `PLATFORM_OIDC_CLIENT_ID`, `PLATFORM_OIDC_CLIENT_SECRET`, optional
`SOS_DEPLOYMENT_MODE`, `FILES_ORIGIN`, and for break-glass support sign-in (ADR-0023)
`SUPPORT_OIDC_CLIENT_ID`, `SUPPORT_OIDC_CLIENT_SECRET` (the support app client of the operator
pool; unset = off) and `SUPPORT_OIDC_ISSUER` (default `PLATFORM_OIDC_ISSUER`). See the root
`.env.example`.

Break-glass support sign-in: the admin panel links to
`/bff/auth/support/login?request=<request id>&tenant=<school id>`; the BFF signs the operator in
again (MFA, fresh sign-in) with the support client, keeps the tokens in
`__Host-sos_support_session`, calls `POST /api/v1/breakglass/support-session` and opens the
school console with a read-only banner. When there is no staff session, the school console
(`requireStaff`, the `/bff/api` proxy, `/bff/auth/session?kind=staff`) runs as that support
session.

## Tests

`npm test -w @schoolos/web` (vitest). The OIDC flow runs real openid-client against an
in-process fake IdP (`src/test/fake-idp.ts`: RS256 ID tokens, PKCE S256 enforced, rotating
refresh tokens); Valkey is replaced by `MemoryKeyValue`. A real-Valkey test runs when
`SOS_WEB_TEST_REDIS_URL` is set:

```bash
docker run --rm -d --name sos-valkey-test -p 127.0.0.1:6390:6379 valkey/valkey:8.1-alpine
SOS_WEB_TEST_REDIS_URL=redis://127.0.0.1:6390/15 npm test -w @schoolos/web
```

`npm run e2e -w @schoolos/web` (Playwright, after `npm run build`) checks the redirect to
sign-in, the signed-out page (CSP, Telugu), the health check, and axe-core (WCAG 2.2 AA) on
the signed-out page, without an IdP.

With `E2E_STAND_IN=1` (and Valkey at `REDIS_URL`) it also signs in through a scripted
stand-in IdP and canned API (`e2e/support/stand-in.ts`; synthetic data only) and runs axe
plus keyboard-only paths on school pages (billing, support, home, the picker, settings,
structure and promotions, users, documents, the audit check) and platform pages (dashboard, schools, a school
whose provisioning stopped, invoices, plans, the provision wizard, dialogs). It also fails on
horizontal overflow at 1366×768 and on any Tab stop without a visible focus indicator:

```bash
docker run --rm -d --name sos-e2e-valkey -p 127.0.0.1:6391:6379 valkey/valkey:8.1-alpine
npm run build -w @schoolos/web
E2E_STAND_IN=1 E2E_PORT=3100 REDIS_URL=redis://localhost:6391/1 npm run e2e -w @schoolos/web
```

If the Playwright browser download is blocked, run the same command inside
`mcr.microsoft.com/playwright:v1.63.0-noble` with `--network host`.

## Manual verification with the dev OIDC stub

The compose `oidc` service (profile `dev`, `ghcr.io/navikt/mock-oauth2-server`, config
`infra/docker/oidc.json`) issues 10-minute tokens for issuers `schoolos` and `platform`.
Both issuers add `sos:mfa: "true"` and `amr: ["pwd", "mfa"]` to every token, so staff and
operators alike only **type a subject** at the stub's login page (no claims JSON), including
owner, principal and office_admin, whose roles require MFA.

**Production MFA is unchanged.** Only the local fake identity provider says "MFA done"; the
API and the BFF check MFA and step-up exactly as in staging/prod, where the identity provider
is Cognito with real MFA (ADR-0012, ADR-0018). The stub runs only in the compose `dev`
profile, bound to `127.0.0.1`, and the API refuses its issuers outside `SOS_ENV=local`
(`apps/api/tests/deploy/test_dev_oidc_stub.py` pins all of this, including that no Terraform,
dedicated-host, Dockerfile or deploy-workflow file mentions the stub). Because the stub's
claims win over anything typed in its claims box, a non-MFA staff session cannot be simulated
locally; the MFA-denial paths are covered by the API and BFF test suites.

**Dev sign-in page:** under `next dev` (`make dev-host`) with a local issuer, open
<http://localhost:3000/en/dev/sign-in> (the signed-out page links to it). It lists the
synthetic subjects per school (`synth-a`, `synth-b`) and role, with "Copy" and "Sign in"
(the normal `/bff/auth/login` flow). It is a 404 unless `NODE_ENV=development` **and**
`OIDC_ISSUER` is on a loopback or `*.localhost` host, so `next start`/production builds
(including the compose `web` container) never serve it. `make dev-host` also prints a short
"Sign in as" list when it starts.

**Step-up locally:** the stub does not put `auth_time` in its tokens, and the API only
accepts a step-up when `auth_time` is at most 5 minutes old. When an action asks you to
confirm it's you, select "Copy step-up claims" on the dev sign-in page (it copies
`{"auth_time": <now>}`), paste it in the stub's claims box and sign in again with the same
subject. Without it the action keeps asking for step-up: nothing is bypassed.

**One issuer name everywhere:** `http://oidc.localhost:8080/<issuer>`. Browsers resolve any
`*.localhost` name to loopback (RFC 6761), so they reach the stub on `127.0.0.1:8080`; inside
compose the `oidc` service has the network alias `oidc.localhost`, so the `web` and `api`
containers reach the same name. The stub derives `iss` from the Host header, so tokens carry
`http://oidc.localhost:8080/…` for everyone. (A network alias is used rather than
`extra_hosts: host-gateway`, which cannot reach a port published on `127.0.0.1` on Linux.)
The web config accepts an http issuer only on loopback or `*.localhost`, and only when the
app itself runs on `http://localhost…`; the API's `is_dev_issuer` treats `*.localhost` as a
development issuer (allowed in `local`, refused in staging/prod).

1. `cp .env.example .env` (dev-only values; issuers already point at `oidc.localhost`).
2. `docker compose --profile dev up -d --build` (db, valkey, s3, migrate, api, web, oidc…),
   then `make seed-synthetic` for synthetic schools and staff (subjects like
   `synthetic|synth-a|principal|1`) and
   `uv run python -m app.platform.bootstrap_owner --subject <sub> --email … --display-name …`
   for the first operator.
3. Open <http://localhost:3000/en/settings/structure> and sign in at the stub by typing a
   synthetic subject (for example `synthetic|synth-a|owner|1`; leave the claims box empty).
   One school → the page; several → the picker.
4. Check in the browser dev tools: cookie `sos_session` is HttpOnly; no `Authorization`
   header or token appears in any `/bff/*` response, `localStorage` or `sessionStorage`;
   POSTs carry `X-CSRF-Token` (creating POSTs also `Idempotency-Key`).
5. Operators: <http://localhost:3000/en/platform> signs in with the `platform` issuer.

Running the web app (or API) on the host instead of in compose: set all four issuer
variables to `http://localhost:8080/…` (host processes may not resolve `*.localhost`), and
`API_INTERNAL_URL`/`REDIS_URL` to localhost as in `.env.example`.

### Verified end to end (2026-09-26)

`next start` + Valkey 8.1 + the real API (`uvicorn app.main:app`) on Postgres 16 + pgvector
(migrations to `0007_accept_invitations`, `seed-synthetic --tenants 2`, `bootstrap_owner`),
with the scripted stand-in IdP (the stub image cannot be pulled in the build sandbox):
staff sign-in → accept-invitations → schools → login event; menu filtered by `/me`;
Plan & billing (no subscription → friendly note); structure; support ticket opened (T-1),
thread + reply; operator dashboard KPIs; plan created and published (step-up fresh);
school provisioned (201, owner invite created), subscription tab, "Go live"; offboarding
requested, then approval by the same operator refused with `409 same_operator` and
explained; operator reply on the school's ticket; platform audit chain verified (intact).
This run found one bug (a client-module function called from a server page), now fixed and
guarded by `src/app/client-boundary.test.ts`.
