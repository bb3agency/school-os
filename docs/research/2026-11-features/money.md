# Feature research 2026-11: school money (fees, payments, Tally, fee reminders) — prefix FM

| Field | Value |
|---|---|
| Date | 2026-11-09 |
| Area | `app/fees` (ledger, receipts, concessions, late fees, day close, UPI, bank statements, Razorpay), `app/tally` (read-only connector), `app/messaging` (fee reminders and receipts only); web `features/fees`, `features/payments`, `features/tally`, `features/public` (parent dues page) |
| Method | Read CLAUDE.md, docs/01, 02, 14 (§1-2, D1-D83, §5), 18, the 2026-10 market report, ADR-0032, ADR-0042, ADR-0046, ADR-0052, the money audits (`docs/security/audit-2026-11-09-money.md`, `audit-2026-11-08-web2-fees-messaging.md`) and the code. Web checks on 2026-11-09 (sources at the end). Research only: no code was changed |
| Status | Proposals for the owner and lead. Nothing here is decided |

## 1. Summary

The fee module is far more complete than a typical first release. Everything in FR-FEE-001..030 and FR-PAY-001..016 is built and has been through two audit rounds: one gap-free receipt series for every mode, maker-checker on concessions, adjustments, cancellations and refunds, late fees, day close, UPI by UTR with bank-statement confirmation, Razorpay links in the school's own account with webhook reconciliation, WhatsApp reminders and receipts, and parent dues pages.

The gaps are where a school's year does not fit "one plan per class, one account per student per year, today's dues only":

1. **Last year's dues disappear from view after the year changes.** The dues list, the reminders and the counter suggestions read only the current academic year (`apps/api/app/fees/reports.py:338-344`, `apps/api/app/fees/service.py:432-439`). In AP the year turns in June, which is also when most arrears are chased.
2. **Charges that differ per student** (transport by route, hostel, optional activities) do not fit a plan that is set per class and category (`apps/api/app/fees/schemas.py:205-212`).
3. **The accountant still types everything into Tally again.** The Tally XML voucher export is a recorded follow-up of ADR-0042 (line 62) that has not been built. There is also no period, head-wise or demand-collection-balance report for the management or the trust's CA.
4. **Parents who pay by the school's UPI QR from the dues page cannot tell the school.** The page shows the QR (`apps/web/src/features/public/PublicPages.tsx:326-339`), but SchoolOS learns of the payment only when a clerk types the UTR. In practice the parent sends a screenshot on WhatsApp.
5. **Families and sponsors.** Siblings are paid for one child at a time (`ReceiptCreate.account_id`, `apps/api/app/fees/schemas.py:431-442`), and fees paid by a sponsor or a scheme cannot be tracked apart from the parent's dues.

Regulation is quieter than expected. The AP school fee commission (APSERMC) has not issued a school fee order since the High Court struck down GOs 53 and 54 in December 2021, and no 2025-26 or 2026-27 school order was found. Telangana's fee regulation bill was still pending as of May 2026. GST stays exempt for school fees. There is one new tax fact: from 1 April 2026 the tuition-fee deduction is in Section 123 / Schedule XV of the Income-tax Act 2025 (formerly 80C). Parents need a tuition fee certificate for each financial year, which is a cheap and visible feature (FM-06).

## 2. Regulatory and market facts checked (2026-11-09)

