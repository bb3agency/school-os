# Penetration-test scope: R2 public surface (and the R3 disclosure page) and its neighbours

| Field | Value |
|---|---|
| Status | Scope agreed in docs (2026-10-21, wave R2-3I); R3 disclosure route added 2026-11-01 (wave R3-2F, NFR-SEC-008); test not yet scheduled |
| Requirement | NFR-SEC-007 (R2 public surface tested before the first school uses it); NFR-SEC-008 (the R3 disclosure route tested before the first CBSE school publishes); NFR-SEC-006 and SEC-025 (independent test before paid go-live, then yearly) |
| Controls under test | SEC-003..006, SEC-008, SEC-010, SEC-015, SEC-021, SEC-022, SEC-026..030, SEC-034..040, SEC-050 (docs/07 §16) |
| Threats | T28-T35, T37 (docs/07 §4); trust boundaries TB1-TB12 (docs/07 §3) |
| Related | docs/07 §11.2, docs/07 §11.3, docs/09 §2.7 (rate limits), ADR-0042, ADR-0043, ADR-0045, ADR-0046, ADR-0047, ADR-0051, docs/18 §8 D29 and D31 |

This document tells an external tester what to test, what not to touch, and how to work. It is
written for the R2 release: parent link pages, certificate verification and provider webhooks are
the first SchoolOS routes that take no staff session. The test also covers the parts these routes
rely on (the BFF, sign-in) and the other machine and operator entry points, so that one engagement
answers NFR-SEC-007 and prepares SEC-025.

## 1. Prerequisites (before the test is booked)

- A staging environment on AWS ap-south-1 built from `infra/terraform/envs/staging`. Today the
  Terraform is validated in CI but has never been applied (docs/14 M0 status), so this is the
  first gate.
- Staging data is synthetic only (invariant 11). `make seed-synthetic` refuses to run outside
  `SOS_ENV=local|ci`, so loading the same synthetic schools into staging needs a decided method
  first (for example the generator run against staging from a CI job with an explicit, audited
  override, or synthetic spreadsheets imported through the normal import screens). **Open: the
  platform owner picks the method before the test.**
- Fake providers are refused in staging (`SOS_PAYMENTS_PROVIDER_MODE`, `SOS_MESSAGING_PROVIDER_MODE`),
  so staging uses Razorpay **test mode** keys and a Meta **test** WhatsApp number and WABA, never a
  live merchant or a real parent's phone.
- `SOS_PUBLIC_BASE_URL`, `SOS_VERIFY_BASE_URL` (API) and `PUBLIC_BASE_URL`, `VERIFY_BASE_URL` (web)
  set to staging hosts, separate from the staff host (D29).
- The WAF has per-IP rate rules for `/bff/auth/`, the machine paths (`/api/v1/fleet/`,
  `/api/v1/edge/`) and, since R2-4, `/p/`, `/v/` and `/api/v1/webhooks/`
  (`infra/terraform/modules/alb_waf`; SEC-036); confirm they are applied on staging. The
  application limits below apply either way.

## 2. In scope

### 2.1 Public surface (no staff session)

API routes, exactly those listed in `apps/api/app/public/public_routes.yaml` (the route-enumeration
test fails on any other unguarded route):

| Route | Guard |
|---|---|
| `POST /api/v1/public/links/exchange` | `require_public_handle` (parent kinds) |
| `GET /api/v1/public/links/current` | `require_public_handle` (parent kinds) |
| `GET /api/v1/public/links/current/document` | `require_public_handle` (`parent_notice`, `parent_receipt`) |
| `POST /api/v1/public/links/current/pay` | `require_public_handle` (`parent_dues`) |
| `GET /api/v1/public/links/current/payment` | `require_public_handle` (`parent_dues`) |
| `POST /api/v1/public/links/current/optin` | `require_public_handle` (`parent_optin`) |
| `GET /api/v1/public/certificates/{code}` | `require_public_handle` (`cert_verify`) |
| `GET /api/v1/public/disclosure/{key}` | `require_public_handle` (`school_disclosure`; R3-1C, NFR-SEC-008) |
| `GET /api/v1/public/disclosure/{key}/documents/{n}` | `require_public_handle` (`school_disclosure`; R3-1C, NFR-SEC-008) |
| `GET /api/v1/webhooks/meta` | `require_meta_verify_token` |
| `POST /api/v1/webhooks/meta` | `require_webhook_signature("meta")` |
| `POST /api/v1/webhooks/razorpay/{route}` | `require_webhook_signature("razorpay")` |

