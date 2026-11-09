# Accountant guide

English only; Telugu material is deferred ([ADR-0036](../adr/ADR-0036-english-first-telugu-hidden.md)).
All examples are synthetic. Start with the [common section](README.md#things-every-staff-member-should-know)
on signing in and "Confirm it's you".

The school is always the merchant: parents pay the school's own bank, UPI or Razorpay account.
SchoolOS never holds money. A receipt is never edited or deleted; mistakes are fixed by a
cancellation or refund that a **second person** approves.

## What your role can do

- **Fees** menu: **Collect fees**, **Receipts**, **Fee approvals**, **Daily collection**,
  **Fee dues**, **Reconcile UPI**, **Online reconciliation**, **Fee setup**.
- **School admin**: **Fee settings**, **UPI QR**, **Tally connector** and the plan and billing page.
- Approve concessions, cancellations and refunds that **someone else** asked for (confirm it's you).
- Ask the school about fees (numbers from the fee ledger).

You do not send WhatsApp reminders or payment links: the principal does that from **Fee dues**.
You cannot see student identity details beyond what fees need.

## 1. Start of year: fee setup

1. Open **Fee setup**. Under **Fee heads**, select **Add fee head** for each head (for example code
   TUI, name "Tuition fee"). A head marked **Taxable (GST)** is never collected with a SchoolOS
   receipt; invoice it in Tally.
2. Under **Fee plans**, select **New fee plan**. Choose the **Academic year** and **Class**, type
   each head's amount per instalment, name the instalments and their due dates, and choose the
   **Late fee** rule.
3. Select **Activate**. An active plan cannot be edited; changes are made with **New version**.
4. Select **Apply to class…**, check the **Preview of applying the plan**, then **Apply now**.
5. In **Fee settings**, set the receipt paper, the GST line and the reminder days. On **UPI QR**,
   enter the school's UPI details so the counter can show a QR.

If a plan changes mid-year, make a **New version**, activate it, then **Propose differences**: each
student's change becomes an adjustment that a second person approves.

## 2. Every day: the counter

1. Open **Collect fees**. Press **/** or **F2** for the search box.
2. Type at least two letters of the name or the admission number (for example SYN-0042).
3. Check **What is due**. The amount is the **Total due now**; type less for a part payment
   (advances are not taken).
4. Choose **Paid by**: **C** cash, **Q** cheque (number, bank and date), **B** bank transfer (bank
   reference), **U** UPI (the 12-digit UTR from the parent's phone).
5. **Where the money goes** uses the oldest due first; select **Change allocation** to change it.
6. Select **Save and print**, hand over the receipt, then **Next student**.

## 3. Every day: close the day

1. Open **Daily collection** for today.
2. Count the cash drawer against **Cash to hand over** and the cheques against
   **Cheques to hand over**. Check **By mode** and **By collector**.
3. Select **Print (A4)** for the file, and **Download day book (CSV)** if you need it in a sheet.
4. Select **Close day** and confirm. After closing, no more receipts can be dated today; payments go
   to the next day.

## 4. Concessions, cancellations and refunds

Ask (anyone with the permission):

- **Concession**: on the student's **Fee account**, select **Request concession**, choose the fee
  line, an amount or a percentage, and the reason.
- **Adjustment**: **Request adjustment** on the fee account.
- **Cancellation**: open the receipt on **Receipts**, select **Request cancellation**, choose the
  reason and explain what happened.
- **Refund**: on the receipt, select **Request refund** and the amount.

Decide (you, or the principal):

1. Open **Fee approvals**.
2. Read the reason. Select **Approve** or **Reject**, and confirm it's you.
3. You cannot decide a request you made yourself.

Nothing changes on the account until the request is approved. An approved refund writes a refund
voucher today.

## 5. Dues and follow-up

**Fee dues** lists overdue fees by student and class, with the oldest due date and the last payment.
Filter by class and print it for the principal. The principal can then **Send reminder** or
**Send payment link** on WhatsApp to the parents who agreed.

**Fee reports** answers month-end and year-end questions. Pick the dates (at most one year):

- **Collections**: what was collected by day, head, mode and collector. Cancelled receipts keep
  their numbers and are listed apart; refunds are taken off. Select **Print (A4)** for the file.
- **Demand and balance (DCB)**: for a school year, per class and section, what was charged,
  conceded, written off, collected and still owed, and the earlier years' dues still open.
- **Registers**: concessions, adjustments (write-offs show as "Written off by management"),
  cancellations and refunds, with who asked and who approved.
- **Ageing**: overdue amounts by how long they are overdue, earlier years in their own column.
- **Year-end pack**: everything above for a financial year in one ZIP for the auditor or the
  trust's CA, with a check that the receipt numbers have no gaps.

Downloads (CSV or Excel) ask you to sign in again with your second step and are recorded.
Typed reasons stay out of a register download unless you tick **Include typed reasons**.

## 6. Reconciliation

**UPI** (**Reconcile UPI**):

1. On **Bank statement**, choose the **Statement file** from the bank and select **Read statement**.
2. Open the statement with **Review**. SchoolOS proposes matches between credits and UPI receipts.
3. Tick the right ones and select **Confirm matches**. Receipts themselves are never changed.
4. For a credit with no receipt, use **Match receipt**, or **Not a fee** for interest or grants.
5. **UPI exceptions** lists UPI receipts not yet seen in the bank. Look into each one.

**Online payments** (**Online reconciliation**): payments made through the school's Razorpay
account become receipts by themselves. This page shows anything that needs a person.

## 7. Tally

If the school keeps accounts in Tally, the **Tally connector** page shows whether the office PC's
Tally edge agent is sending data and links Tally ledgers to students. The agent only reads Tally; it
never writes to it.

## 8. Ask the school

Ask, for example, "What is the total overdue for Class VII?" or "What does SYN-0042 owe?". The answer
comes from the fee ledger with **Sources**. "Not found in the school records you can access" means
there is no matching record you may see; SchoolOS does not guess.

## If something goes wrong

- "Today's collection is closed": the day was closed. Receipts start again tomorrow.
- "No student with a fee account matches": check the spelling or admission number; a new student may
  not have a plan applied yet (section 1, step 4).
- A receipt has the wrong amount or mode: never re-issue on top. Request a cancellation, wait for
  approval, then issue a correct receipt.
- The counted cash does not match: do not close the day. Check **By collector** and the receipts of
  the day first.
- "Someone else must decide this": you made the request; another approver must decide it.

## Cheat sheet (print this page)

**Accountant** · SchoolOS · synthetic examples only

| I want to… | Go to | Press |
|---|---|---|
| Take a fee | **Collect fees** | **/** or **F2** → search → **C/Q/B/U** → **Save and print** → **Next student** |
| Close the day | **Daily collection** | count cash → **Print (A4)** → **Close day** |
| Give a concession | student's **Fee account** | **Request concession** (someone else approves) |
| Cancel a wrong receipt | **Receipts** → receipt | **Request cancellation** |
| Refund | **Receipts** → receipt | **Request refund** |
| Approve a request | **Fee approvals** | **Approve** / **Reject** → **Sign in again** |
| See overdue fees | **Fee dues** | filter, print for the principal |
| Match UPI with the bank | **Reconcile UPI** | **Read statement** → **Review** → **Confirm matches** |
| Set up fees for the year | **Fee setup** | **Add fee head** → **New fee plan** → **Activate** → **Apply now** |
| Ask about fees | **Ask the school** | **Ask**, check **Sources** |

**Remember:** a receipt is never edited · two people for every concession, cancellation and refund ·
close the day only after counting · never type an Aadhaar number · leaving the desk → **Lock now**.
