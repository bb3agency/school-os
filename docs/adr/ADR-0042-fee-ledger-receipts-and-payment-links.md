# ADR-0042: Fee ledger, receipts and payment links (school is the merchant; Razorpay first)

| Field | Value |
|---|---|
| Status | **Accepted** 2026-10-14: decisions 1-5 are the owner's (D4, D5, D8 of 2026-10-10); the engineering choices marked **(P)** and P1-P10 below were accepted by the owner on 2026-10-14 (D12-D30, docs/18 §8) |
| Date | 2026-10-14 |
| Deciders | Product owner (D4 fees, D5 Razorpay first, D8 pricing unchanged); engineering (design) |
| Amends / supersedes | Reverses the BRD §7.3 non-goal "online fee collection from parents" and PRD §9 "parent fee payments". Settles the provider choice left open by [ADR-0016](ADR-0016-payments-provider.md) (Razorpay), for a different payer: ADR-0016 is about schools paying SchoolOS; this ADR is about parents paying schools. Relies on ADR-0010 (maker-checker), ADR-0013 and ADR-0047 (public routing), ADR-0029 (offboarding), ADR-0032 (Tally), ADR-0041 (alongside mode) |

## Context

The 2026-27 plan (docs/18 §3 R2.2) puts a fee ledger and receipts next to the readiness wedge. Owner decision **D4**: build a fee ledger and receipts covering cash, the school's own UPI QR (matched by UTR) and payment links, and the school is always the merchant. **D5**: Razorpay first, behind a provider interface with a fake provider in tests. **D8**: pricing stays as ADR-0038 (nothing is charged per payment).

Facts checked on 2026-10-14 (sources at the end):

- **RBI.** The Regulation of Payment Aggregators Directions of 15 Sep 2025 (RBI/DPSS/2025-26/141) require prior RBI authorisation (net worth ₹15 crore at application, ₹25 crore by the third year) for anyone who receives customers' money and settles it to merchants, and allow a PA to aggregate funds only for merchants it has a contract with. Pure technology providers ("payment gateways") stay outside the licence. SchoolOS therefore must never receive, hold or route parents' money.
- **Razorpay Technology Partners** onboard businesses through **Razorpay OAuth**: the school signs in to its own Razorpay account and authorises the partner's application; the partner exchanges the returned `code` for an `access_token` (about 90 days), a `refresh_token` (180 days, single use) and a `public_token`, and calls Razorpay's APIs with `Authorization: Bearer`. Scopes include `read_only` and `read_write`. A sub-merchant can revoke access at any time; the partner learns of it through the `account.app.authorization_revoked` webhook. The partner webhook API creates webhooks on a sub-merchant account (`account_id` in the path, our own `secret`, URL ≤ 255 characters).
- **Payment Links API.** Amount in paise (INR minimum 100), `reference_id` unique per link (≤ 40 characters), `expire_by` at most six months and at least 15 minutes ahead, `accept_partial` default false, `notify.sms`/`notify.email` (false = we do not ask Razorpay to message the payer), up to 15 `notes`, `callback_url`; statuses `created`, `partially_paid`, `paid`, `expired`, `cancelled`; ids `plink_…`.
- **Webhooks.** Header `X-Razorpay-Signature` = hex HMAC-SHA256 of the **raw body** with the webhook secret; the same event can arrive more than once (deduplicate on `x-razorpay-event-id`) and out of order (`payment.captured` may come before `payment.authorized`). After a secret rotation, retries of older events still carry the old signature.
- **Costs.** Bank-account UPI has zero MDR by rule; Razorpay's standard plan charges a platform fee of about 2% + GST even on UPI. Budget schools prefer cash and their own free bank UPI QR (research notes §4). School fees up to higher secondary are GST-exempt.
- **Today.** SchoolOS reads Tally ledgers (M6, ADR-0032 Proposed, behind a flag) and answers fee-due questions from them; it records no fees itself. The "alongside your current ERP" mode (ADR-0041) lets a school keep fees in its ERP.

## Decision