Web pages of the `(public)` route group (`apps/web/src/app/(public)/`):

- On `PUBLIC_BASE_URL`: `/p/{token}` (exchange, 303 to `/p/view`), `/p/view`, `/p/document`,
  `/p/pay`, `/p/optin`, `/p/paid`, `/p/expired`, `/p/fonts/telugu/{file}`.
- On `VERIFY_BASE_URL`: `/v`, `/v/check`, `/v/{code}`.
- On `PUBLIC_BASE_URL` (R3-1C): `/d/{key}` (the CBSE Appendix IX disclosure page) and
  `/d/{key}/documents/{n}` (redirect to a presigned link of at most 5 minutes).
- Host routing itself: staff paths on the public and verification hosts, and public paths on the
  staff host, must answer 404.

### 2.2 Webhooks

- Meta: `GET /api/v1/webhooks/meta` (verify-token challenge) and `POST` (`X-Hub-Signature-256`,
  app secret, body up to 3 MB, WABA-id routing; FR-MSG-020, TB11).
- Razorpay: `POST /api/v1/webhooks/razorpay/{route}` (per-school route handle and secret,
  `X-Razorpay-Signature`, `x-razorpay-event-id`, body up to 256 KB, 7-day previous secret after a
  rotation; FR-PAY, TB10).

### 2.3 BFF (`apps/web/src/app/bff`)

- `/bff/api/v1/{...path}` proxy: the service token, user token forwarding, refusal of
  `/api/v1/public/*` and `/api/v1/webhooks/*` from the staff app.
- `/bff/auth/*`: login, callback, logout, session, sessions, step-up, active-tenant, and the
  operator (`/bff/auth/platform/*`) and support (`/bff/auth/support/*`) sign-in flows.
- Cookies (`__Host-` prefixed staff, support and `__Host-sos_parent` cookies), CSRF protection on
  staff writes and the parent form posts (HMAC token bound to the parent cookie plus the Origin
  check), redirects (Razorpay hosts only from `/p/pay`).

### 2.4 Operator console

- `/platform/*` pages on the operator host and every `/api/v1/platform/*` route
  (`require_platform`), with the separate OIDC client (TB6, SEC-027).
- Step-up within 300 s for ᴿ platform permissions; the two-person rules for
  `platform.tenants.offboard` and `platform.breakglass.emergency` (72-hour expiry, 7-day role age,
  not granted by the first operator; SEC-029).
- Privilege separation: no operator path reads tenant data (SEC-026; ADR-0020).
- Break-glass support access as seen by the school (SEC-021).

### 2.5 Dedicated tier

- A dedicated host built from `deploy/dedicated/` (`compose.yaml`, `Caddyfile`): TLS, security
  headers, exposed ports (only 80 and 443), parent and verification pages on the school's own host
  (TB8, SEC-030).
- The fleet heartbeat `POST /api/v1/fleet/heartbeat` (`require_fleet_signature`: HMAC-SHA256 per
  deployment, ±5 minute window, nonce replay cache, strict schema; TB7, SEC-028).
- Messaging routes answer 404 `module_unavailable` on the dedicated tier (FR-MSG-024).

### 2.6 Edge agent (Tally)

- `/api/v1/edge/tally/*`: enrolment by a one-time code and signed agent requests
  (`require_edge_agent_enrolment`, `require_edge_agent_signature`; TB9, ADR-0032).
- The agent build in `apps/edge-agent` on a test PC: it may only send Tally `Export` requests to
  `localhost` and must not accept inbound connections.

### 2.7 Sign-in

- OIDC with Amazon Cognito through the BFF (ADR-0012): school users, operators (separate client)
  and support sign-in; MFA enforcement and step-up (ADR-0018, SEC-004..006); session timeouts and
  revocation; account enumeration on sign-in and invitation acceptance.

## 3. Objectives per area