| Topic | Finding | Confidence |
|---|---|---|
| AP fee regulation (APSERMC) | The AP School Education Regulatory and Monitoring Commission Act 2019 lets the commission regulate fees and inspect schools. Its 2021-24 block fee order (GOs 53/54) was struck down by the AP High Court on 27 Dec 2021: the state may regulate fees but not fix them by geography. Later the commission warned schools against collecting fees in advance for the next year and against collecting more than the fixed fee. No 2025-26 or 2026-27 school fee order found. July 2026 news covers appointments to the higher-education commission (APHERMC), not the school one | Medium: news sources, no official notification found for 2025-26 or 2026-27. **Unverified: whether a new school block period is being prepared** |
| Telangana | The draft "Private Unaided School Fee Regulatory and Monitoring Commission Bill" (education commission draft of 24 Jan 2026) would cover pre-school to Class 12, all boards. Proposals range from 8% per two years to 10-15% per three years. It was slated for Cabinet in May 2026; passage not confirmed | Medium. **Unverified: Cabinet or Assembly outcome** |
| Advance collection | APSERMC warned against collecting next year's fees in advance. This matches owner decision D18 (no advances) | Medium |
| GST | Notification 12/2017-CT(R) entry 66 exempts services by educational institutions up to higher secondary. Advance rulings deny the exemption for transport when parents pay the operator directly; transport the school itself provides or buys is treated differently. Books and uniforms sold by a school are a supply of goods (not checked in detail) | Medium (advance rulings bind only their applicants). SchoolOS already refuses taxable heads at the counter (ADR-0042 P6) |
| TDS / TCS | Nothing found that applies to receiving school fees. TDS arises on the school's own payments to vendors and staff, which is Tally's job | Medium. **A CA should confirm** |
| Income tax: tuition fees | The Income-tax Act 2025 is in force from 1 Apr 2026. The tuition-fee deduction (formerly 80C) is in Section 123 / Schedule XV: old regime only, ₹1.5 lakh overall cap, two children, tuition only (not admission, transport or books under the 1961 rules) | Medium: secondary sources agree. **Unverified against the official Schedule XV text** |
| RTE 12(1)(c) in AP | Lottery on cse.ap.gov.in. 2026-27 phase 1 allotments reported to schools 26 Mar-7 Apr 2026. Reimbursement is the lower of per-child state cost and the fee charged. AP figures reported as ₹8,000 urban, ₹6,500 rural and ₹5,100 tribal per year (date of the order unknown). Schools have challenged the GO, and a Jan 2026 report says schools still refuse RTE admissions | Low-medium. **Unverified: current AP per-child rate, claim cycle** |
| Thalliki Vandanam (AP) | ₹15,000 per child per year (about ₹13,000 paid to the mother, about ₹2,000 kept for school maintenance) for Classes 1-12 including recognised private schools, with 75% attendance. Released around 22 Jul 2026 (G.O.Ms 15/16 of 16 Jul 2026 reported) | Low-medium (aggregator sites). Relevant to timing: many parents pay the first instalment after this payout |
| RBI payment aggregators | PA Directions of 15 Sep 2025: holding merchants' funds needs authorisation. The school-as-merchant model (ADR-0042) stays right | High (already in docs/18) |
| UPI dynamic QR | NPCI's merchant QR rules treat `tr` (reference) and `am` (amount) as critical in a dynamic QR and expect them to be passed by banks. For a plain bank static QR (P2P or small merchant) it is unclear whether a note or reference reaches the bank statement | Low. **Unverified** (see "not worth building") |
| WhatsApp pricing | Per-message pricing since 1 Jul 2025. Sources disagree on whether utility templates inside the 24-hour window are free after 30 Sep 2026. INR billing migration deadline is 31 Dec 2026 | Medium. **Check Meta's rate card before quoting costs to schools** |

## 3. What exists today (inventory)

| Capability | Where | Notes |
|---|---|---|
| Heads (kinds tuition/admission/exam/books/transport/other, `gst_exempt`) | `apps/api/app/fees/schemas.py:27,146-171` | Taxable heads are planned but never collected (P6) |
| Plans per year × class × optional category, instalments ≤ 24, late-fee rule, versions with differences and adjustments | `schemas.py:178-260`, `setup.py` | One plan per account; no per-student add-ons |
| Accounts per student per academic year, append-only ledger, statement | `ledger.py`, `api.py:342-361` | |
| Counter receipts (cash, cheque, bank transfer, UPI QR) with allocations, gap-free `RCT/FY/n` | `receipts.py`, `schemas.py:431-442` | One account per receipt |
| Concessions (sibling, staff_ward, merit, hardship, rte, other), adjustments, cancellations, refunds: maker-checker and step-up | `approvals.py` | Concessions are entered by hand per account |
| Late fees nightly, waivers | `late_fees.py` | |
| Daily report, print, day-book CSV, day close | `reports.py:67-330` | No period, head-wise, DCB or concession register report |
| Dues list and CSV, current year only | `reports.py:330-480` | |
| UPI settings and static QR (no amount) | `upi_qr.py:27-34` | |
| Bank statement import, UPI UTR matching only | `statements.py:1-24` | Cheques and NEFT credits are not matched |
| Razorpay connection (OAuth or manual keys), links, webhook inbox, reconciliation, online refunds | `online.py`, `api_online.py` | Single provider |
| Parent pages: receipt, dues with "Pay online" and UPI QR | `apps/api/app/public/pages.py:148-167`, `PublicPages.tsx:326-339` | Read-only (ADR-0046) |
| WhatsApp fee reminders (offsets −3/0/+7, minimum amount, 10:00), manual bulk reminders and payment links from the dues list, receipt messages | `messaging/config.yaml:83-90`, `fees/service.py:424-461` | Current year only |
| Ask fee tool on the ledger | FR-KB-044 | |
| Tally: read-only edge agent, parties, bill-wise dues, links to students | `apps/api/app/tally/`, ADR-0032 | Never writes to Tally (ADR-0032:57). The ledger is not reconciled with Tally (ADR-0042 decision 12) |
| RTE register | ADR-0052 | Designed, not built (D10, D43) |

## 4. Proposals

### FM-01 Previous-year dues (arrears) carried into the new year

