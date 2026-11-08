# Staff guides (English)

| Field | Value |
|---|---|
| Version | 0.1 · 2026-11-08 (draft for the pilot) |
| Decision | Lead decision D76 ([docs/14](../14-roadmap.md), lead decisions table); pilot gate item 10a ([checklist](../security/pilot-readiness-checklist.md)) |
| Language | English only. Telugu staff material is deferred with the rest of Telugu ([ADR-0036](../adr/ADR-0036-english-first-telugu-hidden.md)): while `SOS_TELUGU_ENABLED` is off, every screen these guides describe is in English. |
| Data | Every example is synthetic (for example "Synthetica Student", admission number SYN-0042, Class VII-B). Never use real student details in training. Train on a school made by `make seed-synthetic`. |

These guides help school staff do their daily and term work in SchoolOS. Each one is written from
the screens as they are built today: the menu names, page titles and button names match what staff
see. The owner (or the person running the pilot) still runs the training session; these guides are
the handout.

## Which guide do I read?

| Your role in SchoolOS | Guide | Cheat sheet |
|---|---|---|
| Office staff (office clerk) and office admin | [Office guide](office.md) | [Office cheat sheet](office.md#cheat-sheet-print-this-page) |
| Principal (and the school owner for approvals and privacy) | [Principal guide](principal.md) | [Principal cheat sheet](principal.md#cheat-sheet-print-this-page) |
| Accountant | [Accountant guide](accountant.md) | [Accountant cheat sheet](accountant.md#cheat-sheet-print-this-page) |
| Exam coordinator | [Exam coordinator guide](exam-coordinator.md) | [Exam coordinator cheat sheet](exam-coordinator.md#cheat-sheet-print-this-page) |
| Class teacher (and teacher) | [Class teacher guide](class-teacher.md) | [Class teacher cheat sheet](class-teacher.md#cheat-sheet-print-this-page) |

Your role decides which menu items you see. If a guide mentions a page you do not have, your role
does not include it: ask the principal or office admin, who manage **Users and roles**.

## Things every staff member should know

### Signing in

1. Open SchoolOS in Chrome or Edge and select **Sign in to the school office**.
2. Sign in with your own account. Never share an account or sign in for someone else.
3. If you work for more than one school, pick the school on **Choose your school**.
4. If a school has invited you, accept it under **Invitations waiting for you** with **Accept**.

**Two-step verification (MFA).** Owners, principals and office admins must use a second step (an
authenticator app on your phone). The first time, the sign-in page asks you to set it up. If you
see "Your account needs two-step verification (MFA)", set it up and sign in again.

**"Confirm it's you" (step-up).** Some actions are sensitive: approving a correction, waiving a
problem, downloading an export, approving a fee concession, inviting staff and similar. SchoolOS then
shows **Confirm it's you**:

1. Select **Sign in again**. A small window opens.
2. Sign in with your password and your second step.
3. The page continues by itself. What you typed is kept.

If the window was blocked, allow pop-ups for SchoolOS, or select **Continue in this tab** (what you
typed on that page is then lost).

**Shared office computers.** After a while without use SchoolOS asks **Are you still there?** and
then signs you out. When you leave the desk, use **Lock now** from your account menu. **Sign out
everywhere** signs you out on every computer and phone.

### The menu

The menu on the left is grouped: **Overview**, **Records**, **Checks and submissions**, **Ask**,
**Circulars and tasks**, **Classroom**, **Fees**, **Privacy** and **School admin**. **Home** shows the
work waiting for you.

### Rules for everyone

- SchoolOS never changes an official record by itself. It lists problems; a person decides.
- Never type an Aadhaar number anywhere. SchoolOS keeps only the last four digits and masks any
  full Aadhaar number it finds.
- Every important action is recorded in the school's audit log with your name.
- Identity details (name, date of birth and similar) change only through a **correction request**
  that someone else approves.

### If something goes wrong

- Read the message on the screen: it says what to do next.
- Reload the page if it says "This could not be loaded".
- If you are stuck, open **Support** in the menu and create a ticket. Do not put student names or
  photos of records in the ticket; describe the page and the step.
- SchoolOS support can only look at your school after the principal or owner approves a request on
  **Support access**, and the school sees every page they open.

## Related documents

- What the product does: [docs/02-PRD.md](../02-PRD.md)
- Roles and permissions: [docs/07 §6.2](../07-security-architecture.md) and `apps/api/app/authz/roles.yaml`
- Screen design rules: [docs/17-ui-design-system.md](../17-ui-design-system.md)
