# SchoolOS subscription terms (template)

| Field | Value |
|---|---|
| template_version | 0.1 (2026-10-21) |
| Placeholders | `{{school_legal_name}}`, `{{schoolos_legal_name}}`, `{{plan_name}}`, `{{one_time_fee}}`, `{{monthly_fee}}`, `{{tier}}`, `{{effective_date}}`, `{{governing_law_place}}`, `{{payment_terms}}` |
| Based on | ADR-0015 (managed SaaS, shared and dedicated tiers), ADR-0038 (one-time fee, monthly subscription, AI answer bundles), docs/16 (billing, suspension, offboarding), docs/08 §14 |

> **Not legal advice.** Drafted by SchoolOS (D30). Business terms between SchoolOS and the school; the processing of school data is governed by the Data Processing Agreement (`dpa-template.md`). Items marked **(to confirm)** need a professional reviewer.

---

1. **Service.** SchoolOS provides {{school_legal_name}} the SchoolOS service on the {{tier}} tier, plan {{plan_name}}, as a managed service operated by SchoolOS (ADR-0015).
2. **Fees.** A one-time fee of {{one_time_fee}} and {{monthly_fee}} per month plus GST, and any AI answer bundle the school chooses, with overage at the bundle's rate (ADR-0038). Invoices are GST invoices; payment terms {{payment_terms}} **(to confirm)**.
3. **Messages and payments cost.** WhatsApp messages are paid by the school to Meta directly (D7); payment-provider charges are paid by the school to its provider. SchoolOS never holds the school's or parents' money (ADR-0042).
4. **Suspension.** For non-payment SchoolOS may suspend the service after notice; while suspended the school keeps access to its full data export, plan and billing (docs/16 §5.5). Suspension is never automatic and never during a declared exam window without the school's approval (docs/07 T26).
5. **Ending the subscription.** The school takes its full data export; SchoolOS deletes the school's data within 30 days and issues a certificate of deletion (DPA clause 9).
6. **The school's responsibilities.** Lawful basis, notices to parents and staff, accuracy of what it enters, its staff's accounts and devices, its own portal submissions (SchoolOS prepares and checks; the school submits), and its own legal documents and registers.
7. **What SchoolOS does not do.** It does not give legal advice, does not submit to government portals, does not decide anything about a child automatically, and does not guarantee that a portal or board will accept a record.
8. **Availability and support.** As in the plan; support during school hours; incidents communicated on the status page.
9. **Liability.** **(to confirm: cap, exclusions and indemnities.)**
10. **Changes.** SchoolOS gives notice of material changes to these terms; the version is recorded.
11. **Law.** Indian law; courts at {{governing_law_place}} **(to confirm)**.