- **Users:** accountant, principal, correspondent; parent (dues page).
- **Problem and evidence.** Fee accounts are per academic year. The dues list (`reports.py:338-344`) and the automatic reminders (`service.py:432-439`) read only `tenancy.get_current_academic_year`. When the school moves to 2027-28 in June, unpaid 2026-27 balances drop out of the dues list, the reminders and the principal's totals, while the old account stays open. Arrears are the largest single collection problem in budget schools: they are what a correspondent asks about at the start of the year and what the CA asks about at year end. There is no "students who left with dues" view either. The TC must not be withheld (ADR-0042 decision 13, D16), so the school needs another, lawful way to follow those dues up.
- **Exists today:** per-year accounts with an append-only ledger. The old account remains collectable at the counter if a clerk finds it.
- **Proposal:**
  - Dues list, CSV and summary tiles cover every open account of the student (current year plus earlier years), with a "year" column and an "arrears only" filter.
  - Automatic reminders include overdue instalments from earlier years. One message per student shows the total, not one message per year (new template variable, or reuse `fee_reminder` with the total).
  - The counter and the parent dues page show "Earlier years" first and allocate oldest first (already the default within an account; extend it across accounts with an explicit choice).
  - "Left with dues" report: students whose status is withdrawn or TC-issued with an open balance, for management follow-up. Never printed on a TC and never blocking one.
  - Write-off as a maker-checker adjustment with reason `write_off` (step-up, principal or accountant). The ledger keeps it, and it appears in FM-04 reports.
- **BRD objective:** fewer counter interruptions and reliable money records (BRD P5 and the accountant persona, §5 "fee questions answered without re-entry").
- **Invariants and compliance:** inv. 3 (scoped; `finance.read` school-wide only), inv. 7 (write-off audited), D16 (TC never withheld). No ADR, but an ADR-0042 follow-up note for the write-off reason and the cross-year allocation.
- **Dependencies:** none outside fees and messaging.
- **Effort:** M (2 weeks).
- **Value:** **H.** Without it the module's numbers are wrong from the first June after go-live, which is the first month pilot schools will judge it.
- **Risks:** a parent's message showing old-year dues for a child who has since left. Send only to opted-in guardians of current students, and keep left students out of automatic reminders (manual only).
- **Owner questions:** may automatic reminders cover earlier-year dues, or only manual sends? Is "write-off" acceptable wording, or should it be "waived by management"?

### FM-02 Per-student add-on charges (transport by route or stop, hostel, optional activities)

- **Users:** accountant, office admin; transport in-charge (reads only).
- **Problem and evidence.** A plan is per class and optional category (`schemas.py:205-212`), and one account has one plan. Transport is the most common fee that differs between students of the same class (by distance band or stop). Today a school must create a category per band, which multiplies plans by classes, or post manual adjustments one by one, each needing maker-checker. Transport as an operation (routes, GPS) is out of scope (docs/18 §3 "Kept out of scope"). Its **fee** is not.
- **Exists today:** head kind `transport` exists. There are no per-student assignments.
- **Proposal:**
  - "Add-on fee" templates per year: head, amount per instalment, a label such as "Band 2 (5-10 km)" or "Hostel".
  - Assign or unassign students in bulk (class, section, or a picked list) with a start instalment. Assignment posts charges to the account through the ledger. Removal mid-year credits future instalments only (no refund without the refund flow).
  - Per-student lists for the in-charge: who is on which band, with no amounts unless the user has `finance.read`.
  - Shown on the counter, receipts, parent dues page and FM-04 reports as normal lines.
- **BRD objective:** fee ledger completeness (D4). One system for every fee the school receives instead of a separate transport book.
- **Invariants and compliance:** inv. 6 does not apply (fees are not official identity records). Inv. 7: assignments audited. GST: school-provided transport stays exempt only if the school itself supplies or contracts it. If a parent pays an operator directly, that is not a school receipt (advance rulings, §2). Note in the setup help text.
- **Dependencies:** plan versions logic (`setup.py`) must treat add-ons as separate charge sources.
- **Effort:** M (2-3 weeks).
- **Value:** **H** for schools with buses or hostels (most 500+ student AP schools). Without it they keep a second register.
- **Risks:** interaction with plan versions and late fees. Keep add-on charges as their own `source` in the ledger.
- **Owner questions:** should a mid-year change of band need maker-checker (it changes money owed), or only the reduction?

### FM-03 Tally voucher export (XML import file) and ledger-vs-Tally check

- **Users:** accountant; the trust's CA.
- **Problem and evidence.** The accountant types every SchoolOS receipt into Tally again. Only the day-book CSV exists (ADR-0042 decision 12). "Tally XML voucher export" is a listed follow-up (ADR-0042 line 62). Double entry is the main reason accountants resist a second system (BRD: "replaces work, not systems").
- **Exists today:** day-book CSV (`reports.py:236`). The Tally edge agent is read-only by design (ADR-0032:57).
- **Proposal:**
  - Mapping screen: each head → a Tally income ledger; each mode → a cash or bank ledger; the student → a party ledger (reuse the existing Tally party links where present, otherwise a single "Fee receivable" ledger per school choice).
  - Download per day or date range: a TallyPrime XML `Import` envelope of Receipt vouchers (and Journal vouchers for cancellations and refunds) that the accountant imports in Tally himself. The **edge agent stays read-only**: it never posts. Each voucher carries the SchoolOS receipt number as voucher number or narration, so a second import is spotted.
  - Optional check after the next agent sync: the totals by ledger and day that Tally has, compared with SchoolOS, listing days that differ (read-only, no correction).
  - Only closed days can be exported (day close, FR-FEE-019), so a file never changes after it is made.
