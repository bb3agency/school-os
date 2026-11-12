# Office guide: office staff and office admin

English only; Telugu material is deferred ([ADR-0036](../adr/ADR-0036-english-first-telugu-hidden.md)).
All examples are synthetic. Start with the [common section](README.md#things-every-staff-member-should-know)
on signing in and "Confirm it's you".

## What your role can do

| Task | Office staff (clerk) | Office admin |
|---|---|---|
| Add and edit students (not identity fields) | Yes | Yes |
| Import a spreadsheet: upload, map columns, check rows | Yes | Yes |
| Import: **Add rows** and **Undo import** | No, ask the office admin | Yes |
| Run checks, resolve problems | Yes | Yes |
| Accept a problem as it is (waive) | No | Yes (confirm it's you) |
| Request a correction of an identity field | Yes | Yes |
| Approve a correction | No | No (principal or owner) |
| Prepare certificates | Yes | Yes |
| Collect fees at the counter | Yes | Yes |
| Request a fee concession | No | Yes |
| Draft parent notices | Yes | Yes |
| Send an approved notice on WhatsApp | No | Yes |
| Record WhatsApp agreement from a parent | Yes | Yes |
| Attendance | No | Yes (whole school) |
| Log privacy requests | No | Yes |
| Board and portal exports | No | Yes (confirm it's you) |
| Invite staff (**Users and roles**) | No | Yes (confirm it's you) |
| Ask the school | Yes | Yes |

Office admins must use two-step verification (MFA). Office staff may.

## 1. Onboarding: import the admission register

Use this at the start, and whenever you get a fresh list from your old software.

1. Open **Imports** and select **Upload a spreadsheet** (Excel or CSV).
2. **Upload**: choose the file, or drag it on the box, then select **Upload and read the file**.
3. **Map columns**: for each column, choose which field it fills (for example "Adm. No." fills the
   admission number). If you import the same layout again, use **Save as a template** and next time
   **Fill in from a template**.
4. **Check rows**: select **Check the rows**. Checking saves nothing to student records.
5. Look at the rows with errors. Fix them in the spreadsheet and **Upload the file again**.
6. **Add** (office admin): select **Add rows**, read the summary, then confirm.

**The 24-hour undo.** After rows are added, the import page shows "If something is wrong, you can
undo this import until …". Select **Undo import** to remove what it added. Undo is refused once the
24 hours have passed or when someone has already changed those records; then correct individual
records instead.

**Register photos.** If you only have a paper register, **Register photos** reads photographed
pages. Every read value waits in a verification queue for a person to confirm it.

### If something goes wrong

- "Some rows have errors": the rows marked with errors are not added. Fix them in the file and upload
  it again.
- "The file was blocked": the virus check stopped it. Save the sheet again from Excel and retry.
- "you can't add new students": your role cannot create students; ask the office admin.
- "The 24 hours to undo have passed": correct the records one by one, or ask the principal.

## 2. Checks, readiness and the portal calendar

**Check before submitting** lists every mismatch SchoolOS finds between your records and other
sources (admission register, Aadhaar as printed, UDISE+, board registration).

1. Open **Check before submitting**. **Blockers** must be fixed before a portal submission;
   **Warnings** are worth checking.
2. After each import, start a fresh check with **Start check** (on **Check student records**).
3. Open a problem. Read **What was found**, **Values from each source** and **How to fix it**.
4. Fix it in one of three ways:
   - The record is wrong in an identity field: select **Request a correction** (section 3).
   - You corrected it some other way: select **Resolve**, write **What was done**, and confirm.
     Resolving a blocker asks you to confirm it's you.
   - The values are right as they are (for example a known spelling difference): office admin
     selects **Accept as it is** and writes why. This asks you to confirm it's you.
5. Use **Print** on the list to take a paper copy to the class teacher.

Nothing on the student's record changes when you resolve or accept. The next check looks again;
if the same problem is found, it comes back.

**Board readiness** shows, for each board or portal, how many students are ready. Select
**Start the check** to refresh it. Open a section to see each student; **Print parent slip** or
**Print slips for this section** gives slips for parents to correct their details.

**Portal calendar** lists portal windows (UDISE+, board registration, the AP SSC exam fee and
similar) and the school's own deadlines (**School administration**: recognition renewal on PSIS,
CBSE SARAS and OASIS, the DPDP duties; **RTE admissions**: the lottery rounds). Dates marked
"Expected dates are not official" come from last year. When the circular arrives, select
**Confirm the dates**, type the dates and choose **Who owns the work**. That person gets a task and
reminders 30, 14, 7 and 1 days before the window closes (some windows remind on other days). **Add
our own event** adds a school date. Before paying the AP SSC exam fee, open **Board readiness** and
fix what is not ready: the board takes its data from UDISE+.

**UDISE+ corrections** lists the names, dates of birth and gender that are wrong in UDISE+
(the school cannot change them there; the MEO office does). Choose the check, then:

1. Select **Prepare request**. Every correction not yet asked for goes into one request. Nothing in
   the school's records changes.
2. The principal or office admin opens the request and selects **Print letter for the MEO**, then
   **Open letter to print** (it asks you to confirm it's you). The letter lists each student's PEN,
   the value in UDISE+, the correct value and the evidence the school holds. It never shows an
   Aadhaar number, only "Matches Aadhaar: yes/no".
3. After you hand it over, select **Record as sent**, the date and the office.
4. Import the UDISE+ student list again later: each line shows **Corrected** or **Still wrong**.
   Then select **Mark done** (or **Mark rejected**).

**Letters to fix owners** prints the other letters a readiness difference needs (FA-08). They are
fixed templates filled from the school's records, not written by AI, and nothing in the records
changes:

1. **Letters to parents (Aadhaar update)**: one letter per student whose Aadhaar card does not
   match the school's record. It names the detail, the value in the school's record and what
   differs, and asks the parent to update Aadhaar at an Aadhaar centre and bring a copy back by a
   date. It never shows the Aadhaar number or the Aadhaar details. Select **Print letters for this
   section** or **Print letter** for one student, then, after handing them out, **Record letters
   given**: the follow-up list then shows the parent as contacted by letter.
2. **Letter to the board office**: the board registration corrections in one table. The principal
   or office admin chooses who it is addressed to, selects **Prepare the board letter** (it asks
   you to confirm it's you) and **Open the board letter**; after sending it, **Record letter
   sent**.

**Importing the UDISE+ student list.** Upload the Excel file as downloaded from the UDISE+ Student
Module; you do not need to add an admission number column. SchoolOS finds each row's student by
PEN, then by the same name, date of birth and class. **Matched by** says how. A row marked
**Choose the student** found no single student: select **Choose student** and search by name or
admission number, or **Skip row**. The import never creates a student and never changes the
admission register; UDISE+ values are kept beside it for the checks.

## 3. Correction requests (two people)

Identity fields (name, date of birth, parent names and similar) are never changed directly.

1. Open the student and select **Request a change** next to the field, or open the problem on
   **Check before submitting** and select **Request a correction**. (You can also start from
   **Correction requests** → **Request a correction**.)
2. Under **What to correct**, choose the field and type the **Correct value** exactly as on the
   evidence document.
3. Under **Evidence**, select **Upload a file now** and attach a scan of the certificate or register
   page.
4. Select **Send for approval**. The principal or owner approves or rejects it.
5. Watch **Correction requests**. A rejection shows the reason; fix and send a new request.

### If something goes wrong

- "Someone else must decide this": you cannot approve your own request.
- "A request is already waiting": open the waiting request instead of making a new one.
- "Remove the Aadhaar number": never type full Aadhaar numbers in any field.

## 4. Certificates

1. Open the student and select **Issue a certificate**.
2. Under **Which certificate?**, choose transfer certificate (TC), bonafide, study or conduct.
3. Fill in **Details for this certificate**. Check **What it will print**.
4. If **Fix these first** appears, the certificate cannot be issued until those problems are fixed
   (usually open blockers on **Check before submitting**).
5. For a TC, select **Send for approval**: the principal approves and issues it. For other
   certificates, select **Issue certificate**.
6. Open the certificate and use **Open print view** to print it.

A TC takes the student off the rolls ("The student leaves the rolls"). For a lost certificate,
use **Issue a duplicate**; a certificate issued by mistake can be cancelled. Both are recorded in
the registers.

## 5. Fees counter

Only if your school uses SchoolOS for fees.

1. Open **Collect fees**. Press **/** or **F2** to jump to the search box.
2. Type at least two letters of the name or the admission number (for example SYN-0042). Use the
   arrow keys and **Enter**.
3. Check **What is due**. The amount is filled with **Total due now**; type less for a part payment.
4. Choose **Paid by**: **C** cash, **Q** cheque, **B** bank transfer, **U** UPI. For UPI, type the
   12-digit **UPI reference (UTR)** from the parent's phone.
5. Select **Save and print**. Hand over the receipt, then **Next student**.

A wrong receipt is never edited. Open it on **Receipts** and select **Request cancellation**; the
principal or accountant approves. If "Today's collection is closed" appears, the accountant has
closed the day; receipts start again tomorrow.

Office admins can also **Request concession** on a student's fee account; a second person approves.

## 6. Parent notices and WhatsApp

1. Open **Parent notices** and select **Start a notice**. Describe what the notice is about. Do not
   include personal details.
2. AI drafts the notice. Edit the text, then **Save draft**.
3. The principal or owner checks it and selects **Approve notice**.
4. On the approved notice, either **Print (A4)** or (office admin) use **Send on WhatsApp**:
   choose the sections, select **Check who will get it**, read the cost and who is skipped, then
   **Send to … parents**.
5. **Messages** shows delivery. **Withdraw notice** stops the parent link and cancels messages not
   yet sent.

Only parents who agreed get WhatsApp messages. To record a parent's decision, open the student,
find **WhatsApp messages**, select **Record a decision**, choose **Messages about**, the
**Decision** and its date, then **Save**. **WhatsApp join slips** on **Messages** prints slips
parents can use to join.

To stop a parent link sent by mistake, open **Messages**, find the message and select **Stop link**.
This cannot be undone.

## 7. Circulars and tasks

1. Open **Circulars** and select **Upload a circular** (a PDF or photo from the department).
2. SchoolOS reads it and shows **What the circular says** and **Suggested deadlines**.
3. For each suggestion: **Confirm as task** or **Dismiss**.
4. When every suggestion is decided, select **Mark reviewed**.
5. Your work is on **Tasks**. **Add a task** adds one by hand.

If the circular shows "Read this circular yourself", the AI could not read it: add its deadlines on
**Tasks** yourself.

## 8. Attendance (office admin)

See the [class teacher guide](class-teacher.md#1-attendance): office admins can mark and print
attendance for every section.

## 9. Ask the school

1. Open **Ask the school**, type your question (for example "How many students in Class VII-B have
   no date of birth recorded?") and select **Ask**.
2. Read the answer and its **Sources**. Open a source to check it.
3. If the answer is "Not found in the school records you can access", SchoolOS did not guess: the
   records you may see do not hold it.
4. Tell us with **Was this answer helpful?**.
5. **Search documents** searches the school's documents without AI.

Office admins can **Save as verified answer** for questions staff ask often.

## 10. Privacy requests (office admin)

When a parent asks to see, correct or delete their child's data, or complains:

1. Open **Requests and complaints** and select **Log a request**.
2. Fill **What they asked for**, **Who asked**, **Who it is about**, **Received on** and
   **What they asked** (in their words; never an Aadhaar number). Record
   **How you checked who they are** now or later.
3. Select **Log the request**, then **Print acknowledgement** and hand it over.
4. The request shows a **Due** date. The principal or owner decides. If the person withdraws, use
   **Mark as withdrawn**.

## Cheat sheet (print this page)

**Office staff and office admin** · SchoolOS · synthetic examples only

| I want to… | Go to | Press |
|---|---|---|
| Import the admission register | **Imports** | **Upload a spreadsheet** → **Check the rows** → **Add rows** (admin) |
| Undo an import (24 h) | **Imports** → the import | **Undo import** (admin) |
| See mismatches | **Check before submitting** | **Start check** |
| Fix a mismatch | the problem | **Request a correction** / **Resolve** / **Accept as it is** (admin) |
| Fix a name or date of birth | student page (**Request a change**) or problem | evidence → **Send for approval** |
| See portal readiness | **Board readiness** | **Start the check**, **Print parent slip** |
| Set portal dates | **Portal calendar** | **Confirm the dates** → **Who owns the work** |
| Issue a certificate | student page | **Issue a certificate** → **Send for approval** (TC) / **Issue certificate** |
| Take a fee | **Collect fees** | **/** search → **C/Q/B/U** → **Save and print** |
| Fix a wrong receipt | **Receipts** | **Request cancellation** |
| Notice to parents | **Parent notices** | **Start a notice** → **Save draft** → (approved) **Send on WhatsApp** |
| Record a parent's WhatsApp yes/no | student page | **Record a decision** |
| Read a circular | **Circulars** | **Upload a circular** → **Confirm as task** → **Mark reviewed** |
| Ask a question | **Ask the school** | **Ask**, check **Sources** |
| Log a privacy request | **Requests and complaints** | **Log a request** → **Print acknowledgement** |

**Remember:** never type an Aadhaar number · nothing changes an official record without a person ·
"Confirm it's you" → **Sign in again** · leaving the desk → **Lock now** · stuck → **Support**.
