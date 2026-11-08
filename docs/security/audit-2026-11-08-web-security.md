# Security audit 2026-11-08: BFF and web security (web-security)

Worktree branch from `813c3996`. Area: the BFF route handlers (`apps/web/src/server`,
`app/**/route.ts`; the app has no server actions), session handling (cookie flags, rotation,
idle and absolute timeouts, sign-out), tokens never reaching browser JavaScript, CSRF on every
mutating BFF route, the API proxy's path allowlist (traversal, SSRF, header injection), open
redirects (`next`, sign-in callback, `/p/pay` to the pay hosts), CSP and security headers
(`src/proxy.ts`, `lib/security-headers.ts`, `lib/public-surface.ts`), the public pages (`/p`,
`/v`, `/d`), operator, staff and support session separation, the step-up flow, error pages,
unsanitised HTML, `window.open`/`target=_blank`, file downloads, browser storage and console
logging. The earlier reports (`audit-2026-10-04-web-ai-infra.md`, `audit-2026-10-17-web.md`)
were read first; their findings and "checked and clean" items are not repeated. Synthetic data
only.

**Status: complete. All four findings fixed (two medium, two low), each with tests that failed
first. WS-03 and WS-04 were first reported and then fixed on the lead's decision of 2026-11-08.**

## Findings

| ID | Severity | Area | Evidence | Fix | Test | Status |
|---|---|---|---|---|---|---|
| WS-01 | Medium | Step-up, sign-in (FR-IAM-001, FR-IAM-002, invariant 7; ADR-0018, ADR-0023) | `apps/web/src/server/auth/handlers.ts` `handleCallback` (before the fix, lines 488-557): when someone other than the session's owner signed in at a step-up prompt (a colleague at the shared office PC, or the owner's session had been replaced), the callback created a new session for that person but skipped `afterStaffSignIn`, because it only ran when the transaction was not a step-up. So that person's sign-in was **never audited** (no `auth.login.succeeded` in the school's chain, invariant 7; a later school choice did not record it either, since `loginEventPending` was not set), pending invitations were not accepted, the **MFA-required check** (FR-IAM-002: a privileged role without MFA gets no session) did not run, and the session had no school. For a support session (ADR-0023), another operator signing in at the support step-up prompt got a support-kind session without a grant (the API refuses it, but it should not exist). | `55e9f96b`: a step-up callback whose person differs from the session's owner is a fresh sign-in (invitations, MFA check, school, audited login event; goes home, never the first person's `next`). A support step-up completed by another operator is refused (`support_not_allowed`) and leaves the grant's session as it was. | `server/auth/handlers.test.ts` "FR-IAM-001 WS-01 …" and "FR-IAM-002 WS-01 …"; `server/auth/support.test.ts` "SEC-021 WS-01 …" (all three fail before the fix) | Fixed |
| WS-02 | Medium | Idle timeout across tabs (FR-IAM-003, SEC-006, SEC-021) | `apps/web/src/components/session/SessionControls.tsx` `IdleWarning`: each tab timed the idle warning from its own BFF calls only, but the session (one `__Host-` cookie) is shared by every tab. A school-console tab left in the background showed the warning and then **signed the person out (server-side revocation) while they were working in another tab**. Worse for SchoolOS support (break-glass): the school console's BFF client reports activity as `staff`, while the support session's `IdleWarning` listens for `support`, so an operator was signed out 15 minutes after the page loaded however much they worked. Conversely, after "Lock now" in one tab, another tab kept showing school data until its own timer ran (shared office PC). | `64d04827`: before the warning and before signing out, the tab re-reads the server's deadline with `GET /bff/auth/session` (which never slides it). A later deadline reschedules the timers; an ended session leaves the page at once; a failed read keeps the old behaviour. The server keeps enforcing the timeout on every request either way. | `components/session/SessionControls.test.tsx` "FR-IAM-003 WS-02: another tab kept the session alive …", "… the session ended in another tab …" (`64d04827`), "SEC-021 WS-02: a support session in use is not signed out …" (`84d2a636`); all fail on the code before `64d04827`. The test's fake BFF now models the server's deadline, so the existing idle tests exercise the re-read too. | Fixed |
| WS-03 | Low (hardening) | Download links (presigned URLs), payment sign-in link (SEC-010) | `features/{documents,exports,admin,circulars,disclosure}/data.ts` (`openDownload`), `features/certificates/parts.tsx:145`, `features/fees/ReceiptDetailScreen.tsx:88`, `features/change-requests/ChangeRequestDetailScreen.tsx:91-92` (download and image preview), `features/payments/PaymentsSettingsScreen.tsx:180` (Razorpay OAuth start): the URL from the API was passed to `window.location.assign` (or an `<img src>`) without a scheme check. The parent pages, the disclosure route and the Ask page chip already checked. Not exploitable without a compromised API (boto3 builds these URLs; the nonce CSP blocks `javascript:` navigation). | `6fa98a89` (lead decision 2026-11-08): `src/lib/open-file-url.ts` (`safeFileUrl`, `checkedFileUrl`, `openFileUrl`) accepts only absolute `https:` links without credentials; plain `http:` only between loopback hosts **when the page itself is served over http on localhost**. This differs from the brief's "non-production builds": the e2e suite runs `next start` (a production build) against the stand-in, whose presigned links are `http://localhost:<port>/…`; every real deployment is https (the BFF config refuses http outside loopback), so it never accepts http. `javascript:`, `data:`, `blob:`, relative and protocol-relative links throw `UnsafeFileUrlError`, which `describeApiError` shows as the generic message in each caller's existing error alert. All the call sites use it. | `lib/open-file-url.test.ts` (helper), `lib/open-file-url.callers.test.ts` (the five download starters), and one screen test each in exports, documents, certificates, fees (receipt PDF), change-requests (evidence) and payments (OAuth); the 11 caller tests fail before the fix | Fixed |
| WS-04 | Low (availability) | Public pages (FR-PUB-001) | `app/(public)/p/view/page.tsx`, `p/paid/page.tsx`, `p/document/route.ts`, `p/pay/route.ts`, `p/optin/route.ts`, `p/[token]/route.ts`, `d/[key]/page.tsx`, `d/[key]/documents/[n]/route.ts`, `v/[code]/page.tsx` called `getAuthRuntime()`, which connects to Valkey on first use, and (except `/p/{token}`) did not catch its failure: a Valkey outage turned the parent, disclosure and verification pages into Next.js's generic 500 page, although they hold no session and no rate-limit state (the API limits the public routes). No details leaked. | `fb075e4f` (lead decision 2026-11-08): `server/public/runtime.ts` `getPublicRuntime()` is the configuration, `fetch` and the clock, never a Valkey connection; every page and route of the `(public)` group uses it. No public route needs Valkey, so there is nothing left to catch. | `server/public/runtime.test.ts`: with `connectRedis` refusing, `/p/document`, `/p/pay`, `/p/optin`, `/p/{token}` and `/d/{key}/documents/{n}` still answer and Valkey is never contacted (fail before the fix); a source check pins that no `(public)` file uses `getAuthRuntime` | Fixed |

