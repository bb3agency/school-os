# Security audit 2026-11-08, round 2: BFF and web server layer (web2-bff-server)

Worktree branch from `b64d0dbf`. Area: the server side of `apps/web`: every BFF route handler
(`app/bff/**/route.ts`, `app/(public)/**/route.ts`, `app/fonts`, `app/healthz`),
`src/server/**` (auth handlers, OIDC client, refresh, session store, cookies, BFF proxy and
upstream client, public client), the Next.js proxy (`src/proxy.ts`), `lib/security-headers.ts`,
`lib/public-surface.ts`, `next.config.ts`, the browser side of the BFF (`lib/bff/fetch.ts`,
`session-client.ts`, `step-up.ts`) and the server components that call the API
(`(school)/layout.tsx`, `platform/layout.tsx`, `choose-school`, `no-access`). Round 1
(`audit-2026-11-08-web-security.md`, WS-01..04) and the earlier reports were read first; their
findings and "checked and clean" items are not repeated. Synthetic data only.

**Status: complete. Six findings fixed (one medium, five low), each with tests that failed
first. XB-04 was first reported, then fixed on the lead's decision of 2026-11-08, which also
assigned XB-05 and XB-06 (first noted under "outside my area") to this audit.**

## Findings

| ID | Severity | Area | Evidence | Fix | Test | Status |
|---|---|---|---|---|---|---|
| XB-01 | Medium | Print pages served from the API (SEC-010; R2-1B fee receipts, ADR-0039 APAAR, FR-MSG-009, ADR-0048) | `apps/web/src/proxy.ts:39` `OWN_CSP_PATHS` listed only the correction memo, certificate print, the three registers and the readiness slips. Every other `text/html` page of the API that the browser opens through `/bff/api/v1/*` got the page-wide CSP from the proxy (`style-src 'self'`, no `'unsafe-inline'` in production). Next.js keeps a header the proxy set over the route handler's (`next/dist/server/send-response.js`: a header already present is not replaced), so the API's own policy with the hash of its `<style>` block never applied and the style block was blocked: fee receipts and refund vouchers (`/fees/{receipts,refunds}/{id}/print`), the fee day report (`/fees/reports/daily/print`), APAAR consent forms (student and section), purpose consent forms (`/consents/forms`), WhatsApp join slips (`/sections/{id}/join-slips`), privacy notice previews and request letters printed as unstyled text in production. `next dev` adds `'unsafe-inline'` to `style-src`, so it only showed outside development. | `e9e77509`: the nine paths are in `OWN_CSP_PATHS` (the BFF handler then keeps the API's policy only when `isStrictPolicy` passes, as for the memo). A second test reads `apps/api/openapi.json` and fails when any `text/html` GET of the API is left under the page-wide policy, so a new print page cannot drift again. | `src/proxy.test.ts` "XB-01 (SEC-010): every API print page…" and "XB-01 (SEC-010): no HTML page of the API…" | Fixed |
| XB-02 | Low | Session end on API 401 (FR-IAM-004, SEC-004) | `apps/web/src/server/bff/proxy.ts` (before the fix, lines 311-334): after one refresh and retry for `token_expired`, any further 401 (or a 401 with another code: a token the API no longer accepts, an API clock or key problem, a support grant that ended) was passed through and the BFF session kept. The browser client then went to `/bff/auth/login?next=<page>`; `handleLogin` saw the live session and sent it straight back to the page, whose next call got the same 401: an endless full-page reload loop on the office PC. | `3a3e02bd`: a final 401 from the API revokes the BFF session, clears its cookie and answers the BFF's own `unauthenticated` problem with `login_url` (as for `no_membership`), logged as `session_ended_api_unauthenticated` (kind and request id only). The next sign-in then goes to the IdP and gets fresh tokens. | `src/server/bff/proxy.test.ts` "XB-02 (FR-IAM-004): a 401 the refresh does not cure…", "XB-02 …any other 401…" | Fixed |
| XB-03 | Low (hardening) | Cross-site GETs through the API proxy (SEC-004) | `proxy.ts` checked `Origin`/`Sec-Fetch-Site` and the CSRF token for unsafe methods only. A top-level navigation from another site carries the `SameSite=Lax` session cookie, and several API GETs have effects: audited print views (the fee day report needs no id at all, the APAAR and consent forms), audited download links. Another site could make a signed-in clerk's browser create those audit events in their name. The response itself is not readable cross-site. | `53998ed8`: `/bff/api/v1/*` refuses `Sec-Fetch-Site: cross-site` and `same-site` for every method (403 `csrf_failed`, the API is never called). `same-origin` (the app), `none` (bookmark, typed address) and browsers that send no header pass. The app's print links and windows are same-origin, so nothing in the product changes. | `src/server/bff/proxy.test.ts` "XB-03 (SEC-004): a GET from another site…" (2), "…a GET with Sec-Fetch-Site same-origin / none passes" (2) | Fixed |
| XB-04 | Low | Token refresh lock (FR-IAM-004) | `apps/web/src/server/auth/refresh.ts:94` held the Valkey refresh lock for 15 s, but one refresh can take longer: when the OIDC discovery document is not cached (first request after a web task starts, or after a discovery failure) `refresh()` waits for discovery and then the token request, each with the 10 s client timeout (`oidc.ts:79`). If the lock expired first, a request on another web task took the lock, read the not-yet-replaced refresh token, found it already spent and revoked the whole family: the person was signed out on every device and a false `refresh_token_reuse_detected` error was logged. Reproduced with fake timers (19 s refresh, second task at 16 s: `refresh_reuse`). | `bf2a6be8`: the TTL derives from the IdP timeout (`refreshLockTtlMs`: 2 x `OIDC_TIMEOUT_SECONDS` + 10 s, at least 30 s; `oidc.ts` exports the timeout it uses). Just before the IdP call the holder checks it still owns the lock and re-arms it; a holder that lost it un-spends the refresh token and answers `RefreshBusyError` (503 with `Retry-After: 1`) instead of spending a token another task may be using. The ownership check and `PEXPIRE` are two commands (no compare-and-expire in `KeyValue`); the window between them is a Valkey round trip, against a TTL of 30 s. | `src/server/auth/refresh.test.ts` "XB-04 (FR-IAM-004): the lock outlives a slow refresh…", "XB-04 …a holder whose lock has expired does not spend the refresh token" | Fixed |
| XB-05 | Low | Choosing a school after the session ended (FR-IAM-004, FR-IAM-013) | `server/auth/handlers.ts` `handleActiveTenant` answered every non-2xx except 429 and 5xx as 403 `tenant_not_available`, also an API 401: a plain `token_expired` (clock skew), or a token the API no longer accepts. The picker then said "You can't open this school" for a school the person can open, and the session stayed. | `5bc2bb29`: one refresh and retry for `token_expired`, as the proxy does; any other or final 401 ends the BFF session (as XB-02), clears its cookie and answers `unauthenticated` with `login_url`. `features/auth/ChooseSchoolView.tsx` sends that answer to sign-in (back to the picker afterwards). | `src/server/auth/handlers.test.ts` "XB-05 (FR-IAM-004): an API 401 ends the session…", "XB-05 …token_expired is refreshed and retried once…"; `src/features/school/school.test.tsx` "XB-05 (FR-IAM-004): a session that has ended goes to sign-in…" | Fixed |
| XB-06 | Low | Idle timeout on a shared PC (FR-IAM-003, SEC-006) | The automatic status polls (`refetchInterval` while a job is queued or running) in `features/exports/data.ts` (`useExportList`, `useExport`), `features/documents/data.ts` (`useDocumentList`, `useDocument`), `features/circulars/data.ts` (`useCirculars`, `useCircular`, `useNotices`, `useNotice`) and `features/admin/data.ts` (`useTenantExports`) were ordinary GETs, so each slid the session's idle timeout. A job that stayed queued (worker down) kept a visible tab on an unattended office PC signed in past the school's timeout. | `996df309`: `lib/bff/pollHeaders(context)` adds `x-sos-passive: 1` to every fetch of a query whose data is already on screen; the first load is the person's own request and still counts. The BFF already reads a passive GET without sliding the session (`proxy.test.ts` "a passive GET reads the session without sliding the idle timeout"), and the browser client neither reports activity nor leaves the page on a passive 401 (the idle dialog handles it). Only the query functions of those nine hooks changed. Known limit: TanStack does not tell an interval refetch from a refetch on window focus or after an invalidation, so those are passive too; the mutation behind an invalidation already counted as activity. | `src/lib/bff/poll.test.tsx` (helper, and the first load active / every poll passive for each of the nine hooks) | Fixed |

No finding needs an API change.

## Checked and clean (no finding)

- **Proxy request side:** only `accept`, `content-type`, `if-match`, `if-none-match` and
  `idempotency-key` are copied (every header the web client sends to the API: 90 `If-Match` and
  77 `Idempotency-Key` call sites); `Accept-Language` is negotiated against the enabled
  languages; the request body is read once (1 MiB cap, declared and streamed) and reused for the
  `token_expired` retry; the browser's abort signal is passed on; a 30 s headers timeout applies.
- **Proxy response side:** headers from an allowlist (no hop-by-hop, no `Content-Encoding` or
  `Content-Length` from the decoded upstream body, no `Set-Cookie`); `ETag`, `Location`
  (rewritten to `/bff`), `Retry-After`, `RateLimit*`, `Deprecation`, `Sunset`,
  `Content-Disposition` pass through; `no-store` when the API sets no cache policy; SSE gets
  `no-cache, no-transform` (also keeps Next's compression from buffering) and
  `X-Accel-Buffering: no`; 204/304 bodies are null from `fetch`, so `new Response` is safe.
- **Refresh races:** one in-flight promise per session per process, a Valkey lock across tasks,
  waiters re-read the stored tokens and accept them only when they differ from the refused one;
  a network failure un-spends the refresh token; a non-rotating IdP keeps its token usable.
- **Errors:** every BFF failure is an RFC 9457 problem with `no-store` and a deny-all CSP;
  `withRuntime` turns configuration, Valkey and handler exceptions into 503/500 problems without
  internals; the API's own problems pass through unchanged.
- **Cookies:** `__Host-` prefix, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain` on
  https; the RSC helpers derive the same names from `APP_BASE_URL` as the BFF config; first
  occurrence wins when a cookie name repeats.
- **Open redirects:** `safeNext` refuses control characters, backslash, `//`, other origins,
  dot-segment tricks and `/bff`; an old locale prefix is dropped before the `//` check; the
  choose-school page re-validates `next` and refuses loops to itself; `/signed-out` renders only
  allowlisted error codes.
- **Server components:** `apiGetAsSession` uses the session's own `X-Active-Tenant` (support
  sessions are pinned to their grant's school), returns data only, and applies the idle timeout
  only from a `/me` answer about the active school; the platform layout uses the operator session
  only.
- **Security headers:** CSP with per-request nonce and `strict-dynamic`, `frame-ancestors 'none'`,
  `nosniff`, COOP/CORP, HSTS on https, `Permissions-Policy`; static assets get the same headers
  from `next.config.ts`; `poweredByHeader: false`, no browser source maps.
- **Health check and fonts:** `healthz` returns only `{"status":"ok"}` with `no-store`; the
  Telugu font routes serve a fixed allowlist (404 while Telugu is off) with `nosniff`.

## Outside my area (noticed, not changed)

- `features/ask/conversations.ts` also polls (`refetchInterval`) without the passive header. It
  was not in the lead's list, so it is unchanged; `pollHeaders` applies the same way.

## Verification

- New tests fail before their fixes (checked by running them against the earlier code) and pass
  after: `npx vitest run src/proxy.test.ts src/server src/lib/bff`.
- `npx tsc --noEmit -p .`: clean. `npx eslint . --quiet`: clean.
  `npx prettier --check` on every touched file: clean.
- Full `npx vitest run` (first pass, XB-01..03): 160 files, 2409 passed, 3 skipped (the Valkey-backed session tests).
- Second pass (XB-04..06): `tsc`, `eslint .` and prettier clean; full `npx vitest run`: 161 files, 2424 passed, 3 skipped.
- Playwright not run (per the brief). No e2e spec asserts the CSP of a print page or a 401 from
  the proxy; the e2e print links are same-origin, so XB-03 does not affect them.
