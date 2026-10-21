# ADR-0053: Portal calendar and readiness reminders (UDISE+, BSEAP SSC, CBSE, CISCE, APAAR)

| Field | Value |
|---|---|
| Status | **Accepted (lead, 2026-10-21; owner may revisit)** (docs/18 §8 D45) |
| Date | 2026-10-21 |
| Deciders | Lead engineer (for the product owner, who may revisit) |
| Amends / supersedes | none. Builds on ADR-0040 (readiness profiles and summaries) and the M4 tasks and reminders (FR-TASK-001..007) |

## Context

- docs/18 §3 R3 item 4: readiness reminders tied to the portal calendar (UDISE+ freeze, SSC window, CBSE registration). The pain is dated: for March 2026 the AP SSC board took nominal rolls from UDISE+ with a three-day correction window, and the next window is likely February 2027 (docs/18 §1.3).
- Dates are not published in one place, move every year and differ by state; the R1 readiness profiles already carry `source` URLs and `verified: false` (D3).
- The R1 readiness check gives per-profile summaries ("142 of 160 ready", `GET /dq/readiness`); M4 has tasks with one owner, a due date and a daily in-app reminder job.

## Decision

1. **A versioned calendar file per academic year.** `app/dq/config/portal_calendar.yaml`: events with `key` (for example `udise_plus_2027_freeze`, `bseap_ssc_2027_nominal_roll`, `cbse_registration_2027`, `cbse_loc_2027`, `cisce_registration_2027`, `apaar_generation_drive`), portal, label, the readiness profile keys it concerns, board and classes it applies to, an **expected window** (`opens_on`, `closes_on`, either may be approximate with `approximate: true`), `source` URLs, `verified: false`, and lead days for reminders (default 30, 14, 7 and 1 days before the window closes). Nothing in the file claims a date is official.
2. **The school confirms its dates.** `ops.portal_calendar_dates` (C1): per school and event, the confirmed `opens_on`/`closes_on`, where the date came from (a circular document, a portal screen, a phone call; optional document id), who confirmed it, and the owner of the work (a member). The school may also add its own events. Confirmed dates win over the file; an unconfirmed event shows as "Expected around …".
3. **Reminders create staff tasks and in-app notifications only.** A daily job (07:30 IST) finds events applicable to the school (its boards, its classes, profiles it uses), and at each lead day creates or updates **one** M4 task for the event's owner (`ops.tasks.source = portal_calendar`, due on the closing date, linked to the event) and sends one in-app notification to the owner and to holders of `dq.readiness.manage`, with the readiness summary of the linked profiles as counts only ("UDISE+ freeze in 14 days: 142 of 160 students ready"). Idempotent per school, event, year and lead day (`ops.portal_reminders`). No message ever goes to parents from this job; the readiness slips (R1) remain the way to involve parents.
4. **Shown where people look.** The readiness overview and each profile page show the next window for that profile with its state (expected, confirmed, open now, closed); the school's calendar screen lists all events for the year.
5. **Circulars can confirm a date.** When a circular's AI reading suggests a deadline (M4), a person can link the confirmed task to a portal event, which records the date as confirmed from that circular (still a person's action; invariant 9).
6. Permission `readiness.calendar.manage` (principal, office admin, exam coordinator) to confirm dates, add events and choose owners; reading needs `dq.readiness.read` (docs/07 §6.8). No new schema; no parent-facing output.

## Consequences

- Good: nobody has to remember the window; the reminder carries the number that matters (how many students are not ready yet) and lands as a task with an owner.
- Bad / costs: the shipped calendar is a guess until confirmed each year; a release per academic year updates the file.
- Follow-up: each year's file is refreshed from circulars and confirmed by pilot schools; if a state publishes machine-readable dates, a later ADR may read them.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Hard-coded dates in code | Invariant 13 (config in versioned files); dates move every year |
| Reminders by WhatsApp to staff or parents | Staff reminders belong in the app (tasks); parent messages are utility messages about their child, not portal deadlines (ADR-0043) |
| Let schools type every date themselves | Most will not; a shipped expectation plus confirmation gives a useful default |

## Related requirements

FR-RDY-001..010, FR-TASK-001..007, FR-DQ-040..048; docs/02 C31; docs/03 §3.30; docs/05 §16.6; docs/07 §6.8; docs/09 "R3 planned endpoints"; docs/17 §5.10.