- **BRD objective:** "works alongside Tally" and less re-entry for the accountant (BRD §5 accountant).
- **Invariants and compliance:** the agent's no-write rule stays unchanged. The XML file holds student names as party names: it is a data export, audited as `fee.report.downloaded`, and kept out of logs (inv. 5). Formula-safe is not relevant (XML), but use XXE-safe generation and escape names. No new sub-processor.
- **Dependencies:** a test company in TallyPrime to check the import (a pilot accountant). Tally ledger names must exist before import.
- **Effort:** M (2-3 weeks including a mapping screen and import trials).
- **Value:** **H.** It turns the accountant from a veto into a supporter, and Tally coexistence is the product's stated position.
- **Risks:** Tally import errors on unknown ledgers (the file must create nothing implicitly). Version differences between Tally ERP 9 and TallyPrime.
- **Owner questions:** one party per student, or one receivable ledger? (This is a CA and accountant preference; ask a pilot.)

### FM-04 Accountant and management reports (period, head-wise, DCB, registers)

- **Users:** accountant, principal, correspondent and trust, read-only auditor, the trust's CA.
- **Problem and evidence.** Reports are daily only (`reports.py:67-330`), plus dues and UPI. Management asks month-end and year-end questions: collected by head and mode for a period, the demand-collection-balance (DCB) per class, concessions given by type (sibling, staff ward, RTE…), refunds and cancellations, and outstanding by age. Trusts registered under the Income-tax Act need audited accounts. The CA asks for registers that today exist only as approvals screens.
- **Exists today:** daily report and print, day-book CSV, dues CSV, UPI report, approvals list.
- **Proposal:**
  - Collection register for a date range: by day, head, mode and collector, print (A4) and CSV.
  - DCB by class and section for a year: demand (charges), concessions, adjustments, collected, balance.
  - Concession register and refund/cancellation register (approved items with requester, approver and reason codes; free text excluded from CSV by default).
  - Outstanding ageing (0-30, 31-90, 90+ days, earlier years from FM-01).
  - A "year-end pack" ZIP for the auditor role: the above for the financial year, plus the receipt series' first and last numbers and any gaps (there should be none).
- **BRD objective:** management visibility (correspondent: "visibility, risk reduction"), audit readiness.
- **Invariants and compliance:** `finance.read` school-wide. CSVs formula-safe, audited downloads. No cross-school data (inv. 14).
- **Dependencies:** FM-01 for earlier-year ageing (can ship without it).
- **Effort:** M (2 weeks). Reads only, with no schema change except optional indexes.
- **Value:** **H** for the buyer (correspondent and trust) at low risk.
- **Risks:** heavy queries on large schools. Run as SQL aggregates on `fin` with indexes, and cap the date range at one financial year.
- **Owner questions:** which three reports does the pilot school's CA ask for first? (Ask in pilot interviews.)

### FM-05 Parent "I have paid by UPI" report on the dues page (UTR claim → staff confirmation)

- **Users:** parent; accountant or office clerk.
- **Problem and evidence.** The parent dues page shows the school's UPI QR (`PublicPages.tsx:326-339`). After paying, a parent has no way to tell the school, so they send a screenshot on WhatsApp or come to the counter, which defeats the point. SchoolOS learns of the payment only when a clerk types the UTR (`statements.py:1-24`, D19).
- **Exists today:** UTR check (`api_upi.py:92`), bank statement matching for UPI receipts, and the UPI exceptions list.
- **Proposal:**
  - On the dues page after "Pay by UPI": a small form for the 12-digit UTR, the amount and the instalments paid. No other data is collected.
  - This creates a **claim**, not a receipt. Claims appear in a staff queue. When a bank statement credit with the same UTR and amount is imported, the claim is proposed for one-click confirmation, which issues the receipt (dated the day of confirmation, as no backdating, D20). A person always confirms (inv. 9 spirit, D19).
  - The parent page shows "Payment reported, waiting for the school to confirm". The receipt message goes out when issued (existing flow).
  - Rate limits and one open claim per UTR. A UTR already on a receipt is refused, as at the counter.
- **BRD objective:** P5 (parents queue at the counter for fees), and zero-MDR UPI kept first-class (ADR-0042 consequences).
- **Invariants and compliance:** this is the **first write by a parent on a public page**. ADR-0046 made parent pages read-only, so this needs an **ADR amendment to ADR-0046/0047** and a public-route entry in `public_routes.yaml`, plus a pen-test scope update (NFR-SEC-007). The UTR field is exempt from the Aadhaar mask as at the counter (PRV-015). No new data class beyond a UTR and amount. A privacy review is light, but it is recommended.
- **Dependencies:** pilot evidence that parents use the QR on the page (FM questions in docs/18 §5 interviews).
- **Effort:** M (2 weeks, including the public route hardening).
- **Value:** **H** where schools use UPI QR (the research says budget schools prefer it).
- **Risks:** abuse (fake claims). This is harmless because nothing is issued without a statement match or a person, but it adds queue noise; rate-limit per link. Receipt date versus payment date: the receipt says "paid on" from the bank line (data), "issued on" today.
- **Owner questions:** reopening the read-only rule for parent pages for this single form. Yes or no?

