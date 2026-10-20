# ADR-0046: Parent link pages: short-lived, unguessable pages without a parent login

| Field | Value |
|---|---|
| Status | **Accepted** 2026-10-14: the scope is the owner's (docs/18 §3 R2.3); token design, lifetimes (30/90/7 days), page content (the receipt page shows the child's name, class and admission number) and limits **(P)** were accepted by the owner on 2026-10-14 (D22, D29, docs/18 §8) |
| Date | 2026-10-14 |
| Deciders | Product owner (scope); engineering (design) |
| Amends / supersedes | Reverses the BRD §7.3 non-goal "parent-facing pages" in part: read-only pages reached from a message, **no parent accounts or logins** (PRD §9 "parent logins" stays a non-goal). Relies on ADR-0047 |

## Context

WhatsApp templates should be short and utility-only (ADR-0043). A parent still needs the full notice (with its PDF), a receipt, or the list of dues with a "Pay" button. A parent app or parent login is out of scope (BRD §7.3; MyClassboard's parent app rates 2.4/5, research). A link in a message is a bearer credential: whoever has the message can open it, the link may be forwarded, it lands in browser history, proxy and load balancer logs (the shared ALB logs full URLs, docs/07 §11), and in WhatsApp's own link handling.

## Decision

1. **Read-only pages on a separate public origin (owner scope).** Shared tier: `SOS_PUBLIC_BASE_URL` (for example `https://my.schoolos.in`, D29), a different origin from the staff app, with no staff cookies and its own strict CSP; served by the web app's `(public)` route group, which calls the API's public routes through the BFF with the service token (no user token). Dedicated tier: the school's own host under `/p/` and `/v/`. Pages are mobile-first, work on Android Chrome over slow connections, and contain no third-party scripts, analytics or fonts from CDNs.
2. **What a page may show (P).** Only the minimum for its purpose:
   - *Notice*: school name, title, date, body and the approved notice's PDF (notices never carry student data, 08 §8).
   - *Receipt*: school name, receipt number and date, student name, class and section, admission number, amounts per head, mode, and "Cancelled on …" if cancelled.
   - *Dues*: school name, student first name and class, the outstanding instalments with due dates and amounts, late fees, and "Pay online" when the school has online payments on (creates or reuses a Razorpay payment link, ADR-0042), the school's UPI QR and payee name when set, and the office's phone.
   - *Opt-in / opt-out confirmation*: school name, the categories, the choice made, and a language choice (ADR-0044).
   No page shows a phone number, address, date of birth, Aadhaar data, marks, attendance, insights, other children or other guardians, and no page lets the parent change any record.
3. **(P) Tokens.** A token is 32 random bytes from the OS CSPRNG, written base64url (43 characters), with no structure, no school or student identifier and no expiry inside it. Only its SHA-256 is stored, in a public handle row (ADR-0047) with kind (`parent_notice`, `parent_receipt`, `parent_dues`, `parent_optin`), target id, guardian id, expiry, revocation and view counters. A token is issued per guardian per message (never shared between guardians), so it can be revoked for one person.
4. **(P) Lifetimes.** Notice 30 days; receipt 90 days; dues 7 days (amounts change); opt-in 7 days. A parent who opens an expired link sees "This link has expired. Ask the school office, or reply to the school's WhatsApp message, for a new one." A new message carries a new token.
5. **(P) Revocation.** Automatic when the guardian is unlinked or deleted, the guardian's phone changes, the guardian opts out (all open tokens of that guardian), the notice is withdrawn, or the school disconnects WhatsApp; manual by staff holding `message.send` ("Revoke link", audited). A cancelled receipt's link keeps working and says "Cancelled", because the parent must see that.
6. **(P) Leak reduction.** The token is in the path (`/p/{token}`), never in a query string. The first request exchanges it for a `__Host-` cookie scoped to the public origin with the same expiry and redirects (303) to `/p/view` without the token, so the token is in at most one access-log line and not in browser history after the redirect; `Referrer-Policy: no-referrer`; `Cache-Control: private, no-store`; `noindex`. Link preview fetchers (requests without cookies that do not follow the redirect) see only a generic school-name preview and do not count as views. The shared ALB access logs keep the one line with the token (S3, restricted, 13 months): accepted, because the token expires and opens only the minimum above.
7. **(P) Rate limits and enumeration.** Per IP 60 page requests a minute; failed token lookups 20 per 10 minutes per IP (fail closed, 429), alarmed globally; per token 30 views a minute. Unknown, expired and revoked tokens get the same page and status (404), so nothing distinguishes them; no answer names a school unless the token is valid.
8. **(P) Audit.** Each token issue is audited with the message that carried it (`public.link.issued`: kind, target id, guardian id, expiry); the first view and every revocation are audited (`public.link.viewed`, `public.link.revoked`); later views are counted on the row, not audited one by one. "Read" status shown to staff comes from WhatsApp's `read` status and the page's first view.
9. **(P) Payment from the dues page.** "Pay online" creates (or reuses while it is valid) one payment intent for the selected instalments of that student and redirects to the Razorpay link (ADR-0042). It is limited to 5 new intents per token per day. The page never collects card or bank details.

## Consequences

- Good: parents see full notices, receipts and dues without an app or login; the school controls lifetime and can revoke per guardian.
- Bad / costs: a forwarded link shows its content to whoever receives it within the lifetime (the receipt page carries the child's name and class); this is the same exposure as a paper receipt handed over and is stated in the DPIA.
- Bad / costs: a public origin adds an internet-facing surface with its own threat model (docs/07 §11.2) and a second set of rate limits.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Parent login (OTP to phone) | A parent identity system, OTP costs and support load; out of scope by BRD §7.3 |
| Put everything in the WhatsApp message | Long messages risk marketing classification and cannot carry a PDF or a pay button cleanly |
| Signed, stateless tokens (JWT) | Cannot be revoked per guardian without a list anyway; leaks structure |
| Token in the query string | Logged and leaked through referrers more often; the path plus exchange-and-redirect is tighter |
| One-time links | WhatsApp and browsers pre-fetch; parents open links more than once |

## Related requirements

FR-PUB-005..012 (docs/03 §3.22), FR-MSG-010, FR-PAY-005, SEC-036, SEC-037, SEC-039, PRV-026, PRV-028; US-2401..US-2405 (docs/02 C24). Docs: 05 §15.5, 07 §3 TB12, §11.2, 08 §15, 09 §4 "Public", 10 §11.2, 17 §5.8.

## Follow-up (2026-10-17, wave R2-0 built)

The exchange answers `{kind, expires_at}`; the `__Host-sos_parent` cookie carries the token itself (HttpOnly, Secure, SameSite=Lax, Max-Age = remaining lifetime), and the BFF sends it to the API in `X-SOS-Public-Token` on every page request, so each request re-resolves the handle and a revocation applies at once (no separate handle reference to store). A dead link answers 303 to `/p/expired`, which is the same 404 page for every failure (the first hop is a 303 for live and dead links alike, so preview fetchers learn nothing). `/p/view` counts views; the exchange does not. Guardian unlink and phone change revoke the guardian's open links (opt-out, notice withdrawal and WhatsApp disconnect follow in R2-1C/R2-3G through `public.service.revoke_for_guardian` / `revoke_for_target`).

## Follow-up (2026-10-18, wave R2-3H built)

- **Targets pinned per kind.** `parent_notice` → `parent_notice` (an approved notice), `parent_receipt` → `fee_receipt`, `parent_dues` → `fee_account`, `parent_optin` → `guardian` (the guardian itself); `app.public.config.PARENT_TARGET_TYPES`, issued through `public.service.issue_parent_link`. Receipt, dues and opt-in links need their guardian, and open only while that guardian is linked to the child.
- **Opt-in page = confirmation and language only.** It shows the categories with their state and sets "Read messages in" (ADR-0044 decision 4). It records no opt-in (ADR-0043 decision 7 accepts only the join message, a signed form or in person) and no opt-out (STOP on WhatsApp stays the parent's way; a page button would need a new opt-out method and a migration, left for an owner decision).
- **Pay (decision 9).** The 5-a-day limit counts new payment links only; choosing the same instalments again returns the same open link. The redirect target must be an https Razorpay host (API `pay_redirect_hosts`, BFF check, CSP `form-action`). The return page reads the status through the dues link and checks again with a meta refresh (no JavaScript).
- **Notice PDF.** Offered while the rendered PDF is kept (6 days after rendering, `circulars/config.yaml`); a 30-day link after that shows the text without the PDF button.

## Follow-up (2026-10-21, wave R2-3J built)

Decision 5's "the notice is withdrawn" is built: `POST /notices/{id}/withdraw` (`notice.approve`, FR-NOTICE-009) revokes the notice's open parent links through `public.service.revoke_for_target` in the withdrawing transaction (reason `notice_withdrawn`, so `/p/*` answers the uniform 404 at once), and the outbox event `notice.withdrawn` has the WhatsApp sender cancel the messages not sent yet and revoke any link issued meanwhile. A withdrawn notice's page never comes back (`withdrawn` is final in the database).
