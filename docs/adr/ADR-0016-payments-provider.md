# ADR-0016: Payments provider

| Field | Value |
|---|---|
| Status | **Accepted** 2026-10-14 (owner decision D5 of 2026-10-10: Razorpay first; docs/18 §8). Decision 1 stands; decision 2's adapter is still built only after its listed reviews |
| Date | 2026-09-26 |
| Deciders | Founder (pending privacy review) |
| Amends / supersedes | none · related: [ADR-0042](ADR-0042-fee-ledger-receipts-and-payment-links.md) (parents paying schools, a different payer and module) |

## Context

ADR-0015 makes SchoolOS a recurring subscription. Schools in Andhra Pradesh usually pay suppliers by bank transfer (NEFT/RTGS/IMPS), UPI or cheque, often after an internal approval, and may deduct TDS. Online card/UPI collection through a payment aggregator would reduce manual reconciliation, but it adds a **new sub-processor**, which needs a privacy review and a sub-processor register update before use (08 §1; 14 §5).

Billing data is **school business data**: the school's legal name, GSTIN, billing address, billing email and phone, and a billing contact's name. It is **never student data**. For this data SchoolOS acts on its own behalf (its customer relationship), not as the school's processor (08 §14).

## Decision (proposed)

1. **M0: manual payments only.** A `PaymentProvider` interface exists in the `platform` module with one implementation, `manual`: a `billing_admin` records a bank/UPI/cheque payment (amount, date, reference such as UTR, TDS deducted) against an issued invoice (FR-PLT-018). No provider SDK, no card data, no webhooks.
2. **Candidate adapter: Razorpay** (an Indian payment aggregator), to be built only after:
   - a privacy review of what data the provider receives (school billing contact and invoice amounts only; no student data),
   - confirming its RBI payment-aggregator authorisation and data-location terms at decision time,
   - adding it to the sub-processor register and notifying schools per the DPA (08 §9),
   - a security review (hosted checkout only; webhook signature verification; no card data touches our servers; idempotent webhook handling).
3. If accepted, the adapter records payments in `platform.payments` with `provider = 'razorpay'` and the provider's payment ID; invoices and GST handling stay in SchoolOS.

## Consequences (if accepted)

- Good: faster collection; fewer manual reconciliations.
- Bad: new sub-processor, webhook endpoint and failure modes; fees per transaction.
- Neutral: manual payments remain available for schools that prefer bank transfer.

## Alternatives considered

| Option | Why not chosen (so far) |
|---|---|
| Manual payments only, forever | Works at Stage 0–1; costly in staff time at scale |
| Other Indian payment aggregators (e.g., Cashfree, PayU) | Comparable; evaluate alongside Razorpay at decision time |
| Store card or bank-mandate data ourselves | Out of scope; PCI DSS burden; never |

## Related requirements

FR-PLT-015..019, PRV-012; 08 §1, §9, §14; 16 §5.9, §10.

## Amendments (2026-10-14)

- **D5 recorded.** The product owner chose Razorpay first (docs/18 §8, D5). This ADR is accepted with Razorpay as the provider for **SchoolOS's own subscription invoices** (the school pays SchoolOS; SchoolOS is the merchant of its own Razorpay account; `platform` module only). Nothing else in the decision changes: manual payments stay the default, and the adapter waits for the privacy, RBI-authorisation, sub-processor and security reviews of decision 2.
- **Not the fee module.** Parents paying fees to schools is decided separately in [ADR-0042](ADR-0042-fee-ledger-receipts-and-payment-links.md): the school is the merchant, the tenant module `app/fees` holds its own provider interface, and no code is shared with `platform` (CLAUDE.md §4: `platform` never imports tenant modules, and tenant modules never import `platform`).
