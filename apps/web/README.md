# @schoolos/web — school console, platform admin panel and BFF

Next.js 16 (App Router) app. The browser never holds a token: it talks only to the
same-origin BFF under `/bff/*`, which keeps the OIDC tokens server-side in Valkey
(docs/07 §5, docs/09 §1, ADR-0012, ADR-0018).

## Layout

| Path                      | What                                                                                                                                        |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/server/config.ts`    | Env parsing; refuses short secrets, plain http outside localhost, dev placeholders on https                                                 |
| `src/server/session/`     | Session store (Valkey, `sos:web:sess:*`), AES-256-GCM token sealing, cookies, RSC helpers (`getSession`, `requireStaff`, `requireOperator`) |
| `src/server/auth/`        | OIDC (openid-client v6: code + PKCE S256, refresh, end session), `next` validation, refresh rotation + reuse detection, route handlers      |
| `src/server/bff/`         | `/bff/api/v1/*` proxy, service token (jose HS256), CSRF checks, upstream call                                                               |
| `src/app/bff/**/route.ts` | Thin route files that call the handlers above                                                                                               |
| `src/lib/bff/`            | Browser side: session info, typed BFF client (CSRF header, 401 → sign-in, 428 → step-up), TanStack Query hooks                              |
| `src/components/session/` | "Lock now" button and the idle-timeout `<dialog>`                                                                                           |
| `src/instrumentation.ts`  | Validates the BFF config once at server start                                                                                               |

## Sessions and security (summary)

- Cookies: `__Host-sos_session` (staff) and `__Host-sos_platform_session` (operators),
  `HttpOnly; Secure; SameSite=Lax; Path=/`, no `Max-Age` (end with the browser). Only when
  `APP_BASE_URL` is `http://localhost…` (or 127.0.0.1 / [::1]) are they named without the
  `__Host-` prefix and sent without `Secure`; any other http URL is a startup error.
- Cookie value: 256-bit random id. Valkey keys use its SHA-256. Tokens are sealed with
  AES-256-GCM (HKDF-SHA256 key from `SESSION_SECRET`, AAD binds each ciphertext to its session).
- Idle timeout 15 min (sliding on each authenticated request; the session-info GET does not
  slide it), absolute 12 h for staff, 8 h for operators. Warning dialog 1 min before idle expiry.
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
`SOS_DEPLOYMENT_MODE`, `FILES_ORIGIN`. See the root `.env.example`.

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
sign-in, the signed-out page (CSP, Telugu) and the health check without an IdP.

## Manual verification with the dev OIDC stub

The compose `oidc` service (profile `dev`, `ghcr.io/navikt/mock-oauth2-server`, config
`infra/docker/oidc.json`) issues 10-minute tokens for issuers `schoolos` and `platform`;
operators get `sos:mfa: "true"`. The issuer URL is `http://localhost:8080/...` as the
browser sees it, which the web **container** cannot reach, so run the web app on the host:

1. `cp .env.example .env` (dev-only values) and `make dev` with the `dev` profile
   (`docker compose --profile dev up -d db valkey s3 s3-init migrate api oidc`).
2. Create a synthetic school and a staff member whose `core.users.idp_subject` equals the
   subject you will type at the stub's login page (`make seed-synthetic` once Task 13 lands;
   until then insert one as in `apps/api/tests/api/world.py`). Owner, principal and
   office_admin need MFA: enter the claim `{"sos:mfa": "true"}` at the stub's login page if
   your stub version offers the claims box.
3. Run the web app on the host with the root `.env`:
   `set -a; . ./.env; set +a; npm run dev -w @schoolos/web`
   (`.env.example` points `API_INTERNAL_URL` and `REDIS_URL` at localhost).
4. Open <http://localhost:3000/en/settings/structure>: you are sent to the stub, sign in,
   and come back to the page. Check in the browser dev tools: cookie `sos_session` is
   HttpOnly; no `Authorization` header or token appears in any `/bff/*` response,
   `localStorage` or `sessionStorage`; POSTs carry `X-CSRF-Token`.
5. Structure, users and audit load from the API; "Lock now" signs out through the stub's
   end-session endpoint to `/en/signed-out`; after 14 idle minutes the warning dialog opens.
6. Operators: <http://localhost:3000/en/platform> signs in with the `platform` issuer.
   Platform API routes that are not built yet show "Not available yet".

This flow was also exercised end to end (2026-09-26) with `next start`, Valkey 8.1, the
real API on Postgres 16 + pgvector and a scripted stand-in IdP (the stub image cannot be
pulled in the build sandbox): sign-in, `GET /me`, structure lists, users, a CSRF-protected
`POST /classes` (201), `auth.login.succeeded` in the audit log, the staff → `/platform` 403,
token-free page HTML, logout and the 401 afterwards all behaved as described above.