## Checked and clean (no finding)

- **Proxy allowlist:** `/bff/api/v1/*` only; `%2f`, `%5c`, `%2e`, backslash and dot segments
  refused on the raw path; `/edge`, `/webhooks`, `/public` and (on dedicated hosts) `/platform`
  refused on the percent-decoded path; the URL base is `API_INTERNAL_URL` with
  `redirect: "manual"`; request headers copied from a five-name allowlist (no Cookie, Host,
  Authorization or X-Forwarded-* from the browser; the client address only from the trusted
  hop); response headers from an allowlist, no `Set-Cookie`, `Location` rewritten to `/bff/…` or
  dropped; `X-Request-Id` accepted only in its fixed shape. `callPublicApi` refuses any path
  outside `/api/v1/public/` and any `?`, `#`, `%`, backslash or dot segment.
- **CSRF:** synchronizer token (constant-time) plus Origin and `Sec-Fetch-Site` on every unsafe
  BFF call, logout, keep-alive, active-tenant and session revocation; the parent forms (`/p/pay`,
  `/p/optin`) use an HMAC token bound to the parent cookie, the public origin check and
  `application/x-www-form-urlencoded` only. `GET` routes with effects (sign-in and step-up
  starts) only set a sealed, host-only transaction cookie; a forged callback fails on state,
  nonce and PKCE.
- **Sessions:** `__Host-` cookies, `HttpOnly`, `Secure`, `SameSite=Lax`, no `Max-Age`; opaque
  256-bit id, stored as its SHA-256; new id at every sign-in and step-up; idle and absolute
  limits enforced server-side on every read; refresh-token rotation with reuse detection and
  family revocation; at most five sessions per person; sign-out revokes at the IdP and in Valkey
  and leaves the page with a full navigation (no query cache survives). Step-up starts a new
  absolute window (`created` is reset): acceptable, because it is a fresh MFA sign-in.