### FM-06 Annual tuition fee certificate for income tax (Section 123 / Schedule XV, formerly 80C)

- **Users:** parent; office clerk (fewer counter requests); accountant.
- **Problem and evidence.** Salaried parents on the old tax regime ask the school every January to March for a certificate of **tuition fees paid in the financial year** (only the tuition part counts: §2). Schools type these by hand from receipts. The receipts' FY series (P2) already makes the period exact.
- **Exists today:** receipts with allocations per head. Head kind `tuition` exists (`schemas.py:27`).
- **Proposal:**
  - A head setting "counts as tuition for tax" (default on for kind `tuition`).
  - Certificate per student per financial year: tuition paid (allocations of issued, not cancelled, receipts, less refunds of those lines), receipt numbers and dates, school details, signature block, serial number (reuse the certificates serial mechanism or a fees series).
  - Bulk generation for a class, a print view, and a link on the parent receipt or dues page (the existing parent link carries a "Tax certificate FY 2026-27" button from 1 Jan).
  - Optional WhatsApp utility message in January ("Your fee certificate for FY 2026-27 is ready") through an existing template category, if Meta approves it as utility.
- **BRD objective:** P5 (counter interruptions), parent value at low cost.
- **Invariants and compliance:** wording reviewed by a CA ("not tax advice"; the school certifies amounts paid, not eligibility). Section numbering is **unverified against the official Schedule XV text**: keep the legal reference in config (inv. 13). No new data.
- **Dependencies:** none.
- **Effort:** S (about 1 week).
- **Value:** **M-H.** Every fee-paying school gets these requests every year. It is cheap and makes the fee module visible to parents.
- **Risks:** a parent claiming non-tuition amounts. The certificate lists tuition only and says so.
- **Owner questions:** issue on request only, or automatically for all accounts after 31 March?

### FM-07 Family counter and sibling handling

- **Users:** office clerk, accountant; parent.
- **Problem and evidence.** One receipt covers one account (`schemas.py:431-442`). A parent with two or three children pays at one visit, and the clerk repeats the search, allocation and print for each child. Payment links are per account too, so a parent gets one link per child. The sibling concession is entered by hand per account (concession type `sibling`, `schemas.py:35`), so it is easy to forget for a new admission.
- **Exists today:** guardians are linked to students in `students`. Tally family ledgers are already shared between siblings (D54).
- **Proposal:**
  - At the counter, "Siblings" shows the other children of the same guardian (only students the clerk can see). One payment form issues one receipt per child in a single transaction (same mode details, consecutive numbers), with one combined print or hand-over slip.
  - "Family payment link": one Razorpay link covering the chosen instalments of several siblings. The webhook posts one receipt per account (the intent holds the split, computed server-side).
  - Sibling concession **suggestion**: when a plan is applied or a student is admitted with a sibling already enrolled, a pending concession request of the school's configured sibling rule is drafted for a person to submit and approve (maker-checker; never auto-approved).