| Area | What the tester tries | Expected result |
|---|---|---|
| Public: token and code guessing | Brute-force or predict parent tokens (32 random bytes) and certificate codes (80 bits, Crockford base32); look for structure, reuse or derivation from ids | No usable pattern; `public_lookup_fail` (20 per 10 minutes per IP, fail closed) and `cert_verify_ip` (20 per 10 minutes) stop guessing; a used-up budget refuses valid keys too, so it is no oracle (SEC-036, SEC-037, T32) |
| Public: enumeration | Find out which schools use SchoolOS, or whether a student, receipt or certificate exists, from URLs, error bodies, status codes, headers or timing | No school identifier in any URL; the same 404 body for unknown, malformed, expired, revoked, wrong-kind and suspended-school keys; problem+json `instance` shows the route template, never the token, code or route handle (T33, SEC-039) |
| Public: rate limits | Exceed `public_ip` (60/60 s), `public_token` (30/60 s per handle), `public_pay` (5 new links per day per handle), `webhook_route` | 429 with `Retry-After`; limits hold across instances (Valkey); Valkey down fails closed (FR-PUB-011) |
| Public: data minimisation and BOLA | Read another child, guardian or school through a valid link; swap targets; reuse a link after the guardian is unlinked or the phone changes | Only the link's own target, per kind allowlist (FR-PUB-009); unlinked guardian, phone change, opt-out and revocation give the uniform 404 (FR-PUB-007) |
| Public: token leakage | Find the token in later URLs, `Referer`, browser history after the redirect, caches, link previews, logs or traces | Token only in the first URL, exchanged for the cookie and a 303; `no-referrer`, `private, no-store`, `noindex`; previews get a generic page; log lines and exported spans masked by `core.redaction.redact_log` (SEC-008, T35) |
| Public: CSP and cross-site | XSS in notice text, school name or receipt fields; framing; CSRF on `/p/pay` and `/p/optin`; open redirect through the pay flow | Strict CSP with no third-party scripts, `frame-ancestors 'none'`; forms refused without the bound CSRF token and a matching Origin; redirects only to Razorpay's https hosts (SEC-010, SEC-036) |
| Public: disclosure page (R3) | Find student data on `/d/{key}` or in its documents and HTML/PDF files; read a draft, a withdrawn or a superseded snapshot; guess keys (128-bit) or find out which schools publish; reuse a document link; inject HTML, scripts or non-https links through the school's inputs | Frozen snapshot only, field allowlist, no student items, counts below 10 shown as "fewer than 10"; the uniform 404 for unknown, malformed, revoked, other-kind, withdrawn, unpublished and suspended-school keys; `noindex`; presigned links of at most 5 minutes; inputs rendered escaped, https links only (SEC-050, T37, NFR-PRV-006) |
| Webhooks: forgery | Unsigned, wrongly signed, truncated or tampered bodies; another school's secret or WABA; header case and encoding tricks | 401 without detail; nothing stored; a forged "paid" event alone never creates a receipt because the worker re-fetches the payment from Razorpay (T28, SEC-034) |
| Webhooks: replay and order | Replay a valid event; send events out of order; replay after a secret rotation; oversized bodies | Duplicates acknowledged without effect; statuses never move backwards; the previous secret works only for 7 days; 413 above the limit (T29) |
| BFF | Call the API without the BFF; send a user token to `/public/*`; reach `/api/v1/webhooks/*` through the BFF; steal or fix session cookies; CSRF on staff writes | API refuses missing service token and user tokens on public routes (401); BFF refuses public and webhook paths; `__Host-` cookies, HttpOnly, Secure; CSRF enforced (SEC-004) |
| Cross-tenant and BOLA (staff) | With accounts in two schools, read or change the other school's students, receipts, fee accounts, payment links, messages, opt-ins, verifications and documents by id | 404 for the other school; 403 for a missing permission; class teachers see only their sections and no fee data (SEC-001, SEC-015, FR-FEE-024) |
| Money integrity | Issue duplicate or out-of-sequence receipts concurrently; self-approve a cancellation, concession or refund; backdate; overpay | Gap-free numbers; DB `CHECK` refuses self-approval; step-up on approvals; no backdating, no advances (SEC-038, T30) |
| SSRF | Make the API fetch an attacker URL through webhook payloads, payment-link callbacks, document uploads, OIDC metadata or template variables | Only `app/fees/providers/*` and `app/messaging/providers/*` reach their pinned provider hosts; egress allowlist; no URL from input is fetched (SEC-040) |
| Injection | SQL, template, header, CSV/formula and log injection through every public and staff input, including webhook payload fields and statement imports | Bound parameters only; formula-safe exports (SEC-017); full Aadhaar numbers refused and masked (SEC-013) |
| Secrets | Recover Razorpay keys, webhook secrets, Meta tokens or PINs from any API response, export, error, log or idempotency replay | Never returned after the one-time display; wrapped bound to their row (SEC-035, T31) |
| Suspended school (D31) | Use links, verification codes and webhooks of a school the operator has suspended | Parent pages are the uniform 404 except receipt and certificate kinds (FR-PUB-011); nothing is sent while suspended; a parent's STOP is applied once the school is reactivated (Meta retries) |
| Operator console | Reach `/api/v1/platform/*` with a school user; skip step-up; act as both people of a two-person rule; read tenant data from the control plane | Refused; step-up older than 300 s refused; second person must be different, aged 7 days and not granted by the first; no tenant data (SEC-026, SEC-027, SEC-029) |
| Dedicated tier | Forge or replay heartbeats; send personal data in a heartbeat; reach admin ports; downgrade TLS | Signature, window and nonce checks; strict schema; only 80/443 (SEC-028, SEC-030) |
| Edge agent | Reuse or guess enrolment codes; replay signed agent calls; enrol against a suspended school; make the agent talk to anything but `localhost` | One-time code, 30 minutes, same 401 as a wrong code for an inactive school; signature checks (TB9) |
| Sign-in | Bypass MFA for privileged roles and operators; abuse step-up; enumerate accounts; session fixation; logout and revocation | MFA enforced, step-up honoured, sessions revocable, no enumeration (SEC-005, SEC-006) |