1. **A fee ledger and receipts in a new module `app/fees` (owner D4).** Fee heads, fee plans per academic year and class with instalment schedules, concessions, late fees, one fee account per student and academic year, an **append-only ledger**, and receipts. The ledger is the school's record of what is due and what was received; SchoolOS does not do accounting (Tally stays, BRD §7.3) and does not issue GST invoices for fees.
2. **SchoolOS never touches money (owner D4; RBI PA Directions).** Cash and cheques are counted by the school; UPI QR payments go to the school's own bank account through the bank's QR; online payments go to the school's **own Razorpay account** (the school is Razorpay's merchant, with its own KYC and settlement to its own bank account). SchoolOS creates payment links in the school's account, reads its webhooks and API to reconcile, and asks it for refunds. SchoolOS has no merchant account of its own for fees, no escrow, no split settlement (Razorpay Route) and no payout capability, and takes no commission or convenience fee (D8). SchoolOS declines any partner commission from Razorpay (owner decision D28 of 2026-10-14); changing that needs a new ADR and disclosure to schools.
3. **Razorpay first, behind a provider interface (owner D5).** `app/fees/providers/` defines `PaymentProvider` (create and cancel a payment link, fetch a payment or link, list a day's payments and settlements, refund, verify and parse a webhook, connect and disconnect an account). Implementations: `razorpay` and `fake` (deterministic, local and CI only; refused in staging and prod like the extraction fake). Only `app/fees/providers/*` may call a payment provider (import-linter contract and egress allowlist), the same rule as the LLM gateway (ADR-0005).
4. **One receipt series per school, for every mode (owner D4).** Cash, cheque, bank transfer, UPI QR and Razorpay receipts share one gap-free series per school and **financial year** (April-March), default `RCT/2026-27/000001` (format in `app/fees/config.yaml`). Numbers are allocated in the issuing transaction from a locked counter row, like certificate serials (FR-CERT-006). A receipt's printed content is frozen with a SHA-256 at issue.
5. **Receipts are never edited; a cancellation is a new document (owner D4).** A wrong receipt is cancelled through a cancellation record (reason, requester, approver ≠ requester, DB `CHECK`), which posts a reversing ledger entry; the receipt keeps its number and shows "Cancelled" in every list, print and parent page. Money returned to a payer is a **refund voucher** in its own series (`RFD/2026-27/0001`), never a negative receipt. Concessions and manual adjustments are maker-checker too (ADR-0010 pattern).
6. **(P) Razorpay connection by OAuth; manual keys only as a pilot fallback.** SchoolOS registers as a Razorpay Technology Partner and connects each school by OAuth with scope `read_write` (create links, refunds). The access and refresh tokens are **per school secrets**, wrapped by the school's key wrapper bound to the row (`core.crypto.wrap_bound`, encryption context `{tenant_id, resource: "payment_connection/<id>"}`, docs/07 §8), refreshed by a daily job before expiry (alert at 14 days to refresh-token expiry). After connecting, SchoolOS creates a webhook on the school's account through the partner webhook API, with a **per-school random secret** (wrapped the same way) and a per-school URL `/api/v1/webhooks/razorpay/{route}`, where `route` is a random public handle (ADR-0047). Until partner approval, a pilot school MAY paste a key id and key secret from its own dashboard and add the webhook itself with the URL and secret SchoolOS shows once (`mode = manual_keys`); the same wrapping applies. SchoolOS never stores a school's dashboard password and never asks for card or bank credentials.
7. **(P) Webhook handling: verify, store, then process.** `require_webhook_signature("razorpay")` resolves the route handle, loads the school's secret in its own `tenant_session`, verifies the HMAC over the raw body in constant time (body ≤ 256 KB), and answers 200 only after the event is stored in the inbox `fin.provider_events` (encrypted payload, unique `(tenant_id, provider, event_id)`). A duplicate is answered 200 and ignored. A worker processes the inbox in order of `created_at` per payment, **re-reads the payment from the API** before posting (amount, currency, status, our `reference_id`), and posts receipt + ledger entry in one transaction, idempotent on the provider payment id (unique). Unknown or mismatched payments go to the reconciliation exceptions list; nothing is guessed.
8. **(P) What Razorpay receives (minimisation).** A payment link carries the amount, `reference_id` = the payment intent id, a description "School fees · <school short name> · <intent code>", and `notes` with the intent id only. No student or parent name, phone or email is sent (`customer` omitted, `notify.sms = notify.email = false`); SchoolOS's own WhatsApp message or the parent page carries the link. `callback_url` points to the parent page (ADR-0046).
9. **(P) UPI QR and UTR.** The school uploads its own bank's static UPI QR (an image) and payee name. At the counter the clerk records a UPI payment with the **UTR / UPI reference** (12 digits) shown on the parent's phone; the receipt is issued at once with mode `upi_qr` and marked "Not yet seen in bank statement". The accountant imports the bank statement (CSV/XLSX, parsed then deleted like attendance sheets); exact UTR + amount matches are proposed and confirmed by a person in one step, never posted silently; unmatched UPI receipts older than 7 days (configurable) appear on an exceptions list. A UTR can be on one issued receipt only. The `utr` field is typed and exempt from the Aadhaar mask in that field only (a 12-digit UPI reference passes the Verhoeff check about one time in ten); free text keeps the mask (PRV-015, the APAAR precedent in PRV-020).
10. **(P) Reconciliation and refunds.** A daily job (06:00 IST) lists the previous day's Razorpay payments and settlements for each connected school and compares them with receipts: paid-but-not-receipted, receipted-but-not-captured, amount mismatch, refunded, settlement missing after T+3. A refund of an online payment is requested by the accountant, approved by a second person (`fee.refund.approve`, step-up) and sent to Razorpay; the voucher and ledger entry are posted when `refund.processed` arrives. Cash refunds are recorded as vouchers only.
11. **(P) GST note.** Receipts state "Education services: exempt from GST" (configurable text). A head marked `taxable` cannot be collected through SchoolOS receipts in R2 (the school invoices it in Tally); this keeps SchoolOS out of GST invoicing for fees.
12. **(P) Coexistence.** The "alongside" mode (ADR-0041) hides `fees` by default (added to `operating_modes.yaml`), and a school may also simply leave fees off (feature flag `fees.enabled`). When fees are on, the Ask tool `get_fee_dues` reads the fee ledger instead of Tally (finance permission unchanged); the Tally connector keeps working as a read-only view and is not reconciled against the ledger in R2. A daily receipts CSV ("day book") is exportable for entry into Tally; a Tally XML voucher export is a follow-up.
13. **(P) Never withhold a TC for dues.** The TC flow may show the student's outstanding balance to the clerk as information but MUST NOT block, delay or print dues on a TC (RTE Act and state practice; owner decision D16 of 2026-10-14). No fee data is ever shown to class teachers or in Ask answers to anyone without `finance.read`.
14. **Purpose limit (invariant 14).** Fee data is used only to run the school's fees. No referrals to lenders or fee-financing partners, no sharing with Razorpay beyond decision 8, no cross-school analytics.

### Engineering choices (accepted by the owner 2026-10-14)

| # | Choice | Decision (2026-10-14) |
|---|---|---|
| P1 | OAuth Technology Partner onboarding (decision 6) and manual keys for pilots | SchoolOS applies to Razorpay's Technology Partner programme once a staging demo exists (D13); until approval, pilots connect with manual per-school keys |
| P2 | Receipt series per financial year (not academic year) | Financial year April-March, one series for all modes; A5 receipts by default (D17) |
| P3 | Who cancels a receipt | Requested by the collector or accountant, approved by a different person (principal or accountant, `fee.receipt.cancel`); never self-cancel (D17) |
| P4 | Concession, adjustment and refund approval | Requested by office admin or accountant, approved by a different person: principal or accountant (step-up) (D17) |
| P5 | Late fee rules | Flat amount per overdue instalment after a grace period, or per day with a cap; never compounding; waivers need approval; no advances in R2 (D18) |
| P6 | Taxable heads | Not collectable through SchoolOS in R2 |
| P7 | UPI verification | Receipt at once; bank-statement confirmation by a person later |
| P8 | Backdated receipts | Not allowed (receipt date = today, IST); online receipts dated by capture time; day close included (D20) |
| P9 | Cheques | Allowed as a mode; a dishonoured cheque is a cancellation with reason `cheque_dishonoured` |
| P10 | Schema | New tenant schema `fin` (bootstrap, RLS allowlist `tenant_schemas`) rather than adding to `sis` or `ops` (D14) |
| — | Retention and commission | Financial records 8 years after the financial year (D21; a CA confirms when available); SchoolOS declines any Razorpay partner commission (D28) |

## Consequences

- Good: schools get one receipt book for every mode, parents get receipts and links on their phone, the accountant reconciles from webhooks and a statement import instead of a paper register; money never touches SchoolOS.
- Good: the fake provider and recorded payload fixtures let the whole flow be tested without Razorpay.
- Bad / costs: a large new module (about 20 tables), a second public machine endpoint, and per-school secrets to rotate and protect; Razorpay partner approval is a dependency for OAuth.
- Bad / costs: schools on Razorpay pay its platform fee (about 2% + GST) unless they negotiate; SchoolOS must say so plainly and keep cash and UPI QR first-class.
- Follow-up: privacy review and DPA wording (Razorpay is the school's own provider, not SchoolOS's sub-processor, docs/08 §15); CA review of receipt layout and retention; Tally XML export; a second provider (Cashfree/Easebuzz) only through the same interface and a new ADR amendment.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| SchoolOS collects into its own account and pays schools | Makes SchoolOS a payment aggregator under RBI's Directions (licence, ₹15-25 crore net worth); never |
| Razorpay Route / split settlement through a SchoolOS master account | Same problem: funds flow through a SchoolOS-controlled account |
| Store each school's key id and secret only (no OAuth) | Schools would have to create keys and webhooks by hand; long-lived secrets; kept only as a pilot fallback |
| Separate receipt series per mode | The owner asked for one series; schools reconcile one book |
| Edit a wrong receipt | Breaks the audit trail and the counterfoil; cancellation is a new document |
| Auto-post bank-statement matches | Wrong postings are hard to undo; a person confirms in one step |
| Build on the Tally connector (write vouchers to Tally) | Writing to Tally from SchoolOS breaks its read-only design (ADR-0032) and needs the office PC online |

## Follow-up (R2-1B, built 2026-10-18)

Decisions 1, 4, 5, 11, 12, 13 and 14 and choices P2-P6, P8, P9 and P10 are implemented in `app/fees` (migration `0058_fees_core`); the provider interface, online payments and UPI statements (decisions 3, 6-10) follow in waves R2-2D and R2-2E. Choices the build made (owner-visible ones in docs/03 §3.20 "as built"):

- **Fees on/off (decision 12).** Besides the flag `fees.enabled`, the school setting `fees_enabled` must be true; in `alongside` mode it is the owner's way to turn fees on despite the ERP.
- **Frozen plans (P-none).** Every active plan is frozen, not only one with posted charges; changes are new versions whose per-student differences become adjustment requests (FR-FEE-004).
- **Late-fee keys (P5).** Posted once per instalment (flat) or per day (per-day, capped); a new plan version does not post the same flat late fee again; "never compounding" = late fees are computed on collectable fee lines only.
- **Taxable heads (P6).** Planned and charged, never collected through a receipt; they are left out of "overdue" and the counter's collectable total.
- **Cancellations and refunds (decision 5, P3, P4, P9).** A receipt with a refund is not cancelled; a dishonoured cheque is a cancellation with reason `cheque_dishonoured`; cash, cheque and bank-transfer refunds are written as vouchers on approval.
- **Applying a plan** runs in one transaction with a dry-run summary, not a background job (classes of up to 2,000 students).
- **Audit summaries** of the full data export name only tables that hold rows (the archive passed 50 tables, the audit summary limit).

## Follow-up (R2-2E, built 2026-10-18)

Decision 9 (P7) is implemented in `app/fees/upi.py`, `statements.py`, `statements_parse.py`, `upi_config.py` (+ `upi.yaml`), `upi_qr.py`, `api_upi.py` and `upi_tasks.py` (migration `0060_fees_upi`, permission `fee.reconcile`). Choices the build made (details in docs/03 §3.20 "As built (R2-2E)"):

- **Generated static QR instead of an uploaded image.** The school types its UPI ID and payee name once; SchoolOS draws the QR of `upi://pay?pa=<vpa>&pn=<payee>&cu=INR` (no amount) with segno. It is the content of a bank's static QR, needs no image upload, scan or storage, and cannot be swapped for another payee's picture. A bank QR that carries merchant fields (a merchant code) is a follow-up if a pilot school needs it.
- **Synchronous import, parse then delete.** A statement (≤ 2 MB, ≤ 5,000 transactions) is read and its file deleted in one committed transaction, then parsed in memory with versioned bank presets (generic, SBI, HDFC, ICICI, Axis, Kotak; `verified: false` until a pilot confirms, D3). The import answers 201, not 202.
- **UTRs are not Aadhaar numbers.** The file is refused for an Aadhaar number written as printed (groups of four); unbroken 12-digit numbers are UPI references (decision 9's exemption), masked in the narration with every other long number.
- **Two rules, both only proposals.** Exact UTR and amount (pre-ticked); for credits without a UTR, the single unconfirmed UPI receipt of the same amount near the date (not ticked). No maker-checker for confirmations: a confirmation is a fact about a receipt, moves no money and edits nothing; the database checks each one (same amount, same UTR when the bank shows one, an issued UPI receipt).
- **Never auto-correct.** A credit whose UTR is on a receipt with another amount stays an exception next to that receipt; the remedy is the normal cancellation and a new receipt, after which a person matches the credit again (a confirmed credit can confirm the receipt issued in place of a cancelled one).

## Follow-up (R2-2D, built 2026-10-18)

Decisions 2, 3 and 6-10 and P1 are implemented (`app/fees/providers/`, `online.py`, `api_online.py`, `webhook.py`, `tasks_online.py`; migration `0059_payments_online`; docs/03 §3.21 "as built"). Choices the build made:

- **Pilot first (P1, D13).** Manual keys (`SOS_RAZORPAY_MANUAL_KEYS_ALLOWED`) are the working path; OAuth is built against the documented endpoints and the fake provider but stays off until the partner application exists (409 `oauth_not_available`). The OAuth callback is a POST with the code in the body, from a staff page that strips the code from the address.
- **Finding the intent (decision 7).** Payment webhooks carry the payment and, for links, its order; Razorpay copies the link's `reference_id` into the order's `receipt`. The worker therefore re-fetches the **payment and its order** (never trusting the event body) and finds the intent by the order's receipt under RLS; payments without a SchoolOS reference are ignored, a SchoolOS-shaped reference this school does not have is an `unknown_intent` exception.
- **Counter and online for the same dues.** The counter's lock order (account, then its links) is used by the worker too, and any receipt closes open links on the instalments it pays. If the parent pays while the clerk takes the same dues, exactly one receipt covers them; the other money is a `paid_not_receipted` / `dues_already_paid` exception for the accountant to refund in the Razorpay dashboard or explain. A payment is never spread onto other instalments the parent did not choose.
- **Receipt date (P8, FR-FEE-022).** The capture's IST date, unless that day was closed (then today); if today is closed too, the event waits for the next day.
- **Refunds (decision 10).** `receipt` = the refund id is the idempotency reference on Razorpay's refund API (documented as treated as an idempotency key); the voucher waits for `refund.processed` or the hourly re-fetch.
- **Minimisation (decision 8).** The webhook body is reduced to ids, statuses and amounts before it is encrypted into the inbox; payer contact, email, VPA, card and bank never reach the database or logs.
- **Rotation (FR-PAY-014).** The previous webhook secret verifies for 7 days (`payments.yaml`), then is ignored (and erased on the next rotation or disconnect).
- **Permission for reconciliation.** `fee.reconcile` belongs to wave R2-2E; the online list is read with `finance.read` or `fee.manage`, and items are explained with `fee.manage` (the same principal and accountant), so both waves merge without a duplicate key. **At the merge with R2-2E (2026-10-18)** the online list and summary moved to `fee.reconcile` or `finance.read`, explaining an item to `fee.reconcile`, and the morning exceptions notification to `fee.reconcile` holders (the same principal and accountant; owner and auditor keep reading through `finance.read`).

Sources checked 2026-10-18 for this wave: webhook signature, event id, ordering and rotation (https://razorpay.com/docs/webhooks/validate-test/); payment link payloads with `order_id` and `reference_id` = order `receipt` (https://razorpay.com/docs/webhooks/payloads/payment-links/); create a payment link (https://razorpay.com/docs/api/payments/payment-links/create-standard/); fetch a payment (https://razorpay.com/docs/api/payments/fetch-with-id/) and payments (https://razorpay.com/docs/api/payments/fetch-all-payments/, `count` ≤ 100); refunds and the `receipt` idempotency reference (https://razorpay.com/docs/api/refunds/create-normal/); settlement recon (https://razorpay.com/docs/api/settlements/fetch-recon/); OAuth authorize, token, refresh and revoke (https://razorpay.com/docs/partners/technology-partners/onboard-businesses/integrate-oauth/integration-steps); partner webhooks (https://razorpay.com/docs/api/partners/webhooks/create.md; update and delete by webhook id are assumed to be `PATCH`/`DELETE` on the same path, to be confirmed with Razorpay during partner onboarding).

## Related requirements

FR-FEE-001..030, FR-PAY-001..016 (docs/03 §3.19-3.20), SEC-034..038, PRV-022..024; US-2101..US-2112 (docs/02 C21). Docs: 01 §7.3, 02 §9, 05 §15.1-15.2, 07 §3 TB10, §6.7, 08 §15, 09 §4 "Fees", "Webhooks", 10 §11.2, 14 R2, 17 §5.8, 18 §3.

## Sources (checked 2026-10-14)

- RBI, Regulation of Payment Aggregators Directions, 15 Sep 2025: https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=12896 ; summary: https://indiacorplaw.in/2025/10/09/decoding-rbis-overhaul-of-the-payment-aggregator-directions/
- Razorpay Technology Partners: https://razorpay.com/docs/partners/technology-partners/
- Razorpay OAuth integration steps (authorize, token, refresh, revoke, scopes, `account.app.authorization_revoked`): https://razorpay.com/docs/partners/technology-partners/onboard-businesses/integrate-oauth/integration-steps
- Razorpay partner webhooks API (create a webhook on a sub-merchant account): https://razorpay.com/docs/api/partners/webhooks/create.md
- Razorpay Payment Links, create standard link: https://razorpay.com/docs/api/payments/payment-links/create-standard/
- Razorpay webhooks, validate and test (signature, `x-razorpay-event-id`, ordering): https://razorpay.com/docs/webhooks/validate-test/
- Razorpay UPI charges (MDR vs platform fee): https://razorpay.com/blog/upi-charges-explained-mdr-vs-platform-fees/
- Research notes: docs/research/2026-10-market/notes/parent_comms_fees.md §4-§6

## Follow-up (2026-10-21, wave R2-4)

- Decision 13 (never withhold a TC for dues): the TC preview's information note is built (FR-CERT-025, ADR-0045 follow-up).
- Ask the school reads the fee ledger through `fees.service.ask_student_fees` / `ask_school_fees` while fees are on (FR-KB-044; `finance.read` school-wide only; Tally not consulted).
- "Send payment link" from the dues list and the fee account sends the parent dues page, whose "Pay online" creates the link through the parent page's path (FR-PAY-005; FR-MSG-027; ADR-0043 follow-up). Who may send is unchanged (D33).