- **Tokens never reach browser JavaScript:** `sessionInfo` carries only the CSRF token and
  timings; `SessionView` (RSC) has no token or CSRF; `apiGetAsSession` returns data only.
- **Open redirects:** `safeNext` on login, step-up, support login, the 428 `step_up_url` (from a
  same-origin Referer) and `/choose-school`; `safeStepUpUrl` in the browser client; sign-out goes
  to the BFF's own `redirect_to`; `/p/pay` goes only to `https` on `rzp.io`/`razorpay.com` (no
  userinfo or port); presigned file redirects require `https`; `/v/check` redirects only to a
  normalised code; the legacy-locale 308 keeps a single leading slash.
- **CSP and headers:** per-request nonce with `strict-dynamic`, no inline styles in production,
  `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`, `form-action 'self'` (parent
  pages add only the pay hosts); Meta's SDK only on `/settings/whatsapp`, with
  `same-origin-allow-popups` there only and `message` events accepted only from `*.facebook.com`;
  API HTML pages keep their CSP only when it is hash-only and `default-src 'none'`
  (`isStrictPolicy`); HSTS on https. The inline shell script (`SHELL_STATE_SCRIPT`) is constant
  and carries the nonce.
- **Public pages:** `/p/{token}` exchanges and redirects at once (token out of the address bar;
  never in a query string or a log line); every public response has `Referrer-Policy:
  no-referrer`, `Cache-Control: private, no-store` and `X-Robots-Tag: noindex`; the parent cookie
  is host-only on the public origin, `Max-Age` = the link's lifetime; `/p/paid` ignores
  Razorpay's query values except a 32-hex reference; the disclosure `link` is `https` only
  (validated by the API) and rendered with `rel="noreferrer noopener"`.
- **Session separation:** operators cannot use school routes and staff or support cannot use
  `/platform/*` (403 `wrong_session`); support sessions are pinned to the grant's school; the
  RSC helpers and the BFF pick staff before support. (Info: `requireSession` in
  `server/session/rsc.ts` falls back to a support cookie without checking
  `config.supportEnabled`, unlike `readSchoolSession`; harmless because no support session can
  be created while it is off.)
- **HTML and links:** no `dangerouslySetInnerHTML` or `innerHTML`; AI answers and notices render
  as text; every `target="_blank"` has `rel="noopener"` (same-origin print views) or
  `noopener noreferrer`; `window.open` for cited pages uses `noopener,noreferrer`; the step-up
  window reports back over a same-origin `BroadcastChannel` with no data.
- **Downloads:** presigned GETs carry `ResponseContentDisposition` (attachment unless PDF or a
  raster image), `ResponseContentType` and `no-store`, on the separate files origin.
- **Browser storage and console:** `localStorage` holds only UI preferences (sidebar, density,
  hidden table columns) under `sos.`; nothing in `sessionStorage`; no `console.*` in client code;
  server logs go through `logEvent` with allowlisted fields only.
- **Error pages:** BFF handlers answer RFC 9457 problems without internals (`withRuntime`); no
  custom error page renders exception text; the healthz route returns only `{"status":"ok"}`.

## Outside my area (noticed, not changed)

- None.

## Verification

- `npx vitest run src/server/auth src/components/session`: all pass (the new tests fail before
  their fixes; checked by running them against the earlier code).
- `npx tsc --noEmit -p .`: clean. `npx eslint . --quiet`: clean. `npx prettier --check` on
  every touched file: clean. `src/i18n` and `src/app/english-only.test.tsx`: 159 passed.
- Second pass (WS-03 `6fa98a89`, WS-04 `fb075e4f`, after merging main at `55282d1c`): `tsc`,
  `eslint .` and prettier clean. Full `npx vitest run`: 156 files, 2368 passed, 3 skipped, 2
  failed: two announcement tests in `features/platform/actions.test.tsx` hit their 5 s timeout
  under the full parallel load (they also timed out before these changes); that file run on its
  own passes 38/38.
- First pass, full `npx vitest run`: 152 files, 2326 passed, 3 skipped (the Valkey-backed session tests). A
  first full run made at the same time as the i18n run had 5 timeouts in unrelated form tests;
  the run on its own was green.
- Playwright not run (per the audit brief); no e2e spec asserts the idle or step-up behaviour
  changed here.