## 4. Out of scope

- The infrastructure of Meta, Razorpay, AWS, Google Cloud and Amazon Cognito themselves (test only
  how SchoolOS uses them; follow each provider's own testing policy).
- Denial-of-service and volumetric testing (rate limits are tested at low volume only).
- Social engineering of staff, schools, parents or providers; phishing.
- Physical access to offices, servers or school PCs.
- Any real student, parent, staff or phone data, and any live Razorpay merchant or live WhatsApp
  number.
- Production and every school's dedicated host in use.
- The LLM and RAG behaviour (covered by the evaluation hard gates, docs/06 §13), except prompt
  injection that would lead to the data-exposure objectives above.

## 5. Rules of engagement

- **Where:** staging only (and one dedicated test host built from `deploy/dedicated/` for §2.5).
  Never production.
- **Data:** synthetic only (invariant 11; method per §1). Testers must not enter real names,
  phone numbers, Aadhaar numbers, cards or bank details.
- **Providers:** Razorpay test mode with its published test cards and UPI test flows; a Meta test
  WABA and test recipient numbers owned by SchoolOS. No live payments or messages.
- **Accounts:** only the accounts in §6, created by the platform owner. Testers do not create
  accounts through other means and do not share them.
- **Notice:** the tester sends the test window, source IP addresses and contact to SchoolOS
  operations at least two working days before starting; operations confirms that alarms
  (failed lookups, webhook rejections, WAF blocks) are expected in that window.
- **Stop conditions:** stop and call operations at once on any sign of real personal data, access
  to another school's data, access to production, a working secret or token of a provider, or
  service degradation in staging that affects other users. Do not keep or exfiltrate data beyond
  the minimum needed as evidence.
- **Reporting:** findings with severity (CVSS), affected requirement or control id, steps to
  reproduce, evidence redacted per invariant 5, and a suggested fix; critical findings reported
  the same day. Fix SLAs per docs/07 §15 (critical 7 days, high 30, medium 90); the retest
  confirms fixes. The report is kept with the security audits in `docs/security/` as a summary
  without exploit details for anything not yet fixed.
- **After the test:** the platform owner disables the test accounts, rotates any provider test
  keys, webhook secrets and heartbeat keys used, and revokes open parent links of the test schools.

## 6. Test accounts

All synthetic, created by the platform owner on staging before the test:

- **Two synthetic schools** (A and B) on the shared tier, both with fees on, Razorpay test mode
  connected, a Meta test number connected, UPI settings set, and certificates with verification
  codes; plus one school suspended by the operator for the D31 checks.
- **One account per school role in each of schools A and B** (for cross-tenant tests): `owner`,
  `principal`, `office_admin`, `office_staff`, `accountant`, `exam_coordinator`, `class_teacher`
  (one section), `teacher`, `auditor_readonly`; MFA enrolled where the role requires it.
- **One operator account per operator role:** `platform_owner` (two of them, for the two-person
  rules), `platform_engineer`, `support_agent`, `billing_admin`, `platform_viewer`.
- **Parent links** of every kind (`parent_notice`, `parent_receipt`, `parent_dues`, `parent_optin`)
  issued to synthetic guardians of both schools, plus expired and revoked ones, handed to the
  tester out of band.
- **One dedicated test host** with its heartbeat key, and **one edge-agent enrolment code** for a
  test PC.