- **BRD objective:** counter time (P5); correct concessions.
- **Invariants and compliance:** inv. 3 (siblings shown only within the clerk's scope), inv. 6 (no auto-correction: the suggestion is a draft), D17 maker-checker. The family link sends no names to Razorpay (ADR-0042 decision 8 unchanged).
- **Dependencies:** guardian links reliable enough (imports often lack them; the DQ module could flag shared phone numbers but must not merge).
- **Effort:** M (2-3 weeks). The family link is the larger part and could follow later.
- **Value:** **M-H.** Sibling families are common in AP private schools (unverified share; ask pilots).
- **Risks:** a wrongly linked guardian shows another family's child. Show siblings only where the guardian link is confirmed (not suggested).
- **Owner questions:** should the sibling rule (for example 10% on tuition for the second child) be a school setting at all, or always hand-entered?

### FM-08 Bank statement matching for cheques and bank transfers; cheque clearing status

- **Users:** accountant.
- **Problem and evidence.** Statement import matches UPI receipts only (`statements.py:1-24`). Cheque and NEFT/IMPS receipts stay "issued" with no sign of whether the money arrived. A bounced cheque is discovered only when the accountant reads the statement by eye, after which it becomes a `cheque_dishonoured` cancellation (P9). Parents also pay by NEFT directly into the school account without coming in, and these credits are invisible.
- **Exists today:** statement import with presets, unmatched-credit list, cheque mode with number, bank and date.
- **Proposal:**
  - Matching rules for cheques (cheque number in the narration plus amount, within N days) and bank transfers (reference plus amount). They propose only, and a person confirms, as for UPI.
  - Cheque status on the receipt: "Deposited / Cleared (seen in statement) / Returned". "Returned" opens the existing cancellation request with reason `cheque_dishonoured` prefilled.
  - An unmatched-credits list for credits without any receipt (a direct NEFT by a parent), where staff can start a counter receipt from the credit (amount and reference prefilled, dated today, D20).
  - Cheque deposit slip print (list of the day's cheques) for the bank.
- **BRD objective:** reconciliation (accountant), fewer surprises at audit.
- **Invariants and compliance:** narration stays encrypted and erased after 90 days (as built). No new data class.
- **Dependencies:** the narration formats of the preset banks (pilots' banks).
- **Effort:** M (about 2 weeks).
- **Value:** **M.** It matters most for mid-fee schools where cheques are common.
- **Risks:** narration formats vary by bank, so keep rules per preset and never auto-confirm.
- **Owner questions:** none beyond pilot bank formats.

### FM-09 Defaulter follow-up log (calls, promises to pay, pauses)

- **Users:** accountant, principal; correspondent (summary only).
- **Problem and evidence.** The dues list (`reports.py:330-400`) and reminders exist, but follow-up is a phone call recorded nowhere. Schools keep a paper "defaulters' register" with call notes and promised dates. Reminders keep going to a family that has agreed a date, or that is in hardship, which damages goodwill.
- **Exists today:** dues list, manual and automatic reminders (switches per school, D34), last payment date, Messages tab on the account (FR-MSG-028).
- **Proposal:**
  - Per account: follow-up entries (date, channel, outcome code such as "promised", "hardship", "no answer", short note), a "promised by" date, and a "pause automatic reminders until" date.
  - A follow-up queue: promised dates passed, no contact in 30 days, high balance first.
  - Hardship can lead to a concession request (existing maker-checker), never an automatic waiver.
  - Explicit limits written into the screen help: no public lists, no naming children in class, no withholding of TC, exams or results (D16; RTE Act section 17 on harassment and screening, and child-protection norms).
- **BRD objective:** collection without office chaos (accountant, principal).
- **Invariants and compliance:** inv. 5 (notes never logged; notes are C2 personal financial data, with the Aadhaar refusal on free text, PRV-015). Inv. 14 (no lenders or financing referrals: ADR-0042 decision 14). Visible only to `finance.read`; never to class teachers or in Ask answers for others. A **privacy note** is needed in docs/08 (a new kind of free-text note about a family's finances). It is not a new data class, but it is sensitive, so add a retention rule (delete notes N years after the account closes).
- **Dependencies:** FM-01 for earlier-year dues.
- **Effort:** S-M (1-2 weeks).
- **Value:** **M.**
- **Risks:** notes with judgemental wording. Use outcome codes plus a short note with a length cap.
- **Owner questions:** should the pause be per account or per guardian (siblings)?

### FM-10 Fee structure notice and year-on-year change summary

- **Users:** correspondent, principal; parents (notice board, admission enquiries); inspectors.
- **Problem and evidence.** Parents and regulators expect the approved fee structure to be displayed (parent groups in AP asked for display on notice boards; CBSE Appendix IX publishes fees by class, already built for CBSE schools via ADR-0051). Fee hikes are the trigger for regulator attention in both AP and Telangana (§2). Management also needs to see the percentage change against last year before activating plans.
- **Exists today:** `fees.service.fee_structure(session, academic_year_id)` (`service.py:224`), used by the disclosure module.
- **Proposal:**
  - Printable A4 "Fee structure 2027-28" by class (heads, annual amount, instalments and due dates, late-fee rule, refund policy text from settings). It can also be published as a parent-facing page through the existing public handles, read-only.
  - "Change against last year" view while plans are drafts: per class and head, amount and %, with a configurable warning threshold (for example 10%; the value is school config, not a legal rule).
  - Optional: attach the management's approval minute (a document) to the activation of a year's plans.
- **BRD objective:** compliance readiness, trust with parents.
- **Invariants and compliance:** no personal data. A published page uses ADR-0047 handles (an existing kind may suffice, as with `school_disclosure`; check whether a new handle kind needs an ADR amendment).
- **Dependencies:** none.
- **Effort:** S (about 1 week).
- **Value:** **M.** It is cheap and useful if AP or Telangana regulation returns, and it sells to correspondents.
- **Risks:** implying legal compliance. The wording must say "the school's fee structure", not "approved by the commission".
- **Owner questions:** none blocking.

### FM-11 Sponsor and scheme payers (management scholarships, trust or CSR sponsors)

- **Users:** accountant, correspondent.
- **Problem and evidence.** Some fees are paid not by the parent but by a sponsor: a church or trust scholarship, a company's CSR, a management scholarship paid from another account, or a sports quota. Today these are either a concession (which hides that money is expected from a third party) or a receipt in the student's name with no payer. `fees/subjects.py:14` notes that receipts record no payer at all.
- **Exists today:** concession types (`merit`, `other`), receipts per account.
- **Proposal:**
  - Payers list (sponsor name, kind). A sponsorship on an account covers heads and instalments up to an amount and moves those lines to "expected from sponsor" (no reminders to parents for them).
  - Sponsor statement: students covered, amounts expected and received. Receipts for sponsor payments can cover several students (one receipt per account, grouped as in FM-07).
  - In FM-04 reports, sponsor receivables are a separate column.
- **BRD objective:** ledger completeness; management visibility.
- **Invariants and compliance:** a sponsor is an organisation (no new personal data class). Inv. 14: no data shared with sponsors beyond what the school itself sends. SchoolOS sends nothing to sponsors.
- **Dependencies:** FM-07 grouping (optional).
- **Effort:** M (2 weeks).
- **Value:** **L-M.** It is important for mission and trust schools, and rare elsewhere. **Validate with pilots before building.**
- **Risks:** scope creep into accounts receivable, which is Tally's job. Keep it to "who pays this line".
- **Owner questions:** is this common among target schools? (Interview question.)

### FM-12 RTE reimbursement tracking (reopen proposal: money part of ADR-0052 only)

- **Users:** accountant, correspondent.
- **Problem and evidence.** RTE 12(1)(c) children must not be charged, and the state reimburses the school (AP per-child figures reported at ₹8,000 / ₹6,500 / ₹5,100 per year; unverified date). Reimbursement claims and receipts are a recurring money question for management, and outstanding state dues are material for a school with many RTE admissions. ADR-0052 is designed but not scheduled (D10, D43).
- **Exists today:** concession type `rte` with maker-checker (`schemas.py:35`). ADR-0052 design for admissions and claims (C3 restricted).
- **Proposal (reopen proposal, label: needs the owner to lift D10 in part):**
  - Build only ADR-0052 decision 3 (claims per year and period: claimed, received, rejected) and decision 4 ("Request RTE concession"), with the minimum admission record needed to list claim lines (allotment reference, category).
  - The claim export lists students by admission number only. RTE status stays C3 (`rte.read`), never in prints, messages, parent pages or Ask (ADR-0052 decision 5).
  - Show state receivable in FM-04 reports as one total (no names).
- **BRD objective:** management money visibility; compliance record.
- **Invariants and compliance:** ADR-0052 already covers visibility. **Privacy review** (C3 sensitive status of a child). The `ap.yaml` rules are unverified until a school confirms them.
- **Dependencies:** owner decision to reopen D10, and a pilot school with RTE admissions.
- **Effort:** M (2-3 weeks for claims plus the minimum register).
- **Value:** **M**, uncertain: AP RTE implementation is contested and many schools have few RTE children (§2).
- **Risks:** building to unverified state rules.
- **Owner questions:** reopen D10 for the money part only? Is there a pilot with RTE children?

## 5. Ranked shortlist (value / effort)

| Rank | ID | Name | Value | Effort | Why now |
|---|---|---|---|---|---|
| 1 | FM-01 | Previous-year dues carried into the new year | H | M | The first year change (June 2027) breaks dues totals and reminders for every school with fees on |
| 2 | FM-04 | Accountant and management reports (period, DCB, registers, year-end pack) | H | M | The buyer (correspondent and trust) and the CA judge the module by these; reads only, low risk |
| 3 | FM-06 | Tuition fee certificate for income tax (Sec 123 / Sch XV) | M-H | S | Cheap and visible to every parent; requests peak January-March 2027, inside the pilot season |
| 4 | FM-03 | Tally voucher XML export and ledger-vs-Tally check | H | M | Removes double entry, the accountant's main objection; ADR-0042 follow-up already recorded |
| 5 | FM-02 | Per-student add-on charges (transport bands, hostel) | H | M | Without it, larger schools keep a second fee register for buses |

Next in line: FM-05 (parent UPI report; needs the ADR-0046 amendment), FM-07 (family counter), FM-10 (fee structure notice, S), FM-08, FM-09, FM-11, FM-12.

## 6. Not worth building (now) and why

| Idea | Why not |
|---|---|
| SchoolOS-run payment collection, convenience fee, split settlement or Razorpay Route | RBI PA Directions (licence); ADR-0042 decisions 2 and 14; D8, D28 |
| Fee financing, EMI or loan partner offers | Invariant 14 and ADR-0042 decision 14 (purpose limit); reputational risk with parents |
| Blocking TC, hall tickets, results or class access for dues; public defaulter lists | Against D16 and RTE Act protections; child-protection and DPDP risk. Follow-up stays private (FM-09) |
| UPI Autopay / e-mandates for fees | Needs aggregator subscriptions and mandate handling; conflicts with "no advances" (D18) and APSERMC's warning on advance collection; little evidence of demand in budget schools |
| Dynamic UPI QR with amount and reference for the school's own bank VPA | NPCI treats `tr` and `am` as merchant (P2M) parameters for dynamic QRs; whether a note or reference reaches a small school's bank statement is unverified; static QR plus UTR (built) or FM-05 is safer |
| GST invoicing for taxable heads (uniforms, books, canteen) inside SchoolOS | ADR-0042 P6: Tally invoices them; GST invoicing brings e-invoice and return obligations; keep refusing at the counter |
| TDS or TCS features | Nothing found that applies to fee receipts; TDS on vendor and staff payments is accounting (Tally) |
| Writing vouchers into Tally through the edge agent | ADR-0032 forbids writes (the Tally XML server has no auth); an import file (FM-03) gives the same benefit with a person in control |
| A second payment provider (Cashfree, Easebuzz) now | The interface is ready, but there is no pilot demand yet; Razorpay partner approval (D13) is still open. Revisit after pilots |
| Automatic bank feeds (Account Aggregator) | Needs FIU onboarding and consent flows; a new sub-processor and data class; statement upload is adequate at pilot scale |
| AI-suggested concessions or waivers | Invariant 9 (AI read-only); concessions must be human maker-checker |

## 7. Time-sensitive dates

- **31 Dec 2026:** Indian WhatsApp Business accounts must be billed in INR (Meta). Each school pays Meta directly (ADR-0043), so pilot schools' WABAs must be migrated. This is an onboarding checklist item, not a feature.
- **January-March 2027:** tuition fee certificate requests for FY 2026-27, the first year under the Income-tax Act 2025 numbering (FM-06).
- **June 2027:** first academic year change for pilot schools with fees on (FM-01 must be live before it).
- **July 2027 (expected):** Thalliki Vandanam payout. Many parents pay first-term fees after it, so schools may want the first instalment's due date and reminder offsets set accordingly (a setup tip, no feature).
- **Unknown:** any new APSERMC school fee block or a Telangana fee act. Watch, and use FM-10 if either lands.

## 8. Questions for the owner

1. FM-05: allow one write form (UTR report) on parent pages, amending ADR-0046's read-only rule?
2. FM-12: reopen D10 for RTE reimbursement claims only?
3. FM-01: may automatic WhatsApp reminders include earlier-year dues, and is "write-off" the right name for management waivers of old dues?
4. FM-03: one Tally party per student, or one receivable ledger? (Ask the pilot accountant or CA.)
5. Add to the pilot interviews (docs/18 §5): share of sibling families, cheque use, sponsor-paid fees, transport fee bands, how many parents ask for tax fee certificates.

## Sources (accessed 2026-11-09)

- AP HC strikes down GOs fixing private school fees: https://www.livelaw.in/news-updates/andhra-pradesh-high-court-strikes-down-govt-orders-fixing-fees-for-private-unaided-schools-based-on-geographical-location-188511 and https://www.thenewsminute.com/andhra-pradesh/andhra-hc-strikes-down-fee-limit-pvt-schools-asks-govt-prepare-new-structure-159239
- APSERMC fee orders and warnings: https://news.careers360.com/andhra-pradesh-government-fixes-school-intermediate-college-fee/amp ; https://www.inkl.com/news/schools-colleges-warned-against-collecting-more-than-fee-fixed-by-panel ; https://www.thenewsminute.com/amp/story/andhra-pradesh/pvt-schools-ap-flouting-govt-norms-charging-excessive-fees-regulatory-commission-118395
- APHERMC appointments (2026, higher education): https://www.deccanchronicle.com/amp/southern-states/andhra-pradesh/andhra-govt-issues-orders-appoints-members-for-aphefrmc-1971129
- Telangana fee bill status: https://www.deccanchronicle.com/amp/southern-states/telangana/telangana-cabinet-meet-on-may-21-may-take-up-private-school-fee-bill-1956521 ; https://www.deccanchronicle.com/southern-states/telangana/commission-submits-draft-bill-on-school-fee-regulation-1856457 ; https://telanganatoday.com/telangana-private-schools-reject-proposal-for-8-fee-hike-every-two-years
- GST entry 66 and school transport rulings: https://www.taxmann.com/post/blog/no-gst-on-services-of-picking-up-and-dropping-of-school-children-staff-of-higher-secondary-school-aar/ ; https://taxguru.in/?p=865492
- Section 123 / Schedule XV (secondary): https://cleartax.in/s/section-123-income-tax-act-2025 ; https://www.policybazaar.com/income-tax/section-123-income-tax-act-2025/
- AP RTE: https://www.thenewsminute.com/andhra-pradesh/andhra-govt-s-25-rte-quota-plan-hits-roadblock-unhappy-pvt-schools-move-court-174158 ; https://www.newsx.com/education/ap-rte-result-2026-declared-check-first-phase-lottery-list-admission-dates-how-to-download-188936 ; https://www.thehansindia.com/andhra-pradesh/8583-students-selected-for-free-admissions-in-pvt-schools-983852
- Thalliki Vandanam (aggregators): https://www.apteachers.in/2026/05/talliki-vandanam-status-check-2026.html ; https://theprint.in/politics/naidu-rolls-out-thalliki-vandanam-expanding-jagans-amma-vodi-scheme-24-lakh-more-students-covered/2656370
- NPCI UPI merchant QR parameters: https://www.npci.org.in/PDF/npci/upi/circular/2017/Circular18_BankCompliances_to_enbaleUPIMerchantecosystem_0.pdf
- WhatsApp pricing: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing.md ; https://m.aisensy.com/blog/whatsapp-pricing-update-october-2026/
- RBI PA Directions (from docs/18): https://indiacorplaw.in/2025/10/09/decoding-rbis-overhaul-of-the-payment-aggregator-directions/
